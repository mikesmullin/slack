"""record subcommand — start/stop network recording (HTTP + WebSocket) via CDP."""

import sys
from typing import Optional

import typer

from ..utils import get_client, SERVER_URL

app = typer.Typer(help="Record network traffic (HTTP + WebSocket) via CDP")


@app.command("start")
def record_start(
    file: Optional[str] = typer.Argument(
        None,
        help="Output file path (default: tmp/<unix_timestamp>.jsonl)",
    ),
):
    """Start recording HTTP requests/responses and WebSocket frames to a JSONL file."""
    import httpx

    payload = {}
    if file:
        payload["file"] = file

    try:
        with get_client() as client:
            resp = client.post(f"{SERVER_URL}/record/start", json=payload)
            data = resp.json()
            if resp.status_code == 503:
                print(f"❌ {data.get('detail', 'Browser not running')}", file=sys.stderr)
                sys.exit(1)
            if resp.status_code == 409:
                print(f"❌ {data.get('detail', 'Already recording')}", file=sys.stderr)
                sys.exit(1)
            resp.raise_for_status()
            print("✅ Recording started")
            print(f"   File: {data['file']}")
            print("   Stop with: slack-chat record stop")
    except httpx.ConnectError:
        print("❌ Server not reachable. Start it with: slack-chat server start", file=sys.stderr)
        sys.exit(1)


@app.command("stop")
def record_stop():
    """Stop the active recording and close the output file."""
    import httpx

    try:
        with get_client() as client:
            resp = client.post(f"{SERVER_URL}/record/stop")
            data = resp.json()
            if resp.status_code == 409:
                print(f"❌ {data.get('detail', 'No recording in progress')}", file=sys.stderr)
                sys.exit(1)
            resp.raise_for_status()
            print("✅ Recording stopped")
            print(f"   File:   {data['file']}")
            print(f"   Events: {data['events']}")
    except httpx.ConnectError:
        print("❌ Server not reachable.", file=sys.stderr)
        sys.exit(1)
