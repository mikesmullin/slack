---
name: slack-chat
description: communicate via slack-chat channels and direct messages with users
---

# Slack Chat

## Overview

`slack-chat` is a company-agnostic Slack CLI that calls the Slack web API
**directly over HTTP** using saved session credentials. It is written in Bun
(ES `.mjs` modules); the only dependency is `js-yaml`.

- Day-to-day commands need **no browser** — they read `.tokens.yaml` and call the
  Slack API with `fetch`.
- The browser is used **only** during `auth login`, to capture credentials.
- `listen` streams realtime events directly from the Slack WebSocket (no browser).

All examples below are anonymized (`@jdoe`, `#sre-team`, placeholder IDs/URLs).

## Authentication — `.tokens.yaml`

Commands require a `.tokens.yaml` file in the workspace root, written by
`slack-chat auth login`:

```yaml
token: xoxc-...        # xoxc client token from browser localStorage
cookie: ...            # Slack 'd' session cookie (HttpOnly, captured via the browser tool)
workspace_url: https://org.enterprise.slack.com  # from auth.test
is_enterprise: true
enterprise_id: E0123456
gateway_server: T0123456-1   # cached WebSocket shard (for `listen`)
refreshed_at: 2026-06-21T12:00:00.000Z
```

**Login flow:**
1. `slack-chat auth login` starts the headed `browser` tool and navigates to Slack.
2. You complete SSO in the browser window.
3. It captures the `xoxc` token (localStorage) and the `d` cookie (CDP), validates
   them with `auth.test`, writes `.tokens.yaml`, and closes the browser.

**Checking / refreshing:**
- `slack-chat auth status` prints whether credentials are present and still valid
  (live `auth.test`), the workspace URL, and the enterprise flag.
- If API calls start returning auth errors, run `slack-chat auth login` again.

## The `target` identifier (global message id)

`target` uniquely identifies a single message and is the canonical argument for
`read-message`, `reply`, `react`, and `resolve`.

```
{channel_id}:{timestamp}[@{thread_ts}]
```

Examples:

```
C05R34P9KAA                                        # a whole channel (no message)
C05R34P9KAA:1709253181.804579                      # one top-level message
C05R34P9KAA:1709253181.804579@1707924824.356449    # a reply within a thread
D0AVD0EANE9:1782044100.717799                       # a message in a DM
```

Friendlier inputs are accepted and normalized to a `target`:

| Input | Example |
|---|---|
| Channel name | `#sre-team` / `sre-team` |
| User name / ID | `@jdoe` / `jdoe` / `U01ABCDEF2` |
| Channel / event ID | `C01ABCDEF2`, `C01ABCDEF2:1709253181.804579` |
| Permalink URL | `https://org.slack.com/archives/C01.../p1709253181804579?thread_ts=1707924824.356449` |

## Caching

- User/channel metadata cache: `db/cache/users.yml`, `db/cache/channels.yml`.
- Resolution is cache-first; on a miss, the API result is written back.
- `resolve --refresh` bypasses the cache (e.g. after a rename).

## Command Reference

> All commands print a **compact, line-dense** format by default (one message
> per line, names/IDs inlined) — that's what you normally want. While there is also
> `--yaml` parameter to output machine-readable format. it's a niche option intended
> moreso for deterministic script parsing or fetching obscure metadata; it's too verbose
> for most cases, so don't reach for it unless you're certain you need it.

### `slack-chat read-message`
- Purpose: read a bounded message stream, or an around-context window.
- Usage: `slack-chat read-message <target> [--count N] [--before N] [--after N] [--yaml]`
- Modes:
  1. Cursor stream: `--count`; when target includes `:TIMESTAMP`, resumes strictly after it.
  2. Around mode: `-B/--before` and/or `-A/--after` with a timestamped target.
- Examples:
```bash
slack-chat read-message C01ABCDEF2 -n 20
slack-chat read-message "C01ABCDEF2:1709253181.804579@1707924824.356449" -n 5
slack-chat read-message "C01ABCDEF2:1709253181.804579@1707924824.356449" -B 2 -A 2
```
- Around-mode marks the focal line with `[target]`.

### `slack-chat search`
- Purpose: search remote messages (paginated; inline `<@USER>` expanded).
- Usage: `slack-chat search <query> [--count N] [--page N] [--yaml]`
- Example: `slack-chat search "site reliability support" -n 3 -p 1`

### `slack-chat activity`
- Purpose: activity feed — mentions, thread replies, reactions.
- Usage: `slack-chat activity [--tab TAB] [--limit N] [--after EVENT_ID] [--yaml]`
- `--tab` / `-t`: `all` (default), `mentions`, `threads`, `reactions`
- `--limit` / `-n`: max items (default 25)
- `--after` / `-a`: only items newer than this event id or raw timestamp
- Resume by passing the event id printed at the end of a header line to `--after`.
- Example: `slack-chat activity --tab mentions -n 10`

### `slack-chat resolve`
- Purpose: resolve any target into parsed components and names.
- Usage: `slack-chat resolve <target> [--refresh]`
- Examples:
```bash
slack-chat resolve @jdoe
slack-chat resolve #sre-team
slack-chat resolve U01ABCDEF2
slack-chat resolve "C01ABCDEF2:1709253181.804579@1707924824.356449"
slack-chat resolve C01ABCDEF2 --refresh
```

