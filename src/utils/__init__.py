"""Utility functions for slack-chat CLI."""

from .const import (
    WORKSPACE_ROOT,
    SERVER_URL,
    PID_FILE,
    LOG_FILE,
    READ_TRACKING_FILE,
    CHANNELS_FILE,
    user_cache,
)

from .api import (
    get_client,
    call_api,
    is_enterprise,
    handle_response,
)

from .tracking import (
    load_read_events,
    save_read_event,
    is_event_read,
)

from .resolution import (
    get_user_info,
    get_channel_name_by_id,
    get_user_name_by_id,
    resolve_channel,
    enrich_messages,
)

from .formatting import (
    format_event_id,
    parse_event_id,
    format_channel_display,
    truncate_text,
    generate_slack_url,
    extract_thread_ts_from_permalink,
    extract_image_urls,
)

from .server import (
    get_server_pid,
)

from .slack import (
    fetch_unread_counts,
    fetch_subscribed_threads,
    fetch_mentions,
    fetch_reactions_to_me,
    get_reaction_details,
)
