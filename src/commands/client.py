"""Direct Slack client API commands."""

import typer
import sys
import yaml
import json
import httpx
import os
import re
from urllib.parse import urlparse
from typing import Any, Optional

from ..utils import (
    get_client,
    resolve_channel,
    SERVER_URL,
    get_user_name_by_id,
    get_channel_name_by_id,
    format_event_id,
    parse_event_id,
    parse_slack_permalink,
)

# Create Typer app for client commands
app = typer.Typer(help="Slack client commands")


def _supports_color() -> bool:
    """Return True when ANSI color output should be enabled."""
    if os.getenv("NO_COLOR"):
        return False
    if os.getenv("CLICOLOR_FORCE", "") not in {"", "0"}:
        return True
    if os.getenv("FORCE_COLOR", "") not in {"", "0"}:
        return True
    return sys.stdout.isatty() and os.getenv("TERM", "") != "dumb"


def _fg_rgb(text: str, r: int, g: int, b: int) -> str:
    if not _supports_color():
        return text
    return f"\033[38;2;{r};{g};{b}m{text}\033[0m"


def _parse_response_data(response: httpx.Response) -> dict:
    """Parse API response, handling historical double-serialized payloads."""
    data = response.json()
    if isinstance(data, str):
        data = json.loads(data)
    return data


