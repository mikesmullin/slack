import typer
import httpx
import sys
import json
import os
import subprocess
import time
import yaml
import signal
from pathlib import Path
from typing import Optional, List

app = typer.Typer(help="Slack CLI via browser-use")
server_app = typer.Typer(help="Manage the Slack browser server")
client_app = typer.Typer(help="Slack client commands")
inbox_app = typer.Typer(help="Inbox-style unread activity management")

app.add_typer(server_app, name="server")
app.add_typer(client_app, name="client")
app.add_typer(inbox_app, name="inbox")

SERVER_URL = "http://localhost:3002"
WORKSPACE_ROOT = Path(__file__).parent.parent
CHANNELS_FILE = WORKSPACE_ROOT / "storage" / "channels.yaml"
PID_FILE = WORKSPACE_ROOT / "slack-server.pid"
LOG_FILE = WORKSPACE_ROOT / "slack-server.log"

user_cache = {}

def get_client():
    return httpx.Client(timeout=60.0)

def get_user_info(client, user_id):
    if user_id in user_cache:
        return user_cache[user_id]
    
    try:
        response = client.post(f"{SERVER_URL}/api", json={"endpoint": "users.info", "params": {"user": user_id}})
        if response.status_code == 200:
            data = response.json()
            if data.get("ok"):
                user = data.get("user", {})
                profile = user.get("profile", {})
                info = {
                    "real_name": profile.get("real_name") or user.get("real_name") or user_id,
                    "display_name": profile.get("display_name") or user.get("name") or user_id,
                    "email": profile.get("email"),
                }
                user_cache[user_id] = info
                return info
    except Exception:
        pass
    return {"real_name": user_id, "display_name": user_id}

def enrich_messages(client, messages):
    enriched = []
    for msg in messages:
        user_id = msg.get("user")
        if user_id:
            user_info = get_user_info(client, user_id)
            # Transform message for YAML output
            new_msg = {
                "timestamp": msg.get("ts"),
                "user": user_info["real_name"],
                "user_id": user_id,
                "text": msg.get("text", ""),
                "reply_count": msg.get("reply_count", 0),
                "has_thread": msg.get("reply_count", 0) > 0,
                "message_type": msg.get("type", "message")
            }
            enriched.append(new_msg)
        else:
            enriched.append(msg)
    return enriched

def handle_response(response):
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

def resolve_channel(channel_name_or_id: str):
    import re
    if re.match(r'^[CDG][A-Z0-9]{8,}$', channel_name_or_id):
        return {"id": channel_name_or_id, "name": channel_name_or_id}
    
    name = channel_name_or_id.lstrip('#')
    
    if CHANNELS_FILE.exists():
        with open(CHANNELS_FILE, 'r') as f:
            channels = yaml.safe_load(f) or []
            for ch in channels:
                if ch.get('name') == name or ch.get('name_normalized') == name:
                    return ch
    
    return {"id": channel_name_or_id, "name": channel_name_or_id}

def get_server_pid():
    if PID_FILE.exists():
        try:
            return int(PID_FILE.read_text().strip())
        except ValueError:
            return None
    return None

@server_app.command("status")
def server_status():
    """Check the status of the Slack browser server."""
    pid = get_server_pid()
    is_running = False
    if pid:
        try:
            os.kill(pid, 0)
            is_running = True
        except OSError:
            pass

    try:
        with get_client() as client:
            response = client.get(f"{SERVER_URL}/status")
            data = response.json()
            
            status = "🟢 running" if is_running else "🔴 stopped"
            url = data.get("url", "N/A")
            has_token = "✅" if data.get("has_token") else "❌"
            
            print(f"Server: {status} (PID {pid})" if pid else f"Server: {status}")
            print(f"URL: {url}")
            print(f"Token: {has_token}")
    except httpx.ConnectError:
        if is_running:
            print(f"Server: 🟡 starting (PID {pid})")
        else:
            print("Server: 🔴 stopped")

