---
name: slack-chat
description: communicate via slack-chat channels and direct messages with users
---

# Slack Chat

## Overview

`slack-chat` is a Slack CLI with an offline-first storage model.

It supports two operational modes:
1. Local/offline workflows backed by `storage/` markdown and cache files.
2. Remote/API workflows through the browser-backed server.

This doc is the operational usage reference for agents:
- command invocations
- parameter expectations
- stdout format examples

All examples below are anonymized (`@jdoe`, `#sre-team`, placeholder IDs/URLs).

## Core IDs and Targets

### Event IDs
- Channel message: `CHANNEL_ID:TIMESTAMP`
- Thread message context: `CHANNEL_ID:TIMESTAMP@THREAD_TS`

### Generic target format
The unified target parser (used by `resolve`, `read-message`, `post-message`) accepts:
- User name: `jdoe`
- User name with prefix: `@jdoe`
- User ID: `U01ABCDEF2`
- Channel name: `sre-team`
- Channel name with prefix: `#sre-team`
- Channel ID: `C01ABCDEF2`
- Event ID: `C01ABCDEF2:1709253181.804579`
- Event ID with thread: `C01ABCDEF2:1709253181.804579@1707924824.356449`
- Slack permalink URL: `https://workspace.slack.com/archives/C123/p1771347628831459?thread_ts=1771345654.149809&cid=C123`

### Cache behavior
- User/channel metadata cache location: `storage/_cache/users.yml`, `storage/_cache/channels.yml`
- Resolution is cache-first.
- On cache miss, API fallback is used.
- Successful fallback writes back to cache.

## Command Hierarchy

### Local commands
- `pull`
- `inbox`
  - `summary`
  - `list`
  - `view`
  - `read`
  - `mark-thread`
  - `mark-channel`
  - `unread`
  - `context`
- `mute`
- `reply`
- `react`


### Remote commands
- `server`
  - `status`
  - `start`
  - `stop`
  - `navigate`
  - `reload`
- `search`
- `read-message`
- `resolve`
- `post-message`
- `post-reaction`
- `channel`
  - `describe`
  - `tab`
  - `resolve`
  - `list`
  - `find`
  - `pending`
- `user`
  - `resolve`
  - `list`
  - `find`
  - `status-get`
  - `status-set`

## Command Reference

### `slack-chat pull`
- Purpose: pull unread Slack activity into local storage.
- Usage: `slack-chat pull --since <date> [--limit N] [--type TYPE] [--channel CH] [--quiet]`
- Example:
```bash
slack-chat pull --since "1 day ago" --limit 20 --type threads
```
- Stdout pattern:
```text
Pulling messages since ...
...
Done: <stored> stored, <skipped> skipped, <fetched> fetched total
```

### `slack-chat reply`
- Purpose: reply to channel/thread target.
- Usage: `slack-chat reply <id_or_channel> <text>`
- Accepts channel names/IDs, storage IDs, event IDs.
- Example:
```bash
slack-chat reply #sre-team "Following up"
```
- Stdout pattern:
```yaml
ok: true
channel: C01ABCDEF2
message_ts: '1709253181.804579'
```

### `slack-chat react`
- Purpose: add reaction using storage ID or event ID.
- Usage: `slack-chat react <id_or_event> <emoji>`
- Example:
```bash
slack-chat react C01ABCDEF2:1709253181.804579 eyes
```
- Stdout pattern:
```yaml
ok: true
channel: C01ABCDEF2
timestamp: '1709253181.804579'
emoji: eyes
```

### `slack-chat mute`
- Purpose: mute channel notifications.
- Usage: `slack-chat mute <channel_id>`
- Example:
```bash
slack-chat mute C01ABCDEF2
```
- Stdout pattern:
```yaml
ok: true
channel: C01ABCDEF2
muted: true
```

