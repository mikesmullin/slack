"""slack-chat listen — connect directly to Slack WebSocket without browser.

Receives real-time events and dispatches them to configured signal handlers
(from the `signals:` section of config.yaml).

Usage:
    slack-chat listen                  # connect and print events
    slack-chat listen --raw            # print raw JSON lines to stdout
    slack-chat listen --event message  # filter to specific event types
    slack-chat listen --signals        # also dispatch to signal handlers

Credential handling:
  On startup, if .tokens.yaml is missing or the WS rejects the token,
  `listen` calls `server refresh-session` automatically.  If the browser
  server is not running it starts one in headless mode, waits for auth,
  refreshes credentials, then stops the server — the browser is only needed
  for that brief window.
"""

import asyncio
import json
import logging
import os
import signal as _signal
import subprocess
import sys
from typing import List, Optional

import typer

from ..ws_client import SlackWSClient, SlackAuthError, _load_tokens, _save_tokens
from ..signals import SignalEngine
from ..utils.const import SERVER_URL, PID_FILE, LOG_FILE, WORKSPACE_ROOT
from ..utils.server import get_server_pid

logger = logging.getLogger(__name__)

app = typer.Typer(help="Listen to Slack WebSocket events directly (no browser needed)")


@app.callback(invoke_without_command=True)
def listen_command(
    event: Optional[List[str]] = typer.Option(
        None, "--event", "-e",
        help="Only print/dispatch events of this type (repeatable). Default: all.",
    ),
    raw: bool = typer.Option(
        False, "--raw",
        help="Print raw JSON lines to stdout (one per event).",
    ),
    signals: bool = typer.Option(
        False, "--signals/--no-signals",
        help="Dispatch events to signal handlers defined in config.yaml.",
    ),
    quiet: bool = typer.Option(
        False, "--quiet", "-q",
        help="Suppress human-readable output (useful when --signals is the goal).",
    ),
) -> None:
    """Connect directly to Slack WebSocket and stream real-time events."""
    try:
        asyncio.run(_listen(
            filter_types=set(event) if event else None,
            raw=raw,
            use_signals=signals,
            quiet=quiet,
        ))
    except KeyboardInterrupt:
        if not quiet:
            print("\nDisconnected.", file=sys.stderr)


# ---------------------------------------------------------------------------
# Server lifecycle helpers
# ---------------------------------------------------------------------------

async def _server_is_up() -> bool:
    import httpx
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            r = await c.get(f"{SERVER_URL}/status")
            return r.status_code == 200
    except Exception:
        return False


async def _stop_server(quiet: bool) -> None:
    """Stop the browser server."""
    pid = get_server_pid()
    if pid:
        try:
            os.kill(pid, _signal.SIGTERM)
        except OSError:
            pass
    if PID_FILE.exists():
        PID_FILE.unlink()
    if not quiet:
        print("Browser server stopped.", file=sys.stderr)


async def _refresh_session(quiet: bool) -> bool:
    """Ensure .tokens.yaml has fresh credentials. Returns True if WE started the server.

    If the browser server is already running: POST /refresh-session and return False.
    If not: stop any stale process, start a fresh headless server, wait for auth,
    then POST /refresh-session and return True.

    In both cases, clears cached gateway_server so ws_client re-probes with
    the new token.
    """
    import httpx

    server_up = await _server_is_up()
    we_started_server = False

    if not server_up:
        if not quiet:
            print("Browser server not running — starting it (headless)...", file=sys.stderr)

        # Stop any stale process cleanly
        pid = get_server_pid()
        if pid:
            try:
                os.kill(pid, _signal.SIGTERM)
            except OSError:
                pass
        if PID_FILE.exists():
            PID_FILE.unlink()
        try:
            res = subprocess.run(
                ["lsof", "-ti", ":3002"], capture_output=True, text=True, timeout=5
            )
            for p in res.stdout.strip().split("\n"):
                if p.strip():
                    try:
                        os.kill(int(p.strip()), _signal.SIGKILL)
                    except (ValueError, OSError):
                        pass
        except Exception:
            pass

        await asyncio.sleep(1)

        # Start server headless (browser window not needed — just token extraction)
        cmd = [
            sys.executable, "-m", "uvicorn",
            "src.server:app", "--port", "3002", "--log-level", "warning",
        ]
        env = os.environ.copy()
        env["SLACK_HEADLESS"] = "1"
        with open(str(LOG_FILE), "a") as log_f:
            proc = subprocess.Popen(
                cmd, stdout=log_f, stderr=log_f,
                start_new_session=True, cwd=str(WORKSPACE_ROOT), env=env,
            )
        PID_FILE.write_text(str(proc.pid))
        we_started_server = True

        # Wait for server to come up and authenticate (max 20 s)
        if not quiet:
            print("Waiting for browser to authenticate...", file=sys.stderr)
        authenticated = False
        for _ in range(20):
            await asyncio.sleep(1)
            try:
                async with httpx.AsyncClient(timeout=2) as c:
                    r = await c.get(f"{SERVER_URL}/status")
                    if r.status_code == 200 and r.json().get("authenticated"):
                        authenticated = True
                        break
            except Exception:
                pass

        if not authenticated:
            raise RuntimeError(
                "Browser server failed to start/authenticate within 20 s. "
                "Check slack-server.log and ensure you are logged in to Slack."
            )

    # Refresh credentials via server API
    if not quiet:
        print("Refreshing session credentials...", file=sys.stderr)
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{SERVER_URL}/refresh-session")
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"refresh-session failed: {data.get('error')}")

    # Clear cached gateway_server — force re-probe with the new token
    try:
        tokens = _load_tokens()
        tokens.pop("gateway_server", None)
        _save_tokens(tokens)
    except Exception:
        pass  # tokens file may have just been created by refresh-session

    if not quiet:
        print("✅ Session refreshed", file=sys.stderr)

    return we_started_server