@server_app.command("start")
def server_start(background: bool = typer.Option(True, "--background", "-b", help="Run the server in the background")):
    """Start the Slack browser server."""
    # Check if already running
    pid = get_server_pid()
    if pid:
        try:
            os.kill(pid, 0)
            print(f"❌ Server is already running (PID {pid})")
            print("Stop it first with: slack server stop")
            sys.exit(1)
        except OSError:
            # Process not found, remove stale PID file
            PID_FILE.unlink()

    cmd = [sys.executable, "-m", "uvicorn", "src.server:app", "--port", "3002", "--log-level", "info"]
    
    if background:
        print(f"Starting server in background... logs at {LOG_FILE}")
        # Use start_new_session to ensure it keeps running
        with open(LOG_FILE, "a") as f:
            process = subprocess.Popen(cmd, stdout=f, stderr=f, start_new_session=True)
        
        PID_FILE.write_text(str(process.pid))
        
        # Wait for server to be ready (max 5 seconds)
        for _ in range(5):
            try:
                with get_client() as client:
                    resp = client.get(f"{SERVER_URL}/status")
                    if resp.status_code == 200:
                        print("✅ Server started")
                        return
            except httpx.ConnectError:
                time.sleep(1)
        print("⏳ Server starting... check `slack server status`")
    else:
        subprocess.run(cmd)

@server_app.command("stop")
def server_stop():
    """Stop the Slack browser server."""
    pid = get_server_pid()
    if not pid:
        print("Server is not running.")
        return

    try:
        os.kill(pid, signal.SIGTERM)
        print(f"✅ Server stopped (PID {pid})")
        if PID_FILE.exists():
            PID_FILE.unlink()
    except OSError:
        print(f"Could not stop server (PID {pid}). It might have already exited.")
        if PID_FILE.exists():
            PID_FILE.unlink()

@server_app.command("navigate")
def server_navigate(url: str = typer.Argument(..., help="URL to navigate to")):
    """Navigate the browser to a specific URL."""
    try:
        with get_client() as client:
            response = client.post(f"{SERVER_URL}/navigate", json={"url": url})
            handle_response(response)
    except httpx.ConnectError:
        print("Server is not running.")

@client_app.command("post-message")
def post_message(
    channel: str = typer.Argument(..., help="Channel name or ID"),
    text: str = typer.Argument(..., help="Message text"),
    thread_ts: Optional[str] = typer.Option(None, "--thread-ts", help="Thread timestamp to reply to")
):
    """Post a message to a Slack channel."""
    ch = resolve_channel(channel)
    params = {"channel": ch['id'], "text": text}
    if thread_ts:
        params["thread_ts"] = thread_ts
    
    with get_client() as client:
        response = client.post(f"{SERVER_URL}/api", json={"endpoint": "chat.postMessage", "params": params})
        handle_response(response)

@client_app.command("post-thread-reply")
def post_thread_reply(
    channel: str = typer.Argument(..., help="Channel name or ID"),
    thread_ts: str = typer.Argument(..., help="Thread timestamp"),
    text: str = typer.Argument(..., help="Message text")
):
    """Reply to a message thread."""
    ch = resolve_channel(channel)
    params = {"channel": ch['id'], "thread_ts": thread_ts, "text": text}
    with get_client() as client:
        response = client.post(f"{SERVER_URL}/api", json={"endpoint": "chat.postMessage", "params": params})
        handle_response(response)

@client_app.command("add-reaction")
def add_reaction(
    channel: str = typer.Argument(..., help="Channel name or ID"),
    timestamp: str = typer.Argument(..., help="Message timestamp"),
    name: str = typer.Argument(..., help="Reaction name (emoji)")
):
    """Add a reaction to a message."""
    ch = resolve_channel(channel)
    params = {"channel": ch['id'], "timestamp": timestamp, "name": name}
    with get_client() as client:
        response = client.post(f"{SERVER_URL}/api", json={"endpoint": "reactions.add", "params": params})
        handle_response(response)

