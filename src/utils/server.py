"""Server Management."""

from .const import PID_FILE

def get_server_pid():
    """Get server PID from file."""
    if PID_FILE.exists():
        try:
            return int(PID_FILE.read_text().strip())
        except ValueError:
            return None
    return None
