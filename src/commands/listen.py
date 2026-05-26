"""slack-chat listen — connect directly to Slack WebSocket without browser.

Receives real-time events and dispatches them to configured signal handlers
(from the `signals:` section of config.yaml).

Usage:
    slack-chat listen                  # dispatch signals, print events
    slack-chat listen --raw            # print raw JSON lines to stdout
    slack-chat listen --event message  # filter to specific event types
    slack-chat listen --no-signals     # don't trigger signal handlers
"""

import asyncio
import json
import logging
import sys
from typing import List, Optional

import typer

from ..ws_client import SlackWSClient
from ..signals import SignalEngine

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
        True, "--signals/--no-signals",
        help="Dispatch events to signal handlers in config.yaml.",
    ),
    quiet: bool = typer.Option(
        False, "--quiet", "-q",
        help="Suppress human-readable output (useful when --signals is the goal).",
    ),
) -> None:
    """Connect directly to Slack WebSocket and stream real-time events."""
    asyncio.run(_listen(
        filter_types=set(event) if event else None,
        raw=raw,
        use_signals=signals,
        quiet=quiet,
    ))


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

    client = SlackWSClient()

    if not quiet:
        print("Connecting to Slack WebSocket…", file=sys.stderr)

    try:
        async for event in client.events():
            event_type = event.get("type", "unknown")

            # Apply event-type filter
            if filter_types and event_type not in filter_types:
                continue

            # Dispatch to signal engine
            if sig_engine:
                sig_engine.dispatch(event)

            # Output
            if raw:
                print(json.dumps(event), flush=True)
            elif not quiet:
                _print_event(event)

    except KeyboardInterrupt:
        if not quiet:
            print("\nDisconnected.", file=sys.stderr)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        raise typer.Exit(1)


def _print_event(event: dict) -> None:
    """Print a human-readable summary of a WS event."""
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
        print(f"hello  region={region}  start={event.get('start')}", file=sys.stderr)

    elif t in ("user_typing", "presence_change", "dnd_updated_user"):
        pass  # suppress noisy events in human mode

    else:
        line = json.dumps(event)
        print(f"{t}  {line[:120]}")
