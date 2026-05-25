"""api subcommand — call any Slack API endpoint directly."""

import json
import sys
from typing import Optional

import httpx
import typer

from ..utils import load_tokens


def api_command(
    endpoint: str = typer.Argument(..., help="Slack API endpoint, e.g. users.list"),
    params: Optional[str] = typer.Option(
        None, "--params", "-p",
        help='Query/form parameters as JSON, e.g. \'{"limit": 10}\'',
    ),
    data: Optional[str] = typer.Option(
        None, "--data", "-d",
        help='Additional POST body parameters as JSON (merged with --params)',
    ),
    method: str = typer.Option(
        "POST", "--method", "-X",
        help="HTTP method: GET or POST (default POST)",
    ),
    yaml_out: bool = typer.Option(False, "--yaml", help="Output as YAML instead of JSON"),
):
    """Call any Slack API endpoint directly using saved credentials."""
    tokens = load_tokens()
    if not tokens.get("token"):
        print("❌ No credentials found. Run `slack-chat server start` first.", file=sys.stderr)
        sys.exit(1)

    token = tokens.get("token")
    cookie = tokens.get("cookie")
    workspace_url = tokens.get("workspace_url") or "https://slack.com"
    url = f"{workspace_url.rstrip('/')}/api/{endpoint}"

    # Parse and merge --params and --data
    merged: dict = {}
    for flag, raw in (("--params", params), ("--data", data)):
        if raw:
            try:
                merged.update(json.loads(raw))
            except json.JSONDecodeError as e:
                print(f"❌ Invalid {flag} JSON: {e}", file=sys.stderr)
                sys.exit(1)

    headers: dict = {"Authorization": f"Bearer {token}"}
    if cookie:
        headers["Cookie"] = f"d={cookie}"

    try:
        with httpx.Client(timeout=60.0) as http:
            if method.upper() == "GET":
                query = {"token": token, **{k: (json.dumps(v) if isinstance(v, (dict, list)) else str(v)) for k, v in merged.items()}}
                resp = http.get(url, headers=headers, params=query)
            else:
                headers["Content-Type"] = "application/x-www-form-urlencoded"
                form = {"token": token, **{k: (json.dumps(v) if isinstance(v, (dict, list)) else str(v)) for k, v in merged.items()}}
                resp = http.post(url, headers=headers, data=form)
            resp.raise_for_status()
            result = resp.json()
    except httpx.HTTPError as e:
        print(f"❌ HTTP error: {e}", file=sys.stderr)
        sys.exit(1)

    if yaml_out:
        import yaml
        print(yaml.dump(result, default_flow_style=False, sort_keys=False, allow_unicode=True), end="")
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))
