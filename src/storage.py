"""
Storage module for offline Slack message cache.

Handles reading/writing .md files with YAML frontmatter to storage/ directory.
Also manages ID resolution cache under storage/_cache/.
"""

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, List, Any, Tuple

import yaml

WORKSPACE_ROOT = Path(__file__).parent.parent
STORAGE_DIR = WORKSPACE_ROOT / "storage"
CACHE_DIR = STORAGE_DIR / "_cache"
USERS_CACHE_FILE = CACHE_DIR / "users.yml"
CHANNELS_CACHE_FILE = CACHE_DIR / "channels.yml"


def ensure_storage_dirs():
    """Ensure storage and cache directories exist."""
    STORAGE_DIR.mkdir(exist_ok=True)
    CACHE_DIR.mkdir(exist_ok=True)


def generate_storage_id(channel_id: str, timestamp: str, thread_ts: Optional[str] = None) -> str:
    """
    Generate SHA1 hash for storage file ID.
    
    Format: SHA1(channel_id:timestamp) or SHA1(channel_id:timestamp@thread_ts)
    """
    if thread_ts:
        key = f"{channel_id}:{timestamp}@{thread_ts}"
    else:
        key = f"{channel_id}:{timestamp}"
    return hashlib.sha1(key.encode()).hexdigest()


def get_storage_path(storage_id: str) -> Path:
    """Get the full path for a storage file."""
    return STORAGE_DIR / f"{storage_id}.md"


def file_exists(storage_id: str) -> bool:
    """Check if a storage file already exists."""
    return get_storage_path(storage_id).exists()


def find_by_partial_id(partial_id: str) -> Optional[Tuple[str, Path]]:
    """
    Find a storage file by partial ID (git-style).
    
    Returns (full_id, path) if unique match found, None otherwise.
    Raises ValueError if ambiguous (multiple matches).
    """
    partial_id = partial_id.replace(".md", "")
    matches = []
    
    for f in STORAGE_DIR.glob("*.md"):
        if f.name.startswith("_"):
            continue  # Skip cache files
        file_id = f.stem
        if file_id.startswith(partial_id):
            matches.append((file_id, f))
    
    if len(matches) == 0:
        return None
    elif len(matches) == 1:
        return matches[0]
    else:
        ids = [m[0] for m in matches]
        raise ValueError(f"Ambiguous ID '{partial_id}' matches: {', '.join(ids[:5])}{'...' if len(ids) > 5 else ''}")


def load_all_messages() -> List[Tuple[str, Dict[str, Any]]]:
    """
    Load all messages from storage.
    
    Returns list of (storage_id, frontmatter_dict) tuples, sorted by timestamp (newest first).
    """
    messages = []
    
    for f in STORAGE_DIR.glob("*.md"):
        if f.name.startswith("_"):
            continue  # Skip cache directory
        
        try:
            frontmatter, _ = read_message_file(f)
            if frontmatter:
                messages.append((f.stem, frontmatter))
        except Exception:
            continue
    
    # Sort by timestamp (newest first)
    messages.sort(key=lambda x: x[1].get("timestamp", "0"), reverse=True)
    return messages


def read_message_file(path: Path) -> Tuple[Optional[Dict[str, Any]], str]:
    """
    Read a message file, parsing YAML frontmatter and body.
    
    Returns (frontmatter_dict, body_content).
    """
    content = path.read_text(encoding="utf-8")
    
    if not content.startswith("---"):
        return None, content
    
    # Find the closing ---
    end_idx = content.find("\n---", 3)
    if end_idx == -1:
        return None, content
    
    frontmatter_str = content[4:end_idx]
    body = content[end_idx + 4:].strip()
    
    try:
        frontmatter = yaml.safe_load(frontmatter_str)
        return frontmatter, body
    except yaml.YAMLError:
        return None, content


