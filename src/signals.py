"""Signal engine: fire shell commands in response to Slack WebSocket events.

Signals are named event types defined under the `signals:` key in config.yaml.
When a matching WebSocket event arrives, all configured shell commands for that
signal are launched in parallel (fire-and-forget).

Supported signal names and the WS event type each maps to:
  new_message     <- type: "message"
  badge_updated   <- type: "badge_counts_updated"
  channel_marked  <- type: "channel_marked"

Environment variables set for every signal:
  $_SIGNAL   - signal name (e.g. "new_message")
  $_BUFFER   - path to a JSON file containing the full WS event payload

Additional variables per signal:

  new_message:
    $_CHANNEL    - channel ID
    $_USER       - user ID who sent the message
    $_TS         - message timestamp
    $_TEXT       - plain text of the message
    $_THREAD_TS  - thread timestamp (empty for top-level messages)
    $_SUBTYPE    - message subtype (empty for regular user messages)

  badge_updated:
    $_BADGE_CHANNEL    - unread channel message count
    $_BADGE_DM         - unread DM count
    $_BADGE_AT_USER    - @mention count
    $_BADGE_THREAD     - unread thread count

  channel_marked:
    $_CHANNEL              - channel ID
    $_TS                   - last-read timestamp
    $_UNREAD_COUNT         - total unread message count
    $_UNREAD_COUNT_DISPLAY - display unread count (may be capped)
"""

import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

WORKSPACE_ROOT = Path(__file__).parent.parent
CONFIG_FILE = WORKSPACE_ROOT / "config.yaml"

# Maps Slack WS event type -> signal name
_EVENT_TO_SIGNAL: dict[str, str] = {
    "message": "new_message",
    "badge_counts_updated": "badge_updated",
    "channel_marked": "channel_marked",
}


def _build_env(signal_name: str, payload: dict) -> dict[str, str]:
    env = os.environ.copy()
    env["_SIGNAL"] = signal_name

    if signal_name == "new_message":
        env["_CHANNEL"] = payload.get("channel", "")
        env["_USER"] = payload.get("user", "")
        env["_TS"] = payload.get("ts", "")
        env["_TEXT"] = payload.get("text", "")
        env["_THREAD_TS"] = payload.get("thread_ts", "")
        env["_SUBTYPE"] = payload.get("subtype", "")

    elif signal_name == "badge_updated":
        av2 = payload.get("activity_v2") or {}
        env["_BADGE_CHANNEL"] = str(av2.get("channel", 0))
        env["_BADGE_DM"] = str(av2.get("dm", 0))
        env["_BADGE_AT_USER"] = str(av2.get("at_user", 0))
        env["_BADGE_THREAD"] = str(av2.get("thread_v2", 0))

    elif signal_name == "channel_marked":
        env["_CHANNEL"] = payload.get("channel", "")
        env["_TS"] = payload.get("ts", "")
        env["_UNREAD_COUNT"] = str(payload.get("unread_count", 0))
        env["_UNREAD_COUNT_DISPLAY"] = str(payload.get("unread_count_display", 0))

    return env


class SignalEngine:
    """Fires shell commands when named Slack WebSocket signals arrive."""

    def __init__(self):
        self._handlers: dict[str, list[str]] = {}

    def load_config(self) -> bool:
        """Load signal handlers from config.yaml. Returns True if any handlers loaded."""
        if not CONFIG_FILE.exists():
            return False
        try:
            data = yaml.safe_load(CONFIG_FILE.read_text()) or {}
            signals_cfg = data.get("signals") or {}
            self._handlers = {k: list(v) for k, v in signals_cfg.items() if isinstance(v, list)}
            if self._handlers:
                total = sum(len(cmds) for cmds in self._handlers.values())
                logger.info(
                    f"Loaded {total} signal command(s) across signals: {list(self._handlers)}"
                )
            return bool(self._handlers)
        except Exception as e:
            logger.error(f"Failed to load signals config: {e}")
            return False

    def dispatch(self, payload: dict) -> None:
        """Check payload type and fire all matching signal commands in parallel."""
        if not isinstance(payload, dict):
            return
        event_type = payload.get("type")
        signal_name = _EVENT_TO_SIGNAL.get(event_type)
        if not signal_name:
            return
        cmds = self._handlers.get(signal_name)
        if not cmds:
            return

        env = _build_env(signal_name, payload)

        # Write payload to a temp file so commands can read the full event
        buf_dir = WORKSPACE_ROOT / "tmp"
        buf_dir.mkdir(exist_ok=True)
        fd, buf_path = tempfile.mkstemp(
            prefix=f"sig_{signal_name}_", suffix=".json", dir=buf_dir
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(payload, f)
        except Exception:
            pass
        env["_BUFFER"] = buf_path

        loop = asyncio.get_event_loop()
        for cmd in cmds:
            loop.create_task(_run_cmd(signal_name, cmd, env))
            logger.debug(f"Signal {signal_name!r} dispatched: {cmd[:60]}")


async def _run_cmd(signal_name: str, cmd: str, env: dict[str, str]) -> None:
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(WORKSPACE_ROOT),
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            logger.warning(
                f"Signal {signal_name!r} cmd exited {proc.returncode}: "
                f"{stderr.decode()[:300]}"
            )
        else:
            logger.debug(f"Signal {signal_name!r} cmd completed ok")
    except Exception as e:
        logger.error(f"Signal {signal_name!r} cmd error: {e}")
