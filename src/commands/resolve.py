"""Channel and user resolution commands."""

import typer
import sys
import yaml
import re
import os
from typing import Optional

from .. import storage
from ..utils import (
    get_client,
    get_channel_name_by_id,
    get_user_name_by_id,
    resolve_channel,
    parse_event_id,
    parse_slack_permalink,
    format_event_id,
    truncate_text,
    call_api,
    SERVER_URL,
)

channel_app = typer.Typer(help="Channel operations")
user_app = typer.Typer(help="User operations")


def _supports_color() -> bool:
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


def _color_event_id(value: str) -> str:
    if ":" not in value:
        return _fg_rgb(value, 214, 224, 255)
    channel_id, rest = value.split(":", 1)
    if "@" in rest:
        ts, thread_ts = rest.split("@", 1)
        return (
            f"{_fg_rgb(channel_id, 66, 184, 131)}"
            f"{_fg_rgb(':', 140, 153, 173)}"
            f"{_fg_rgb(ts, 91, 192, 235)}"
            f"{_fg_rgb('@', 140, 153, 173)}"
            f"{_fg_rgb(thread_ts, 255, 159, 67)}"
        )
    return (
        f"{_fg_rgb(channel_id, 66, 184, 131)}"
        f"{_fg_rgb(':', 140, 153, 173)}"
        f"{_fg_rgb(rest, 91, 192, 235)}"
    )


def _color_scalar(key: str, value) -> str:
    if isinstance(value, bool):
        return _fg_rgb(str(value).lower(), 250, 208, 44)
    if value is None:
        return _fg_rgb("null", 140, 153, 173)

    text = str(value)
    if key in {"event_id"}:
        return _color_event_id(text)
    if key.endswith("_id") or key == "id":
        return _fg_rgb(text, 127, 219, 202)
    if key.endswith("_type") or key == "target_type" or key == "resolved_kind":
        return _fg_rgb(text, 255, 199, 95)
    if text.startswith("http://") or text.startswith("https://"):
        return _fg_rgb(text, 120, 180, 255)
    return _fg_rgb(text, 214, 224, 255)


def _print_resolve_output(output: dict):
    """Print resolve output with ANSI truecolor styling for readability."""
    if not _supports_color():
        print(yaml.dump(output, indent=2, sort_keys=False, default_flow_style=False))
        return

    for key, value in output.items():
        key_text = _fg_rgb(str(key), 255, 105, 180)
        value_text = _color_scalar(str(key), value)
        print(f"{key_text}: {value_text}")


def _find_user_by_name(client, name: str) -> dict | None:
    """Find a user by exact name/display_name/real_name (case-insensitive)."""
    cached = storage.find_user_by_name(name)
    if cached:
        return cached

    data = call_api(client, "users.list", {})
    if not data.get("ok"):
        return None

    lowered = name.lower()
    for user in data.get("members", []):
        candidates = [
            user.get("name", ""),
            user.get("real_name", ""),
            user.get("profile", {}).get("display_name", ""),
            user.get("profile", {}).get("real_name", ""),
        ]
        if any(c and c.lower() == lowered for c in candidates):
            user_id = user.get("id")
            if user_id:
                storage.cache_user(user_id, user)
            return user
    return None


def _find_channel_by_name(client, name: str) -> dict | None:
    """Find a channel by exact name/name_normalized (case-insensitive)."""
    cached = storage.find_channel_by_name(name)
    if cached:
        return cached

    data = call_api(client, "conversations.list", {"exclude_archived": True, "limit": 1000})
    if not data.get("ok"):
        return None

    lowered = name.lower()
    for channel in data.get("channels", []):
        candidates = [
            channel.get("name", ""),
            channel.get("name_normalized", ""),
        ]
        if any(c and c.lower() == lowered for c in candidates):
            channel_id = channel.get("id")
            if channel_id:
                storage.cache_channel(channel_id, channel)
            return channel
    return None