@client_app.command("get-channel-info")
def get_channel_info(channel: str = typer.Argument(..., help="Channel name or ID")):
    """Get information about a channel."""
    ch = resolve_channel(channel)
    params = {"channel": ch['id']}
    with get_client() as client:
        response = client.post(f"{SERVER_URL}/api", json={"endpoint": "conversations.info", "params": params})
        handle_response(response)

@client_app.command("read-channel-messages")
def read_channel_messages(
    channel: str = typer.Argument(..., help="Channel name or ID"),
    limit: int = typer.Option(10, "--limit", help="Number of messages to read")
):
    """Read recent messages from a channel."""
    ch = resolve_channel(channel)
    params = {"channel": ch['id'], "limit": limit}
    with get_client() as client:
        response = client.post(f"{SERVER_URL}/api", json={"endpoint": "conversations.history", "params": params})
        
        try:
            response.raise_for_status()
            data = response.json()
            
            # Handle case where response is a JSON string (double-serialized)
            if isinstance(data, str):
                import json
                data = json.loads(data)
            
            if data.get("ok"):
                messages = data.get("messages", [])
                messages = enrich_messages(client, messages)
                
                output = {
                    "channel": ch['name'],
                    "channel_id": ch['id'],
                    "message_count": len(messages),
                    "messages": messages
                }
                print(yaml.dump(output, indent=2, sort_keys=False))
            else:
                print(yaml.dump(data, indent=2, sort_keys=False))
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

@client_app.command("read-message-thread-replies")
def read_message_thread_replies(
    channel: str = typer.Argument(..., help="Channel name or ID"),
    thread_ts: str = typer.Argument(..., help="Thread timestamp")
):
    """Read replies in a message thread."""
    ch = resolve_channel(channel)
    params = {"channel": ch['id'], "ts": thread_ts}
    with get_client() as client:
        response = client.post(f"{SERVER_URL}/api", json={"endpoint": "conversations.replies", "params": params})
        handle_response(response)

@client_app.command("search-messages")
def search_messages(query: str = typer.Argument(..., help="Search query")):
    """Search for messages."""
    params = {"query": query}
    with get_client() as client:
        response = client.post(f"{SERVER_URL}/api", json={"endpoint": "search.messages", "params": params})
        handle_response(response)


# ============================================================================
# INBOX COMMANDS - Optimized mailbox-style unread activity management
# Uses undocumented but stable Slack internal APIs for efficiency:
# - users.counts: All channels/DMs with names and unread counts (1 call)
# - subscriptions.thread.getView: Subscribed threads with unreads (1 call)
# - search.messages: Mentions (to:me) and reactions (from:me has:reaction)
# ============================================================================

def call_api(client, endpoint: str, params: dict = None):
    """Helper to call Slack API and return parsed response."""
    if params is None:
        params = {}
    response = client.post(f"{SERVER_URL}/api", json={"endpoint": endpoint, "params": params})
    if response.status_code != 200:
        return {"ok": False, "error": f"HTTP {response.status_code}"}
    data = response.json()
    if isinstance(data, str):
        data = json.loads(data)
    return data


def fetch_unread_counts(client) -> dict:
    """Fetch all unread counts using users.counts (single API call)."""
    data = call_api(client, "users.counts", {})
    if not data.get("ok"):
        print(f"❌ users.counts failed: {data.get('error')}", file=sys.stderr)
        return {"channels": [], "groups": [], "ims": []}
    return data


def fetch_subscribed_threads(client, cursor: str = None) -> dict:
    """Fetch subscribed threads with unread info using subscriptions.thread.getView."""
    params = {}
    if cursor:
        params["cursor"] = cursor
    data = call_api(client, "subscriptions.thread.getView", params)
    if not data.get("ok"):
        print(f"❌ subscriptions.thread.getView failed: {data.get('error')}", file=sys.stderr)
        return {"threads": [], "total_unread_replies": 0, "has_more": False}
    return data


