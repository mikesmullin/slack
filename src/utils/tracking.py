"""Read Tracking (Offline Metadata)."""

import yaml
from .const import READ_TRACKING_FILE

def load_read_events() -> set:
    """Load the set of event IDs that have been marked as read."""
    if not READ_TRACKING_FILE.exists():
        return set()
    try:
        with open(READ_TRACKING_FILE, "r") as f:
            data = yaml.safe_load(f) or {}
            return set(data.get("read_events", []))
    except Exception:
        return set()

def save_read_event(event_id: str):
    """Add an event ID to the read tracking file."""
    read_events = load_read_events()
    read_events.add(event_id)
    
    with open(READ_TRACKING_FILE, "w") as f:
        yaml.dump(
            {"read_events": sorted(list(read_events))},
            f,
            default_flow_style=False,
            sort_keys=False,
        )

def is_event_read(event_id: str) -> bool:
    """Check if an event has been marked as read."""
    read_events = load_read_events()
    
    # Direct match
    if event_id in read_events:
        return True
    
    # If this is a threaded event, check if base message is marked read
    if "@" in event_id:
        base_id = event_id.split("@")[0]
        if base_id in read_events:
            return True
    
    # Check if any variant of this event is marked read
    base_id = event_id.split("@")[0]
    for read_id in read_events:
        if read_id.startswith(base_id + "@") or read_id.startswith(base_id + ":"):
            if read_id.split("@")[0] == base_id:
                return True
    
    return False
