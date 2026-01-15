"""Inbox Read/Mark Commands."""

import sys
import yaml
from .. import storage
from ..utils import (
    get_client,
    call_api,
    format_event_id,
    parse_event_id,
    save_read_event,
)

def inbox_read(id_or_event: str, offline_only: bool):
    """Mark message as read (updates local storage and optionally Slack)."""
    # Resolve the storage ID
    storage_id = None
    channel_id = None
    timestamp = None
    thread_ts = None

    # Try partial ID first
    try:
        result = storage.find_by_partial_id(id_or_event)
        if result:
            storage_id, path = result
            frontmatter, _ = storage.read_message_file(path)
            if frontmatter:
                channel_id = frontmatter.get("channel_id")
                timestamp = frontmatter.get("timestamp")
                thread_ts = frontmatter.get("thread_ts")
    except ValueError as e:
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)

    # Try event ID format
    if not storage_id and ":" in id_or_event:
        channel_id, timestamp, thread_ts = parse_event_id(id_or_event)
        storage_id = storage.generate_storage_id(channel_id, timestamp, thread_ts)
        if not storage.file_exists(storage_id):
            storage_id = None

    if not storage_id:
        print(f"❌ Message not found: {id_or_event}", file=sys.stderr)
        sys.exit(1)

    # Update local storage
    storage.update_message_offline_status(storage_id, read=True)

    # Also update legacy read tracking file
    if channel_id and timestamp:
        event_id = format_event_id(channel_id, timestamp, thread_ts)
        save_read_event(event_id)

    result = {
        "ok": True,
        "marked_read_locally": storage_id[:8] + "...",
    }

    # Mark on Slack server (unless offline-only)
    if not offline_only and channel_id and timestamp:
        with get_client() as client:
            data = call_api(
                client, "conversations.mark", {"channel": channel_id, "ts": timestamp}
            )
            if data.get("ok"):
                result["marked_read_on_slack"] = True
            else:
                result["slack_error"] = data.get("error", "unknown")

    print(yaml.dump(result, indent=2, sort_keys=False))


def inbox_mark_thread(id_or_event: str, offline_only: bool):
    """Mark all messages in a thread as read."""
    # Resolve the storage ID to get thread info
    thread_ts = None
    channel_id = None

    # Try partial ID first
    try:
        result = storage.find_by_partial_id(id_or_event)
        if result:
            _, path = result
            frontmatter, _ = storage.read_message_file(path)
            if frontmatter:
                channel_id = frontmatter.get("channel_id")
                thread_ts = frontmatter.get("thread_ts") or frontmatter.get("timestamp")
    except ValueError as e:
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)

    # Try event ID format
    if not thread_ts and ":" in id_or_event:
        channel_id, timestamp, parsed_thread_ts = parse_event_id(id_or_event)
        thread_ts = parsed_thread_ts or timestamp

    if not thread_ts or not channel_id:
        print(f"❌ Could not determine thread for: {id_or_event}", file=sys.stderr)
        sys.exit(1)

    # Find all messages in this thread
    all_messages = storage.load_all_messages()
    thread_messages = []
    for storage_id, fm in all_messages:
        if fm.get("channel_id") == channel_id:
            msg_thread_ts = fm.get("thread_ts") or fm.get("timestamp")
            if msg_thread_ts == thread_ts or fm.get("timestamp") == thread_ts:
                thread_messages.append((storage_id, fm))

    if not thread_messages:
        print(f"❌ No messages found for thread: {thread_ts}", file=sys.stderr)
        sys.exit(1)

    # Mark all as read
    marked_ids = []
    for storage_id, fm in thread_messages:
        storage.update_message_offline_status(storage_id, read=True)
        # Update legacy tracking
        event_id = format_event_id(
            fm.get("channel_id"), fm.get("timestamp"), fm.get("thread_ts")
        )
        save_read_event(event_id)
        marked_ids.append(storage_id[:8])

    result = {
        "ok": True,
        "thread_ts": thread_ts,
        "channel_id": channel_id,
        "marked_count": len(marked_ids),
        "marked_ids": marked_ids,
    }

    # Mark thread as read on Slack
    if not offline_only:
        with get_client() as client:
            data = call_api(
                client, "conversations.mark", {"channel": channel_id, "ts": thread_ts}
            )
            if data.get("ok"):
                result["marked_read_on_slack"] = True
            else:
                result["slack_error"] = data.get("error", "unknown")

    print(yaml.dump(result, indent=2, sort_keys=False))


def inbox_mark_channel(channel_id: str, offline_only: bool):
    """Mark all messages in a channel as read."""
    # Find all messages in this channel
    all_messages = storage.load_all_messages()
    channel_messages = [
        (storage_id, fm)
        for storage_id, fm in all_messages
        if fm.get("channel_id") == channel_id
    ]

    if not channel_messages:
        print(f"❌ No messages found for channel: {channel_id}", file=sys.stderr)
        sys.exit(1)

    # Get the latest timestamp for marking on Slack
    latest_ts = max(fm.get("timestamp", "0") for _, fm in channel_messages)

    # Mark all as read
    marked_ids = []
    for storage_id, fm in channel_messages:
        storage.update_message_offline_status(storage_id, read=True)
        # Update legacy tracking
        event_id = format_event_id(
            fm.get("channel_id"), fm.get("timestamp"), fm.get("thread_ts")
        )
        save_read_event(event_id)
        marked_ids.append(storage_id[:8])

    result = {
        "ok": True,
        "channel_id": channel_id,
        "marked_count": len(marked_ids),
        "marked_ids": marked_ids,
    }

    # Mark channel as read on Slack (up to latest message)
    if not offline_only:
        with get_client() as client:
            data = call_api(
                client, "conversations.mark", {"channel": channel_id, "ts": latest_ts}
            )
            if data.get("ok"):
                result["marked_read_on_slack"] = True
            else:
                result["slack_error"] = data.get("error", "unknown")

    print(yaml.dump(result, indent=2, sort_keys=False))


def inbox_unread_offline(id_or_event: str):
    """Mark message as unread (local storage only)."""
    # Resolve the storage ID
    storage_id = None

    # Try partial ID first
    try:
        result = storage.find_by_partial_id(id_or_event)
        if result:
            storage_id, _ = result
    except ValueError as e:
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)

    # Try event ID format
    if not storage_id and ":" in id_or_event:
        channel_id, timestamp, thread_ts = parse_event_id(id_or_event)
        storage_id = storage.generate_storage_id(channel_id, timestamp, thread_ts)
        if not storage.file_exists(storage_id):
            storage_id = None

    if not storage_id:
        print(f"❌ Message not found: {id_or_event}", file=sys.stderr)
        sys.exit(1)

    # Update local storage
    storage.update_message_offline_status(storage_id, read=False)

    print(yaml.dump({
        "ok": True,
        "marked_unread_locally": f"{storage_id[:8]}...",
    }, indent=2, sort_keys=False))