def read_message(storage_id: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """
    Read a message by storage ID.
    
    Returns (frontmatter_dict, body_content).
    """
    path = get_storage_path(storage_id)
    if not path.exists():
        return None, ""
    return read_message_file(path)


def write_message(
    channel_id: str,
    timestamp: str,
    message_data: Dict[str, Any],
    thread_ts: Optional[str] = None,
    skip_existing: bool = True
) -> Optional[str]:
    """
    Write a message to storage as a .md file with YAML frontmatter.
    
    Args:
        channel_id: Slack channel ID
        timestamp: Message timestamp
        message_data: Raw message data from Slack API
        thread_ts: Thread timestamp (if this is a thread reply)
        skip_existing: If True, don't overwrite existing files
    
    Returns:
        storage_id if written, None if skipped
    """
    ensure_storage_dirs()
    
    storage_id = generate_storage_id(channel_id, timestamp, thread_ts)
    path = get_storage_path(storage_id)
    
    if skip_existing and path.exists():
        return None
    
    # Build frontmatter
    frontmatter = {
        "channel_id": channel_id,
        "timestamp": timestamp,
        "thread_ts": thread_ts,
        "user_id": message_data.get("user"),
        "type": message_data.get("type", "message"),
        "text": message_data.get("text", ""),
        "permalink": message_data.get("permalink", ""),
        "reactions": message_data.get("reactions", []),
        "attachments": message_data.get("attachments", []),
        "files": message_data.get("files", []),
        "_stored_id": storage_id,
        "_stored_at": datetime.now(timezone.utc).isoformat(),
        "offline": {
            "read": False
        }
    }
    
    # Include any extra fields from the raw message
    for key in ["reply_count", "reply_users_count", "latest_reply", "subtype"]:
        if key in message_data:
            frontmatter[key] = message_data[key]
    
    # Build markdown body
    body = build_message_body(frontmatter)
    
    # Write file
    frontmatter_yaml = yaml.dump(frontmatter, default_flow_style=False, allow_unicode=True, sort_keys=False)
    content = f"---\n{frontmatter_yaml}---\n\n{body}"
    
    path.write_text(content, encoding="utf-8")
    return storage_id


def build_message_body(frontmatter: Dict[str, Any]) -> str:
    """Build the markdown body section of a message file."""
    lines = []
    
    # Header
    user_id = frontmatter.get("user_id", "Unknown")
    channel_id = frontmatter.get("channel_id", "")
    timestamp = frontmatter.get("timestamp", "")
    permalink = frontmatter.get("permalink", "")
    thread_ts = frontmatter.get("thread_ts")
    
    # Title
    if thread_ts:
        lines.append(f"# Thread Reply in {channel_id}")
    else:
        lines.append(f"# Message in {channel_id}")
    
    lines.append("")
    
    # Metadata
    lines.append(f"**From:** {user_id}")
    lines.append(f"**Channel:** {channel_id}")
    lines.append(f"**Timestamp:** {timestamp}")
    if thread_ts:
        lines.append(f"**Thread:** {thread_ts}")
    if permalink:
        lines.append(f"**Permalink:** [{permalink}]({permalink})")
    
    lines.append("")
    lines.append("---")
    lines.append("")
    
    # Message text (verbatim, Slack mrkdwn format)
    text = frontmatter.get("text", "")
    lines.append(text if text else "(no text)")
    
    # Reactions
    reactions = frontmatter.get("reactions", [])
    if reactions:
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("## Reactions")
        lines.append("")
        for r in reactions:
            name = r.get("name", "?")
            count = r.get("count", 0)
            lines.append(f"- :{name}: ({count})")
    
    # Attachments
    attachments = frontmatter.get("attachments", [])
    files = frontmatter.get("files", [])
    
    if attachments or files:
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("## Attachments")
        lines.append("")
        
        for att in attachments:
            title = att.get("title") or att.get("fallback") or "Attachment"
            url = att.get("image_url") or att.get("thumb_url") or att.get("from_url", "")
            if url:
                lines.append(f"- [{title}]({url})")
            else:
                lines.append(f"- {title}")
        
        for f in files:
            name = f.get("name") or f.get("title") or "File"
            url = f.get("url_private") or f.get("permalink", "")
            mimetype = f.get("mimetype", "")
            if url:
                if mimetype.startswith("image/"):
                    lines.append(f"- ![{name}]({url})")
                else:
                    lines.append(f"- [{name}]({url})")
            else:
                lines.append(f"- {name}")
    
    return "\n".join(lines)


def update_message_offline_status(storage_id: str, read: bool) -> bool:
    """
    Update the offline.read status of a message.
    
    Returns True if updated, False if file not found.
    """
    path = get_storage_path(storage_id)
    if not path.exists():
        return False
    
    frontmatter, body = read_message_file(path)
    if frontmatter is None:
        return False
    
    # Update offline status
    if "offline" not in frontmatter:
        frontmatter["offline"] = {}
    
    frontmatter["offline"]["read"] = read
    if read:
        frontmatter["offline"]["readAt"] = datetime.now(timezone.utc).isoformat()
    elif "readAt" in frontmatter["offline"]:
        del frontmatter["offline"]["readAt"]
    
    # Write back
    frontmatter_yaml = yaml.dump(frontmatter, default_flow_style=False, allow_unicode=True, sort_keys=False)
    content = f"---\n{frontmatter_yaml}---\n\n{body}"
    path.write_text(content, encoding="utf-8")
    
    return True


def is_message_read(frontmatter: Dict[str, Any]) -> bool:
    """Check if a message is marked as read offline."""
    return frontmatter.get("offline", {}).get("read", False)


# =============================================================================
# ID Resolution Cache
# =============================================================================

def _load_cache(cache_file: Path) -> Dict[str, Any]:
    """Load a cache file, returning empty dict if not exists."""
    if not cache_file.exists():
        return {}
    try:
        content = cache_file.read_text(encoding="utf-8")
        return yaml.safe_load(content) or {}
    except Exception:
        return {}


def _save_cache(cache_file: Path, data: Dict[str, Any]):
    """Save data to a cache file."""
    ensure_storage_dirs()
    content = yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)
    cache_file.write_text(content, encoding="utf-8")


