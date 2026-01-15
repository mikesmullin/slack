"""Inbox List Commands."""

import yaml
from typing import Optional, List, Dict
from ..utils import (
    get_client,
    fetch_unread_counts,
    fetch_subscribed_threads,
    fetch_mentions,
    fetch_reactions_to_me,
    get_reaction_details,
    format_event_id,
    format_channel_display,
    generate_slack_url,
    extract_thread_ts_from_permalink,
    extract_image_urls,
    load_read_events,
    is_event_read,
)
from .. import storage
from .. import pull as pull_module

# Helper functions for online list
def _fetch_channels_and_dms(client, events: List[Dict], type_filter: Optional[str]):
    # Channels and DMs (from users.counts - 1 API call)
    if type_filter and type_filter not in ["all", "channels", "dms"]:
        return

    counts = fetch_unread_counts(client)

    # Channels with unreads
    if not type_filter or type_filter in ["all", "channels"]:
        channels = counts.get("channels", []) + counts.get("groups", [])
        for ch in channels:
            unread = ch.get("unread_count_display", 0)
            if unread > 0:
                channel_id = ch.get("id")
                events.append(
                    {
                        "id": channel_id,
                        "type": "channel",
                        "name": f"#{ch.get('name', channel_id)}",
                        "url": generate_slack_url(channel_id),
                        "unread_count": unread,
                        "mention_count": ch.get("mention_count_display", 0),
                    }
                )

    # DMs with unreads
    if not type_filter or type_filter in ["all", "dms"]:
        ims = counts.get("ims", [])
        for im in ims:
            dm_count = im.get("dm_count", 0)
            if dm_count > 0:
                channel_id = im.get("id")
                events.append(
                    {
                        "id": channel_id,
                        "type": "dm",
                        "name": f"@{im.get('name', im.get('user_id', channel_id))}",
                        "url": generate_slack_url(channel_id),
                        "unread_count": dm_count,
                    }
                )

def _fetch_threads(client, events: List[Dict], type_filter: Optional[str], thread_cursor: Optional[str]) -> Optional[Dict]:
    thread_pagination = None
    if type_filter and type_filter not in ["all", "threads"]:
        return None

    threads_data = fetch_subscribed_threads(client, thread_cursor)
    threads = threads_data.get("threads", [])

    for t in threads:
        root = t.get("root_msg", {})
        # Check if thread has unread replies
        last_read = root.get("last_read", "0")
        latest_reply = root.get("latest_reply", "0")

        # Only show threads with unread replies
        if float(latest_reply) > float(last_read):
            channel_id = root.get("channel")
            thread_ts = root.get("thread_ts")
            event = {
                "id": format_event_id(channel_id, thread_ts),
                "type": "thread",
                "channel_id": channel_id,
                "url": generate_slack_url(channel_id, thread_ts),
                "text": root.get("text", "")[:80],
                "reply_count": root.get("reply_count", 0),
                "latest_reply": latest_reply,
                "last_read": last_read,
            }
            # Add image URLs if present
            images = extract_image_urls(root)
            if images:
                event["images"] = images
            events.append(event)

    # Include pagination info
    if threads_data.get("has_more"):
        thread_pagination = {
            "has_more": True,
            "next_cursor": threads_data.get("max_ts"),
            "hint": f"Use --thread-cursor {threads_data.get('max_ts')} to see more",
        }
    return thread_pagination

def _fetch_mentions_helper(client, events: List[Dict], type_filter: Optional[str], limit: int, count_only: bool = False) -> List[str]:
    fetched_ids = []
    if type_filter and type_filter not in ["all", "mentions"]:
        return []

    mentions = fetch_mentions(client, limit)
    for m in mentions[:limit]:
        channel = m.get("channel", {})
        channel_id = channel.get("id")
        ts = m.get("ts")
        permalink = m.get("permalink", "")

        thread_ts = m.get("thread_ts") or extract_thread_ts_from_permalink(
            permalink
        )
        event_id = format_event_id(channel_id, ts, thread_ts)
        fetched_ids.append(event_id)

        if count_only:
            continue

        event = {
            "id": event_id,
            "type": "mention",
            "channel": format_channel_display(channel),
            "from": m.get(
                "user", m.get("username")
            ),
            "text": m.get("text", "")[:80],
            "timestamp": ts,
            "url": permalink or generate_slack_url(channel_id, ts),
        }
        if thread_ts:
            event["type"] = "mention (thread reply)"

        images = extract_image_urls(m)
        if images:
            event["images"] = images
        
        if not is_event_read(event_id):
            events.append(event)
    return fetched_ids

