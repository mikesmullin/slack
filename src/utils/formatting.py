"""Formatting and Parsing."""

import os
from .const import SERVER_URL

def format_event_id(
    channel_id: str, timestamp: str = None, thread_ts: str = None
) -> str:
    """Format event ID as channel:timestamp or channel:timestamp@thread_ts."""
    if timestamp:
        event_id = f"{channel_id}:{timestamp}"
        if thread_ts:
            event_id += f"@{thread_ts}"
        return event_id
    return channel_id


def parse_event_id(event_id: str) -> tuple:
    """Parse event ID into (channel_id, timestamp, thread_ts)."""
    thread_ts = None
    
    if ":" in event_id:
        parts = event_id.split(":", 1)
        channel_id = parts[0]
        timestamp_part = parts[1]
        
        # Check for thread format
        if "@" in timestamp_part:
            timestamp, thread_ts = timestamp_part.split("@", 1)
            return channel_id, timestamp, thread_ts
        
        return channel_id, timestamp_part, thread_ts
    
    return event_id, None, None


def format_channel_display(channel_data: dict) -> str:
    """Format channel for display."""
    name = channel_data.get("name", "")
    channel_id = channel_data.get("id", "")
    
    # For multi-person DMs, show the channel ID
    if name.startswith("mpdm-"):
        return f"#{channel_id}"
    
    return f"#{name or channel_id}"


def truncate_text(text: str, max_len: int = 50) -> str:
    """Truncate text to max length."""
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."


def generate_slack_url(channel_id: str, timestamp: str = None) -> str:
    """Generate a Slack URL for a message or channel."""
    workspace_domain = os.environ.get(
        "SLACK_WORKSPACE_DOMAIN", "bigco-producta.slack.com"
    )
    
    if timestamp:
        ts_formatted = timestamp.replace(".", "")
        return f"https://{workspace_domain}/archives/{channel_id}/p{ts_formatted}"
    else:
        return f"https://{workspace_domain}/archives/{channel_id}"


def extract_thread_ts_from_permalink(permalink: str) -> str:
    """Extract thread_ts from Slack permalink URL."""
    if not permalink or "thread_ts=" not in permalink:
        return None
    
    try:
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(permalink)
        params = parse_qs(parsed.query)
        thread_ts_list = params.get("thread_ts", [])
        return thread_ts_list[0] if thread_ts_list else None
    except Exception:
        return None


def extract_image_urls(message: dict) -> list:
    """Extract image URLs from message attachments."""
    images = []
    
    # Check for files
    if "files" in message:
        for file in message.get("files", []):
            if file.get("mimetype", "").startswith("image/"):
                url = file.get("url_private") or file.get("url")
                if url:
                    images.append(url)
    
    # Check for attachments
    if "attachments" in message:
        for attachment in message.get("attachments", []):
            image_url = attachment.get("image_url")
            if image_url:
                images.append(image_url)
            thumb_url = attachment.get("thumb_url")
            if thumb_url:
                images.append(thumb_url)
    
    return images
