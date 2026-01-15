import typer
import sys
import yaml
from typing import Optional
from datetime import datetime, timedelta

from .. import storage
from .. import pull as pull_module
from ..utils import (
    get_client,
    resolve_channel,
    parse_event_id,
    call_api,
)

# @app.command - exported("pull")
def pull_command(
    since: str = typer.Option(
        ..., "--since", "-s", help="Cutoff date: YYYY-MM-DD, yesterday, or 'N days ago'"
    ),
    limit: int = typer.Option(
        100, "--limit", "-n", help="Max messages to fetch per category"
    ),
    type_filter: Optional[str] = typer.Option(
        None, "--type", "-t", help="Filter: channels, dms, threads, mentions, all"
    ),
    channel: Optional[str] = typer.Option(
        None, "--channel", "-c", help="Only pull from this channel (ID or name)"
    ),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress verbose output"),
):
    """Pull unread Slack messages to local storage.

    Fetches unreads from Slack and stores them as .md files in storage/.
    Existing files are skipped (deduplication by message ID).

    Examples:

        slack pull --since "7 days ago"

        slack pull --since yesterday --limit 50

        slack pull --since 2026-01-01 --type channels

        slack pull --since "1 day ago" --channel nexint

        slack pull --since yesterday --channel C0A8JJBAVU2
    """
    # Resolve channel name to ID if provided
    channel_id = None
    if channel:
        import re

        channel = channel.lstrip("#")
        if re.match(r"^[CDG][A-Z0-9]{8,}$", channel):
            channel_id = channel
        else:
            # Resolve name to ID
            resolved = resolve_channel(channel)
            channel_id = resolved.get("id")
            if not channel_id:
                print(f"❌ Could not resolve channel '{channel}'", file=sys.stderr)
                sys.exit(1)
            if not quiet:
                print(f"📍 Resolved '{channel}' to {channel_id}")

    with get_client() as client:
        stats = pull_module.pull_messages(
            client=client,
            call_api_fn=call_api,
            since=since,
            limit=limit,
            type_filter=type_filter,
            channel_filter=channel_id,
            verbose=not quiet,
        )

        if stats["errors"]:
            for err in stats["errors"]:
                print(f"⚠️  {err}", file=sys.stderr)
            sys.exit(1)


# =============================================================================
# Reply Command (Online - post message to channel or thread)
# =============================================================================


# @app.command - exported("reply")
def reply_command(
    target: str = typer.Argument(
        ...,
        help="Channel ID, channel name (#channel), storage ID (partial), or event ID (CHANNEL:TS or CHANNEL:TS@THREAD_TS)",
    ),
    message: str = typer.Argument(..., help="Message text to post"),
):
    """Reply to a channel or thread.

    Smart targeting - accepts:
    - Channel ID: C0A7RJWRZPT (posts to channel)
    - Channel name: #prod-tech-internal-ProductA (posts to channel)
    - Storage ID: b89c7a (replies to that message's thread)
    - Event ID: C0A7RJWRZPT:1767815267.099869 (replies to thread)
    - Event ID with thread: C0A7RJWRZPT:1767815267.099869@1767773973.649239

    Examples:

        slack-chat reply C0A7RJWRZPT "Hello everyone!"

        slack-chat reply "#prod-tech-internal-ProductA" "Hello world!"

        slack-chat reply b89c7a "Thanks for the update!"

        slack-chat reply C0A7RJWRZPT:1767815267.099869 "Replying to thread"
    """
    import re

    channel_id = None
    thread_ts = None

    # Try to resolve as storage ID first (hex string like b89c7a)
    if re.match(r"^[0-9a-f]+$", target.lower()):
        try:
            result = storage.find_by_partial_id(target)
            if result:
                _, path = result
                frontmatter, _ = storage.read_message_file(path)
                if frontmatter:
                    channel_id = frontmatter.get("channel_id")
                    # Use thread_ts if it's a thread reply, otherwise use timestamp to reply to that message
                    thread_ts = frontmatter.get("thread_ts") or frontmatter.get(
                        "timestamp"
                    )
        except ValueError as e:
            # Ambiguous ID - report error
            print(f"❌ {e}", file=sys.stderr)
            sys.exit(1)

    # Try event ID format (CHANNEL:TS or CHANNEL:TS@THREAD_TS)
    if not channel_id and ":" in target:
        channel_id, timestamp, parsed_thread_ts = parse_event_id(target)
        # If there's a thread_ts in the event ID, use it; otherwise use the timestamp
        thread_ts = parsed_thread_ts or timestamp

    # Try channel name (starts with # or doesn't look like ID)
    if not channel_id:
        ch = resolve_channel(target)
        if ch.get("id") != target or target.startswith("#"):
            # Successfully resolved to a channel
            channel_id = ch.get("id")
            if channel_id == target.lstrip("#") and not re.match(
                r"^[CDG][A-Z0-9]{8,}$", channel_id
            ):
                # Could not resolve channel name
                print(f"❌ Could not resolve channel: {target}", file=sys.stderr)
                print(
                    f"   Try 'slack-chat channel find <keyword>' to search cached channels.",
                    file=sys.stderr,
                )
                sys.exit(1)
            thread_ts = None  # Post to channel, not a thread
        else:
            # Treat as plain channel ID
            channel_id = target
            thread_ts = None

    # Build params
    params = {"channel": channel_id, "text": message}
    if thread_ts:
        params["thread_ts"] = thread_ts

    with get_client() as client:
        data = call_api(client, "chat.postMessage", params)

        if data.get("ok"):
            result = {
                "ok": True,
                "channel": channel_id,
                "message_ts": data.get("ts"),
            }
            if thread_ts:
                result["thread_ts"] = thread_ts
            if data.get("message", {}).get("permalink"):
                result["permalink"] = data["message"]["permalink"]
            print(yaml.dump(result, indent=2, sort_keys=False))
        else:
            print(
                yaml.dump(
                    {
                        "ok": False,
                        "error": data.get("error", "unknown"),
                    },
                    indent=2,
                    sort_keys=False,
                ),
                file=sys.stderr,
            )
            sys.exit(1)


