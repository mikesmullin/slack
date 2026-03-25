# Slack

Uses [browser-use](https://github.com/browser-use/browser-use) (via Chrome DevTools Protocol) to integrate with [Slack](https://slack.com/).

It uses a client-server architecture for persistent browser sessions and automated authentication.

## 🏗️ File Structure

```
src/
├── cli.py                # Typer CLI (server + client commands)
└── server.py             # FastAPI browser server
storage/
└── users.yaml            # User ID to name mapping
pyproject.toml            # Project configuration
```

## 🚀 Quick Start

### Prerequisites

- Python 3.12+
- `uv` package manager

### Installation

Install the tool globally in **editable mode** using `uv`:

```bash
uv tool install --editable .
```

Editable mode means the `slack` command will always use the current source code from your workspace, so changes are reflected immediately without reinstalling.

Now you can use the `slack` command anywhere!

### Usage

#### Server Management

```bash
# Start the server (backgrounds by default, opens browser)
slack server start

# Check status
slack server status

# Stop the server
slack server stop
```

#### Client Commands

All client commands require the server to be running.

```bash
# Read channel messages
slack client read-channel-messages CHANNEL_ID [--limit N]

# Read thread replies
slack client read-message-thread-replies CHANNEL_ID THREAD_TS [--limit N]

# Post a message
slack client post-message CHANNEL_ID "Your message here"

# Post a thread reply
slack client post-thread-reply CHANNEL_ID THREAD_TS "Your reply here"

# Add a reaction
slack client add-reaction CHANNEL_ID TIMESTAMP "emoji_name"

# Get channel info
slack client get-channel-info CHANNEL_ID

# Search messages
slack client search-messages "search query" [--limit N]
```

#### Message URL Helpers

For commands that take an event ID, you can now use either format:

- `CHANNEL_ID:TIMESTAMP` (or `CHANNEL_ID:TIMESTAMP@THREAD_TS`)
- A full Slack permalink URL

```bash
# Convert permalink to reusable parts
slack-chat message parse-url "https://workspace.slack.com/archives/C123/p1771347628831459?thread_ts=1771345654.149809&cid=C123"

# Use permalink directly with message around
slack-chat message around "https://workspace.slack.com/archives/C123/p1771347628831459?thread_ts=1771345654.149809&cid=C123" -B 2 -A 2
```

#### Inbox Commands (Mailbox-Style Unread Management)

The inbox provides a unified view of all unread activity across Slack using optimized internal APIs (3 calls instead of 50+).

```bash
# Quick summary of unread counts
slack inbox summary

# List all unread activity (channels, DMs, threads, mentions, reactions)
slack inbox list [--type channels|dms|threads|mentions|reactions|all] [--limit N]

# Paginate through threads
slack inbox list --type threads --thread-cursor CURSOR

# View details of a specific event
slack inbox view CHANNEL_ID:TIMESTAMP
slack inbox view CHANNEL_ID  # View channel info only

# Mark as read (server-side, updates Slack's read state)
slack inbox read CHANNEL_ID:TIMESTAMP
slack inbox read CHANNEL_ID  # Mark entire channel as read

# View surrounding context (thread or preceding messages)
slack inbox context CHANNEL_ID:TIMESTAMP [--limit N]
```

**Event ID Format**: `CHANNEL_ID:TIMESTAMP` (e.g., `C06SERVER01:1766558024.931179`) or just `CHANNEL_ID`

## 🔧 Development

### Running from Source

Run directly without installing using `uv`:

```bash
# Server commands
uv run slack server start
uv run slack server status
uv run slack server stop

# Client commands
uv run slack client read-channel-messages C06SERVER01 --limit 5

# Inbox commands
uv run slack inbox summary
uv run slack inbox list
```

### Editable Installation (Recommended for Development)

If you want to use the `slack` command globally while developing:

```bash
uv tool install --editable .
```

This creates symlinks, so any code changes are immediately reflected when running the `slack` command.

## 🎯 How It Works

1. **Server Start**: Launches a headed Chromium browser with persistent storage in `.browser_data/`
2. **Authentication**: User logs in manually via the browser (supports any auth method)
3. **Token Capture**: Lazily fetches Slack API token from browser's localStorage when needed
4. **API Proxy**: Client commands are proxied through the browser's fetch context with proper Slack headers
5. **Persistence**: Browser session persists between restarts (cookies, localStorage saved)

## 📝 Notes

- The browser runs in **headed mode** (visible window) - this is intentional for manual login
- First time: navigate to `app.slack.com` and log in with your preferred method
- Session data is stored in `.browser_data/` (gitignored)
- The server must be running for client commands to work
