# 💬 Slack Chat

Slack's UI is a notification firehose built for humans clicking around — not for agents or power users who want to read a thread, search history, or fire off a reply without leaving the terminal.

`slack-chat` talks **directly to the Slack web API** over HTTP using saved session credentials — no browser in the hot path, no server to babysit. It's fast, scriptable, and battle-tested with modern  AI. The browser is launched exactly once, for SSO login, then closed.

## Stack

| Layer | Technology |
|---|---|
| Runtime | [Bun](https://bun.sh) v1.3+ |
| Language | JavaScript (ES modules, `.mjs`) |
| Transport | native `fetch` + `WebSocket` (Slack web API) |
| Data | YAML files ([`js-yaml`](https://github.com/nodeca/js-yaml)) |
| Login | the reusable [`browser`](https://github.com/) tool (headed SSO, one-time) |

## Setup

```bash
bun install
bun link                 # exposes the `slack-chat` command globally
slack-chat auth login    # one-time SSO login (requires the `browser` tool)
slack-chat auth status   # confirm credentials are valid
```

## Usage

```bash
slack-chat read-message "C05R34P9KAA:1709253181.804579" -B 2 -A 2
slack-chat search "site reliability" -n 5
slack-chat activity --tab mentions -n 10
slack-chat post-message "#sre-team" "Heads up: deploy starting"
slack-chat resolve @jdoe
slack-chat listen
```

See [`SKILL.md`](SKILL.md) for the full command reference, parameters, and output formats.

## Caching

User and channel metadata are cached under `db/cache/{users,channels}.yml`.
Lookups are cache-first; on a miss the API result is written back. This keeps the
tool fast and avoids re-fetching IDs Slack won't expand for you. Use `--refresh`
on `resolve` to bypass the cache after a rename.
