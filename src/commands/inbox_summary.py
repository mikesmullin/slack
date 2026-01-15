"""Inbox Summary Commands."""

import yaml
from ..utils import (
    get_client,
    fetch_unread_counts,
    fetch_subscribed_threads,
)
from .. import storage

def inbox_summary_online():
    """Quick summary of unread counts (single API call)."""
    with get_client() as client:
        counts = fetch_unread_counts(client)
        threads = fetch_subscribed_threads(client)
        channels = counts.get("total_channels", 0)
        im_counts = counts.get("ims", [])
        ims = [i for i in im_counts if i.get("unread_count_display", 0) > 0]
        
        output = {
            "summary": {
                "unread": counts.get("channels", 0) + counts.get("dms", 0) + threads.get("total_unread_replies", 0),
                "unread_channels": counts.get("channels", 0),
                "unread_dms": counts.get("dms", 0),
                "unread_threads": threads.get("total_unread_replies", 0),
                "total_channels": channels,
                "total_dms": len(ims),
            }
        }
        print(yaml.dump(output, indent=2, sort_keys=False, default_flow_style=False))


def inbox_summary_offline():
    """Show counts from local storage (offline)."""
    # Offline: read from local storage
    messages = storage.load_all_messages()

    unread = 0
    read = 0
    by_type = {"channel": 0, "dm": 0, "thread": 0, "mention": 0}

    for storage_id, frontmatter in messages:
        if storage.is_message_read(frontmatter):
            read += 1
        else:
            unread += 1
            # Categorize by type
            thread_ts = frontmatter.get("thread_ts")
            channel_id = frontmatter.get("channel_id", "")
            is_mention = frontmatter.get("_mention", False)

            if is_mention:
                by_type["mention"] += 1
            elif thread_ts:
                by_type["thread"] += 1
            elif channel_id.startswith("D"):
                by_type["dm"] += 1
            else:
                by_type["channel"] += 1

    output = {
        "summary": {
            "unread": unread,
            "read": read,
            "total": len(messages),
            "by_type": by_type,
        }
    }
    print(yaml.dump(output, indent=2, sort_keys=False, default_flow_style=False))
