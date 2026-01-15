"""
Pull module for fetching Slack messages to local storage.

Implements the `slack pull` command which fetches unread messages
from Slack and stores them in storage/*.md files.
"""

import sys
from datetime import datetime, timezone
from typing import Optional

from . import storage


def parse_since_date(date_str: str) -> datetime:
    """
    Parse date from various formats.
    
    Supported formats:
    - YYYY-MM-DD: Exact date at midnight UTC
    - yesterday: Yesterday at midnight UTC
    - "N days ago": N days before now at midnight UTC
    
    Returns datetime at midnight UTC.
    """
    date_str = date_str.strip().lower()
    now = datetime.now(timezone.utc)
    
    if date_str == "yesterday":
        result = now.replace(hour=0, minute=0, second=0, microsecond=0)
        result = result.replace(day=result.day - 1)
        return result
    
    if date_str.endswith("ago"):
        import re
        match = re.match(r'^(\d+)\s+days?\s+ago$', date_str)
        if match:
            days_ago = int(match.group(1))
            from datetime import timedelta
            result = now - timedelta(days=days_ago)
            result = result.replace(hour=0, minute=0, second=0, microsecond=0)
            return result
        raise ValueError(f"Invalid date format: '{date_str}'. Use 'N days ago' format.")
    
    # Try YYYY-MM-DD
    import re
    if re.match(r'^\d{4}-\d{2}-\d{2}$', date_str):
        return datetime.fromisoformat(date_str + "T00:00:00+00:00")
    
    raise ValueError(
        f"Invalid date format: '{date_str}'. "
        "Accepted formats: YYYY-MM-DD, yesterday, or 'N days ago'."
    )


def timestamp_to_datetime(ts: str) -> datetime:
    """Convert Slack timestamp to datetime."""
    try:
        unix_ts = float(ts.split(".")[0])
        return datetime.fromtimestamp(unix_ts, tz=timezone.utc)
    except (ValueError, AttributeError):
        return datetime.min.replace(tzinfo=timezone.utc)


def pull_messages(
    client,
    call_api_fn,
    since: str,
    limit: int = 100,
    type_filter: Optional[str] = None,
    channel_filter: Optional[str] = None,
    verbose: bool = True
) -> dict:
    """
    Pull unread messages from Slack and store locally.
    
    Args:
        client: HTTP client for API calls
        call_api_fn: Function to call Slack API (endpoint, params) -> response
        since: Date string for cutoff (YYYY-MM-DD, yesterday, "N days ago")
        limit: Maximum messages to fetch per category
        type_filter: Filter by type (channels, dms, threads, mentions, all)
        channel_filter: If provided, only pull from this channel ID
        verbose: Print progress to stdout
    
    Returns:
        dict with stats: {"fetched": N, "stored": N, "skipped": N, "errors": [...]}
    """
    since_dt = parse_since_date(since)
    stats = {"fetched": 0, "stored": 0, "skipped": 0, "errors": []}
    
    if verbose:
        if channel_filter:
            print(f"📥 Pulling messages from {channel_filter} since {since_dt.strftime('%Y-%m-%d')}...")
        else:
            print(f"📥 Pulling messages since {since_dt.strftime('%Y-%m-%d')}...")
    
    # If channel_filter is provided, just pull from that channel directly
    if channel_filter:
        if verbose:
            print(f"  📢 Fetching from {channel_filter}...")
        _pull_single_channel(client, call_api_fn, channel_filter, since_dt, limit, stats, verbose)
        if verbose:
            print(f"\n✅ Done: {stats['stored']} stored, {stats['skipped']} skipped, {stats['fetched']} fetched total")
            if stats['errors']:
                print(f"⚠️  {len(stats['errors'])} errors occurred")
        return stats
    
    # Determine which types to fetch
    fetch_all = type_filter is None or type_filter == "all"
    fetch_channels = fetch_all or type_filter == "channels"
    fetch_dms = fetch_all or type_filter == "dms"
    fetch_threads = fetch_all or type_filter == "threads"
    fetch_mentions = fetch_all or type_filter == "mentions"
    
    # 1. Channels with unreads
    if fetch_channels:
        if verbose:
            print("  📢 Fetching channel messages...")
        _pull_channel_messages(client, call_api_fn, since_dt, limit, stats, verbose)
    
    # 2. DMs with unreads
    if fetch_dms:
        if verbose:
            print("  💬 Fetching DM messages...")
        _pull_dm_messages(client, call_api_fn, since_dt, limit, stats, verbose)
    
    # 3. Thread replies
    if fetch_threads:
        if verbose:
            print("  🧵 Fetching thread replies...")
        _pull_thread_messages(client, call_api_fn, since_dt, limit, stats, verbose)
    
    # 4. Mentions (@me)
    if fetch_mentions:
        if verbose:
            print("  📣 Fetching mentions...")
        _pull_mentions(client, call_api_fn, since_dt, limit, stats, verbose)
    
    if verbose:
        print(f"\n✅ Done: {stats['stored']} stored, {stats['skipped']} skipped, {stats['fetched']} fetched total")
        if stats['errors']:
            print(f"⚠️  {len(stats['errors'])} errors occurred")
    
    return stats