def get_cached_user(user_id: str) -> Optional[Dict[str, Any]]:
    """Get user info from cache, or None if not cached."""
    cache = _load_cache(USERS_CACHE_FILE)
    return cache.get(user_id)


def cache_user(user_id: str, user_data: Dict[str, Any]):
    """Cache user info from API response."""
    cache = _load_cache(USERS_CACHE_FILE)
    user_data["_cached_at"] = datetime.now(timezone.utc).isoformat()
    cache[user_id] = user_data
    _save_cache(USERS_CACHE_FILE, cache)


def get_cached_channel(channel_id: str) -> Optional[Dict[str, Any]]:
    """Get channel info from cache, or None if not cached."""
    cache = _load_cache(CHANNELS_CACHE_FILE)
    return cache.get(channel_id)


def cache_channel(channel_id: str, channel_data: Dict[str, Any]):
    """Cache channel info from API response."""
    cache = _load_cache(CHANNELS_CACHE_FILE)
    channel_data["_cached_at"] = datetime.now(timezone.utc).isoformat()
    cache[channel_id] = channel_data
    _save_cache(CHANNELS_CACHE_FILE, cache)


def get_all_cached_channels() -> Dict[str, Dict[str, Any]]:
    """Get all cached channels."""
    return _load_cache(CHANNELS_CACHE_FILE)


def get_all_cached_users() -> Dict[str, Dict[str, Any]]:
    """Get all cached users."""
    return _load_cache(USERS_CACHE_FILE)


def find_channel_by_name(name: str) -> Optional[Dict[str, Any]]:
    """
    Find a channel by name in the cache.
    
    Matches against 'name' or 'name_normalized' fields.
    Returns channel data if found, None otherwise.
    """
    name = name.lstrip('#').lower()
    cache = _load_cache(CHANNELS_CACHE_FILE)
    
    for channel_id, channel_data in cache.items():
        channel_name = channel_data.get("name", "").lower()
        channel_name_normalized = channel_data.get("name_normalized", "").lower()
        if channel_name == name or channel_name_normalized == name:
            return channel_data
    
    return None


def find_channels_by_keyword(keyword: str) -> List[Dict[str, Any]]:
    """
    Find channels whose name contains the keyword (case-insensitive).
    
    Returns list of channel data dicts, sorted by name.
    """
    keyword = keyword.lower()
    cache = _load_cache(CHANNELS_CACHE_FILE)
    
    matches = []
    for channel_id, channel_data in cache.items():
        channel_name = channel_data.get("name", "").lower()
        if keyword in channel_name:
            matches.append(channel_data)
    
    # Sort by name
    matches.sort(key=lambda x: x.get("name", ""))
    return matches


def find_user_by_name(name: str) -> Optional[Dict[str, Any]]:
    """
    Find a user by name in the cache.
    
    Matches against 'name', 'real_name', or 'display_name' fields.
    Returns user data if found, None otherwise.
    """
    name = name.lstrip('@').lower()
    cache = _load_cache(USERS_CACHE_FILE)
    
    for user_id, user_data in cache.items():
        user_name = user_data.get("name", "").lower()
        real_name = user_data.get("real_name", "").lower()
        display_name = user_data.get("profile", {}).get("display_name", "").lower()
        if user_name == name or real_name == name or display_name == name:
            return user_data
    
    return None


def find_users_by_keyword(keyword: str) -> List[Dict[str, Any]]:
    """
    Find users where any string field contains the keyword (case-insensitive).
    
    Searches across all fields: name, real_name, display_name, title, email, 
    team/project custom fields, etc.
    
    Returns list of user data dicts, sorted by real_name.
    """
    keyword = keyword.lower()
    cache = _load_cache(USERS_CACHE_FILE)
    
    def search_in_value(value: Any) -> bool:
        """Recursively search for keyword in any string value."""
        if isinstance(value, str):
            return keyword in value.lower()
        elif isinstance(value, dict):
            return any(search_in_value(v) for v in value.values())
        elif isinstance(value, list):
            return any(search_in_value(v) for v in value)
        return False
    
    matches = []
    for user_id, user_data in cache.items():
        if search_in_value(user_data):
            matches.append(user_data)
    
    # Sort by real_name
    matches.sort(key=lambda x: x.get("real_name", x.get("name", "")))
    return matches
