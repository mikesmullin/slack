"""Constants for slack-chat CLI."""

from pathlib import Path

WORKSPACE_ROOT = Path(__file__).parent.parent.parent
SERVER_URL = "http://localhost:3002"
PID_FILE = WORKSPACE_ROOT / "slack-server.pid"
LOG_FILE = WORKSPACE_ROOT / "slack-server.log"
READ_TRACKING_FILE = WORKSPACE_ROOT / "storage" / "read_events.yaml"
CHANNELS_FILE = WORKSPACE_ROOT / "storage" / "channels.yaml"

# In-memory cache
user_cache = {}