def _pull_single_channel(client, call_api_fn, channel_id: str, since_dt: datetime, limit: int, stats: dict, verbose: bool):
    """Pull messages from a single specific channel."""
    # Fetch recent messages from channel
    history = call_api_fn(client, "conversations.history", {
        "channel": channel_id,
        "limit": min(limit, 100)
    })
    
    if not history.get("ok"):
        stats["errors"].append(f"Failed to fetch history for {channel_id}: {history.get('error', 'unknown')}")
        return
    
    for msg in history.get("messages", []):
        stats["fetched"] += 1
        ts = msg.get("ts", "")
        msg_dt = timestamp_to_datetime(ts)
        
        if msg_dt < since_dt:
            continue
        
        # Store message
        thread_ts = msg.get("thread_ts") if msg.get("thread_ts") != ts else None
        storage_id = storage.write_message(
            channel_id=channel_id,
            timestamp=ts,
            message_data=msg,
            thread_ts=thread_ts,
            skip_existing=True
        )
        
        if storage_id:
            stats["stored"] += 1
            if verbose:
                print(f"    + {storage_id[:8]}... ({channel_id})")
        else:
            stats["skipped"] += 1


def _pull_channel_messages(client, call_api_fn, since_dt: datetime, limit: int, stats: dict, verbose: bool):
    """Pull unread messages from channels."""
    # Get channels with unreads
    counts_data = call_api_fn(client, "users.counts", {})
    if not counts_data.get("ok"):
        # Try enterprise API
        counts_data = call_api_fn(client, "client.counts", {})
    
    if not counts_data.get("ok"):
        stats["errors"].append("Failed to fetch channel counts")
        return
    
    channels = counts_data.get("channels", []) + counts_data.get("groups", [])
    channels_with_unreads = [
        c for c in channels 
        if c.get("unread_count_display", 0) > 0 or c.get("has_unreads")
    ]
    
    for ch in channels_with_unreads[:limit]:
        channel_id = ch.get("id")
        if not channel_id:
            continue
        
        # Fetch recent messages from channel
        history = call_api_fn(client, "conversations.history", {
            "channel": channel_id,
            "limit": min(limit, 100)
        })
        
        if not history.get("ok"):
            stats["errors"].append(f"Failed to fetch history for {channel_id}")
            continue
        
        for msg in history.get("messages", []):
            stats["fetched"] += 1
            ts = msg.get("ts", "")
            msg_dt = timestamp_to_datetime(ts)
            
            if msg_dt < since_dt:
                continue
            
            # Store message
            thread_ts = msg.get("thread_ts") if msg.get("thread_ts") != ts else None
            storage_id = storage.write_message(
                channel_id=channel_id,
                timestamp=ts,
                message_data=msg,
                thread_ts=thread_ts,
                skip_existing=True
            )
            
            if storage_id:
                stats["stored"] += 1
                if verbose:
                    print(f"    + {storage_id[:8]}... ({channel_id})")
            else:
                stats["skipped"] += 1


