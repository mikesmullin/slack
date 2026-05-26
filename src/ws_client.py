"""Direct WebSocket client for Slack — no browser required.

Connects to wss://wss-primary.slack.com using the xoxc token and d cookie
stored in .tokens.yaml (extracted from the browser session by the server).

Connection parameters are reverse-engineered from browser HAR recordings.
gateway_server is discovered by probing shards and cached in .tokens.yaml.

Usage:
    client = SlackWSClient()
    async for event in client.events():
        print(event["type"], event)
"""

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import AsyncIterator, Optional
from urllib.parse import urlencode

import websockets
import yaml

logger = logging.getLogger(__name__)


class SlackAuthError(RuntimeError):
    """Raised when the Slack gateway rejects our credentials (invalid_auth)."""


WORKSPACE_ROOT = Path(__file__).parent.parent
TOKENS_FILE = WORKSPACE_ROOT / ".tokens.yaml"

WS_BASE = "wss://wss-primary.slack.com/"
WS_ORIGIN = "https://app.slack.com"
PING_INTERVAL = 9  # seconds — server drops connection after ~30s without activity

# start_args passed in the WS URL query string.
# connect_only=true: don't block on initial channel sync — events still flow.
START_ARGS = (
    "?agent=client&org_wide_aware=true&agent_version=0"
    "&eac_cache_ts=true&cache_ts=0&name_tagging=true"
    "&only_self_subteams=true&connect_only=true&ms_latest=true"
)


def _load_tokens() -> dict:
    if not TOKENS_FILE.exists():
        raise SlackAuthError(
            ".tokens.yaml not found — credentials unavailable"
        )
    return yaml.safe_load(TOKENS_FILE.read_text()) or {}


def _save_tokens(data: dict) -> None:
    TOKENS_FILE.write_text(yaml.dump(data, default_flow_style=False))
    TOKENS_FILE.chmod(0o600)


def _gateway_base(enterprise_id: str) -> str:
    """Derive gateway_server base ID from enterprise/team ID.

    Enterprise grid IDs start with 'E'; the gateway server uses the same
    suffix but with a 'T' prefix (e.g. EHLH96WBS -> THLH96WBS).
    Regular workspace team IDs already start with 'T'.
    """
    if enterprise_id.startswith("E"):
        return "T" + enterprise_id[1:]
    return enterprise_id


def _build_url(
    token: str,
    enterprise_id: str,
    gateway_server: str,
    frt: Optional[str] = None,
) -> str:
    params: dict = {}
    if frt:
        params["frt"] = frt
    params.update({
        "token": token,
        "sync_desync": "1",
        "slack_client": "desktop",
        "start_args": START_ARGS,
        "no_query_on_subscribe": "1",
        "flannel": "3",
        "lazy_channels": "1",
        "gateway_server": gateway_server,
        "enterprise_id": enterprise_id,
        "batch_presence_aware": "1",
    })
    return WS_BASE + "?" + urlencode(params)


async def _auth_test(token: str, cookie: str) -> dict:
    import httpx
    headers = {
        "Authorization": f"Bearer {token}",
        "Cookie": f"d={cookie}",
    }
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(
            "https://slack.com/api/auth.test",
            headers=headers,
            data={"token": token},
        )
    return r.json()


async def discover_gateway_server(
    token: str,
    cookie: str,
    enterprise_id: str,
) -> Optional[str]:
    """Probe shards 1–5 to find a working gateway_server. Returns None on failure."""
    base = _gateway_base(enterprise_id)
    headers = {"Origin": WS_ORIGIN, "Cookie": f"d={cookie}"}

    for shard in range(1, 6):
        gw = f"{base}-{shard}"
        url = _build_url(token, enterprise_id, gw)
        logger.debug(f"Probing gateway_server={gw}")
        try:
            ws = await websockets.connect(url, additional_headers=headers, open_timeout=8)
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=6))
            await ws.close()
            if msg.get("type") == "hello":
                logger.info(f"gateway_server={gw} works")
                return gw
        except Exception as e:
            logger.debug(f"Shard {gw} failed: {e}")

    return None


