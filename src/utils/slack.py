"""Slack API Helpers."""

import sys
from .api import call_api, is_enterprise

def fetch_unread_counts(client) -> dict:
    """Fetch unread counts from Slack."""
    
    try:
        # Check if enterprise
        is_ent = is_enterprise(client)
        
        # Get channel list with unread info
        all_channels = []
        cursor = None
        
        while True:
            params = {
                "types": "public_channel,private_channel,mpim,im",
                "exclude_archived": True,
                "limit": 200,
            }
            if cursor:
                params["cursor"] = cursor
            
            data = call_api(client, "conversations.list", params)
            if not data.get("ok"):
                break
            
            channels = data.get("channels", [])
            all_channels.extend(channels)
            
            cursor = data.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break
        
        # Count unreads
        unread_dms = 0
        unread_channels = 0
        dm_count = 0
        channel_count = 0
        
        for ch in all_channels:
            ch_type = ch.get("is_im")
            has_unread = ch.get("has_unreads", False) or ch.get("unread_count_display", 0) > 0
            
            if ch_type:
                dm_count += 1
                if has_unread:
                    unread_dms += 1
            else:
                channel_count += 1
                if has_unread:
                    unread_channels += 1
        
        # Get thread info
        thread_data = {}
        try:
            thread_data = call_api(client, "subscriptions.thread.getView", {})
        except Exception:
            pass
        
        unread_replies = thread_data.get("total_unread_replies", 0)
        
        return {
            "channels": unread_channels,
            "dms": unread_dms,
            "threads": unread_replies,
            "total_channels": channel_count,
            "total_dms": dm_count,
        }
    
    except Exception as e:
        print(f"Error fetching counts: {e}", file=sys.stderr)
        return {}


def fetch_subscribed_threads(client, cursor: str = None) -> dict:
    """Fetch subscribed threads with unread info."""
    params = {}
    if cursor:
        params["cursor"] = cursor
    data = call_api(client, "subscriptions.thread.getView", params)
    if not data.get("ok"):
        print(
            f"❌ subscriptions.thread.getView failed: {data.get('error')}",
            file=sys.stderr,
        )
        return {"threads": [], "total_unread_replies": 0, "has_more": False}
    return data


def fetch_mentions(client, limit: int = 20) -> list:
    """Fetch @mentions."""
    data = call_api(
        client,
        "search.messages",
        {"query": "to:me", "sort": "timestamp", "count": limit},
    )
    if not data.get("ok"):
        return []
    return data.get("messages", {}).get("matches", [])


def fetch_reactions_to_me(client, limit: int = 20) -> list:
    """Fetch messages with reactions."""
    data = call_api(
        client,
        "search.messages",
        {"query": "from:me has:reaction", "sort": "timestamp", "count": limit},
    )
    if not data.get("ok"):
        return []
    return data.get("messages", {}).get("matches", [])


def get_reaction_details(client, channel_id: str, timestamp: str) -> list:
    """Get details about who reacted to a message."""
    try:
        data = call_api(
            client, "reactions.get", {"channel": channel_id, "timestamp": timestamp}
        )
        if data.get("ok"):
            reactions = data.get("message", {}).get("reactions", [])
            result = []
            for reaction in reactions:
                emoji = reaction.get("name")
                for user_id in reaction.get("users", []):
                    result.append({"emoji": emoji, "user_id": user_id})
            return result
    except Exception:
        pass
    return []
