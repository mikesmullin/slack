"""Direct Slack client API commands."""

import typer
import sys
import yaml
import json
import httpx
import os
import re

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
            event_id = format_event_id(channel_id, msg_ts, thread_ts)
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
            event_id = format_event_id(channel_id, msg_ts, thread_ts)
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
    print(yaml.dump(data, indent=2, sort_keys=False))


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
