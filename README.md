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

Command usage examples and CLI reference are maintained in `SKILL.md`.

## 🔧 Development

### Running from Source

Run commands directly from source with `uv run slack-chat ...`.
See `SKILL.md` for the up-to-date command examples.

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
