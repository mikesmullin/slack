// Help text. Top-level overview + per-group help. Per-command help lives on the
// command module's `help` export.

export const TOP_HELP = `slack-chat

Usage:
    slack-chat <command> [options]

REMOTE commands (direct Slack HTTP API):
    read-message         Read messages, threads, or around-context windows
    search               Search messages
    activity             Show activity feed — mentions, threads, reactions
    resolve              Resolve names/IDs/events/URLs into components
    post-message         Post a message or thread reply (optionally upload files)
    reply                Reply to a channel or thread
    react                Add an emoji reaction (target or event id)
    post-reaction        Add a reaction to a channel + timestamp
    api                  Call any Slack API endpoint directly
    listen               Stream live events from the Slack WebSocket (no browser)

    channel describe     Describe a channel (metadata, bookmarks, tabs)
    channel tab          Fetch a specific channel tab (e.g. canvas)
    channel resolve      Resolve a channel name/ID
    channel list         List cached channels
    channel find         Find cached channels
    channel pending      Show channels with unread/pending state

    user status-get      Get a user's presence/status
    user status-set      Set your own status text/emoji
    user list            List cached users
    user find            Find cached users

    auth status          Show credential + auth.test status
    auth login           Capture a fresh session via the browser tool

Targets:
    Most message commands accept a <target> — Slack's global message id:
        {channel_id}:{timestamp}[@{thread_ts}]
    or a friendlier form: #channel, @user, a bare ID, or a permalink URL.
    See \`slack-chat resolve --help\`.

Use "slack-chat <command> --help" for command-specific help.`;

const GROUP_HELP = {
  channel: `channel

Usage:
    slack-chat channel <subcommand>

Subcommands:
    describe   Describe a channel (metadata, bookmarks, tabs)
    tab        Fetch a specific channel tab
    resolve    Resolve a channel name/ID
    list       List cached channels
    find       Find cached channels
    pending    Show channels with unread/pending state`,
  user: `user

Usage:
    slack-chat user <subcommand>

Subcommands:
    status-get   Get a user's presence/status
    status-set   Set your own status text/emoji
    list         List cached users
    find         Find cached users`,
  auth: `auth

Usage:
    slack-chat auth <subcommand>

Subcommands:
    status   Show credential + live auth.test status
    login    Capture a fresh session via the browser tool`,
};

export function helpFor(path) {
  return GROUP_HELP[path[0]] || TOP_HELP;
}