def _id_prefix_type(identifier: str) -> str:
    """Infer target type from first ID prefix char."""
    if not identifier:
        return "unknown"
    prefix = identifier[0].upper()
    mapping = {
        "U": "user",
        "W": "workspace_app_user",
        "B": "legacy_bot",
        "C": "channel_public",
        "G": "channel_private_or_mpim",
        "D": "direct_message",
        "T": "workspace",
        "E": "enterprise_or_emoji",
        "A": "slack_app",
        "S": "user_group",
        "F": "file",
    }
    return mapping.get(prefix, "unknown")


def resolve_target(
    target: str = typer.Argument(
        ...,
        help="Name, ID, CHANNEL:TIMESTAMP, CHANNEL:TIMESTAMP@THREAD_TS, or Slack permalink URL",
    ),
):
    """Resolve a generic Slack target into IDs, names, and message components."""
    target = target.strip()
    output = {
        "input": target,
    }

    # URL form first.
    parsed_url = parse_slack_permalink(target)
    if parsed_url:
        channel_id = parsed_url["channel_id"]
        timestamp = parsed_url["timestamp"]
        thread_ts = parsed_url.get("thread_ts")

        output.update(
            {
                "target_type": "message_permalink",
                "permalink": target,
                "channel_id": channel_id,
                "timestamp": timestamp,
                "thread_ts": thread_ts,
                "event_id": format_event_id(channel_id, timestamp, thread_ts),
                "channel_id_prefix_type": _id_prefix_type(channel_id),
            }
        )

        with get_client() as client:
            channel_name, _ = get_channel_name_by_id(client, channel_id)
            output["channel_name"] = channel_name

        _print_resolve_output(output)
        return

    # Event ID form.
    channel_id, timestamp, thread_ts = parse_event_id(target)
    if timestamp:
        output.update(
            {
                "target_type": "message_event",
                "channel_id": channel_id,
                "timestamp": timestamp,
                "thread_ts": thread_ts,
                "event_id": format_event_id(channel_id, timestamp, thread_ts),
                "channel_id_prefix_type": _id_prefix_type(channel_id),
            }
        )

        with get_client() as client:
            channel_name, _ = get_channel_name_by_id(client, channel_id)
            output["channel_name"] = channel_name

        _print_resolve_output(output)
        return

    # Bare ID form.
    normalized = target.lstrip("#@")
    if re.match(r"^[A-Z][A-Z0-9]{8,}$", normalized):
        id_type = _id_prefix_type(normalized)
        output.update(
            {
                "target_type": "id",
                "id": normalized,
                "id_prefix_type": id_type,
            }
        )

        with get_client() as client:
            if normalized[0] in {"C", "G", "D"}:
                channel_name, channel_data = get_channel_name_by_id(client, normalized)
                output["resolved_name"] = channel_name
                output["resolved_kind"] = "channel"
                output["is_private"] = channel_data.get("is_private", False)
            elif normalized[0] in {"U", "W", "B"}:
                user_name, user_data = get_user_name_by_id(client, normalized)
                output["resolved_name"] = user_name
                output["resolved_kind"] = "user"
                output["is_bot"] = user_data.get("is_bot", False)

        _print_resolve_output(output)
        return

    # Name form: try channel then user.
    with get_client() as client:
        # Fast path via existing cache-backed channel resolver.
        ch = resolve_channel(normalized)
        if ch.get("id") and ch.get("id") != normalized:
            channel_id = ch.get("id")
            output.update(
                {
                    "target_type": "name",
                    "resolved_kind": "channel",
                    "resolved_name": ch.get("name") or normalized,
                    "resolved_id": channel_id,
                    "id_prefix_type": _id_prefix_type(channel_id),
                }
            )
            _print_resolve_output(output)
            return

        # Fallback: explicit online channel name lookup.
        channel = _find_channel_by_name(client, normalized)
        if channel:
            channel_id = channel.get("id", "")
            output.update(
                {
                    "target_type": "name",
                    "resolved_kind": "channel",
                    "resolved_name": channel.get("name") or normalized,
                    "resolved_id": channel_id,
                    "id_prefix_type": _id_prefix_type(channel_id),
                }
            )
            _print_resolve_output(output)
            return

        user = _find_user_by_name(client, normalized)
        if user:
            user_id = user.get("id", "")
            resolved_name = (
                user.get("real_name")
                or user.get("profile", {}).get("real_name")
                or user.get("profile", {}).get("display_name")
                or user.get("name")
                or normalized
            )
            output.update(
                {
                    "target_type": "name",
                    "resolved_kind": "user",
                    "resolved_name": resolved_name,
                    "resolved_id": user_id,
                    "id_prefix_type": _id_prefix_type(user_id),
                }
            )
            _print_resolve_output(output)
            return

    output.update(
        {
            "target_type": "unknown",
            "error": "Could not resolve target as name, id, event-id, or permalink",
        }
    )
    _print_resolve_output(output)