# =============================================================================
# React Command (Online - add emoji reaction to message)
# =============================================================================


# @app.command - exported("react")
def react_command(
    target: str = typer.Argument(
        ...,
        help="Storage ID (partial) or event ID (CHANNEL:TS or CHANNEL:TS@THREAD_TS)",
    ),
    emoji: str = typer.Argument(
        ..., help="Emoji name (e.g., 'thumbsup', '+1', 'eyes')"
    ),
):
    """Add an emoji reaction to a message.

    Smart targeting - accepts:
    - Storage ID: b89c7a (reacts to that message)
    - Event ID: C0A7RJWRZPT:1767815267.099869 (reacts to message)
    - Event ID with thread: C0A7RJWRZPT:1767815267.099869@1767773973.649239

    Emoji can be specified with or without colons:
    - thumbsup, +1, eyes, white_check_mark
    - :thumbsup:, :+1:, :eyes:

    Examples:

        slack-chat react b89c7a thumbsup

        slack-chat react C0A7RJWRZPT:1767815267.099869 eyes

        slack-chat react b89c7a white_check_mark
    """
    channel_id = None
    timestamp = None

    # Strip colons from emoji if present
    emoji_name = emoji.strip(":")

    # Try to resolve as storage ID first
    try:
        result = storage.find_by_partial_id(target)
        if result:
            _, path = result
            frontmatter, _ = storage.read_message_file(path)
            if frontmatter:
                channel_id = frontmatter.get("channel_id")
                timestamp = frontmatter.get("timestamp")
    except ValueError as e:
        # Ambiguous ID - report error
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)

    # Try event ID format (CHANNEL:TS or CHANNEL:TS@THREAD_TS)
    if not channel_id and ":" in target:
        channel_id, timestamp, _ = parse_event_id(target)

    if not channel_id or not timestamp:
        print(f"❌ Could not resolve message: {target}", file=sys.stderr)
        sys.exit(1)

    # Build params
    params = {"channel": channel_id, "timestamp": timestamp, "name": emoji_name}

    with get_client() as client:
        data = call_api(client, "reactions.add", params)

        if data.get("ok"):
            print(
                yaml.dump(
                    {
                        "ok": True,
                        "channel": channel_id,
                        "timestamp": timestamp,
                        "emoji": emoji_name,
                    },
                    indent=2,
                    sort_keys=False,
                )
            )
        else:
            print(
                yaml.dump(
                    {
                        "ok": False,
                        "error": data.get("error", "unknown"),
                    },
                    indent=2,
                    sort_keys=False,
                ),
                file=sys.stderr,
            )
            sys.exit(1)


# =============================================================================
# Mute Command (Online - mute channel notifications)
# =============================================================================


# @app.command - exported("mute")
def mute_command(
    channel: str = typer.Argument(..., help="Channel ID (e.g., C0A7RJWRZPT)"),
):
    """Mute a channel to stop receiving notifications.

    Uses conversations.setNotificationPrefs to disable all notifications
    for the specified channel.

    Examples:

        slack-chat mute C0A7RJWRZPT

        slack-chat mute C6M7U8DFF
    """
    # Build params for muting (suppress all notifications)
    params = {"channel": channel, "prefs": {"muted": True}}

    with get_client() as client:
        data = call_api(client, "conversations.setNotificationPrefs", params)

        if data.get("ok"):
            print(
                yaml.dump(
                    {
                        "ok": True,
                        "channel": channel,
                        "muted": True,
                    },
                    indent=2,
                    sort_keys=False,
                )
            )
        else:
            print(
                yaml.dump(
                    {
                        "ok": False,
                        "error": data.get("error", "unknown"),
                    },
                    indent=2,
                    sort_keys=False,
                ),
                file=sys.stderr,
            )
            sys.exit(1)


if __name__ == "__main__":
    app()