class SlackWSClient:
    """Long-running direct WebSocket connection to Slack.

    Maintains the connection with periodic pings, handles reconnection
    using frt tokens, and yields every received event dict.
    """

    def __init__(self) -> None:
        self._frt: Optional[str] = None
        self._ping_id: int = 1

    async def events(self) -> AsyncIterator[dict]:
        """Yield Slack WS events indefinitely, reconnecting on drop."""
        tokens = _load_tokens()
        token: str = tokens["token"]
        cookie: str = tokens.get("cookie", "")

        # Resolve enterprise_id — call auth.test if not cached
        enterprise_id: str = tokens.get("enterprise_id", "")
        if not enterprise_id:
            logger.info("enterprise_id not cached, calling auth.test...")
            info = await _auth_test(token, cookie)
            if not info.get("ok"):
                raise RuntimeError(f"auth.test failed: {info.get('error')}")
            enterprise_id = (
                info.get("enterprise_id")
                or info.get("team_id")
                or ""
            )
            if not enterprise_id:
                raise RuntimeError("Could not determine enterprise_id from auth.test")
            tokens["enterprise_id"] = enterprise_id
            _save_tokens(tokens)
            logger.info(f"Cached enterprise_id={enterprise_id}")

        # Resolve gateway_server — probe if not cached
        gateway_server: str = tokens.get("gateway_server", "")
        if not gateway_server:
            logger.info("Discovering gateway_server by probing shards...")
            gateway_server = await discover_gateway_server(token, cookie, enterprise_id)
            if not gateway_server:
                raise RuntimeError(
                    "Could not discover a working gateway_server (tried shards 1–5)"
                )
            tokens["gateway_server"] = gateway_server
            _save_tokens(tokens)
            logger.info(f"Cached gateway_server={gateway_server}")

        headers = {"Origin": WS_ORIGIN, "Cookie": f"d={cookie}"}

        while True:
            url = _build_url(token, enterprise_id, gateway_server, frt=self._frt)
            self._frt = None  # consume the frt — will be refreshed on next reconnect_url

            try:
                async with websockets.connect(
                    url,
                    additional_headers=headers,
                    open_timeout=15,
                    ping_interval=None,   # we manage pings manually
                    ping_timeout=None,
                ) as ws:
                    # First message must be hello; error means bad credentials
                    first_raw = await asyncio.wait_for(ws.recv(), timeout=10)
                    first = json.loads(first_raw)
                    if first.get("type") == "error":
                        err = first.get("error") or {}
                        if err.get("code") == 401 or err.get("msg") == "invalid_auth":
                            raise SlackAuthError(
                                f"Slack rejected credentials: {err.get('msg', 'invalid_auth')}"
                            )
                        logger.warning(f"WS error on connect (non-auth): {first}")
                        continue  # retry connection
                    elif first.get("type") != "hello":
                        logger.warning(f"Expected hello, got {first.get('type')!r}")

                    logger.info("WebSocket connected")
                    self._first_pong_done = False  # reset per session
                    yield first  # let caller see the hello (auth confirmed)
                    async for event in self._run_session(ws):
                        yield event

            except SlackAuthError:
                raise  # propagate — caller must refresh credentials
            except websockets.exceptions.ConnectionClosed as e:
                logger.warning(f"WebSocket closed ({e.code} {e.reason}), reconnecting...")
                await asyncio.sleep(2)
            except Exception as e:
                logger.error(f"WebSocket error: {e}, reconnecting in 5s...")
                await asyncio.sleep(5)

    async def _run_session(self, ws) -> AsyncIterator[dict]:
        """Pump messages from an open WebSocket, sending pings to keep it alive."""
        last_ping = time.monotonic()

        while True:
            now = time.monotonic()
            time_until_ping = PING_INTERVAL - (now - last_ping)

            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=max(0.5, time_until_ping))
            except asyncio.TimeoutError:
                # Time to send a ping
                await ws.send(json.dumps({"type": "ping", "id": self._ping_id}))
                self._ping_id += 1
                last_ping = time.monotonic()
                continue

            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                logger.debug(f"Non-JSON WS frame: {raw[:80]}")
                continue

            event_type = event.get("type")

            # Cache the latest frt for reconnections
            if event_type == "reconnect_url":
                url = event.get("url", "")
                if "frt=" in url:
                    frt_start = url.index("frt=") + 4
                    frt_end = url.index("&", frt_start) if "&" in url[frt_start:] else len(url)
                    self._frt = url[frt_start:frt_end]
                continue  # don't yield reconnect_url — it's housekeeping

            if event_type == "pong":
                if not getattr(self, "_first_pong_done", False):
                    self._first_pong_done = True
                    yield event  # signal first successful ping-pong to caller
                continue  # don't yield subsequent pongs

            yield event