def fetch_mentions(client, limit: int = 20) -> list:
    """Fetch @mentions using search.messages to:me."""
    data = call_api(client, "search.messages", {"query": "to:me", "sort": "timestamp", "count": limit})
    if not data.get("ok"):
        return []
    return data.get("messages", {}).get("matches", [])


def fetch_reactions_to_me(client, limit: int = 20) -> list:
    """Fetch messages I wrote that have reactions using search.messages."""
    data = call_api(client, "search.messages", {"query": "from:me has:reaction", "sort": "timestamp", "count": limit})
    if not data.get("ok"):
        return []
    return data.get("messages", {}).get("matches", [])


def format_event_id(channel_id: str, timestamp: str = None) -> str:
    """Format event ID as channel:timestamp or just channel."""
    if timestamp:
        return f"{channel_id}:{timestamp}"
    return channel_id


def parse_event_id(event_id: str) -> tuple:
    """Parse event ID into (channel_id, timestamp)."""
    if ":" in event_id:
        parts = event_id.split(":", 1)
        return parts[0], parts[1]
    return event_id, None


@inbox_app.command("summary")
def inbox_summary():
    """Quick summary of unread counts (single API call)."""
    with get_client() as client:
        counts = fetch_unread_counts(client)
        threads = fetch_subscribed_threads(client)
        
        channels = counts.get("channels", [])
        groups = counts.get("groups", [])
        ims = counts.get("ims", [])
        
        channels_with_unreads = [c for c in channels if c.get("unread_count_display", 0) > 0]
        groups_with_unreads = [g for g in groups if g.get("unread_count_display", 0) > 0]
        ims_with_unreads = [i for i in ims if i.get("dm_count", 0) > 0]
        
        output = {
            "summary": {
                "channels_with_unreads": len(channels_with_unreads),
                "private_channels_with_unreads": len(groups_with_unreads),
                "dms_with_unreads": len(ims_with_unreads),
                "threads_with_unreads": threads.get("total_unread_replies", 0),
                "total_channels": len(channels),
                "total_dms": len(ims),
            }
        }
        print(yaml.dump(output, indent=2, sort_keys=False, default_flow_style=False))


