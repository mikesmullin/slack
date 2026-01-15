"""Inbox commands for slack-chat CLI."""

import typer
from typing import Optional

from .inbox_summary import (
    inbox_summary_online,
    inbox_summary_offline,
)
from .inbox_list import (
    inbox_list_online,
    inbox_list_offline,
)
from .inbox_view import (
    inbox_view_online,
    inbox_view_offline,
    inbox_context_online,
)
from .inbox_read import (
    inbox_read,
    inbox_mark_thread,
    inbox_mark_channel,
    inbox_unread_offline,
)

app = typer.Typer(help="Inbox operations")

@app.command("summary")
def summary(
    online: bool = typer.Option(
        False, "--online", help="Use online API instead of local storage"
    ),
):
    """Show counts from local storage (offline) or online."""
    if online:
        inbox_summary_online()
    else:
        inbox_summary_offline()


@app.command("list")
def list_messages(
    type_filter: Optional[str] = typer.Option(
        None, "--type", "-t", help="Filter: channels, dms, threads, mentions, all"
    ),
    limit: int = typer.Option(20, "--limit", "-n", help="Maximum items to show"),
    since: Optional[str] = typer.Option(
        None, "--since", help="Only show messages after this date"
    ),
    show_all: bool = typer.Option(False, "--all", "-a", help="Include read messages"),
    online: bool = typer.Option(
        False, "--online", help="Use online API instead of local storage"
    ),
    thread_cursor: Optional[str] = typer.Option(None, "--thread-cursor", help="Pagination cursor for threads (online only)"),
):
    """List messages from local storage (offline) or online."""
    if online:
        inbox_list_online(type_filter, limit, thread_cursor)
    else:
        inbox_list_offline(type_filter, limit, since, show_all)


@app.command("view")
def view(
    id_or_event: str = typer.Argument(
        ..., help="Storage ID (partial) or event ID (CHANNEL:TS)"
    ),
    online: bool = typer.Option(
        False, "--online", help="Use online API instead of local storage"
    ),
):
    """View a single message."""
    if online:
        inbox_view_online(id_or_event)
    else:
        inbox_view_offline(id_or_event)


@app.command("read")
def read(
    id_or_event: str = typer.Argument(
        ..., help="Storage ID (partial) or event ID (CHANNEL:TS)"
    ),
    offline_only: bool = typer.Option(
        False, "--offline-only", help="Only update local storage, don't mark on Slack"
    ),
):
    """Mark message as read (updates local storage and optionally Slack)."""
    inbox_read(id_or_event, offline_only)


@app.command("mark-thread")
def mark_thread(
    id_or_event: str = typer.Argument(
        ...,
        help="Storage ID (partial) or event ID (CHANNEL:TS@THREAD_TS) of any message in the thread",
    ),
    offline_only: bool = typer.Option(
        False, "--offline-only", help="Only update local storage, don't mark on Slack"
    ),
):
    """Mark all messages in a thread as read."""
    inbox_mark_thread(id_or_event, offline_only)


@app.command("mark-channel")
def mark_channel(
    channel_id: str = typer.Argument(..., help="Channel ID (e.g., C0A7RJWRZPT)"),
    offline_only: bool = typer.Option(
        False, "--offline-only", help="Only update local storage, don't mark on Slack"
    ),
):
    """Mark all messages in a channel as read."""
    inbox_mark_channel(channel_id, offline_only)


@app.command("unread")
def unread(
    id_or_event: str = typer.Argument(
        ..., help="Storage ID (partial) or event ID (CHANNEL:TS)"
    ),
):
    """Mark message as unread (local storage only)."""
    inbox_unread_offline(id_or_event)


@app.command("context")
def context(
    event_id: str = typer.Argument(..., help="Event ID (CHANNEL_ID:TIMESTAMP)"),
    limit: int = typer.Option(10, "--limit", "-n", help="Number of context messages"),
):
    """View surrounding context (thread or preceding channel messages)."""
    inbox_context_online(event_id, limit)
