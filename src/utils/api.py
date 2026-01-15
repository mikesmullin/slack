"""HTTP Client for slack-chat CLI."""

import sys
import httpx
from .const import SERVER_URL

def get_client():
    """Create HTTP client with timeout."""
    return httpx.Client(timeout=60.0)

def call_api(client, endpoint: str, params: dict = None):
    """Call Slack API via browser-use server."""
    response = client.post(
        f"{SERVER_URL}/api",
        json={"endpoint": endpoint, "params": params or {}},
    )
    if response.status_code != 200:
        print(f"❌ API error: {response.text}", file=sys.stderr)
        return {}
    return response.json()

def is_enterprise(client) -> bool:
    """Check if this is an enterprise workspace."""
    data = call_api(client, "team.info", {})
    if data.get("ok"):
        team = data.get("team", {})
        return team.get("enterprise_id") is not None
    return False

def handle_response(response):
    """Handle and print API response."""
    import yaml
    try:
        response.raise_for_status()
        data = response.json()
        print(yaml.dump(data, indent=2, sort_keys=False))
    except httpx.HTTPStatusError as e:
        print(f"Error: {e.response.text}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