@channel_app.command("resolve")
def channel_resolve(
    identifier: str = typer.Argument(
        ..., help="Channel ID (C123...) or name (#channel-name)"
    ),
):
    """Resolve a channel ID to name or vice versa."""
    import re

    # Normalize the identifier
    identifier = identifier.lstrip("#@")

    with get_client() as client:
        # Check if it's a channel ID
        if re.match(r"^[CDG][A-Z0-9]{8,}$", identifier):
            # It's an ID, get the info
            name, channel_data = get_channel_name_by_id(client, identifier)
            output = {
                "name": name,
                "is_archived": channel_data.get("is_archived", False),
                "is_private": channel_data.get("is_private", False),
                "created": channel_data.get("created"),
                "creator": channel_data.get("creator"),
                "topic": channel_data.get("topic", {}).get("value", ""),
                "purpose": channel_data.get("purpose", {}).get("value", ""),
            }

            # For group DMs (mpdm), include the members
            if channel_data.get("is_mpim"):
                members = channel_data.get("members", [])
                if members:
                    output["members"] = members

            # Remove None values for cleaner output
            output = {k: v for k, v in output.items() if v is not None}
        else:
            # It's a name, get the ID via API
            try:
                data = call_api(client, "conversations.list", {"exclude_archived": True, "limit": 1000})
                if data.get("ok"):
                    for channel in data.get("channels", []):
                        if (
                            channel.get("name") == identifier
                            or channel.get("name_normalized") == identifier.lower()
                        ):
                            output = {
                                "input": identifier,
                                "type": "channel_name",
                                "resolved_name": channel.get("name"),
                                "resolved_id": channel.get("id"),
                            }
                            print(yaml.dump(output, indent=2, sort_keys=False))
                            return

                # Not found
                output = {
                    "input": identifier,
                    "type": "channel_name",
                    "error": "Channel not found",
                }
            except Exception as e:
                output = {
                    "input": identifier,
                    "error": str(e),
                }

        print(yaml.dump(output, indent=2, sort_keys=False))



@channel_app.command("list")
def channel_list():
    """List all cached channels (offline).

    Prints tab-separated columns: id | name | description

    Use 'slack-chat channel resolve <id>' to fetch and cache channels from Slack.

    Examples:

        slack-chat channel list
    """
    channels = storage.get_all_cached_channels()

    if not channels:
        print(
            "No channels cached. Use 'slack-chat channel resolve <id>' to cache channels.",
            file=sys.stderr,
        )
        sys.exit(0)

    # Sort by name
    sorted_channels = sorted(channels.values(), key=lambda x: x.get("name", "").lower())

    for ch in sorted_channels:
        ch_id = ch.get("id", "")
        name = ch.get("name", "")
        # Use purpose or topic as description
        description = ch.get("purpose", {}).get("value", "") or ch.get("topic", {}).get(
            "value", ""
        )
        description = truncate_text(description, 50)
        print(f"{ch_id}\t{name}\t{description}")


