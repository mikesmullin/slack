"""Slack CLI - Main entry point."""

import sys
import typer

# Import command modules
from .commands import server, client, inbox, resolve, write

TARGET_EXAMPLES = """
Valid target values:
    User name:                        jdoe
    User name (with @):              @jdoe
    User ID:                         U01DT37Q5LG
    Channel name:                    sre-team
    Channel name (with #):           #sre-team
    Channel ID:                      C05R34P9KAA
    Message event ID:                CTXPNCU3T:1709253181.804579
    Thread event ID:                 CTXPNCU3T:1709253181.804579@1707924824.356449
    Slack permalink URL:             https://workspace.slack.com/archives/C123/p1771347628831459?thread_ts=1771345654.149809&cid=C123
""".strip()


HELP_TEXTS = {
        (): """
slack-chat

Usage:
    slack-chat <command> [options]

LOCAL commands (offline storage-first):
    pull                 Pull unread Slack messages to local storage
    inbox summary        Show unread/read counts
    inbox list           List messages from local storage or online
    inbox view           View one stored message
    inbox read           Mark one message as read
    inbox mark-thread    Mark all messages in a thread as read
    inbox mark-channel   Mark all messages in a channel as read
    inbox unread         Mark one message as unread (local only)
    inbox context        View surrounding context
    mute                 Mute a channel
    reply                Reply to a channel or thread
    react                Add an emoji reaction

REMOTE commands (direct remote/API operations):
    server status        Show server health and token status
    server start         Start browser server
    server stop          Stop browser server
    server navigate      Navigate browser to URL
    server reload        Reload config.yaml
    search               Search messages
    read-message         Read messages, threads, or around-context windows
    resolve              Resolve names/IDs/events/URLs into components
    post-message         Post a message or thread reply
    post-reaction        Add reaction to a message
    channel list         List cached channels
    channel find         Find cached channels
    channel describe     Describe a channel
    channel tab          Fetch a specific channel tab
    channel pending      Check unread/pending state in sidebar
    user list            List cached users
    user find            Find cached users

Examples:
    slack-chat search "site reliability support" -n 5 -p 1
    slack-chat read-message "CTXPNCU3T:1709253181.804579@1707924824.356449" -B 1 -A 2
    slack-chat resolve @jdoe
    slack-chat post-message "C05R34P9KAA:1709253181.804579@1707924824.356449" "Thanks for the context"

Use "slack-chat <command> --help" for command-specific help.
""".strip(),
        ("search",): """
search

Usage:
    slack-chat search <query> [--count N] [--page N] [--yaml]

Description:
    Search Slack messages with paginated results.

Options:
    --count, -n   Results per page (default: 20)
    --page, -p    1-based page index (default: 1)
    --yaml        Print raw YAML payload

Example:
    slack-chat search "site reliability support" -n 10 -p 2
""".strip(),
        ("read-message",): f"""
read-message

Usage:
    slack-chat read-message <target> [--count N] [--before N] [--after N] [--yaml]

Description:
    Unified read command.
    - Default mode: bounded cursor reads using target timestamp as resume marker
    - Around mode: context windows around a message via --before/--after

Options:
    --count, -n   Maximum messages to emit (default: 20)
    --before, -B  Context messages before target timestamp (around mode)
    --after, -A   Context messages after target timestamp (around mode)
    --yaml        Print raw YAML payload

{TARGET_EXAMPLES}

Examples:
    slack-chat read-message C05R34P9KAA -n 20
    slack-chat read-message "CTXPNCU3T:1709253181.804579@1707924824.356449" -n 5
    slack-chat read-message "CTXPNCU3T:1709253181.804579@1707924824.356449" -B 2 -A 3
""".strip(),
        ("resolve",): f"""
resolve

Usage:
    slack-chat resolve <target>

Description:
    Resolve a target into parsed components and resolved names.
    Uses cache first. If API fallback is needed and succeeds, cache is updated.

{TARGET_EXAMPLES}

Examples:
    slack-chat resolve jdoe
    slack-chat resolve #sre-team
    slack-chat resolve U01DT37Q5LG
    slack-chat resolve "CTXPNCU3T:1709253181.804579@1707924824.356449"
""".strip(),
        ("post-message",): f"""
post-message

Usage:
    slack-chat post-message <target> <text>

Description:
    Unified write command.
    - If target is channel name/ID: posts channel message
    - If target has timestamp context: posts thread reply

{TARGET_EXAMPLES}

Examples:
    slack-chat post-message #sre-team "Heads up: deploy starting"
    slack-chat post-message "CTXPNCU3T:1709253181.804579@1707924824.356449" "Thanks for the context"
""".strip(),
        ("post-reaction",): """
post-reaction

Usage:
    slack-chat post-reaction <channel> <timestamp> <emoji_name>

Description:
    Add a reaction to a message.

Example:
    slack-chat post-reaction C05R34P9KAA 1709253181.804579 thumbsup
""".strip(),
        ("server",): """
server

Usage:
    slack-chat server <subcommand>

Subcommands:
    status      Show server status
    start       Start server
    stop        Stop server
    navigate    Navigate browser to URL
    reload      Reload config.yaml

Examples:
    slack-chat server status
    slack-chat server start
""".strip(),
        ("server", "status"): """
server status

Usage:
    slack-chat server status

Description:
    Show browser server status, target URL, and token availability.

Example:
    slack-chat server status
""".strip(),
        ("server", "start"): """
server start

Usage:
    slack-chat server start [--background|-b]

Description:
    Start the browser-backed Slack API server.

Options:
    --background, -b   Run in background mode (default: true)

Examples:
    slack-chat server start
    slack-chat server start --background
""".strip(),
        ("server", "stop"): """
server stop

Usage:
    slack-chat server stop

Description:
    Stop the browser server and clean up related processes.

Example:
    slack-chat server stop
""".strip(),
        ("server", "navigate"): """
server navigate

Usage:
    slack-chat server navigate <url>

Description:
    Navigate the managed browser session to a URL.

Example:
    slack-chat server navigate https://app.slack.com/client
""".strip(),
        ("server", "reload"): """
server reload

Usage:
    slack-chat server reload

Description:
    Reload watch/config settings without restarting the server.

Example:
    slack-chat server reload
""".strip(),
        ("inbox",): """
inbox

Usage:
    slack-chat inbox <subcommand>

Subcommands:
    summary        Show counts (offline or --online)
    list           List messages
    view           View a single message
    read           Mark message read
    mark-thread    Mark thread read
    mark-channel   Mark channel read
    unread         Mark message unread (local)
    context        Show surrounding context
""".strip(),
        ("inbox", "summary"): """
inbox summary

Usage:
    slack-chat inbox summary [--online]

Description:
    Show aggregate unread/read totals.
    Uses local storage by default; use --online for live API counts.

Example:
    slack-chat inbox summary
""".strip(),
        ("inbox", "list"): """
inbox list

Usage:
    slack-chat inbox list [--type TYPE] [--limit N] [--since DATE] [--all] [--online]

Description:
    List inbox items from local storage (default) or online API.

Common filters:
    --type channels|dms|threads|mentions|reactions|all
    --since "7 days ago"
    --all
    --online

Examples:
    slack-chat inbox list
    slack-chat inbox list --type threads --limit 10
    slack-chat inbox list --since "2 days ago" --all
""".strip(),
        ("inbox", "view"): """
inbox view

Usage:
    slack-chat inbox view <id_or_event> [--online]

Description:
    View a single inbox message by short storage ID or event ID.

Examples:
    slack-chat inbox view 4aab08
    slack-chat inbox view C01TECH01:1767815267.099869
""".strip(),
        ("inbox", "read"): """
inbox read

Usage:
    slack-chat inbox read <id_or_event> [--offline-only]

Description:
    Mark one message as read locally and optionally on Slack.

Examples:
    slack-chat inbox read 4aab08
    slack-chat inbox read C01TECH01:1767815267.099869 --offline-only
""".strip(),
        ("inbox", "mark-thread"): """
inbox mark-thread

Usage:
    slack-chat inbox mark-thread <id_or_event> [--offline-only]

Description:
    Mark all stored messages in a thread as read.

Example:
    slack-chat inbox mark-thread CTXPNCU3T:1709253181.804579@1707924824.356449
""".strip(),
        ("inbox", "mark-channel"): """
inbox mark-channel

Usage:
    slack-chat inbox mark-channel <channel_id> [--offline-only]

Description:
    Mark all stored messages in a channel as read.

Example:
    slack-chat inbox mark-channel C05R34P9KAA
""".strip(),
        ("inbox", "unread"): """
inbox unread

Usage:
    slack-chat inbox unread <id_or_event>

Description:
    Mark one stored message as unread (local state only).

Example:
    slack-chat inbox unread 4aab08
""".strip(),
        ("inbox", "context"): """
inbox context

Usage:
    slack-chat inbox context <event_id> [--limit N]

Description:
    Show surrounding context around a message.

Example:
    slack-chat inbox context CTXPNCU3T:1709253181.804579 --limit 10
""".strip(),
        ("channel",): """
channel

Usage:
    slack-chat channel <subcommand>

Subcommands:
    describe   Get channel metadata
    tab        Fetch one tab body/content
    resolve    Resolve channel ID/name
    list       List cached channels
    find       Find cached channels
    pending    Check unread/pending in sidebar
""".strip(),
        ("channel", "describe"): """
channel describe

Usage:
    slack-chat channel describe <channel>

Description:
    Fetch metadata for a channel (topic, purpose, flags).

Examples:
    slack-chat channel describe #sre-team
    slack-chat channel describe C05R34P9KAA
""".strip(),
        ("channel", "tab"): """
channel tab

Usage:
    slack-chat channel tab <channel> <tab> [--download] [--navigation-fallback] [--yaml]
    slack-chat channel tab <url> [--navigation-fallback] [--yaml]

Description:
    Resolve and fetch one channel tab through the authenticated browser server.
    Uses XHR/fetch first with navigation fallback enabled by default.
    <tab> can be a 1-based index, tab name, path, or URL.

Examples:
    slack-chat channel tab #example-team 1
    slack-chat channel tab #example-team "Project Notes"
    slack-chat channel tab #example-team "Folder A/Project Notes"
    slack-chat channel tab C05R34P9KAA "Project Notes" --download
    slack-chat channel tab "https://files.slack.com/files-pri/T123-F123/canvas" --yaml
    slack-chat channel tab #example-team "Project Notes" --navigation-fallback
""".strip(),
        ("channel", "resolve"): """
channel resolve

Usage:
    slack-chat channel resolve <identifier>

Description:
    Resolve channel ID to name, or channel name to ID.

Examples:
    slack-chat channel resolve #sre-team
    slack-chat channel resolve C05R34P9KAA
""".strip(),
        ("channel", "list"): """
channel list

Usage:
    slack-chat channel list

Description:
    List channels currently stored in local cache.

Example:
    slack-chat channel list
""".strip(),
        ("channel", "find"): """
channel find

Usage:
    slack-chat channel find <keyword>

Description:
    Search cached channels by keyword.

Examples:
    slack-chat channel find sre
    slack-chat channel find team-o
""".strip(),
        ("channel", "pending"): """
channel pending

Usage:
    slack-chat channel pending <channel>

Description:
    Check unread/pending state from sidebar/browser context.

Examples:
    slack-chat channel pending #sre-team
    slack-chat channel pending C05R34P9KAA
""".strip(),
        ("user",): """
user

Usage:
    slack-chat user <subcommand>

Subcommands:
    resolve   Resolve user ID/name
    list      List cached users
    find      Find cached users
""".strip(),
        ("user", "resolve"): """
user resolve

Usage:
    slack-chat user resolve <identifier>

Description:
    Resolve user ID to user details, or user name to ID.

Examples:
    slack-chat user resolve @jdoe
    slack-chat user resolve U01DT37Q5LG
""".strip(),
        ("user", "list"): """
user list

Usage:
    slack-chat user list

Description:
    List cached users with summary metadata.

Example:
    slack-chat user list
""".strip(),
        ("user", "find"): """
user find

Usage:
    slack-chat user find <keyword>

Description:
    Search cached users by keyword across user fields.

Examples:
    slack-chat user find jdoe
    slack-chat user find "site reliability"
""".strip(),
        ("pull",): """
pull

Usage:
    slack-chat pull --since <date> [--limit N] [--type TYPE] [--quiet]

Description:
    Pull unread Slack activity into local storage.

Examples:
    slack-chat pull --since "1 day ago" --limit 20
    slack-chat pull --since yesterday --type threads
""".strip(),
        ("reply",): """
reply

Usage:
    slack-chat reply <id_or_channel> <text>

Description:
    Reply to a channel or thread target.

Examples:
    slack-chat reply #sre-team "Following up"
    slack-chat reply CTXPNCU3T:1709253181.804579@1707924824.356449 "Thanks"
""".strip(),
        ("react",): """
react

Usage:
    slack-chat react <id_or_event> <emoji>

Description:
    Add emoji reaction using write workflow command.

Examples:
    slack-chat react 4aab08 thumbsup
    slack-chat react CTXPNCU3T:1709253181.804579@1707924824.356449 eyes
""".strip(),
        ("mute",): """
mute

Usage:
    slack-chat mute <channel_id>

Description:
    Mute a channel to suppress notification pulls.

Example:
    slack-chat mute C05R34P9KAA
""".strip(),
}