@inbox_app.command("list")
def inbox_list(
    type_filter: Optional[str] = typer.Option(None, "--type", "-t", help="Filter: channels, dms, threads, mentions, reactions, all"),
    limit: int = typer.Option(20, "--limit", "-n", help="Maximum items per category"),
    thread_cursor: Optional[str] = typer.Option(None, "--thread-cursor", help="Pagination cursor for threads")
):
    """List all unread activity (channels, DMs, threads, mentions, reactions)."""
    with get_client() as client:
        events = []
        thread_pagination = None
        
        # Channels and DMs (from users.counts - 1 API call)
        if not type_filter or type_filter in ["all", "channels", "dms"]:
            counts = fetch_unread_counts(client)
            
            # Channels with unreads
            if not type_filter or type_filter in ["all", "channels"]:
                channels = counts.get("channels", []) + counts.get("groups", [])
                for ch in channels:
                    unread = ch.get("unread_count_display", 0)
                    if unread > 0:
                        events.append({
                            "id": ch.get("id"),
                            "type": "channel",
                            "name": f"#{ch.get('name', ch.get('id'))}",
                            "unread_count": unread,
                            "mention_count": ch.get("mention_count_display", 0),
                        })
            
            # DMs with unreads
            if not type_filter or type_filter in ["all", "dms"]:
                ims = counts.get("ims", [])
                for im in ims:
                    dm_count = im.get("dm_count", 0)
                    if dm_count > 0:
                        events.append({
                            "id": im.get("id"),
                            "type": "dm",
                            "name": f"@{im.get('name', im.get('user_id', im.get('id')))}",
                            "unread_count": dm_count,
                        })
        
        # Subscribed threads with unreads (from subscriptions.thread.getView - 1 API call)
        if not type_filter or type_filter in ["all", "threads"]:
            threads_data = fetch_subscribed_threads(client, thread_cursor)
            threads = threads_data.get("threads", [])
            
            for t in threads:
                root = t.get("root_msg", {})
                # Check if thread has unread replies
                last_read = root.get("last_read", "0")
                latest_reply = root.get("latest_reply", "0")
                
                # Only show threads with unread replies
                if float(latest_reply) > float(last_read):
                    channel_id = root.get("channel")
                    thread_ts = root.get("thread_ts")
                    events.append({
                        "id": format_event_id(channel_id, thread_ts),
                        "type": "thread",
                        "channel_id": channel_id,
                        "text": root.get("text", "")[:80],
                        "reply_count": root.get("reply_count", 0),
                        "latest_reply": latest_reply,
                        "last_read": last_read,
                    })
            
            # Include pagination info
            if threads_data.get("has_more"):
                thread_pagination = {
                    "has_more": True,
                    "next_cursor": threads_data.get("max_ts"),
                    "hint": f"Use --thread-cursor {threads_data.get('max_ts')} to see more"
                }
        
        # Mentions (from search.messages to:me - 1 API call)
        if not type_filter or type_filter in ["all", "mentions"]:
            mentions = fetch_mentions(client, limit)
            for m in mentions[:limit]:
                channel = m.get("channel", {})
                events.append({
                    "id": format_event_id(channel.get("id"), m.get("ts")),
                    "type": "mention",
                    "channel": f"#{channel.get('name', channel.get('id'))}",
                    "from": m.get("username"),
                    "text": m.get("text", "")[:80],
                    "timestamp": m.get("ts"),
                })
        
        # Reactions to my messages (from search.messages from:me has:reaction - 1 API call)
        if not type_filter or type_filter in ["all", "reactions"]:
            reactions = fetch_reactions_to_me(client, limit)
            for r in reactions[:limit]:
                channel = r.get("channel", {})
                events.append({
                    "id": format_event_id(channel.get("id"), r.get("ts")),
                    "type": "reaction",
                    "channel": f"#{channel.get('name', channel.get('id'))}",
                    "text": r.get("text", "")[:60],
                    "timestamp": r.get("ts"),
                    "permalink": r.get("permalink"),
                })
        
        output = {
            "event_count": len(events),
            "events": events[:limit] if type_filter else events,
        }
        
        if thread_pagination:
            output["thread_pagination"] = thread_pagination
        
        print(yaml.dump(output, indent=2, sort_keys=False, default_flow_style=False))


@inbox_app.command("view")
def inbox_view(event_id: str = typer.Argument(..., help="Event ID (CHANNEL_ID or CHANNEL_ID:TIMESTAMP)")):
    """View details of a specific event."""
    channel_id, timestamp = parse_event_id(event_id)
    
    with get_client() as client:
        if timestamp:
            # Get specific message
            data = call_api(client, "conversations.history", {
                "channel": channel_id,
                "latest": timestamp,
                "inclusive": True,
                "limit": 1
            })
            
            if not data.get("ok"):
                print(f"❌ Error: {data.get('error')}", file=sys.stderr)
                sys.exit(1)
            
            messages = data.get("messages", [])
            if not messages:
                print("❌ Message not found", file=sys.stderr)
                sys.exit(1)
            
            msg = messages[0]
            user_id = msg.get("user")
            user_name = user_id
            if user_id:
                user_info = get_user_info(client, user_id)
                user_name = user_info.get("real_name", user_id)
            
            output = {
                "id": event_id,
                "channel_id": channel_id,
                "timestamp": timestamp,
                "from": user_name,
                "from_id": user_id,
                "text": msg.get("text", ""),
                "thread_ts": msg.get("thread_ts"),
                "reply_count": msg.get("reply_count", 0),
                "reactions": msg.get("reactions", []),
            }
        else:
            # Get channel info
            data = call_api(client, "conversations.info", {"channel": channel_id})
            if not data.get("ok"):
                print(f"❌ Error: {data.get('error')}", file=sys.stderr)
                sys.exit(1)
            
            ch = data.get("channel", {})
            output = {
                "id": channel_id,
                "name": ch.get("name"),
                "type": "dm" if ch.get("is_im") else ("group_dm" if ch.get("is_mpim") else "channel"),
                "unread_count": ch.get("unread_count_display", 0),
                "last_read": ch.get("last_read"),
            }
        
        print(yaml.dump(output, indent=2, sort_keys=False, default_flow_style=False))