### `slack-chat post-message`
- Purpose: post a channel message, or reply when the target carries thread context.
- Usage: `slack-chat post-message <target> <text> [--image PATH] [--attachment PATH]`
- `--image` / `-i`, `--attachment` / `-a`: repeatable; `<text>` becomes the upload's initial comment.
- Examples:
```bash
slack-chat post-message #sre-team "Heads up: deploy starting"
slack-chat post-message "C01ABCDEF2:1709253181.804579@1707924824.356449" "Thanks"
slack-chat post-message #sre-team "HTTP 500" --image ./screenshot.png
```

### `slack-chat reply`
- Purpose: reply to a channel or thread.
- Usage: `slack-chat reply <id_or_channel> <text>`
- Example: `slack-chat reply C01ABCDEF2:1709253181.804579 "Following up"`

### `slack-chat react`
- Purpose: add a reaction using an event id.
- Usage: `slack-chat react <id_or_event> <emoji>`
- Example: `slack-chat react C01ABCDEF2:1709253181.804579 eyes`

### `slack-chat post-reaction`
- Purpose: add a reaction to a channel + timestamp.
- Usage: `slack-chat post-reaction <channel> <timestamp> <emoji_name>`
- Example: `slack-chat post-reaction C01ABCDEF2 1709253181.804579 thumbsup`

### `slack-chat api`
- Purpose: call any Slack API endpoint directly with saved credentials.
- Usage: `slack-chat api <endpoint> [--params JSON] [--data JSON] [--method GET|POST] [--yaml]`
- Examples:
```bash
slack-chat api auth.test
slack-chat api users.list --params '{"limit": 10}'
slack-chat api chat.postMessage --data '{"channel":"C01ABCDEF2","text":"Hello"}'
```

### `slack-chat listen`
- Purpose: stream realtime Slack events directly from the WebSocket (no browser).
- Usage: `slack-chat listen [--raw] [--type TYPE,...] [--quiet]`
- `--raw`: print each event as a JSON line
- `--type` / `-t`: comma-separated event types to include (e.g. `message,channel_marked`)
- `--quiet` / `-q`: suppress human-readable output
- Examples:
```bash
slack-chat listen
slack-chat listen --raw
slack-chat listen --type message,channel_marked
```

## `channel` Subcommands

### `slack-chat channel describe`
- Purpose: fetch channel metadata, including `channel.tabs_resolved` (stable tab
  index, name, and URLs for file-backed tabs such as canvases).
- Usage: `slack-chat channel describe <channel>`

### `slack-chat channel tab`
- Purpose: fetch one channel tab body via direct authenticated download.
- Usage:
  - `slack-chat channel tab <channel> <tab> [--download] [--yaml]`
  - `slack-chat channel tab <url> [--yaml]`
- `tab` selector accepts a 1-based index or a tab name.
- Examples:
```bash
slack-chat channel describe #sre-team
slack-chat channel tab #sre-team "Project Notes"
slack-chat channel tab #sre-team 1
```

### `slack-chat channel resolve`
- Usage: `slack-chat channel resolve <identifier>`

### `slack-chat channel list`
- Purpose: list cached channels (offline). Columns: id, name, description.

### `slack-chat channel find`
- Usage: `slack-chat channel find <keyword>`

### `slack-chat channel pending`
- Purpose: check whether a channel has unread messages, via the browser sidebar DOM.
- Usage: `slack-chat channel pending <channel>`
- Requires the `browser` tool running with Slack open (`browser server start`).
- Prints `true` or `false`.

## `user` Subcommands

### `slack-chat user list`
- Purpose: list cached users (offline). Columns: id, name, title, project.

### `slack-chat user find`
- Usage: `slack-chat user find <keyword>`

### `slack-chat user status-get`
- Purpose: get the current Slack status (emoji, text, expiry) for any user.
- Usage: `slack-chat user status-get <identifier>` (ID, username, or `@mention`)

### `slack-chat user status-set`
- Purpose: set your own Slack status. Empty text clears it.
- Usage: `slack-chat user status-set <text> [--emoji EMOJI] [--minutes N]`
- Examples:
```bash
slack-chat user status-set "In a meeting" --emoji :calendar: --minutes 60
slack-chat user status-set ""
```

## `auth` Subcommands

### `slack-chat auth status`
- Purpose: show credential presence + live `auth.test` status, workspace, enterprise.
- Usage: `slack-chat auth status`

### `slack-chat auth login`
- Purpose: capture a fresh session via the `browser` tool (headed SSO), validate,
  write `.tokens.yaml`, and close the browser.
- Usage: `slack-chat auth login`

## Agent Guidance

1. Prefer `resolve` before reasoning about ambiguous inputs.
2. For reading conversation context, prefer `read-message`.
3. Default (compact) output is best for reading; `--yaml` is a rarely-needed
   afterthought for script parsing or obscure metadata.
4. Preserve event IDs (`target`s) in outputs so follow-up commands can reference
   the exact message.
5. Commands work without any browser running, as long as `.tokens.yaml` exists and
   is valid. If a command fails with an auth error, run `slack-chat auth login` to
   renew credentials, then retry.