HELP_TREE = {
        "server": {
                "status": {},
                "start": {},
                "stop": {},
                "navigate": {},
                "reload": {},
        },
        "inbox": {
                "summary": {},
                "list": {},
                "view": {},
                "read": {},
                "mark-thread": {},
                "mark-channel": {},
                "unread": {},
                "context": {},
        },
        "channel": {
                "describe": {},
            "tab": {},
                "resolve": {},
                "list": {},
                "find": {},
                "pending": {},
        },
        "user": {
                "resolve": {},
                "list": {},
                "find": {},
        },
        "resolve": {},
        "search": {},
        "post-message": {},
        "post-reaction": {},
        "read-message": {},
        "pull": {},
        "reply": {},
        "react": {},
        "mute": {},
}


def _help_topic_from_argv(argv: list[str]) -> tuple[str, ...]:
        """Infer help topic path from argv tokens for custom help rendering."""
        tokens = [t for t in argv if t not in {"--help", "-h"}]
        topic = []
        node = HELP_TREE

        for token in tokens:
                if token.startswith("-"):
                        continue
                if isinstance(node, dict) and token in node:
                        topic.append(token)
                        node = node[token]
                else:
                        break

        return tuple(topic)


def _print_custom_help(argv: list[str]) -> bool:
        """Print custom help output when -h/--help is present."""
        if "--help" not in argv and "-h" not in argv:
                return False

        topic = _help_topic_from_argv(argv)
        text = HELP_TEXTS.get(topic)
        if text is None and topic:
                text = HELP_TEXTS.get(topic[:1])
        if text is None:
                text = HELP_TEXTS[()]

        print(text)
        return True

# Create Typer app used for command dispatch.
typer_app = typer.Typer(help="slack-chat")

# Add subcommands
typer_app.add_typer(server.app, name="server")
typer_app.add_typer(inbox.app, name="inbox")
typer_app.add_typer(resolve.channel_app, name="channel")
typer_app.add_typer(resolve.user_app, name="user")

# Add top-level commands from write module
typer_app.command("pull")(write.pull_command)
typer_app.command("reply")(write.reply_command)
typer_app.command("react")(write.react_command)
typer_app.command("mute")(write.mute_command)
typer_app.command("search")(client.search_messages)
typer_app.command("post-message")(client.post_message)
typer_app.command("post-reaction")(client.add_reaction)
typer_app.command("read-message")(client.read_message)
typer_app.command("resolve")(resolve.resolve_target)

# Move channel metadata lookup to channel namespace.
resolve.channel_app.command("describe")(client.get_channel_info)
resolve.channel_app.command("tab")(client.get_channel_tab)


def main():
    """Main entry point."""
    if _print_custom_help(sys.argv[1:]):
        return
    typer_app()


def app():
    """Console entrypoint referenced by project.scripts."""
    main()


if __name__ == "__main__":
    main()
