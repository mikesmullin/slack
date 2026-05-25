"""HTTP Client for slack-chat CLI."""

import sys
import httpx
import json
import yaml
from .const import SERVER_URL, TOKENS_FILE


def get_client():
    """Create HTTP client with timeout."""
    return httpx.Client(timeout=60.0)


def load_tokens() -> dict:
    """Load saved session credentials from .tokens.yaml.

    Returns a dict with keys: token, cookie, workspace_url, is_enterprise.
    Returns an empty dict if the file does not exist or cannot be parsed.
    """
    try:
        if TOKENS_FILE.exists():
            data = yaml.safe_load(TOKENS_FILE.read_text()) or {}
            if data.get("token"):
                return data
    except Exception:
        pass
    return {}


def call_api_direct(endpoint: str, params: dict = None, tokens: dict = None) -> dict:
    """Call Slack API directly via httpx using saved credentials.

    Uses the token and 'd' cookie from .tokens.yaml (or the provided tokens
    dict).  For enterprise workspaces the call is routed to the workspace
    subdomain; otherwise it goes to https://slack.com.

    Args:
        endpoint: Slack API endpoint, e.g. 'conversations.history'
        params:   Form parameters to include in the POST body
        tokens:   Optional pre-loaded tokens dict (avoids re-reading the file)
    """
    if tokens is None:
        tokens = load_tokens()

    token = tokens.get("token")
    cookie = tokens.get("cookie")
    workspace_url = tokens.get("workspace_url") or "https://slack.com"
    base_url = workspace_url.rstrip("/")

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    if cookie:
        headers["Cookie"] = f"d={cookie}"

    form: dict = {"token": token}
    if params:
        for k, v in params.items():
            if isinstance(v, (dict, list)):
                form[k] = json.dumps(v)
            else:
                form[k] = str(v)

    try:
        with httpx.Client(timeout=60.0) as client:
            response = client.post(
                f"{base_url}/api/{endpoint}",
                headers=headers,
                data=form,
            )
            response.raise_for_status()
            data = response.json()
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except json.JSONDecodeError:
                    return {}
            return data
    except httpx.HTTPError as e:
        print(f"❌ Direct API error ({endpoint}): {e}", file=sys.stderr)
        return {}


def call_api(client, endpoint: str, params: dict = None):
    """Call Slack API.

    Prefers direct httpx calls using credentials from .tokens.yaml when
    available.  Falls back to routing the request through the browser-use
    server at localhost:3002 when no token file is present (legacy mode).
    """
    tokens = load_tokens()
    if tokens.get("token"):
        return call_api_direct(endpoint, params, tokens=tokens)

    # Legacy fallback: route through the browser-use server
    response = client.post(
        f"{SERVER_URL}/api",
        json={"endpoint": endpoint, "params": params or {}},
    )
    if response.status_code != 200:
        print(f"❌ API error: {response.text}", file=sys.stderr)
        return {}
    data = response.json()
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            return {}
    return data


def is_enterprise(client) -> bool:
    """Check if this is an enterprise workspace."""
    tokens = load_tokens()
    if tokens.get("is_enterprise") is not None:
        return bool(tokens["is_enterprise"])
    data = call_api(client, "team.info", {})
    if data.get("ok"):
        team = data.get("team", {})
        return team.get("enterprise_id") is not None
    return False


def handle_response(response):
    """Handle and print API response."""
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
