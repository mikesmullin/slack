"""ID Resolution (User/Channel)."""

import yaml
import json
import re
from .const import user_cache, SERVER_URL, CHANNELS_FILE
from .api import call_api
from .. import storage

def get_user_info(client, user_id):
    """Get user info, checking cache first."""
    # Check in-memory cache first
    if user_id in user_cache:
        return user_cache[user_id]
    
    # Check persistent disk cache
    cached = storage.get_cached_user(user_id)
    if cached:
        info = {
            "real_name": cached.get("profile", {}).get("real_name")
            or cached.get("real_name")
            or user_id,
            "display_name": cached.get("profile", {}).get("display_name")
            or cached.get("name")
            or user_id,
            "email": cached.get("profile", {}).get("email"),
        }
        user_cache[user_id] = info
        return info
    
    # Fetch from API
    try:
        response = client.post(
            f"{SERVER_URL}/api",
            json={"endpoint": "users.info", "params": {"user": user_id}},
        )
        if response.status_code == 200:
            data = response.json()
            if data.get("ok"):
                user = data.get("user", {})
                storage.cache_user(user_id, user)
                
                profile = user.get("profile", {})
                info = {
                    "real_name": profile.get("real_name")
                    or user.get("real_name")
                    or user_id,
                    "display_name": profile.get("display_name")
                    or user.get("name")
                    or user_id,
                    "email": profile.get("email"),
                }
                user_cache[user_id] = info
                return info
    except Exception:
        pass
    return {"real_name": user_id, "display_name": user_id}

def get_channel_name_by_id(client, channel_id: str) -> tuple:
    """Get channel info from ID. Returns (name, full_channel_data)."""
    # Check persistent disk cache first
    cached = storage.get_cached_channel(channel_id)
    if cached:
        return cached.get("name", channel_id), cached
    
    # Fetch from API
    try:
        response = client.post(
            f"{SERVER_URL}/api",
            json={"endpoint": "conversations.info", "params": {"channel": channel_id}},
        )
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, str):
                data = json.loads(data)
            
            if data.get("ok"):
                channel = data.get("channel", {})
                storage.cache_channel(channel_id, channel)
                return channel.get("name", channel_id), channel
    except Exception:
        pass
    return channel_id, {}

def get_user_name_by_id(client, user_id: str) -> tuple:
    """Get user info from ID. Returns (name, full_user_data)."""
    # Check persistent disk cache first
    cached = storage.get_cached_user(user_id)
    if cached:
        real_name = cached.get("real_name", "").strip()
        if real_name:
            return real_name, cached
        display_name = cached.get("profile", {}).get("display_name", "").strip()
        if display_name:
            return display_name, cached
        return cached.get("name", user_id), cached
    
    # Fetch from API
    try:
        response = client.post(
            f"{SERVER_URL}/api",
            json={"endpoint": "users.info", "params": {"user": user_id}},
        )
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, str):
                data = json.loads(data)
            
            if data.get("ok"):
                user = data.get("user", {})
                storage.cache_user(user_id, user)
                real_name = user.get("real_name", "").strip()
                if real_name:
                    return real_name, user
                display_name = user.get("profile", {}).get("display_name", "").strip()
                if display_name:
                    return display_name, user
                return user.get("name", user_id), user
    except Exception:
        pass
    return user_id, {}

def resolve_channel(channel_name_or_id: str):
    """Resolve channel name or ID to channel info dict."""
    if re.match(r"^[CDG][A-Z0-9]{8,}$", channel_name_or_id):
        return {"id": channel_name_or_id, "name": channel_name_or_id}
    
    name = channel_name_or_id.lstrip("#")
    
    # Check storage cache first
    cached = storage.find_channel_by_name(name)
    if cached:
        return {"id": cached.get("id"), "name": cached.get("name"), **cached}
    
    # Legacy: check old channels.yaml file
    if CHANNELS_FILE.exists():
        with open(CHANNELS_FILE, "r") as f:
            channels = yaml.safe_load(f) or []
            for ch in channels:
                if ch.get("name") == name or ch.get("name_normalized") == name:
                    return ch
    
    return {"id": channel_name_or_id, "name": channel_name_or_id}

def enrich_messages(client, messages):
    """Enrich messages with user info."""
    enriched = []
    for msg in messages:
        user_id = msg.get("user")
        if user_id:
            user_info = get_user_info(client, user_id)
            new_msg = {
                "timestamp": msg.get("ts"),
                "user": user_info["real_name"],
                "user_id": user_id,
                "text": msg.get("text", ""),
                "reply_count": msg.get("reply_count", 0),
                "has_thread": msg.get("reply_count", 0) > 0,
                "message_type": msg.get("type", "message"),
            }
            enriched.append(new_msg)
        else:
            enriched.append(msg)
    return enriched
