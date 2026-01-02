---
name: slack
description: remotely orchestrate a browser to interact with Slack workspaces
---

# Slack

Uses [browser-use](https://github.com/browser-use/browser-use) (via Chrome DevTools Protocol) to interact with Slack.
Remotely orchestrate a browser to read messages, post replies, add reactions, and search.

## Instructions

- read `README.md` to understand this project

## QUICK REFERENCE

These are CLI commands you may use.

| Task | Command | Purpose |
|------|---------|---------|
| Start Server | `slack server start` | Start background browser server |
| Stop Server | `slack server stop` | Stop background server |
| Server Status | `slack server status` | Check server status and PID |
| Read Messages | `slack client read-channel-messages CHANNEL [--limit N]` | Read channel messages |
| Read Thread | `slack client read-message-thread-replies CHANNEL TS [--limit N]` | Read thread replies |
| Post Message | `slack client post-message CHANNEL "text"` | Post message to channel |
| Reply to Thread | `slack client post-thread-reply CHANNEL TS "text"` | Reply to a thread |
| Add Reaction | `slack client add-reaction CHANNEL TS "emoji"` | Add emoji reaction |
| Channel Info | `slack client get-channel-info CHANNEL` | Get channel details |
| Search | `slack client search-messages "query" [--limit N]` | Search messages |
| **Inbox Summary** | `slack inbox summary` | Quick unread counts (1 API call) |
| **Inbox List** | `slack inbox list [--type TYPE] [--limit N]` | List unreads (channels/dms/threads/mentions/reactions) |
| **Inbox View** | `slack inbox view ID` | View event details (ID or CHANNEL:TS) |
| **Inbox Read** | `slack inbox read ID` | Mark as read |
| **Inbox Context** | `slack inbox context CHANNEL:TS [--limit N]` | View surrounding context |

## Examples

Sample user prompt:

> let's test the slack integration:
> - start the slack server
> - read the latest message from #how-to-rls channel (C6M7U8DFF)
> - if there's a thread, read the replies
> - stop the server

Sample agent output:

```
GitHub Copilot: Created 4 todos

Starting: *Start slack server* (1/4)

Ran terminal command: slack server start

Stdout:
Starting browser server...
✅ Server started in background (PID 12345)
Logs: slack-server.log

Starting: *Read channel messages* (2/4)

Ran terminal command: slack client read-channel-messages C6M7U8DFF --limit 1

Stdout:
channel: C6M7U8DFF
message_count: 1
messages:
  - timestamp: '1766455618.220779'
    user: U0A4DBTGDPZ
    text: 'Is there a good place to request new features?'
    reply_count: 10
    has_thread: true

Starting: *Read thread replies* (3/4)

Ran terminal command: slack client read-message-thread-replies C6M7U8DFF 1766455618.220779 --limit 3

Stdout:
channel: C6M7U8DFF
thread_ts: '1766455618.220779'
reply_count: 3
replies:
  - timestamp: '1766455700.123456'
    user: U1B2C3D4E
    text: 'You can submit feature requests on GitHub!'
  ...

Starting: *Stop server* (4/4)

Ran terminal command: slack server stop

Stdout:
✅ Server stopped (PID 12345)

Completed: All tasks done!
```

## Notes

- The browser runs in **headed mode** - you'll see the browser window
- First time: manually log in to Slack via the browser
- Session persists in `.browser_data/` between restarts
- Channel IDs look like `C6M7U8DFF` (find them in Slack URLs)
- Thread timestamps look like `1766455618.220779`
- Event IDs are formatted as `CHANNEL_ID:TIMESTAMP` (e.g., `C6M7U8DFF:1766558024.931179`)

## Inbox Commands

The inbox provides a mailbox-style interface for managing unread Slack activity.
Uses optimized internal APIs (3 calls vs 50+).

```bash
# Quick summary of unread counts
slack inbox summary

# List unread events (channels, DMs, threads, mentions, reactions)
slack inbox list
slack inbox list --type channels --limit 10
slack inbox list --type threads --thread-cursor CURSOR

# View a specific event
slack inbox view C6M7U8DFF:1766558024.931179
slack inbox view C6M7U8DFF  # channel info only

# Mark as read (updates Slack's server-side state)
slack inbox read C6M7U8DFF:1766558024.931179
slack inbox read C6M7U8DFF  # mark entire channel read

# View surrounding context
slack inbox context C6M7U8DFF:1766558024.931179 --limit 5
```

## Agent Workflow: Processing Unread Activity

When the user asks to "check Slack", "what's new", or "process my unreads", follow this workflow:

### Step 1: Get Overview
```bash
slack inbox summary
```
This returns counts of unread channels, DMs, threads. If all counts are 0, report "No unread activity" and stop.

### Step 2: Fetch Unread Events
```bash
slack inbox list --limit 20
```
This returns ALL types (channels, DMs, threads, mentions, reactions) in one call.

To focus on specific types:
```bash
slack inbox list --type mentions --limit 10   # Just @mentions
slack inbox list --type channels --limit 10   # Just channels
slack inbox list --type reactions --limit 10  # Reactions to your messages
```

### Step 3: Triage and Present
Parse the YAML output. Present a summary to the user:
- Group by type (channels, mentions, threads, etc.)
- Highlight items with `mention_count > 0` (you were @mentioned)
- Show unread counts per channel

Example summary to present:
> You have 3 channels with unreads (#random: 12, #engineering: 5, #announcements: 2), 
> 2 new @mentions, and 1 reaction to your message.

### Step 4: Drill Down (on user request)
When user wants details on a specific item:
```bash
slack inbox view C6M7U8DFF:1766558024.931179  # View specific message
slack inbox context C6M7U8DFF:1766558024.931179 --limit 5  # See surrounding context
```

For channel-level info:
```bash
slack inbox view C6M7U8DFF  # Just channel ID, no timestamp
```

### Step 5: Mark as Read (on user request)
Only mark as read when user explicitly requests:
```bash
slack inbox read C6M7U8DFF  # Mark entire channel as read
slack inbox read C6M7U8DFF:1766558024.931179  # Mark up to specific message
```

### Step 6: Paginate Threads (if needed)
If `thread_pagination.has_more` is true in the response:
```bash
slack inbox list --type threads --thread-cursor 1766562359.000000
```

### Example Agent Session

User: "Check my Slack"

Agent actions:
1. `slack inbox summary` → 5 channels, 0 DMs, 2 mentions
2. `slack inbox list --limit 15` → Get all events
3. Present summary: "You have unreads in 5 channels. 2 @mentions in #engineering from Alice about the deployment."
4. User: "Show me the mentions"
5. `slack inbox view C5E9GMHHN:1763102922.497229` → Show first mention
6. `slack inbox context C5E9GMHHN:1763102922.497229 --limit 5` → Show thread context
7. User: "Mark engineering as read"
8. `slack inbox read C5E9GMHHN` → Mark channel read
9. `slack inbox summary` → Confirm: now 4 channels with unreads