@channel_app.command("find")
def channel_find(
    keyword: str = typer.Argument(..., help="Keyword to search for in channel names"),
):
    """Find cached channels by name keyword (offline).

    Matches channels whose name contains the keyword (case-insensitive).
    For example, 'hal' matches 'thal', 'fhaly', etc.

    Prints tab-separated columns: id | name | description

    Examples:

        slack-chat channel find prod

        slack-chat channel find sre
    """
    matches = storage.find_channels_by_keyword(keyword)

    if not matches:
        print(f"No channels found matching '{keyword}'.", file=sys.stderr)
        sys.exit(0)

    for ch in matches:
        ch_id = ch.get("id", "")
        name = ch.get("name", "")
        description = ch.get("purpose", {}).get("value", "") or ch.get("topic", {}).get(
            "value", ""
        )
        description = truncate_text(description, 50)
        print(f"{ch_id}\t{name}\t{description}")


@channel_app.command("pending")
def channel_pending(
    channel: str = typer.Argument(
        ..., help="Channel name or ID to check for unread messages"
    ),
):
    """Check if a channel has unread messages (via browser DOM).

    Returns 'true' if the channel has unread messages, 'false' otherwise.
    Uses the browser's sidebar to detect the unread CSS class.

    Examples:

        slack-chat channel pending nexint

        slack-chat channel pending C0A8JJBAVU2
    """
    import re

    # Normalize channel name (remove # prefix if present)
    channel = channel.lstrip("#")

    # Build the JavaScript to check unread status
    # Check by channel ID if it looks like an ID, otherwise by name
    if re.match(r"^[CDG][A-Z0-9]{8,}$", channel):
        # It's a channel ID
        js_script = f"""() => {{
            const el = document.querySelector("[data-qa-channel-sidebar-channel-id={channel}]");
            if (!el) return {{ found: false, pending: false }};
            return {{ found: true, pending: el.classList.contains("p-channel_sidebar__channel--unread") }};
        }}"""
    else:
        # It's a channel name - search all channels
        js_script = f"""() => {{
            const channels = document.querySelectorAll("[data-qa=channel-sidebar-channel]");
            for (const el of channels) {{
                const nameEl = el.querySelector(".p-channel_sidebar__name");
                if (nameEl && nameEl.textContent === "{channel}") {{
                    return {{ found: true, pending: el.classList.contains("p-channel_sidebar__channel--unread") }};
                }}
            }}
            return {{ found: false, pending: false }};
        }}"""

    with get_client() as client:
        try:
            response = client.post(f"{SERVER_URL}/execute", json={"script": js_script})
            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    result = data.get("result", {})
                    # Result may be a JSON string, parse it if so
                    if isinstance(result, str):
                        result = json.loads(result)
                    if not result.get("found"):
                        print(
                            f"Channel '{channel}' not found in sidebar", file=sys.stderr
                        )
                        sys.exit(1)
                    print("true" if result.get("pending") else "false")
                else:
                    print(
                        f"Error: {data.get('detail', 'Unknown error')}", file=sys.stderr
                    )
                    sys.exit(1)
            else:
                print(f"Error: Server returned {response.status_code}", file=sys.stderr)
                sys.exit(1)
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)


# ============================================================================
# USER COMMANDS
# ============================================================================


