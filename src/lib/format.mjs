// Message identifier ("target") + formatting helpers.
// Mirrors src2/utils/formatting.py. The target is Slack's global message UUID:
//   {channel_id}:{timestamp}[@{thread_ts}]

/** Build an event id / target string. */
export function formatEventId(channelId, timestamp = null, threadTs = null) {
  if (timestamp) {
    let id = `${channelId}:${timestamp}`;
    if (threadTs) id += `@${threadTs}`;
    return id;
  }
  return channelId;
}

/** Parse a target into [channelId, timestamp, threadTs]. Accepts permalinks. */
export function parseEventId(eventId) {
  const perma = parseSlackPermalink(eventId);
  if (perma) {
    return [perma.channel_id, perma.timestamp, perma.thread_ts ?? null];
  }
  if (eventId.includes(':')) {
    const idx = eventId.indexOf(':');
    const channelId = eventId.slice(0, idx);
    const tsPart = eventId.slice(idx + 1);
    if (tsPart.includes('@')) {
      const [timestamp, threadTs] = tsPart.split('@', 2);
      return [channelId, timestamp, threadTs];
    }
    return [channelId, tsPart, null];
  }
  return [eventId, null, null];
}

/** Convert compact permalink ts (1771347628831459) -> "1771347628.831459". */
export function compactTsToFloat(rawTs) {
  if (!rawTs || !/^\d+$/.test(rawTs) || rawTs.length < 7) return null;
  return `${rawTs.slice(0, -6)}.${rawTs.slice(-6)}`;
}

/** Parse a Slack permalink URL into {channel_id, timestamp, thread_ts, ...}. */
export function parseSlackPermalink(permalink) {
  if (!permalink || !permalink.includes('slack.com/archives/')) return null;
  try {
    const u = new URL(permalink.trim());
    const m = u.pathname.match(/\/archives\/([A-Z0-9]+)\/p(\d+)/);
    if (!m) return null;
    const channelId = m[1];
    const timestamp = compactTsToFloat(m[2]);
    if (!timestamp) return null;
    const threadTs = u.searchParams.get('thread_ts');
    return {
      channel_id: channelId,
      timestamp,
      thread_ts: threadTs || null,
      event_id: formatEventId(channelId, timestamp, threadTs || null),
      url: permalink,
    };
  } catch {
    return null;
  }
}

/** Normalize a ts-like value to the canonical float string, or null. */
export function normalizeTs(ts) {
  if (ts === undefined || ts === null) return null;
  const s = String(ts).trim();
  if (!s) return null;
  if (/^\d+\.\d+$/.test(s)) return s;
  if (/^\d{10,}$/.test(s)) {
    const f = compactTsToFloat(s);
    if (f) return f;
  }
  if (/^\d+$/.test(s)) return `${s}.000000`;
  return s;
}

/** Truncate text to a max length with ellipsis. */
export function truncate(text, max = 50) {
  if (!text || text.length <= max) return text || '';
  return text.slice(0, max - 3) + '...';
}

/** Human-friendly relative age from a slack ts string. */
export function tsAge(tsStr) {
  if (!tsStr) return '?';
  try {
    const secs = parseFloat(String(tsStr).split('.')[0]);
    const age = Date.now() / 1000 - secs;
    if (age < 3600) return `${Math.floor(age / 60)}m ago`;
    if (age < 86400) return `${Math.floor(age / 3600)}h ago`;
    return `${Math.floor(age / 86400)}d ago`;
  } catch {
    return '?';
  }
}

/** Channel id prefix → semantic type. */
export function channelPrefixType(channelId) {
  const map = { C: 'channel_public', G: 'channel_private_or_mpim', D: 'direct_message' };
  return map[(channelId || '').slice(0, 1)] || 'unknown';
}
