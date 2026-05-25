"""Activity feed command — mentions, threads, reactions via activity.feed API."""

import os
import re
import sys
from datetime import datetime, timezone
from typing import Optional

import typer

from ..utils import get_client, call_api_direct, load_tokens, format_event_id, format_event_id
from ..utils.resolution import get_user_name_by_id, get_channel_name_by_id

# ── ANSI 24-bit color helpers (same pattern as client.py) ────────────────────

def _supports_color() -> bool:
    if os.getenv("NO_COLOR"):
        return False
    if os.getenv("CLICOLOR_FORCE", "") not in {"", "0"}:
        return True
    if os.getenv("FORCE_COLOR", "") not in {"", "0"}:
        return True
    return sys.stdout.isatty() and os.getenv("TERM", "") != "dumb"


def _c(text: str, r: int, g: int, b: int) -> str:
    if not _supports_color():
        return text
    return f"\033[38;2;{r};{g};{b}m{text}\033[0m"


def _green(t: str)  -> str: return _c(t, 66, 184, 131)
def _indigo(t: str) -> str: return _c(t, 193, 145, 255)
def _yellow(t: str) -> str: return _c(t, 250, 208, 44)
def _muted(t: str)  -> str: return _c(t, 140, 153, 173)
def _white(t: str)  -> str: return _c(t, 218, 224, 232)


# ── Tab → activity types mapping ─────────────────────────────────────────────

_TAB_TYPES = {
    "mentions": (
        "at_user,at_user_group,at_channel,at_everyone,keyword,"
        "list_user_mentioned,unjoined_channel_mention"
    ),
    "threads": "thread_v2",
    "reactions": "message_reaction",
    "all": (
        "thread_v2,message_reaction,internal_channel_invite,list_record_edited,"
        "bot_dm_bundle,at_user,at_user_group,at_channel,at_everyone,keyword,"
        "list_record_assigned,list_user_mentioned,list_todo_notification,"
        "list_approval_request,list_approval_reviewed,unjoined_channel_mention,"
        "external_channel_invite,external_dm_invite"
    ),
}