### `slack-chat search`
- Purpose: search remote messages.
- Usage: `slack-chat search <query> [--count N] [--page N] [--yaml]`
- Example:
```bash
slack-chat search "site reliability support" -n 3 -p 1
```
- Default stdout format (human/agent friendly):
```text
query: site reliability support
pagination: page 1/12 | per_page 3 | total 34
#sre-team (C01ABCDEF2:1709253181.804579@1707924824.356449) Jane Doe (@U01ABCDEF2): <John Doe|@U01ZZZZZZ1> can you take this?
#eng-general (C09QRSTUV1:1709259999.111111) Pat Example (@U01HELLO12): We can support this.
```
- Inline `<@USER...>` references in message bodies are expanded to `<Name|@USER_ID>`.

### `slack-chat read-message`
- Purpose: unified read command (bounded cursor stream + around-mode context).
- Usage:
```bash
slack-chat read-message <target> [--count N] [--before N] [--after N] [--yaml]
```
- Modes:
1. Cursor mode: use `--count`; when target includes `:TIMESTAMP` it resumes strictly after that timestamp.
2. Around mode: use `-B/--before` and/or `-A/--after` with timestamped target.
- Examples:
```bash
slack-chat read-message C01ABCDEF2 -n 20
slack-chat read-message "C01ABCDEF2:1709253181.804579@1707924824.356449" -n 5
slack-chat read-message "C01ABCDEF2:1709253181.804579@1707924824.356449" -B 2 -A 2
```
- Default stdout format:
```text
target: target_type=message_event, channel_id=C01ABCDEF2, channel_id_prefix_type=channel_public, channel_name=sre-team, timestamp=1709253181.804579, thread_ts=1707924824.356449, event_id=C01ABCDEF2:1709253181.804579@1707924824.356449
pagination: per_page 5 | returned 5 | has_more true
#sre-team (C01ABCDEF2:1709253181.804579@1707924824.356449) Jane Doe (@U01ABCDEF2): Thanks.
```
- Around-mode stdout shows `context: before X | after Y | returned Z` and marks the focal line with `[target]`.

### `slack-chat resolve`
- Purpose: resolve any supported target into parsed components and names.
- Usage: `slack-chat resolve <target>`
- Examples:
```bash
slack-chat resolve @jdoe
slack-chat resolve #sre-team
slack-chat resolve U01ABCDEF2
slack-chat resolve "C01ABCDEF2:1709253181.804579@1707924824.356449"
```
- Stdout pattern:
```text
input: @jdoe
target_type: name
resolved_kind: user
resolved_name: Jane Doe
resolved_id: U01ABCDEF2
id_prefix_type: user
```
- Colorized output is enabled on TTY unless `NO_COLOR=1`.

### `slack-chat post-message`
- Purpose: unified post command (channel post or thread reply from target context).
- Usage: `slack-chat post-message <target> <text>`
- Examples:
```bash
slack-chat post-message #sre-team "Heads up: deploy starting"
slack-chat post-message "C01ABCDEF2:1709253181.804579@1707924824.356449" "Thanks for the context"
```
- Stdout pattern:
```text
target: target_type=channel, channel_id=C01ABCDEF2, channel_id_prefix_type=channel_public, channel_name=sre-team
ok: true
channel: C01ABCDEF2
...
```

### `slack-chat post-reaction`
- Purpose: add reaction to a specific message timestamp.
- Usage: `slack-chat post-reaction <channel> <timestamp> <emoji_name>`
- Example:
```bash
slack-chat post-reaction C01ABCDEF2 1709253181.804579 thumbsup
```
- Stdout pattern:
```yaml
ok: true
channel: C01ABCDEF2
timestamp: '1709253181.804579'
emoji: thumbsup
```

## `server` Subcommands

### `slack-chat server status`
- Purpose: show server/token health.
- Example: `slack-chat server status`

### `slack-chat server start`
- Purpose: start browser server.
- Usage: `slack-chat server start [--background|-b]`

### `slack-chat server stop`
- Purpose: stop browser server.

### `slack-chat server navigate`
- Purpose: navigate managed browser.
- Usage: `slack-chat server navigate <url>`

### `slack-chat server reload`
- Purpose: reload watch/config without restart.

## `inbox` Subcommands

### `slack-chat inbox summary`
- Purpose: show unread/read totals.
- Usage: `slack-chat inbox summary [--online]`

