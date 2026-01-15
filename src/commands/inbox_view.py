"""Inbox View Commands."""

import sys
import yaml
from ..utils import (
    get_client,
    call_api,
    enrich_messages,
    parse_event_id,
    truncate_text,
)
from .. import storage

def inbox_view_online(event_id: str):
    """View details of a specific event - ONLINE."""
    channel_id, timestamp, thread_ts = parse_event_id(event_id)

    with get_client() as client:
        # If it's a channel ID, show checking channel info
        if not timestamp:
            data = call_api(
                client, "conversations.info", {"channel": channel_id}
            )
            print(yaml.dump(data, indent=2, sort_keys=False, default_flow_style=False))
            return

        # Fetch message details
        # We use conversations.history with a small window to get the specific message
        history_params = {
            "channel": channel_id,
            "latest": timestamp,
            "limit": 1,
            "inclusive": True,
        }
        
        message = None
        
        # If it's a thread reply, we might need to fetch thread replies
        if thread_ts and thread_ts != timestamp:
             # Fetch specific thread reply
             # But first try history as it's cheaper/simpler if it's recent
             pass # Logic below handles generic fetch

        data = call_api(client, "conversations.history", history_params)
        messages = data.get("messages", [])
        
        if messages and messages[0].get("ts") == timestamp:
            message = messages[0]
        else:
            # Maybe it is a thread reply that didn't show up in history (if it's old?)
            # Try fetching thread
             if thread_ts:
                thread_data = call_api(
                    client, 
                    "conversations.replies", 
                    {"channel": channel_id, "ts": thread_ts, "latest": timestamp, "limit": 1, "inclusive": True}
                )
                replies = thread_data.get("messages", [])
                for r in replies:
                    if r.get("ts") == timestamp:
                        message = r
                        break

        if not message:
            print(f"❌ Message not found: {event_id}", file=sys.stderr)
            return

        # Enrich with user info
        enriched = enrich_messages(client, [message])
        message = enriched[0]

        # Get context (replies if it's a thread parent)
        replies = []
        if message.get("thread_ts") and message.get("reply_count", 0) > 0:
            # It's a parent message
            thread_data = call_api(
                client, 
                "conversations.replies", 
                {"channel": channel_id, "ts": message.get("thread_ts"), "limit": 5}
            )
            replies = enrich_messages(client, thread_data.get("messages", [])[1:]) # Skip parent

        output = {
            "event": {
                "id": event_id,
                "channel_id": channel_id,
                **message
            }
        }
        if replies:
            output["replies_preview"] = replies

        print(yaml.dump(output, indent=2, sort_keys=False, default_flow_style=False))


def inbox_context_online(
    event_id: str,
    limit: int = 10,
):
    """View surrounding context (thread or preceding channel messages)."""
    channel_id, timestamp, thread_ts = parse_event_id(event_id)

    if not timestamp:
        print(
            "❌ Event ID must include timestamp (CHANNEL_ID:TIMESTAMP)", file=sys.stderr
        )
        sys.exit(1)

    with get_client() as client:
        # Get the message to check if it's part of a thread
        msg_data = call_api(
            client,
            "conversations.history",
            {
                "channel": channel_id,
                "latest": timestamp,
                "limit": 1,
                "inclusive": True,
            },
        )
        
        is_thread_reply = thread_ts is not None
        parent_ts = thread_ts
        
        if not is_thread_reply and msg_data.get("messages"):
            msg = msg_data["messages"][0]
            if msg.get("thread_ts") == msg.get("ts"):
                # It's a thread parent
                parent_ts = msg.get("ts")
                is_thread_reply = True # Treated as thread context

        if is_thread_reply:
            print(f"🧵 Fetching thread context for {event_id}...")
            # Fetch thread replies around this message
            params = {
                "channel": channel_id,
                "ts": parent_ts,
                "limit": limit,
            }
            data = call_api(client, "conversations.replies", params)
        else:
            print(f"📺 Fetching channel context for {event_id}...")
            # Fetch messages before this one
            params = {
                "channel": channel_id,
                "latest": timestamp,
                "limit": limit,
                "inclusive": True,
            }
            data = call_api(client, "conversations.history", params)

        if not data.get("ok"):
            print(f"❌ Error fetching context: {data.get('error')}", file=sys.stderr)
            return

        messages = data.get("messages", [])
        if not is_thread_reply:
            messages.reverse() # History returns newest first, we want chronological

        enriched = enrich_messages(client, messages)

        print(yaml.dump(enriched, indent=2, sort_keys=False, default_flow_style=False))
        

def inbox_view_offline(id_or_event: str):
    """View a single message from local storage (offline)."""
    # Try to find by partial storage ID first
    try:
        result = storage.find_by_partial_id(id_or_event)
        if result:
            full_id, path = result
            content = path.read_text(encoding="utf-8")
            print(content)
            return
    except ValueError as e:
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)

    # Try by event ID format (channel:timestamp)
    if ":" in id_or_event:
        channel_id, timestamp, thread_ts = parse_event_id(id_or_event)
        storage_id = storage.generate_storage_id(channel_id, timestamp, thread_ts)
        frontmatter, body = storage.read_message(storage_id)
        if frontmatter:
            path = storage.get_storage_path(storage_id)
            content = path.read_text(encoding="utf-8")
            print(content)
            return

    print(f"❌ Message not found: {id_or_event}", file=sys.stderr)
    sys.exit(1)