_TYPE_LABELS = {
    "at_user":                  "mention",
    "at_user_group":            "group-mention",
    "at_channel":               "@channel",
    "at_everyone":              "@everyone",
    "keyword":                  "keyword",
    "list_user_mentioned":      "list-mention",
    "unjoined_channel_mention": "unjoined-mention",
    "thread_v2":                "thread",
    "message_reaction":         "reaction",
    "internal_channel_invite":  "invite",
    "external_channel_invite":  "ext-invite",
    "external_dm_invite":       "ext-dm",
    "bot_dm_bundle":            "bot-dm",
    "list_record_edited":       "list-edit",
    "list_record_assigned":     "list-assign",
    "list_todo_notification":   "list-todo",
    "list_approval_request":    "approval-req",
    "list_approval_reviewed":   "approval-rev",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ts_age(ts_str: Optional[str]) -> str:
    if not ts_str:
        return "?"
    try:
        age = datetime.now(timezone.utc).timestamp() - float(str(ts_str).split(".")[0])
        if age < 3600:
            return f"{int(age / 60)}m ago"
        if age < 86400:
            return f"{int(age / 3600)}h ago"
        return f"{int(age / 86400)}d ago"
    except Exception:
        return "?"


def _extract_parts(item_data: dict) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """Return (channel_id, ts, actor_user_id, thread_ts) for an activity item.
    thread_ts is the root thread ts (only set for thread_v2 items)."""
    t = item_data.get("type", "")
    if t == "thread_v2":
        entry = (
            item_data.get("bundle_info", {})
            .get("payload", {})
            .get("thread_entry", {})
        )
        return entry.get("channel_id"), entry.get("latest_ts"), None, entry.get("thread_ts")
    elif t == "message_reaction":
        msg = item_data.get("message", {})
        reaction = item_data.get("reaction", {})
        return msg.get("channel"), msg.get("ts"), reaction.get("user"), None
    else:
        msg = item_data.get("message", {})
        return msg.get("channel"), msg.get("ts"), msg.get("author_user_id"), None


def _resolve_channel_plain(client, channel_id: str, channel_name: str) -> str:
    """Return plain-text channel display (no ANSI). Mirrors _resolve_channel_display logic."""
    if channel_name and channel_name.startswith("mpdm-"):
        try:
            result = call_api_direct("conversations.info", {"channel": channel_id})
            if result.get("ok"):
                member_ids = result.get("channel", {}).get("members", [])
                parts = []
                for uid in member_ids[:6]:
                    name, _ = get_user_name_by_id(client, uid)
                    display = f"@{name} ({uid})" if name else f"({uid})"
                    parts.append(display)
                if parts:
                    return f"Group DM: ({', '.join(parts)}) ({channel_id})"
        except Exception:
            pass
    name_part = f"#{channel_name}" if channel_name else channel_id
    return f"{name_part} ({channel_id})"


def _resolve_channel_display(client, channel_id: str, channel_name: str) -> str:
    """Return green channel display string.
    For mpdm- group DMs, resolves and lists participant usernames."""
    if channel_name and channel_name.startswith("mpdm-"):
        try:
            result = call_api_direct("conversations.info", {"channel": channel_id})
            if result.get("ok"):
                member_ids = result.get("channel", {}).get("members", [])
                parts = []
                for uid in member_ids[:6]:
                    name, _ = get_user_name_by_id(client, uid)
                    display = f"@{name} ({uid})" if name else f"({uid})"
                    parts.append(display)
                if parts:
                    members_str = ", ".join(parts)
                    return _green(f"Group DM: ({members_str}) ({channel_id})")
        except Exception:
            pass
    name_part = f"#{channel_name}" if channel_name else channel_id
    return _green(f"{name_part} ({channel_id})")


_USER_REF = re.compile(r"<@([UW][A-Z0-9]+)(?:\|([^>]+))?>")


def _clean_slack_text(text: str, client=None) -> str:
    """Clean Slack markup. Resolves user mentions (indigo) if client provided.
    Preserves newlines; collapses only horizontal whitespace within each line."""
    # Decode HTML entities
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    # Channel refs
    text = re.sub(r"<#([A-Z0-9]+)\|([^>]+)>", r"#\2", text)
    # Generic links
    text = re.sub(r"<([^|>]+)\|([^>]+)>", r"\2", text)
    text = re.sub(r"<(https?://[^>]+)>", r"\1", text)

    # User refs — resolve name + show ID in indigo
    def _replace_user(m: re.Match) -> str:
        uid = m.group(1)
        name_hint = m.group(2)
        if client:
            resolved, _ = get_user_name_by_id(client, uid)
            name = resolved or name_hint or uid
        else:
            name = name_hint or uid
        return _indigo(f"@{name} ({uid})")

    text = _USER_REF.sub(_replace_user, text)

    # Preserve newlines; collapse horizontal whitespace per line
    lines = text.split("\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in lines]
    return "\n".join(lines).strip()


def _fetch_message(channel_id: str, ts: str, thread_ts: Optional[str] = None) -> tuple[Optional[str], Optional[str]]:
    """Fetch message body and author user_id.
    Returns (text, user_id). For thread replies uses conversations.replies."""
    try:
        # Thread reply: use conversations.replies with root thread_ts
        if thread_ts and thread_ts != ts:
            result = call_api_direct(
                "conversations.replies",
                {
                    "channel": channel_id,
                    "ts": thread_ts,
                    "latest": ts,
                    "oldest": ts,
                    "inclusive": "true",
                    "limit": "1",
                },
            )
            if result.get("ok"):
                msgs = result.get("messages", [])
                for m in msgs:
                    if m.get("ts") == ts:
                        return m.get("text", ""), m.get("user")

        # Standard channel message
        result = call_api_direct(
            "conversations.history",
            {
                "channel": channel_id,
                "oldest": ts,
                "latest": ts,
                "inclusive": "true",
                "limit": "1",
            },
        )
        if result.get("ok"):
            msgs = result.get("messages", [])
            if msgs and msgs[0].get("ts") == ts:
                return msgs[0].get("text", ""), msgs[0].get("user")
    except Exception:
        pass
    return None, None


# ── Command ───────────────────────────────────────────────────────────────────

def _parse_cutoff_ts(after: str) -> Optional[float]:
    """Parse a cutoff timestamp from an event ID (CHANNEL:TS[@THREAD]) or raw float string."""
    if not after:
        return None
    # Strip channel prefix if present: CHANNEL:TS[@THREAD] → TS[@THREAD]
    if ":" in after:
        after = after.split(":", 1)[1]
    # Strip thread suffix: TS@THREAD → TS
    if "@" in after:
        after = after.split("@", 1)[0]
    try:
        return float(after)
    except ValueError:
        return None


def activity_command(
    tab: str = typer.Option(
        "all",
        "--tab", "-t",
        help="Which tab to show: all, mentions, threads, reactions",
    ),
    limit: int = typer.Option(25, "--limit", "-n", help="Max items to return"),
    after: Optional[str] = typer.Option(
        None, "--after", "-a",
        help="Only show items newer than this event ID or timestamp (e.g. C01ABC:1709253181.804579)",
    ),
    yaml_out: bool = typer.Option(False, "--yaml", help="Output raw YAML payload"),
):
    """Show Slack activity feed (mentions, threads, reactions)."""
    tokens = load_tokens()
    if not tokens.get("token"):
        print(
            "❌ No credentials found. Run `slack-chat server start` first.",
            file=sys.stderr,
        )
        sys.exit(1)

    tab = tab.lower()
    if tab not in _TAB_TYPES:
        print(
            f"❌ Unknown tab '{tab}'. Valid values: all, mentions, threads, reactions",
            file=sys.stderr,
        )
        sys.exit(1)

    params = {
        "limit": str(limit),
        "types": _TAB_TYPES[tab],
        "mode": "priority_reads_and_unreads_v1",
        "archive_only": "false",
        "snooze_only": "false",
        "unread_only": "false",
        "priority_only": "false",
        "is_activity_inbox": "false",
    }

    result = call_api_direct("activity.feed", params)
    if not result.get("ok"):
        print(
            f"❌ activity.feed failed: {result.get('error', 'unknown error')}",
            file=sys.stderr,
        )
        sys.exit(1)

    items = result.get("items", [])

    # Apply --after filter: drop items whose ts <= cutoff
    cutoff = _parse_cutoff_ts(after) if after else None
    if cutoff is not None:
        def _item_ts(item: dict) -> float:
            ch_id, ts_val, _, _ = _extract_parts(item.get("item", {}))
            try:
                return float(ts_val) if ts_val else 0.0
            except (ValueError, TypeError):
                return 0.0
        items = [i for i in items if _item_ts(i) > cutoff]

    if not items:
        print(f"No activity ({tab}).")
        return

    enriched = []
    with get_client() as client:
        for item in items:
            item_data = item.get("item", {})
            item_type = item_data.get("type", "unknown")
            label = _TYPE_LABELS.get(item_type, item_type)
            channel_id, ts, actor_id, thread_ts = _extract_parts(item_data)
            is_unread = bool(item.get("is_unread"))

            # ── Age ───────────────────────────────────────────────────────
            age_str = _ts_age(ts)

            # ── Channel ───────────────────────────────────────────────────
            if channel_id:
                if channel_id.startswith("D"):
                    ch_plain = f"DM {channel_id}"
                else:
                    ch_name, _ = get_channel_name_by_id(client, channel_id)
                    ch_plain = _resolve_channel_plain(client, channel_id, ch_name or "")
            else:
                ch_plain = "?"

            # ── Actor ─────────────────────────────────────────────────────
            actor_plain_parts = []
            emoji_name = None
            if actor_id:
                name, _ = get_user_name_by_id(client, actor_id)
                display_name = f"@{name}" if name else f"@{actor_id}"
                actor_plain_parts.append(f"{display_name} ({actor_id})")

            if item_type == "message_reaction":
                emoji_name = item_data.get("reaction", {}).get("name", "")
                if emoji_name:
                    actor_plain_parts.append(f":{emoji_name}:")

            # ── Fetch message (to get text + fallback actor for threads) ──
            raw_text, msg_user_id = (None, None)
            if channel_id and ts:
                raw_text, msg_user_id = _fetch_message(channel_id, ts, thread_ts=thread_ts)

            # Fill in actor from message when the feed item has none (e.g. thread_v2)
            if not actor_id and msg_user_id:
                actor_id = msg_user_id
                name, _ = get_user_name_by_id(client, actor_id)
                display_name = f"@{name}" if name else f"@{actor_id}"
                actor_plain_parts.append(f"{display_name} ({actor_id})")

            actor_plain = " ".join(actor_plain_parts)

            # ── Event ID ──────────────────────────────────────────────────
            event_id = format_event_id(channel_id, ts, thread_ts) if channel_id and ts else ""

            # ── Clean message text ────────────────────────────────────────
            clean_text = _clean_slack_text(raw_text, client=client) if raw_text else ""

            if yaml_out:
                record: dict = {
                    "type": label,
                    "is_unread": is_unread,
                    "age": age_str,
                    "channel_id": channel_id,
                    "channel_display": ch_plain,
                    "actor_id": actor_id,
                    "actor_display": actor_plain,
                    "event_id": event_id,
                    "text": clean_text,
                }
                if emoji_name:
                    record["emoji"] = emoji_name
                enriched.append(record)
            else:
                # ── Human-readable header ─────────────────────────────────
                unread_tag = " " + _yellow("[unread]") if is_unread else ""
                badge = _yellow(f"[{label}]") + unread_tag
                age_disp = _muted(age_str)
                ch_display = _green(ch_plain) if ch_plain != "?" else _muted("?")
                actor_colored_parts = []
                if actor_id:
                    name, _ = get_user_name_by_id(client, actor_id)
                    display_name = f"@{name}" if name else f"@{actor_id}"
                    actor_colored_parts.append(_indigo(f"{display_name} ({actor_id})"))
                if emoji_name:
                    actor_colored_parts.append(_yellow(f":{emoji_name}:"))
                actor_str = " ".join(actor_colored_parts)
                event_id_str = ("  " + _c(event_id + ":", 100, 200, 240)) if event_id else ""
                print(f"{badge}  {age_disp}  {ch_display}  {actor_str}{event_id_str}")
                if clean_text:
                    for line in clean_text.split("\n"):
                        print(f"  {_white(line)}")

    if yaml_out:
        import yaml
        print(yaml.dump(enriched, default_flow_style=False, sort_keys=False, allow_unicode=True), end="")