# ---------------------------------------------------------------------------
# Main listen loop
# ---------------------------------------------------------------------------

async def _listen(
    filter_types: Optional[set],
    raw: bool,
    use_signals: bool,
    quiet: bool,
) -> None:
    sig_engine: Optional[SignalEngine] = None
    if use_signals:
        sig_engine = SignalEngine()
        loaded = sig_engine.load_config()
        if loaded and not quiet:
            print(
                f"[signals] handlers active for: {list(sig_engine._handlers)}",
                file=sys.stderr,
            )

    if not quiet:
        print("Connecting to Slack WebSocket…", file=sys.stderr)

    started_server = False
    MAX_AUTH_RETRIES = 1
    for attempt in range(MAX_AUTH_RETRIES + 1):
        try:
            client = SlackWSClient()
            connected = False
            async for event in client.events():
                # On first event: WS is confirmed connected — release the browser
                if not connected:
                    connected = True
                    if started_server:
                        await _stop_server(quiet)
                        started_server = False

                event_type = event.get("type", "unknown")

                if filter_types and event_type not in filter_types:
                    continue

                if sig_engine:
                    sig_engine.dispatch(event)

                if raw:
                    print(json.dumps(event), flush=True)
                elif not quiet:
                    _print_event(event)

            break  # clean exit

        except SlackAuthError as e:
            if attempt >= MAX_AUTH_RETRIES:
                print(f"Authentication failed: {e}", file=sys.stderr)
                raise typer.Exit(1)
            if not quiet:
                print(f"Authentication failed ({e}) — refreshing session...", file=sys.stderr)
            started_server = await _refresh_session(quiet)
            if not quiet:
                print("Reconnecting...", file=sys.stderr)


# ---------------------------------------------------------------------------
# Human-readable event formatting
# ---------------------------------------------------------------------------

def _print_event(event: dict) -> None:
    t = event.get("type", "unknown")
    channel = event.get("channel", "")
    user = event.get("user", "")
    ts = event.get("event_ts") or event.get("ts", "")

    if t == "message":
        subtype = event.get("subtype", "")
        text = (event.get("text") or "")[:80]
        label = f"[{subtype}] " if subtype else ""
        print(f"message  {label}ch={channel} user={user} ts={ts}  {text!r}")

    elif t == "channel_marked":
        unread = event.get("unread_count", 0)
        print(f"channel_marked  ch={channel}  unread={unread}  ts={ts}")

    elif t == "badge_counts_updated":
        av2 = event.get("activity_v2") or {}
        ch = av2.get("channel", 0)
        dm = av2.get("dm", 0)
        at = av2.get("at_user", 0)
        thr = av2.get("thread_v2", 0)
        print(f"badge_updated  channel={ch}  dm={dm}  @={at}  thread={thr}")

    elif t == "hello":
        region = event.get("region", "")
        print(f"✅ WebSocket authenticated  region={region}", file=sys.stderr)

    elif t == "pong":
        print("✅ Ping-pong OK", file=sys.stderr)

    elif t in ("user_typing", "presence_change", "dnd_updated_user"):
        pass  # suppress noisy high-frequency events

    else:
        line = json.dumps(event)
        print(f"{t}  {line[:120]}")