def _post_api(endpoint: str, params: dict) -> dict:
    """Call API endpoint and return parsed payload with concise errors."""
    try:
        with get_client() as client:
            response = client.post(
                f"{SERVER_URL}/api", json={"endpoint": endpoint, "params": params}
            )
            response.raise_for_status()
            return _parse_response_data(response)
    except httpx.ConnectError:
        print(
            "Error: cannot reach slack-chat server on localhost:3002. Start it with `slack-chat server start`.",
            file=sys.stderr,
        )
        sys.exit(1)
    except httpx.HTTPStatusError as e:
        print(f"Error: {e.response.text}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def _clean_text(text: str) -> str:
    return (text or "").replace("\n", " ").strip()


def _normalize_slack_ts(ts: str | None) -> str | None:
    """Normalize Slack timestamps to float-string form seconds.micros.

    Accepts compact numeric forms like 1776465637929309 and converts them to
    1776465637.929309.
    """
    if not ts:
        return ts

    value = str(ts).strip()
    if not value:
        return None
    if "." in value:
        return value
    if value.isdigit() and len(value) > 10:
        return f"{value[:-6]}.{value[-6:]}"
    return value


def _display_user(client: httpx.Client, message: dict) -> str:
    user_id = message.get("user")
    if user_id:
        name, _ = get_user_name_by_id(client, user_id)
        return f"{name or user_id} (@{user_id})"
    return message.get("username") or message.get("bot_id") or "unknown"


def _format_inline_user_ref(client: httpx.Client, user_id: str, user_cache: dict) -> str:
    """Format an inline message-body user reference as <Name|@USER_ID>."""
    if user_id not in user_cache:
        name, _ = get_user_name_by_id(client, user_id)
        user_cache[user_id] = name or user_id

    name = user_cache[user_id]
    ref_plain = f"<{name}|@{user_id}>"
    if not _supports_color():
        return ref_plain

    return (
        f"{_fg_rgb('<', 140, 153, 173)}"
        f"{_fg_rgb(name, 255, 199, 95)}"
        f"{_fg_rgb('|', 140, 153, 173)}"
        f"{_fg_rgb('@' + user_id, 127, 219, 202)}"
        f"{_fg_rgb('>', 140, 153, 173)}"
    )


def _format_message_text(client: httpx.Client, text: str, user_cache: dict) -> str:
    """Expand inline Slack user refs and colorize body text consistently."""
    clean_text = _clean_text(text)
    pattern = re.compile(r"<@([UW][A-Z0-9]{8,})(?:\|[^>]+)?>")

    if not pattern.search(clean_text):
        return _fg_rgb(clean_text, 218, 224, 232)

    out = []
    cursor = 0
    for match in pattern.finditer(clean_text):
        if match.start() > cursor:
            out.append(_fg_rgb(clean_text[cursor:match.start()], 218, 224, 232))

        user_id = match.group(1)
        out.append(_format_inline_user_ref(client, user_id, user_cache))
        cursor = match.end()

    if cursor < len(clean_text):
        out.append(_fg_rgb(clean_text[cursor:], 218, 224, 232))

    return "".join(out)


def _search_result_event_id(message: dict) -> str:
    """Build event ID for a search result line."""
    ts = message.get("ts", "")
    channel = message.get("channel", {})
    channel_id = channel.get("id") or message.get("channel_id", "")

    # Search payloads may include thread context only in permalink query params.
    thread_ts = message.get("thread_ts")
    if not thread_ts:
        parsed = parse_slack_permalink(message.get("permalink", ""))
        if parsed:
            thread_ts = parsed.get("thread_ts")

    if channel_id and ts:
        return format_event_id(channel_id, ts, thread_ts)
    return ts or "unknown"


def _search_result_channel_display(client: httpx.Client, message: dict, event_id: str) -> str:
    """Build '#channel-name (CHANNEL:TS[@THREAD_TS])' for search result lines."""
    channel = message.get("channel", {})
    channel_id = channel.get("id") or message.get("channel_id", "")

    channel_name = channel.get("name")
    if channel_id:
        resolved_name, _ = get_channel_name_by_id(client, channel_id)
        if resolved_name:
            channel_name = resolved_name

    if channel_name:
        return f"#{channel_name} ({event_id})"
    if channel_id:
        return f"#{channel_id} ({event_id})"
    return event_id


def _print_search_text(query: str, data: dict):
    messages_obj = data.get("messages", {})
    matches = messages_obj.get("matches", [])
    paging = messages_obj.get("paging", {})
    total = paging.get("total", len(matches))
    page = paging.get("page")
    pages = paging.get("pages")
    per_page = paging.get("count", len(matches))

    print(f"{_fg_rgb('query', 250, 208, 44)}: {_fg_rgb(query, 214, 224, 255)}")
    if page and pages:
        print(
            f"{_fg_rgb('pagination', 180, 190, 203)}: "
            f"{_fg_rgb('page', 140, 153, 173)} {page}/{pages} "
            f"{_fg_rgb('|', 90, 98, 110)} "
            f"{_fg_rgb('per_page', 140, 153, 173)} {per_page} "
            f"{_fg_rgb('|', 90, 98, 110)} "
            f"{_fg_rgb('total', 140, 153, 173)} {total}"
        )
    else:
        print(
            f"{_fg_rgb('results', 180, 190, 203)}: {len(matches)} shown "
            f"{_fg_rgb('|', 90, 98, 110)} "
            f"{_fg_rgb('total', 140, 153, 173)} {total}"
        )

    with get_client() as client:
        inline_user_cache = {}
        for msg in matches:
            event_id = _search_result_event_id(msg)
            channel_display = _search_result_channel_display(client, msg, event_id)
            who = _display_user(client, msg)
            text = _format_message_text(client, msg.get("text", ""), inline_user_cache)
            print(
                f"{_fg_rgb(channel_display, 66, 184, 131)} "
                f"{_fg_rgb(who, 193, 145, 255)}"
                f"{_fg_rgb(':', 140, 153, 173)} "
                f"{text}"
            )


def _fetch_page(endpoint: str, base_params: dict, count: int, page: int) -> tuple[dict, int]:
    """Fetch a cursor-paginated endpoint at a 1-based page index."""
    page = max(1, page)
    cursor = None
    current_page = 1

    while True:
        params = {**base_params, "limit": count}
        if cursor:
            params["cursor"] = cursor

        data = _post_api(endpoint, params)
        if not data.get("ok"):
            return data, current_page

        if current_page >= page:
            return data, current_page

        cursor = data.get("response_metadata", {}).get("next_cursor", "")
        if not cursor:
            data["messages"] = []
            data["has_more"] = False
            return data, current_page

        current_page += 1


def _fetch_channel_from_timestamp(
    channel_id: str,
    timestamp: str,
    count: int,
    page: int,
) -> tuple[dict, int]:
    """Fetch channel messages starting near the provided timestamp.

    Returns messages in chronological order (oldest -> newest) beginning from
    the nearest message at/after `timestamp`.
    """
    page = max(1, page)
    ts = _normalize_slack_ts(timestamp) or timestamp
    limit = max(200, count)

    all_messages: list[dict] = []
    seen_ts: set[str] = set()
    cursor = None
    target_needed = page * count
    upstream_has_more = False

    while True:
        params = {
            "channel": channel_id,
            "oldest": ts,
            "inclusive": True,
            "limit": limit,
        }
        if cursor:
            params["cursor"] = cursor

        data = _post_api("conversations.history", params)
        if not data.get("ok"):
            return data, page

        for msg in data.get("messages", []):
            msg_ts = str(msg.get("ts") or "")
            if not msg_ts or msg_ts in seen_ts:
                continue
            seen_ts.add(msg_ts)
            all_messages.append(msg)

        cursor = data.get("response_metadata", {}).get("next_cursor", "")
        upstream_has_more = bool(data.get("has_more") or cursor)

        # We need enough rows to satisfy requested page from oldest-first view.
        if len(all_messages) >= target_needed or not cursor:
            break

    all_messages.sort(key=lambda m: float(str(m.get("ts") or "0") or "0"))
    start = (page - 1) * count
    end = start + count
    page_messages = all_messages[start:end]

    has_more = bool(end < len(all_messages) or upstream_has_more)
    return {
        "ok": True,
        "messages": page_messages,
        "has_more": has_more,
        "response_metadata": {
            "next_cursor": cursor or "",
        },
    }, page


def _print_read_text(
    target_summary: str,
    channel_name: str,
    channel_id: str,
    thread_ts: str | None,
    count: int,
    page: int,
    data: dict,
):
    """Print read output using the same style as search output."""
    messages = data.get("messages", [])
    has_more = bool(data.get("has_more", False))

    total_estimate = None
    if thread_ts and messages:
        parent = messages[0]
        reply_count = int(parent.get("reply_count", max(len(messages) - 1, 0)) or 0)
        total_estimate = reply_count + 1

    print(f"{_fg_rgb('target', 250, 208, 44)}: {_fg_rgb(target_summary, 214, 224, 255)}")
    if total_estimate is not None:
        print(
            f"{_fg_rgb('pagination', 180, 190, 203)}: "
            f"{_fg_rgb('page', 140, 153, 173)} {page} "
            f"{_fg_rgb('|', 90, 98, 110)} "
            f"{_fg_rgb('per_page', 140, 153, 173)} {count} "
            f"{_fg_rgb('|', 90, 98, 110)} "
            f"{_fg_rgb('total_estimate', 140, 153, 173)} {total_estimate} "
            f"{_fg_rgb('|', 90, 98, 110)} "
            f"{_fg_rgb('has_more', 140, 153, 173)} {str(has_more).lower()}"
        )
    else:
        print(
            f"{_fg_rgb('pagination', 180, 190, 203)}: "
            f"{_fg_rgb('page', 140, 153, 173)} {page} "
            f"{_fg_rgb('|', 90, 98, 110)} "
            f"{_fg_rgb('per_page', 140, 153, 173)} {count} "
            f"{_fg_rgb('|', 90, 98, 110)} "
            f"{_fg_rgb('returned', 140, 153, 173)} {len(messages)} "
            f"{_fg_rgb('|', 90, 98, 110)} "
            f"{_fg_rgb('has_more', 140, 153, 173)} {str(has_more).lower()}"
        )

    with get_client() as client:
        inline_user_cache = {}
        for msg in messages:
            msg_ts = msg.get("ts") or msg.get("timestamp", "")
            msg_thread_ts = msg.get("thread_ts") or thread_ts
            if not msg_thread_ts and int(msg.get("reply_count", 0) or 0) > 0:
                msg_thread_ts = msg_ts
            event_id = format_event_id(channel_id, msg_ts, msg_thread_ts)
            channel_display = f"#{channel_name} ({event_id})"
            who = _display_user(client, msg)
            text = _format_message_text(client, msg.get("text", ""), inline_user_cache)
            print(
                f"{_fg_rgb(channel_display, 66, 184, 131)} "
                f"{_fg_rgb(who, 193, 145, 255)}"
                f"{_fg_rgb(':', 140, 153, 173)} "
                f"{text}"
            )


def _build_target_context(target: str) -> dict:
    """Build a resolve-style target context summary for read/post commands."""
    channel_id, timestamp, thread_ts = parse_event_id(target)
    parsed_url = parse_slack_permalink(target)

    if parsed_url and not thread_ts:
        channel_id = parsed_url["channel_id"]
        timestamp = parsed_url["timestamp"]
        thread_ts = parsed_url.get("thread_ts")

    timestamp = _normalize_slack_ts(timestamp)
    thread_ts = _normalize_slack_ts(thread_ts)

    if timestamp and not thread_ts and channel_id:
        data = _post_api(
            "conversations.history",
            {
                "channel": channel_id,
                "oldest": timestamp,
                "latest": timestamp,
                "inclusive": True,
                "limit": 1,
            },
        )
        if data.get("ok"):
            messages = data.get("messages", [])
            if messages:
                msg = messages[0]
                if msg.get("ts") == timestamp:
                    thread_ts = msg.get("thread_ts")
                    if not thread_ts and int(msg.get("reply_count", 0) or 0) > 0:
                        thread_ts = timestamp

    ch = resolve_channel(channel_id)
    resolved_channel_id = ch.get("id", channel_id)
    channel_name = ch.get("name") or resolved_channel_id
    if channel_name == resolved_channel_id:
        with get_client() as client:
            resolved_name, _ = get_channel_name_by_id(client, resolved_channel_id)
            channel_name = resolved_name or channel_name

    prefix_map = {
        "C": "channel_public",
        "G": "channel_private_or_mpim",
        "D": "direct_message",
    }
    channel_prefix_type = prefix_map.get(resolved_channel_id[:1], "unknown")

    target_type = "channel"
    if parsed_url and timestamp:
        target_type = "message_permalink"
    elif timestamp:
        target_type = "message_event"

    target_components = [
        f"target_type={target_type}",
        f"channel_id={resolved_channel_id}",
        f"channel_id_prefix_type={channel_prefix_type}",
        f"channel_name={channel_name}",
    ]
    if timestamp:
        target_components.append(f"timestamp={timestamp}")
    if thread_ts:
        target_components.append(f"thread_ts={thread_ts}")
    if timestamp:
        target_components.append(
            f"event_id={format_event_id(resolved_channel_id, timestamp, thread_ts)}"
        )

    return {
        "resolved_channel_id": resolved_channel_id,
        "channel_name": channel_name,
        "timestamp": timestamp,
        "thread_ts": thread_ts,
        "target_summary": ", ".join(target_components),
    }


def _fetch_around_messages(
    channel_id: str,
    timestamp: str,
    thread_ts: str | None,
    before: int,
    after: int,
) -> list[dict]:
    """Fetch a message context window around a target message."""
    all_messages: list[dict] = []

    # Thread context around a reply or parent.
    if thread_ts:
        data = _post_api("conversations.replies", {"channel": channel_id, "ts": thread_ts, "limit": 200})
        if not data.get("ok"):
            return []
        thread_messages = data.get("messages", [])

        target_index = next(
            (i for i, m in enumerate(thread_messages) if m.get("ts") == timestamp),
            -1,
        )
        if target_index < 0:
            return []

        start_idx = max(0, target_index - max(0, before))
        end_idx = min(len(thread_messages), target_index + 1 + max(0, after))
        return thread_messages[start_idx:end_idx]

    # Channel context around a message.
    target_data = _post_api(
        "conversations.history",
        {
            "channel": channel_id,
            "oldest": timestamp,
            "inclusive": True,
            "limit": 1,
        },
    )
    target_msgs = target_data.get("messages", []) if target_data.get("ok") else []
    if not target_msgs or target_msgs[0].get("ts") != timestamp:
        return []

    if before > 0:
        before_data = _post_api(
            "conversations.history",
            {
                "channel": channel_id,
                "latest": timestamp,
                "inclusive": False,
                "limit": before,
            },
        )
        if before_data.get("ok"):
            all_messages.extend(list(reversed(before_data.get("messages", []))))

    all_messages.append(target_msgs[0])

    if after > 0:
        try:
            target_ts = float(timestamp)
            latest = str(target_ts + 1000000)
        except ValueError:
            latest = timestamp

        after_data = _post_api(
            "conversations.history",
            {
                "channel": channel_id,
                "latest": latest,
                "oldest": timestamp,
                "inclusive": False,
                "limit": after,
            },
        )
        if after_data.get("ok"):
            all_messages.extend(list(reversed(after_data.get("messages", []))))

    return all_messages


def _print_around_text(
    target_summary: str,
    channel_name: str,
    channel_id: str,
    thread_ts: str | None,
    target_ts: str,
    before: int,
    after: int,
    messages: list[dict],
):
    """Print around-mode output with search/read style formatting."""
    print(f"{_fg_rgb('target', 250, 208, 44)}: {_fg_rgb(target_summary, 214, 224, 255)}")
    print(
        f"{_fg_rgb('context', 180, 190, 203)}: "
        f"{_fg_rgb('before', 140, 153, 173)} {before} "
        f"{_fg_rgb('|', 90, 98, 110)} "
        f"{_fg_rgb('after', 140, 153, 173)} {after} "
        f"{_fg_rgb('|', 90, 98, 110)} "
        f"{_fg_rgb('returned', 140, 153, 173)} {len(messages)}"
    )

    with get_client() as client:
        inline_user_cache = {}
        for msg in messages:
            msg_ts = msg.get("ts", "")
            msg_thread_ts = msg.get("thread_ts") or thread_ts
            if not msg_thread_ts and int(msg.get("reply_count", 0) or 0) > 0:
                msg_thread_ts = msg_ts
            event_id = format_event_id(channel_id, msg_ts, msg_thread_ts)
            prefix = "[target] " if msg_ts == target_ts else ""
            channel_display = f"{prefix}#{channel_name} ({event_id})"
            who = _display_user(client, msg)
            text = _format_message_text(client, msg.get("text", ""), inline_user_cache)
            print(
                f"{_fg_rgb(channel_display, 66, 184, 131)} "
                f"{_fg_rgb(who, 193, 145, 255)}"
                f"{_fg_rgb(':', 140, 153, 173)} "
                f"{text}"
            )


def post_message(
    target: str = typer.Argument(
        ...,
        help="Channel name/ID OR CHANNEL:TIMESTAMP OR CHANNEL:TIMESTAMP@THREAD_TS OR Slack permalink",
    ),
    text: str = typer.Argument(..., help="Message text"),
):
    """Post a channel message, or reply to a thread when target includes thread context."""
    context = _build_target_context(target)
    channel_id = context["resolved_channel_id"]
    timestamp = context["timestamp"]
    thread_ts = context["thread_ts"]

    # If target includes message context, post as a thread reply.
    if timestamp:
        params = {
            "channel": channel_id,
            "text": text,
            "thread_ts": thread_ts or timestamp,
        }
    else:
        params = {"channel": channel_id, "text": text}

    data = _post_api("chat.postMessage", params)
    print(f"{_fg_rgb('target', 250, 208, 44)}: {_fg_rgb(context['target_summary'], 214, 224, 255)}")
    print(yaml.dump(data, indent=2, sort_keys=False))


@app.command("post-reaction")
def add_reaction(
    channel: str = typer.Argument(..., help="Channel name or ID"),
    timestamp: str = typer.Argument(..., help="Message timestamp"),
    name: str = typer.Argument(..., help="Reaction name (emoji)"),
):
    """Add a reaction to a message."""
    ch = resolve_channel(channel)
    params = {"channel": ch["id"], "timestamp": timestamp, "name": name}
    data = _post_api("reactions.add", params)
    print(yaml.dump(data, indent=2, sort_keys=False))


def get_channel_info(channel: str = typer.Argument(..., help="Channel name or ID")):
    """Get information about a channel."""
    ch = resolve_channel(channel)
    params = {"channel": ch["id"]}
    data = _post_api("conversations.info", params)
    if data.get("ok") and data.get("channel"):
        data["channel"]["tabs_resolved"] = _resolve_channel_tabs(data["channel"])
    print(yaml.dump(data, indent=2, sort_keys=False))


def _resolve_channel_tabs(channel_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Build a stable tab list enriched with names and URLs where available."""
    props = channel_data.get("properties", {}) or {}
    channel_id = str(channel_data.get("id") or "")
    meeting_notes_file_id = (props.get("meeting_notes") or {}).get("file_id")

    raw_tabs: list[dict[str, Any]] = []
    for key in ("tabs", "tabz"):
        value = props.get(key)
        if isinstance(value, list):
            raw_tabs.extend([tab for tab in value if isinstance(tab, dict)])

    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for tab in raw_tabs:
        tab_id = str(tab.get("id") or "")
        tab_type = str(tab.get("type") or "")
        label = str(tab.get("label") or "")
        folder_bookmark_id = str((tab.get("data") or {}).get("folder_bookmark_id") or "")
        file_id = str((tab.get("data") or {}).get("file_id") or "")
        if not file_id and tab_type == "channel_canvas" and meeting_notes_file_id:
            file_id = str(meeting_notes_file_id)

        # tabz can contain duplicate rows with empty IDs. Normalize to a stable key
        # so named lookups remain deterministic.
        normalized_id = tab_id or tab_type
        dedupe_key = (normalized_id, tab_type, file_id)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        deduped.append(
            {
                "index": len(deduped) + 1,
                "id": tab_id,
                "type": tab_type,
                "label": label,
                "file_id": file_id,
                "folder_bookmark_id": folder_bookmark_id,
                "folder_path": "",
                "path": label or tab_type or tab_id,
                "name": label or tab_type or tab_id,
                "url": None,
                "download_url": None,
                "permalink": None,
            }
        )

    file_cache: dict[str, dict[str, Any]] = {}

    def _enrich_from_file(tab: dict[str, Any], file_id: str):
        if not file_id:
            return

        if file_id not in file_cache:
            info = _post_api("files.info", {"file": file_id})
            file_cache[file_id] = info if isinstance(info, dict) else {}

        info = file_cache[file_id]
        if not info.get("ok"):
            return

        file_obj = info.get("file", {}) if isinstance(info.get("file", {}), dict) else {}
        title = (file_obj.get("title") or file_obj.get("name") or "").strip()
        if title:
            tab["name"] = title
            if not tab.get("label"):
                tab["label"] = title
            if tab.get("folder_path"):
                tab["path"] = f"{tab['folder_path']}/{title}"
            else:
                tab["path"] = title
        tab["url"] = file_obj.get("url_private")
        tab["download_url"] = file_obj.get("url_private_download")
        tab["permalink"] = file_obj.get("permalink")

    for tab in deduped:
        _enrich_from_file(tab, str(tab.get("file_id") or ""))

    # Expand nested folder items via bookmarks API (folder tabs reference bookmark IDs).
    if channel_id:
        bookmarks_data = _post_api("bookmarks.list", {"channel_id": channel_id})
        bookmarks = bookmarks_data.get("bookmarks", []) if bookmarks_data.get("ok") else []
        children_by_parent: dict[str, list[dict[str, Any]]] = {}
        for bm in bookmarks:
            if not isinstance(bm, dict):
                continue
            parent_id = str(bm.get("parent_id") or "")
            if not parent_id:
                continue
            children_by_parent.setdefault(parent_id, []).append(bm)

        # Stable ordering: Slack rank first, then title.
        for parent_id, items in children_by_parent.items():
            items.sort(key=lambda i: (str(i.get("rank") or ""), str(i.get("title") or "").lower()))

        existing_keys = {
            (
                str(t.get("type") or ""),
                str(t.get("id") or ""),
                str(t.get("file_id") or ""),
                str(t.get("folder_path") or ""),
            )
            for t in deduped
        }

        folder_roots = [
            t for t in deduped
            if str(t.get("type") or "") == "folder" and str(t.get("folder_bookmark_id") or "")
        ]

        def _walk_folder(folder_bookmark_id: str, folder_path: str):
            for bm in children_by_parent.get(folder_bookmark_id, []):
                bm_id = str(bm.get("id") or "")
                bm_type = str(bm.get("type") or "")
                bm_title = str(bm.get("title") or bm.get("entity_id") or bm_id or bm_type)
                child_folder_path = folder_path

                if bm_type == "folder":
                    next_path = f"{folder_path}/{bm_title}" if folder_path else bm_title
                    _walk_folder(bm_id, next_path)
                    continue

                file_id = ""
                entity_id = str(bm.get("entity_id") or "")
                if entity_id.startswith("F"):
                    file_id = entity_id

                entry = {
                    "index": len(deduped) + 1,
                    "id": bm_id,
                    "bookmark_id": bm_id,
                    "type": bm_type or "bookmark",
                    "label": bm_title,
                    "file_id": file_id,
                    "folder_bookmark_id": folder_bookmark_id,
                    "folder_path": child_folder_path,
                    "path": f"{child_folder_path}/{bm_title}" if child_folder_path else bm_title,
                    "name": bm_title,
                    "url": bm.get("link"),
                    "download_url": None,
                    "permalink": bm.get("link"),
                }
                _enrich_from_file(entry, file_id)

                key = (
                    str(entry.get("type") or ""),
                    str(entry.get("id") or ""),
                    str(entry.get("file_id") or ""),
                    str(entry.get("folder_path") or ""),
                )
                if key in existing_keys:
                    continue
                existing_keys.add(key)
                deduped.append(entry)

        for root in folder_roots:
            folder_id = str(root.get("folder_bookmark_id") or "")
            root_name = str(root.get("label") or root.get("name") or "")
            _walk_folder(folder_id, root_name)

    # Re-number indexes after folder expansion.
    for idx, tab in enumerate(deduped, start=1):
        tab["index"] = idx

    return deduped


def _select_channel_tab(tab_selector: str, tabs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Select a tab by 1-based index or by case-insensitive name/id/type match."""
    selector = tab_selector.strip()
    if not selector:
        return None

    if selector.isdigit():
        idx = int(selector)
        if 1 <= idx <= len(tabs):
            return tabs[idx - 1]
        if 0 <= idx < len(tabs):
            return tabs[idx]
        return None

    lowered = selector.lower()
    exact_matches = []
    for tab in tabs:
        candidates = {
            str(tab.get("name") or "").lower(),
            str(tab.get("path") or "").lower(),
            str(tab.get("label") or "").lower(),
            str(tab.get("id") or "").lower(),
            str(tab.get("type") or "").lower(),
            str(tab.get("file_id") or "").lower(),
            str(tab.get("url") or "").lower(),
            str(tab.get("permalink") or "").lower(),
        }
        if lowered in candidates:
            exact_matches.append(tab)

    if len(exact_matches) == 1:
        return exact_matches[0]
    if len(exact_matches) > 1:
        # Prefer tabs we can fetch, then preserve list order for stable behavior.
        fetchable = [t for t in exact_matches if t.get("url") or t.get("download_url")]
        return fetchable[0] if fetchable else exact_matches[0]

    partial_matches = []
    for tab in tabs:
        haystack = " ".join(
            [
                str(tab.get("name") or ""),
                str(tab.get("path") or ""),
                str(tab.get("label") or ""),
                str(tab.get("id") or ""),
                str(tab.get("type") or ""),
                str(tab.get("file_id") or ""),
                str(tab.get("url") or ""),
                str(tab.get("permalink") or ""),
            ]
        ).lower()
        if lowered in haystack:
            partial_matches.append(tab)

    if len(partial_matches) == 1:
        return partial_matches[0]
    if len(partial_matches) > 1:
        fetchable = [t for t in partial_matches if t.get("url") or t.get("download_url")]
        return fetchable[0] if fetchable else partial_matches[0]
    return None


def _looks_like_url(value: str) -> bool:
    try:
        parsed = urlparse((value or "").strip())
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
    except Exception:
        return False


def _fetch_url_via_server(url: str, navigation_fallback: bool) -> dict[str, Any]:
    with get_client() as http_client:
        resp = http_client.post(
            f"{SERVER_URL}/xhr",
            json={
                "url": url,
                "method": "GET",
                "navigation_fallback": navigation_fallback,
            },
        )
        resp.raise_for_status()
        return _parse_response_data(resp)


def get_channel_tab(
    target: str = typer.Argument(..., help="Channel name/ID OR a tab URL"),
    tab: Optional[str] = typer.Argument(None, help="Tab index (1-based) or tab name"),
    download: bool = typer.Option(
        False,
        "--download",
        help="Use download URL when available (for file-backed tabs)",
    ),
    navigation_fallback: bool = typer.Option(
        True,
        "--navigation-fallback",
        help="Allow full-page navigation fallback when fetch() is blocked (can visibly reload Slack UI)",
    ),
    yaml_output: bool = typer.Option(
        False,
        "--yaml",
        help="Print tab metadata and fetched response instead of raw body",
    ),
):
    """Fetch a channel tab body through the authenticated browser server proxy."""
    # URL-only mode: allow `slack-chat channel tab <url>`
    if tab is None and _looks_like_url(target):
        try:
            xhr_data = _fetch_url_via_server(target, navigation_fallback)
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

        if not xhr_data.get("ok"):
            print(
                yaml.dump(
                    {
                        "ok": False,
                        "error": xhr_data.get("error", "xhr_failed"),
                        "url": target,
                    },
                    indent=2,
                    sort_keys=False,
                ),
                file=sys.stderr,
            )
            sys.exit(1)

        if yaml_output:
            print(
                yaml.dump(
                    {
                        "ok": True,
                        "fetch": {
                            "status": xhr_data.get("status"),
                            "status_text": xhr_data.get("status_text"),
                            "url": xhr_data.get("url") or target,
                            "content_type": xhr_data.get("content_type"),
                            "fallback": xhr_data.get("fallback"),
                            "download_path": xhr_data.get("download_path"),
                        },
                        "body": xhr_data.get("body", ""),
                    },
                    indent=2,
                    sort_keys=False,
                )
            )
            return

        print(xhr_data.get("body", ""))
        return

    if tab is None:
        print(
            "Error: missing TAB selector. Use `slack-chat channel tab <channel> <tab>` or `slack-chat channel tab <url>`.",
            file=sys.stderr,
        )
        sys.exit(1)

    ch = resolve_channel(target)
    info = _post_api("conversations.info", {"channel": ch["id"]})
    if not info.get("ok"):
        print(yaml.dump(info, indent=2, sort_keys=False), file=sys.stderr)
        sys.exit(1)

    channel_obj = info.get("channel", {}) if isinstance(info.get("channel", {}), dict) else {}
    tabs = _resolve_channel_tabs(channel_obj)
    if not tabs:
        print("No tabs found for channel.", file=sys.stderr)
        sys.exit(1)

    selected = _select_channel_tab(tab, tabs)
    if not selected:
        listing = [
            {
                "index": t.get("index"),
                "name": t.get("name"),
                "type": t.get("type"),
                "id": t.get("id"),
            }
            for t in tabs
        ]
        print(
            yaml.dump(
                {
                    "ok": False,
                    "error": f"tab_not_found: {tab}",
                    "available_tabs": listing,
                },
                indent=2,
                sort_keys=False,
            ),
            file=sys.stderr,
        )
        sys.exit(1)

    url = selected.get("download_url") if download else selected.get("url")
    if not url:
        print(
            yaml.dump(
                {
                    "ok": False,
                    "error": "tab_has_no_fetchable_url",
                    "tab": selected,
                },
                indent=2,
                sort_keys=False,
            ),
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        xhr_data = _fetch_url_via_server(url, navigation_fallback)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if not xhr_data.get("ok"):
        error_text = xhr_data.get("error", "xhr_failed")
        print(
            yaml.dump(
                {
                    "ok": False,
                    "error": error_text,
                    "tab": selected,
                    "url": url,
                },
                indent=2,
                sort_keys=False,
            ),
            file=sys.stderr,
        )
        sys.exit(1)

    if yaml_output:
        print(
            yaml.dump(
                {
                    "ok": True,
                    "channel": {
                        "id": channel_obj.get("id"),
                        "name": channel_obj.get("name"),
                    },
                    "tab": selected,
                    "fetch": {
                        "status": xhr_data.get("status"),
                        "status_text": xhr_data.get("status_text"),
                        "url": xhr_data.get("url"),
                        "content_type": xhr_data.get("content_type"),
                        "fallback": xhr_data.get("fallback"),
                        "download_path": xhr_data.get("download_path"),
                    },
                    "body": xhr_data.get("body", ""),
                },
                indent=2,
                sort_keys=False,
            )
        )
        return

    print(xhr_data.get("body", ""))


def read_message(
    target: str = typer.Argument(
        ...,
        help="Channel name/ID OR CHANNEL:TIMESTAMP OR CHANNEL:TIMESTAMP@THREAD_TS OR Slack permalink",
    ),
    count: int = typer.Option(20, "--count", "-n", help="Results per page"),
    page: int = typer.Option(1, "--page", "-p", help="Results page number"),
    before: int = typer.Option(None, "--before", "-B", help="Context messages before target timestamp"),
    after: int = typer.Option(None, "--after", "-A", help="Context messages after target timestamp"),
    yaml_output: bool = typer.Option(
        False,
        "--yaml",
        help="Output full YAML payload instead of compact text",
    ),
):
    """Read channel messages, or thread replies when target includes @THREAD_TS."""
    context = _build_target_context(target)
    resolved_channel_id = context["resolved_channel_id"]
    channel_name = context["channel_name"]
    timestamp = context["timestamp"]
    thread_ts = context["thread_ts"]
    target_summary = context["target_summary"]

    # Around mode: merge message around functionality into read-message.
    if before is not None or after is not None:
        if not timestamp:
            print(
                "Error: --before/--after require a message target (CHANNEL:TIMESTAMP[@THREAD_TS] or permalink).",
                file=sys.stderr,
            )
            sys.exit(1)

        before_n = max(0, before or 0)
        after_n = max(0, after or 0)
        around_messages = _fetch_around_messages(
            resolved_channel_id,
            timestamp,
            thread_ts,
            before_n,
            after_n,
        )

        if yaml_output:
            output = {
                "target": target,
                "target_summary": target_summary,
                "context_type": "thread" if thread_ts else "channel",
                "before": before_n,
                "after": after_n,
                "message_count": len(around_messages),
                "messages": around_messages,
            }
            print(yaml.dump(output, indent=2, sort_keys=False))
            return

        _print_around_text(
            target_summary,
            channel_name,
            resolved_channel_id,
            thread_ts,
            timestamp,
            before_n,
            after_n,
            around_messages,
        )
        return

    # Thread mode: explicit thread context present.
    if timestamp and thread_ts:
        data, actual_page = _fetch_page(
            "conversations.replies",
            {"channel": resolved_channel_id, "ts": thread_ts},
            count,
            page,
        )

        if yaml_output:
            print(yaml.dump(data, indent=2, sort_keys=False))
            return

        if not data.get("ok"):
            print(yaml.dump(data, indent=2, sort_keys=False))
            return

        display_data = {**data, "messages": data.get("messages", [])[:count]}
        _print_read_text(
            target_summary,
            channel_name,
            resolved_channel_id,
            thread_ts,
            count,
            actual_page,
            display_data,
        )
        return

    # Channel mode anchored to timestamp without thread context.
    if timestamp and not thread_ts:
        data, actual_page = _fetch_channel_from_timestamp(
            resolved_channel_id,
            timestamp,
            count,
            page,
        )

        if yaml_output:
            output = {
                **data,
                "anchor_timestamp": timestamp,
                "ordering": "chronological",
            }
            print(yaml.dump(output, indent=2, sort_keys=False))
            return

        if not data.get("ok"):
            print(yaml.dump(data, indent=2, sort_keys=False))
            return

        _print_read_text(
            target_summary,
            channel_name,
            resolved_channel_id,
            None,
            count,
            actual_page,
            data,
        )
        return

    # Channel mode: channel name/ID, CHANNEL:TIMESTAMP, or non-thread permalink.
    data, actual_page = _fetch_page(
        "conversations.history",
        {"channel": resolved_channel_id},
        count,
        page,
    )

    if yaml_output:
        print(yaml.dump(data, indent=2, sort_keys=False))
        return

    if not data.get("ok"):
        print(yaml.dump(data, indent=2, sort_keys=False))
        return

    _print_read_text(
        target_summary,
        channel_name,
        resolved_channel_id,
        None,
        count,
        actual_page,
        data,
    )


def search_messages(
    query: str = typer.Argument(..., help="Search query"),
    count: int = typer.Option(20, "--count", "-n", help="Results per page"),
    page: int = typer.Option(1, "--page", "-p", help="Results page number"),
    yaml_output: bool = typer.Option(
        False,
        "--yaml",
        help="Output full YAML payload instead of compact text",
    ),
):
    """Search for messages."""
    params = {"query": query, "count": count, "page": page}
    data = _post_api("search.messages", params)

    if yaml_output:
        print(yaml.dump(data, indent=2, sort_keys=False))
        return

    if not data.get("ok"):
        print(yaml.dump(data, indent=2, sort_keys=False))
        return

    _print_search_text(query, data)