@inbox_app.command("read")
def inbox_read(event_id: str = typer.Argument(..., help="Event ID to mark as read (CHANNEL_ID or CHANNEL_ID:TIMESTAMP)")):
    """Mark a channel/message as read (updates Slack's server-side read state)."""
    channel_id, timestamp = parse_event_id(event_id)
    
    with get_client() as client:
        if not timestamp:
            # Get latest message timestamp
            data = call_api(client, "conversations.history", {"channel": channel_id, "limit": 1})
            if data.get("ok"):
                msgs = data.get("messages", [])
                if msgs:
                    timestamp = msgs[0].get("ts")
        
        if not timestamp:
            print("❌ Could not determine timestamp", file=sys.stderr)
            sys.exit(1)
        
        data = call_api(client, "conversations.mark", {"channel": channel_id, "ts": timestamp})
        
        if data.get("ok"):
            print(yaml.dump({
                "ok": True,
                "marked_read": event_id,
                "channel_id": channel_id,
                "timestamp": timestamp
            }, indent=2, sort_keys=False))
        else:
            print(f"❌ Error: {data.get('error')}", file=sys.stderr)
            sys.exit(1)


@inbox_app.command("context")
def inbox_context(
    event_id: str = typer.Argument(..., help="Event ID (CHANNEL_ID:TIMESTAMP)"),
    limit: int = typer.Option(10, "--limit", "-n", help="Number of context messages")
):
    """View surrounding context (thread or preceding channel messages)."""
    channel_id, timestamp = parse_event_id(event_id)
    
    if not timestamp:
        print("❌ Event ID must include timestamp (CHANNEL_ID:TIMESTAMP)", file=sys.stderr)
        sys.exit(1)
    
    with get_client() as client:
        # Get the message to check if it's part of a thread
        msg_data = call_api(client, "conversations.history", {
            "channel": channel_id,
            "latest": timestamp,
            "inclusive": True,
            "limit": 1
        })
        
        thread_ts = None
        if msg_data.get("ok"):
            msgs = msg_data.get("messages", [])
            if msgs:
                thread_ts = msgs[0].get("thread_ts")
        
        messages = []
        context_type = "thread" if thread_ts else "channel"
        
        if thread_ts:
            # Get thread replies
            data = call_api(client, "conversations.replies", {
                "channel": channel_id,
                "ts": thread_ts,
                "limit": limit
            })
            raw_messages = data.get("messages", []) if data.get("ok") else []
        else:
            # Get preceding channel messages
            data = call_api(client, "conversations.history", {
                "channel": channel_id,
                "latest": timestamp,
                "inclusive": True,
                "limit": limit
            })
            raw_messages = list(reversed(data.get("messages", []))) if data.get("ok") else []
        
        for m in raw_messages:
            user_id = m.get("user")
            user_name = get_user_info(client, user_id).get("real_name", user_id) if user_id else None
            messages.append({
                "timestamp": m.get("ts"),
                "from": user_name,
                "text": m.get("text", ""),
                "is_target": m.get("ts") == timestamp,
            })
        
        output = {
            "id": event_id,
            "context_type": context_type,
            "thread_ts": thread_ts,
            "message_count": len(messages),
            "messages": messages
        }
        print(yaml.dump(output, indent=2, sort_keys=False, default_flow_style=False))


if __name__ == "__main__":
    app()
