"""Direct Slack client API commands."""

import typer
import sys
import yaml
import json

from ..utils import (
    get_client,
    resolve_channel,
    enrich_messages,
    handle_response,
    SERVER_URL,
)

# Create Typer app for client commands
app = typer.Typer(help="Slack client commands")


@app.command("post-message")
def post_message(
    channel: str = typer.Argument(..., help="Channel name or ID"),
    text: str = typer.Argument(..., help="Message text"),
    thread_ts: str = typer.Option(
        None, "--thread-ts", help="Thread timestamp to reply to"
    ),
):
    """Post a message to a Slack channel."""
    ch = resolve_channel(channel)
    params = {"channel": ch["id"], "text": text}
    if thread_ts:
        params["thread_ts"] = thread_ts

    with get_client() as client:
        response = client.post(
            f"{SERVER_URL}/api", json={"endpoint": "chat.postMessage", "params": params}
        )
        handle_response(response)


@app.command("post-thread-reply")
def post_thread_reply(
    channel: str = typer.Argument(..., help="Channel name or ID"),
    thread_ts: str = typer.Argument(..., help="Thread timestamp"),
    text: str = typer.Argument(..., help="Message text"),
):
    """Reply to a message thread."""
    ch = resolve_channel(channel)
    params = {"channel": ch["id"], "thread_ts": thread_ts, "text": text}
    with get_client() as client:
        response = client.post(
            f"{SERVER_URL}/api", json={"endpoint": "chat.postMessage", "params": params}
        )
        handle_response(response)


@app.command("add-reaction")
def add_reaction(
    channel: str = typer.Argument(..., help="Channel name or ID"),
    timestamp: str = typer.Argument(..., help="Message timestamp"),
    name: str = typer.Argument(..., help="Reaction name (emoji)"),
):
    """Add a reaction to a message."""
    ch = resolve_channel(channel)
    params = {"channel": ch["id"], "timestamp": timestamp, "name": name}
    with get_client() as client:
        response = client.post(
            f"{SERVER_URL}/api", json={"endpoint": "reactions.add", "params": params}
        )
        handle_response(response)


@app.command("get-channel-info")
def get_channel_info(channel: str = typer.Argument(..., help="Channel name or ID")):
    """Get information about a channel."""
    ch = resolve_channel(channel)
    params = {"channel": ch["id"]}
    with get_client() as client:
        response = client.post(
            f"{SERVER_URL}/api",
            json={"endpoint": "conversations.info", "params": params},
        )
        handle_response(response)


@app.command("read-channel-messages")
def read_channel_messages(
    channel: str = typer.Argument(..., help="Channel name or ID"),
    limit: int = typer.Option(10, "--limit", help="Number of messages to read"),
):
    """Read recent messages from a channel."""
    ch = resolve_channel(channel)
    params = {"channel": ch["id"], "limit": limit}
    with get_client() as client:
        response = client.post(
            f"{SERVER_URL}/api",
            json={"endpoint": "conversations.history", "params": params},
        )

        try:
            response.raise_for_status()
            data = response.json()

            # Handle double-serialized JSON
            if isinstance(data, str):
                data = json.loads(data)

            if data.get("ok"):
                messages = data.get("messages", [])
                messages = enrich_messages(client, messages)

                output = {
                    "channel": ch["name"],
                    "channel_id": ch["id"],
                    "message_count": len(messages),
                    "messages": messages,
                }
                print(yaml.dump(output, indent=2, sort_keys=False))
            else:
                print(yaml.dump(data, indent=2, sort_keys=False))
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)


@app.command("read-message-thread-replies")
def read_message_thread_replies(
    channel: str = typer.Argument(..., help="Channel name or ID"),
    thread_ts: str = typer.Argument(..., help="Thread timestamp"),
):
    """Read replies in a message thread."""
    ch = resolve_channel(channel)
    params = {"channel": ch["id"], "ts": thread_ts}
    with get_client() as client:
        response = client.post(
            f"{SERVER_URL}/api",
            json={"endpoint": "conversations.replies", "params": params},
        )
        handle_response(response)


@app.command("search-messages")
def search_messages(query: str = typer.Argument(..., help="Search query")):
    """Search for messages."""
    params = {"query": query}
    with get_client() as client:
        response = client.post(
            f"{SERVER_URL}/api", json={"endpoint": "search.messages", "params": params}
        )
        handle_response(response)