@user_app.command("resolve")
def user_resolve(
    identifier: str = typer.Argument(..., help="User ID (U123...) or name (@username)"),
):
    """Resolve a user ID to name or vice versa."""
    import re

    # Normalize the identifier
    identifier = identifier.lstrip("#@")

    with get_client() as client:
        # Check if it's a user ID
        if re.match(r"^[UW][A-Z0-9]{8,}$", identifier):
            # It's an ID, get the info
            name, user_data = get_user_name_by_id(client, identifier)
            output = {
                "name": name,
                "is_bot": user_data.get("is_bot", False),
                "is_admin": user_data.get("is_admin", False),
            }
            # Add optional fields if available
            if user_data.get("profile", {}).get("email"):
                output["email"] = user_data.get("profile", {}).get("email")
            if user_data.get("profile", {}).get("display_name"):
                output["display_name"] = user_data.get("profile", {}).get(
                    "display_name"
                )
            if user_data.get("profile", {}).get("title"):
                output["title"] = user_data.get("profile", {}).get("title")
            if user_data.get("profile", {}).get("first_name"):
                output["first_name"] = user_data.get("profile", {}).get("first_name")
            if user_data.get("profile", {}).get("last_name"):
                output["last_name"] = user_data.get("profile", {}).get("last_name")
            if user_data.get("tz"):
                output["tz"] = user_data.get("tz")
            if user_data.get("is_archived"):
                output["is_archived"] = user_data.get("is_archived")

            # Add custom profile fields
            custom_fields = user_data.get("profile", {}).get("fields", {})
            if custom_fields:
                output["custom_fields"] = custom_fields
        else:
            # It's a name, search for the user
            try:
                response = client.post(
                    f"{SERVER_URL}/api", json={"endpoint": "users.list", "params": {}}
                )
                if response.status_code == 200:
                    data = response.json()
                    if data.get("ok"):
                        for user in data.get("members", []):
                            if (
                                user.get("name") == identifier
                                or user.get("real_name") == identifier
                                or user.get("profile", {}).get("display_name")
                                == identifier
                            ):
                                output = {
                                    "resolved_name": user.get("real_name")
                                    or user.get("name"),
                                }
                                print(yaml.dump(output, indent=2, sort_keys=False))
                                return

                # Not found
                output = {
                    "input": identifier,
                    "type": "user_name",
                    "error": "User not found",
                }
            except Exception as e:
                output = {
                    "input": identifier,
                    "error": str(e),
                }

        print(yaml.dump(output, indent=2, sort_keys=False))


@user_app.command("list")
def user_list():
    """List all cached users (offline).

    Prints tab-separated columns: id | name | title | project

    Use 'slack-chat user resolve <id>' to fetch and cache users from Slack.

    Examples:

        slack-chat user list
    """
    users = storage.get_all_cached_users()

    if not users:
        print(
            "No users cached. Use 'slack-chat user resolve <id>' to cache users.",
            file=sys.stderr,
        )
        sys.exit(0)

    # Sort by real_name (first_name + last_name)
    sorted_users = sorted(
        users.values(), key=lambda x: (x.get("real_name") or x.get("name", "")).lower()
    )

    for user in sorted_users:
        user_id = user.get("id", "")
        profile = user.get("profile", {})
        first_name = profile.get("first_name", "")
        last_name = profile.get("last_name", "")
        name = (
            f"{first_name} {last_name}".strip()
            or profile.get("display_name")
            or user.get("name", "")
        )
        title = profile.get("title", "")
        # Get project from custom field XfHJKR6MPT
        project = profile.get("fields", {}).get("XfHJKR6MPT", {}).get("value", "")
        print(f"{user_id}\t{name}\t{title}\t{project}")


@user_app.command("find")
def user_find(
    keyword: str = typer.Argument(..., help="Keyword to search for in user names"),
):
    """Find cached users by name keyword (offline).

    Matches users whose name, real_name, or display_name contains the keyword (case-insensitive).
    For example, 'hal' matches 'thal', 'fhaly', etc.

    Prints tab-separated columns: id | name | title | project

    Examples:

        slack-chat user find john

        slack-chat user find smith
    """
    matches = storage.find_users_by_keyword(keyword)

    if not matches:
        print(f"No users found matching '{keyword}'.", file=sys.stderr)
        sys.exit(0)

    for user in matches:
        user_id = user.get("id", "")
        profile = user.get("profile", {})
        first_name = profile.get("first_name", "")
        last_name = profile.get("last_name", "")
        name = (
            f"{first_name} {last_name}".strip()
            or profile.get("display_name")
            or user.get("name", "")
        )
        title = profile.get("title", "")
        # Get project from custom field XfHJKR6MPT
        project = profile.get("fields", {}).get("XfHJKR6MPT", {}).get("value", "")
        print(f"{user_id}\t{name}\t{title}\t{project}")