def _fetch_reactions_helper(client, events: List[Dict], type_filter: Optional[str], limit: int, count_only: bool = False) -> List[str]:
    fetched_ids = []
    if type_filter and type_filter not in ["all", "reactions"]:
        return []

    reactions = fetch_reactions_to_me(client, limit)
    for r in reactions[:limit]:
        channel = r.get("channel", {})
        channel_id = channel.get("id")
        ts = r.get("ts")
        permalink = r.get("permalink", "")

        thread_ts = r.get("thread_ts") or extract_thread_ts_from_permalink(
            permalink
        )
        event_id = format_event_id(channel_id, ts, thread_ts)
        fetched_ids.append(event_id)

        if count_only:
            continue

        # Get who reacted to this message
        reaction_details = get_reaction_details(client, channel_id, ts)
        
        # Format reaction info
        reaction_summary = ""
        if reaction_details:
             by_emoji = {}
             for rd in reaction_details:
                 emoji = rd.get("emoji")
                 user_id = rd.get("user_id")
                 if emoji not in by_emoji:
                     by_emoji[emoji] = []
                 by_emoji[emoji].append(user_id)

             reaction_summary = ", ".join(
                 [
                     f":{emoji}: ({', '.join(users[:3])}{'...' if len(users) > 3 else ''})"
                     for emoji, users in by_emoji.items()
                 ]
             )

        event = {
            "id": event_id,
            "type": "reaction",
            "channel": format_channel_display(channel),
            "text": r.get("text", "")[:60],
            "timestamp": ts,
            "reactions": reaction_summary or "no reactions",
            "url": permalink or generate_slack_url(channel_id, ts),
        }
        if thread_ts:
            event["type"] = "reaction (thread reply)"

        images = extract_image_urls(r)
        if images:
            event["images"] = images

        if not is_event_read(event_id):
            events.append(event)
    return fetched_ids


def inbox_list_online(
    type_filter: Optional[str] = None,
    limit: int = 20,
    thread_cursor: Optional[str] = None,
):
    """List all unread activity (channels, DMs, threads, mentions, reactions) - ONLINE."""
    with get_client() as client:
        events = []
        
        _fetch_channels_and_dms(client, events, type_filter)
        
        thread_pagination = _fetch_threads(client, events, type_filter, thread_cursor)
        
        # Determine how many more events we need to fill the limit
        # The helpers just append to events
        _fetch_mentions_helper(client, events, type_filter, limit)
        _fetch_reactions_helper(client, events, type_filter, limit)

        # Count read events that were fetched but filtered out
        read_events = load_read_events()
        all_fetched_event_ids = []
        
        fetched_mentions = _fetch_mentions_helper(client, [], type_filter, limit, count_only=True)
        fetched_reactions = _fetch_reactions_helper(client, [], type_filter, limit, count_only=True)
        
        all_fetched_event_ids.extend(fetched_mentions)
        all_fetched_event_ids.extend(fetched_reactions)

        # Count how many were filtered (marked as read locally)
        filtered_count = sum(1 for eid in all_fetched_event_ids if eid in read_events)

        output = {
            "event_count": len(events[:limit]),
        }

        if filtered_count > 0:
            output["filtered_count"] = filtered_count

        output["events"] = events[:limit]

        if thread_pagination:
            output["thread_pagination"] = thread_pagination

        print(yaml.dump(output, indent=2, sort_keys=False, default_flow_style=False))

def inbox_list_offline(
    type_filter: Optional[str] = None,
    limit: int = 20,
    since: Optional[str] = None,
    show_all: bool = False,
):
    """List messages from local storage (Offline)."""
    # Offline: read from local storage
    messages = storage.load_all_messages()

    # Filter by read status
    if not show_all:
        messages = [
            (sid, fm) for sid, fm in messages if not storage.is_message_read(fm)
        ]

    # Filter by since date
    if since:
        since_dt = pull_module.parse_since_date(since)
        messages = [
            (sid, fm)
            for sid, fm in messages
            if pull_module.timestamp_to_datetime(fm.get("timestamp", "0")) >= since_dt
        ]

    # Filter by type
    if type_filter and type_filter != "all":
        filtered = []
        for sid, fm in messages:
            thread_ts = fm.get("thread_ts")
            channel_id = fm.get("channel_id", "")
            is_mention = fm.get("_mention", False)

            if type_filter == "mentions" and is_mention:
                filtered.append((sid, fm))
            elif type_filter == "threads" and thread_ts:
                filtered.append((sid, fm))
            elif type_filter == "dms" and channel_id.startswith("D"):
                filtered.append((sid, fm))
            elif (
                type_filter == "channels"
                and not channel_id.startswith("D")
                and not thread_ts
                and not is_mention
            ):
                filtered.append((sid, fm))
        messages = filtered

    total = len(messages)
    messages = messages[:limit]

    # Format output
    print(f"Showing {len(messages)} of {total} messages:\n")

    for storage_id, frontmatter in messages:
        short_id = storage_id[:6]
        timestamp = frontmatter.get("timestamp", "")
        channel_id = frontmatter.get("channel_id", "")
        user_id = frontmatter.get("user_id", "")
        text = frontmatter.get("text", "")[:60]
        thread_ts = frontmatter.get("thread_ts")

        # Format relative date
        ts_dt = pull_module.timestamp_to_datetime(timestamp)
        date_str = ts_dt.strftime("%Y-%m-%d %H:%M")

        # Format type indicator
        if thread_ts:
            type_str = "(thread)"
        elif channel_id.startswith("D"):
            type_str = "(DM)"
        else:
            type_str = ""

        # Print logic
        # Clean newlines for display
        clean_text = text.replace("\n", " ")
        print(f"{short_id} / {date_str} / {channel_id} / {user_id} {type_str}")
        if clean_text:
            print(f"  {clean_text}")
        print()

    if total > limit:
        print(f"... and {total - limit} more")