### `slack-chat inbox list`
- Purpose: list inbox items.
- Usage:
```bash
slack-chat inbox list [--type TYPE] [--limit N] [--since DATE] [--all] [--online] [--thread-cursor CURSOR]
```

### `slack-chat inbox view`
- Purpose: view one stored/online message.
- Usage: `slack-chat inbox view <id_or_event> [--online]`

### `slack-chat inbox read`
- Purpose: mark one message read.
- Usage: `slack-chat inbox read <id_or_event> [--offline-only]`

### `slack-chat inbox mark-thread`
- Purpose: mark thread read.
- Usage: `slack-chat inbox mark-thread <id_or_event> [--offline-only]`

### `slack-chat inbox mark-channel`
- Purpose: mark channel read.
- Usage: `slack-chat inbox mark-channel <channel_id> [--offline-only]`

### `slack-chat inbox unread`
- Purpose: mark one message unread (local only).
- Usage: `slack-chat inbox unread <id_or_event>`

### `slack-chat inbox context`
- Purpose: show surrounding context.
- Usage: `slack-chat inbox context <event_id> [--limit N]`

## `channel` Subcommands

### `slack-chat channel describe`
- Purpose: fetch channel metadata.
- Usage: `slack-chat channel describe <channel>`
- Notes:
  - Output includes `channel.tabs_resolved` with stable tab index, tab name, and URLs (when tab is file-backed, e.g. canvas).

### `slack-chat channel tab`
- Purpose: fetch one channel tab body/content via authenticated browser server proxy.
- Usage:
  - `slack-chat channel tab <channel> <tab> [--download] [--navigation-fallback] [--yaml]`
  - `slack-chat channel tab <url> [--navigation-fallback] [--yaml]`
- `tab` selector accepts:
  - 1-based index (for example `1`)
  - tab name (for example `Project Notes`)
- Examples (sanitized):
```bash
# Inspect tabs (index/name/url metadata)
slack-chat channel describe #example-team

# Fetch a canvas tab by name
slack-chat channel tab #example-team "Project Notes" --yaml

# Fetch the same tab by index
slack-chat channel tab #example-team 1 --yaml
```

### `slack-chat channel resolve`
- Purpose: resolve channel ID/name.
- Usage: `slack-chat channel resolve <identifier>`

### `slack-chat channel list`
- Purpose: list cached channels.

### `slack-chat channel find`
- Purpose: find cached channels by keyword.
- Usage: `slack-chat channel find <keyword>`

### `slack-chat channel pending`
- Purpose: check unread/pending channel state in sidebar.
- Usage: `slack-chat channel pending <channel>`

## `user` Subcommands

### `slack-chat user resolve`
- Purpose: resolve user ID/name.
- Usage: `slack-chat user resolve <identifier>`

### `slack-chat user list`
- Purpose: list cached users.

### `slack-chat user find`
- Purpose: find cached users by keyword.
- Usage: `slack-chat user find <keyword>`

### `slack-chat user status-get`
- Purpose: get the current Slack status (emoji, text, expiry) for any user.
- Usage: `slack-chat user status-get <identifier>`
- `<identifier>` may be a user ID (`U...`), username, or `@mention`.
- Example: `slack-chat user status-get aarredondo`

### `slack-chat user status-set`
- Purpose: set your own Slack status.
- Usage: `slack-chat user status-set <text> [--emoji EMOJI] [--minutes N]`
- Pass an empty string to clear the status.
- `--emoji` / `-e`: status emoji (e.g. `:calendar:`)
- `--minutes` / `-m`: expiry in minutes from now; 0 means no expiry
- Examples:
  - `slack-chat user status-set "In a meeting" --emoji :calendar: --minutes 60`
  - `slack-chat user status-set ""` (clears status)

## Agent Guidance

1. Prefer `resolve` before reasoning about ambiguous inputs.
2. For reading conversation context, prefer `read-message` command.
3. Use `--yaml` only when raw payload is required; prefer default compact output for context-window efficiency.
4. For threaded analysis, preserve event IDs in outputs to keep direct referential follow-up possible.