def _pull_dm_messages(client, call_api_fn, since_dt: datetime, limit: int, stats: dict, verbose: bool):
    """Pull unread messages from DMs."""
    # Get DMs with unreads
    counts_data = call_api_fn(client, "users.counts", {})
    if not counts_data.get("ok"):
        counts_data = call_api_fn(client, "client.counts", {})
    
    if not counts_data.get("ok"):
        stats["errors"].append("Failed to fetch DM counts")
        return
    
    ims = counts_data.get("ims", [])
    ims_with_unreads = [
        im for im in ims 
        if im.get("dm_count", 0) > 0 or im.get("has_unreads")
    ]
    
    for im in ims_with_unreads[:limit]:
        channel_id = im.get("id")
        if not channel_id:
            continue
        
        # Fetch recent messages from DM
        history = call_api_fn(client, "conversations.history", {
            "channel": channel_id,
            "limit": min(limit, 100)
        })
        
        if not history.get("ok"):
            stats["errors"].append(f"Failed to fetch DM history for {channel_id}")
            continue
        
        for msg in history.get("messages", []):
            stats["fetched"] += 1
            ts = msg.get("ts", "")
            msg_dt = timestamp_to_datetime(ts)
            
            if msg_dt < since_dt:
                continue
            
            thread_ts = msg.get("thread_ts") if msg.get("thread_ts") != ts else None
            storage_id = storage.write_message(
                channel_id=channel_id,
                timestamp=ts,
                message_data=msg,
                thread_ts=thread_ts,
                skip_existing=True
            )
            
            if storage_id:
                stats["stored"] += 1
                if verbose:
                    print(f"    + {storage_id[:8]}... (DM {channel_id})")
            else:
                stats["skipped"] += 1


def _pull_thread_messages(client, call_api_fn, since_dt: datetime, limit: int, stats: dict, verbose: bool):
    """Pull unread thread replies."""
    # Get subscribed threads
    threads_data = call_api_fn(client, "subscriptions.thread.getView", {})
    
    if not threads_data.get("ok"):
        stats["errors"].append("Failed to fetch subscribed threads")
        return
    
    threads = threads_data.get("threads", [])
    
    for t in threads[:limit]:
        root = t.get("root_msg", {})
        channel_id = root.get("channel")
        thread_ts = root.get("thread_ts") or root.get("ts")
        
        if not channel_id or not thread_ts:
            continue
        
        # Fetch thread replies
        replies = call_api_fn(client, "conversations.replies", {
            "channel": channel_id,
            "ts": thread_ts,
            "limit": min(limit, 100)
        })
        
        if not replies.get("ok"):
            stats["errors"].append(f"Failed to fetch thread {channel_id}:{thread_ts}")
            continue
        
        for msg in replies.get("messages", []):
            stats["fetched"] += 1
            ts = msg.get("ts", "")
            msg_dt = timestamp_to_datetime(ts)
            
            if msg_dt < since_dt:
                continue
            
            # For thread replies, thread_ts is the parent; for root, it's None
            msg_thread_ts = thread_ts if ts != thread_ts else None
            
            storage_id = storage.write_message(
                channel_id=channel_id,
                timestamp=ts,
                message_data=msg,
                thread_ts=msg_thread_ts,
                skip_existing=True
            )
            
            if storage_id:
                stats["stored"] += 1
                if verbose:
                    print(f"    + {storage_id[:8]}... (thread in {channel_id})")
            else:
                stats["skipped"] += 1


def _pull_mentions(client, call_api_fn, since_dt: datetime, limit: int, stats: dict, verbose: bool):
    """Pull @mentions to me."""
    # Search for mentions
    search_data = call_api_fn(client, "search.messages", {
        "query": "to:me",
        "sort": "timestamp",
        "count": limit
    })
    
    if not search_data.get("ok"):
        stats["errors"].append("Failed to search mentions")
        return
    
    matches = search_data.get("messages", {}).get("matches", [])
    
    for msg in matches:
        stats["fetched"] += 1
        ts = msg.get("ts", "")
        msg_dt = timestamp_to_datetime(ts)
        
        if msg_dt < since_dt:
            continue
        
        channel = msg.get("channel", {})
        channel_id = channel.get("id") if isinstance(channel, dict) else channel
        
        if not channel_id:
            continue
        
        thread_ts = msg.get("thread_ts")
        if thread_ts == ts:
            thread_ts = None
        
        # Add mention type to message data
        msg_data = dict(msg)
        msg_data["_mention"] = True
        
        storage_id = storage.write_message(
            channel_id=channel_id,
            timestamp=ts,
            message_data=msg_data,
            thread_ts=thread_ts,
            skip_existing=True
        )
        
        if storage_id:
            stats["stored"] += 1
            if verbose:
                print(f"    + {storage_id[:8]}... (mention)")
        else:
            stats["skipped"] += 1
