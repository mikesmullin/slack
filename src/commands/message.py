"""Message operations commands."""

import typer
import sys
import yaml
from typing import Optional

from .. import storage
from ..utils import (
    get_client,
    get_channel_name_by_id,
    get_user_name_by_id,
    parse_event_id,
    call_api,
)

app = typer.Typer(help="Message operations")

@app.command("around")
def message_around(
    event_id: str = typer.Argument(
        ..., help="Event ID (CHANNEL_ID:TIMESTAMP or CHANNEL_ID:TIMESTAMP@THREAD_TS)"
    ),
    before: int = typer.Option(3, "--before", "-B", help="Number of messages before"),
    after: int = typer.Option(3, "--after", "-A", help="Number of messages after"),
):
    """View messages around a target message/event with context.

    Similar to grep -B and -A flags. Shows N messages before and after the target.

    For thread replies, use format: CHANNEL:TIMESTAMP@THREAD_TS

    Examples:
      slack message around C09T76NUR41:1766078390.970449 -B 2 -A 5
      slack message around C09T76NUR41:1765909149.353759@1765321208.614079 -B 2 -A 2
    """
    channel_id, timestamp, thread_ts = parse_event_id(event_id)

    if not timestamp:
        print(
            "❌ Event ID must include timestamp (CHANNEL_ID:TIMESTAMP or CHANNEL_ID:TIMESTAMP@THREAD_TS)",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        target_ts = float(timestamp)
    except ValueError:
        print(f"❌ Invalid timestamp: {timestamp}", file=sys.stderr)
        sys.exit(1)

    with get_client() as client:
        all_messages = []
        target_msg = None

        # If this is a thread reply
        if thread_ts:
            # Fetch all messages in the thread
            replies_data = call_api(
                client,
                "conversations.replies",
                {"channel": channel_id, "ts": thread_ts, "limit": 100},
            )

            if replies_data.get("ok"):
                thread_messages = replies_data.get("messages", [])

                # Find the target message
                for m in thread_messages:
                    if m.get("ts") == timestamp:
                        target_msg = m
                        break

                # If target found, collect before/after messages
                if target_msg:
                    target_index = next(
                        (
                            i
                            for i, m in enumerate(thread_messages)
                            if m.get("ts") == timestamp
                        ),
                        -1,
                    )

                    # Collect before messages
                    if before > 0 and target_index > 0:
                        start_idx = max(0, target_index - before)
                        all_messages.extend(thread_messages[start_idx:target_index])

                    # Add target
                    all_messages.append(target_msg)

                    # Collect after messages
                    if after > 0 and target_index < len(thread_messages) - 1:
                        end_idx = min(len(thread_messages), target_index + 1 + after)
                        all_messages.extend(thread_messages[target_index + 1 : end_idx])

            # If not found in thread, try search
            if not target_msg:
                search_data = call_api(
                    client,
                    "search.messages",
                    {
                        "query": f"in:{channel_id} from:me",
                        "sort": "timestamp",
                        "count": 100,
                    },
                )

                if search_data.get("ok"):
                    matches = search_data.get("messages", {}).get("matches", [])
                    for m in matches:
                        if m.get("ts") == timestamp:
                            target_msg = m
                            all_messages.append(target_msg)
                            break

            if not target_msg:
                print(
                    f"❌ Thread reply {channel_id}:{timestamp}@{thread_ts} not found",
                    file=sys.stderr,
                )
                sys.exit(1)

        # Otherwise, fetch from channel history
        else:
            # Try conversations.history first for efficiency using the official method:
            # Set oldest to the ts, inclusive to true, limit to 1
            history_data = call_api(
                client,
                "conversations.history",
                {
                    "channel": channel_id,
                    "oldest": timestamp,
                    "inclusive": True,
                    "limit": 1,
                },
            )

            if history_data.get("ok") and history_data.get("messages"):
                msg = history_data["messages"][0]
                if msg.get("ts") == timestamp:
                    target_msg = msg

            # If not found, try search with timestamp range (fallback for shared/deleted/edited messages)
            if not target_msg:
                try:
                    # Try searching for messages from the current user in this channel
                    search_data = call_api(
                        client,
                        "search.messages",
                        {
                            "query": f"in:{channel_id} from:me",
                            "sort": "timestamp",
                            "count": 100,
                        },
                    )

                    if search_data.get("ok"):
                        matches = search_data.get("messages", {}).get("matches", [])
                        for m in matches:
                            if m.get("ts") == timestamp:
                                target_msg = m
                                break
                except (ValueError, TypeError):
                    pass

            # Fetch messages before the target (from before_buffer to target)
            # These come in reverse chronological order, so oldest first in the results
            if before > 0 and target_msg:
                before_data = call_api(
                    client,
                    "conversations.history",
                    {
                        "channel": channel_id,
                        "latest": timestamp,
                        "inclusive": False,  # Don't include the target itself
                        "limit": before,
                    },
                )
                if before_data.get("ok"):
                    # Reverse to get chronological order
                    before_messages = list(reversed(before_data.get("messages", [])))
                    all_messages.extend(before_messages)

            # Add the target message
            if target_msg:
                all_messages.append(target_msg)
            else:
                # Message not found - it may be from a reaction
                print(f"❌ Message {channel_id}:{timestamp} not found", file=sys.stderr)
                print(f"", file=sys.stderr)
                print(
                    f"This message ID may be from a reaction. Reactions show up in:",
                    file=sys.stderr,
                )
                print(f"  slack inbox list --type reactions", file=sys.stderr)
                print(f"", file=sys.stderr)
                print(
                    f"View the message via the URL provided in the reaction entry.",
                    file=sys.stderr,
                )
                sys.exit(1)

            # Fetch messages after the target (newer messages)
            if after > 0:
                after_data = call_api(
                    client,
                    "conversations.history",
                    {
                        "channel": channel_id,
                        "latest": str(
                            target_ts + 1000000
                        ),  # Start from just after target
                        "oldest": timestamp,
                        "inclusive": False,  # Don't include the target
                        "limit": after,
                    },
                )
                if after_data.get("ok"):
                    # Reverse because API returns newest first
                    after_messages = list(reversed(after_data.get("messages", [])))
                    all_messages.extend(after_messages)

        # Format messages for display
        messages = []
        for m in all_messages:
            msg_ts = m.get("ts")
            user_id = m.get("user")
            user_name = (
                get_user_info(client, user_id).get("real_name", user_id)
                if user_id
                else "unknown"
            )

            is_target = msg_ts == timestamp
            msg_obj = {
                "timestamp": msg_ts,
                "from": user_name,
                "text": m.get("text", ""),
                "is_target": is_target,
            }

            # Add images if present
            images = extract_image_urls(m)
            if images:
                msg_obj["images"] = images

            messages.append(msg_obj)

        output = {
            "event_id": event_id,
            "channel": channel_id,
            "target_timestamp": timestamp,
            "before": before,
            "after": after,
            "message_count": len(messages),
        }

        # Add thread info if applicable
        if thread_ts:
            output["thread_ts"] = thread_ts
            output["context_type"] = "thread"
        else:
            output["context_type"] = "channel"

        output["messages"] = messages
        print(yaml.dump(output, indent=2, sort_keys=False, default_flow_style=False))