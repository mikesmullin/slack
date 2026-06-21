// Conversation stream + around-context fetching, with inline thread expansion.
// Mirrors src2/commands/client.py stream helpers.
import { slackApi } from './api.mjs';
import { normalizeTs, parseEventId, parseSlackPermalink, formatEventId, channelPrefixType } from './format.mjs';
import { resolveChannel, getChannelNameById } from './resolve.mjs';
import { findUsersByKeyword } from './cache.mjs';

const tsFloat = (ts) => {
  const f = parseFloat(String(ts ?? '0'));
  return Number.isFinite(f) ? f : 0;
};

async function findExactMessage(channelId, ts) {
  const data = await slackApi('conversations.history', {
    channel: channelId,
    oldest: ts,
    latest: ts,
    inclusive: true,
    limit: 1,
  });
  if (!data.ok) return null;
  const msg = (data.messages || [])[0];
  if (!msg || String(msg.ts || '') !== ts) return null;
  return msg;
}

async function fetchAllThreadReplies(channelId, threadTs) {
  let cursor = null;
  const out = [];
  const seen = new Set();
  for (;;) {
    const params = { channel: channelId, ts: threadTs, limit: 200 };
    if (cursor) params.cursor = cursor;
    const data = await slackApi('conversations.replies', params);
    if (!data.ok) break;
    for (const msg of data.messages || []) {
      const mts = String(msg.ts || '');
      if (!mts || mts === threadTs || seen.has(mts)) continue;
      seen.add(mts);
      out.push(msg);
    }
    cursor = (data.response_metadata && data.response_metadata.next_cursor) || '';
    if (!cursor) break;
  }
  out.sort((a, b) => tsFloat(a.ts) - tsFloat(b.ts));
  return out;
}

async function emitThreadAfterCursor(channelId, threadTs, cursorTs, remaining, seen) {
  const replies = await fetchAllThreadReplies(channelId, threadTs);
  const cursorVal = tsFloat(cursorTs);
  const emitted = [];
  for (const msg of replies) {
    const mts = String(msg.ts || '');
    if (!mts || seen.has(mts)) continue;
    // Inclusive resume: emit the cursor message too (dup across pages is OK;
    // missing a message is not). `seen` prevents dupes within a single call.
    if (tsFloat(mts) < cursorVal) continue;
    emitted.push(msg);
    seen.add(mts);
    remaining -= 1;
    if (remaining <= 0) break;
  }
  return { emitted, remaining };
}

async function emitChannelWithThreadsAfterCursor(channelId, rootCursorTs, remaining, seen) {
  const emitted = [];
  let cursor = null;
  const oldest = rootCursorTs || '0';
  const batchLimit = Math.max(200, remaining);
  let exhausted = false;
  // Inclusive resume on the first batch: the target message at `oldest` is
  // included (dup across pages is OK; missing a message is not). `seen`
  // dedupes within this call; the Slack `cursor` ignores oldest/inclusive on
  // subsequent batches.
  let inclusive = true;

  while (remaining > 0) {
    const params = { channel: channelId, oldest, inclusive, limit: batchLimit };
    if (cursor) params.cursor = cursor;
    const data = await slackApi('conversations.history', params);
    if (!data.ok) break;

    const batch = [...(data.messages || [])].reverse();
    for (const root of batch) {
      const rootTs = String(root.ts || '');
      if (!rootTs || seen.has(rootTs)) continue;
      const threadTs = String(root.thread_ts || '');
      if (threadTs && threadTs !== rootTs) continue;

      emitted.push(root);
      seen.add(rootTs);
      remaining -= 1;
      if (remaining <= 0) return { emitted, remaining, hasMore: true };

      if (parseInt(root.reply_count || 0, 10) > 0) {
        const r = await emitThreadAfterCursor(channelId, threadTs || rootTs, rootTs, remaining, seen);
        emitted.push(...r.emitted);
        remaining = r.remaining;
        if (remaining <= 0) return { emitted, remaining, hasMore: true };
      }
    }
    cursor = (data.response_metadata && data.response_metadata.next_cursor) || '';
    if (!cursor) {
      exhausted = true;
      break;
    }
  }
  const hasMore = remaining <= 0 || !exhausted;
  return { emitted, remaining, hasMore };
}

async function emitLatestChannelWithThreads(channelId, remaining, seen) {
  const emitted = [];
  let cursor = null;
  let exhausted = false;
  const batchLimit = Math.max(200, remaining);

  while (remaining > 0) {
    const params = { channel: channelId, limit: batchLimit };
    if (cursor) params.cursor = cursor;
    const data = await slackApi('conversations.history', params);
    if (!data.ok) break;

    for (const root of data.messages || []) {
      const rootTs = String(root.ts || '');
      if (!rootTs || seen.has(rootTs)) continue;
      const threadTs = String(root.thread_ts || '');
      if (threadTs && threadTs !== rootTs) continue;

      emitted.push(root);
      seen.add(rootTs);
      remaining -= 1;
      if (remaining <= 0) return { emitted, remaining, hasMore: true };

      if (parseInt(root.reply_count || 0, 10) > 0) {
        const r = await emitThreadAfterCursor(channelId, threadTs || rootTs, rootTs, remaining, seen);
        emitted.push(...r.emitted);
        remaining = r.remaining;
        if (remaining <= 0) return { emitted, remaining, hasMore: true };
      }
    }
    cursor = (data.response_metadata && data.response_metadata.next_cursor) || '';
    if (!cursor) {
      exhausted = true;
      break;
    }
  }
  const hasMore = remaining <= 0 || !exhausted;
  return { emitted, remaining, hasMore };
}

/** Bounded inline root+thread stream using target ts as a strict-resume cursor. */
export async function fetchReadStream(channelId, cursorTs, threadHintTs, count, threadScoped = false) {
  let remaining = Math.max(1, count);
  const seen = new Set();
  const emitted = [];

  cursorTs = normalizeTs(cursorTs);
  threadHintTs = normalizeTs(threadHintTs);

  if (!cursorTs) {
    const r = await emitLatestChannelWithThreads(channelId, remaining, seen);
    return { ok: true, messages: r.emitted, has_more: Boolean(r.hasMore) };
  }

  let rootResumeTs = cursorTs;
  let threadRootTs = threadHintTs;
  if (!threadRootTs) {
    const msg = await findExactMessage(channelId, cursorTs);
    if (msg) {
      const t = normalizeTs(String(msg.thread_ts || ''));
      if (t) threadRootTs = t;
    }
  }

  if (threadRootTs) {
    if (cursorTs === threadRootTs && remaining > 0) {
      const rootMsg = await findExactMessage(channelId, threadRootTs);
      if (rootMsg) {
        const rv = String(rootMsg.ts || '');
        if (rv && !seen.has(rv)) {
          emitted.push(rootMsg);
          seen.add(rv);
          remaining -= 1;
        }
      }
    }
    const r = await emitThreadAfterCursor(channelId, threadRootTs, cursorTs, remaining, seen);
    emitted.push(...r.emitted);
    remaining = r.remaining;
    rootResumeTs = threadRootTs;
  }

  if (threadScoped && threadRootTs) {
    const threadExhausted = remaining > 0;
    return {
      ok: true,
      messages: emitted,
      has_more: !threadExhausted,
      thread_end: true,
      next_channel_ts: rootResumeTs,
    };
  }

  let hasMore;
  if (remaining > 0) {
    const r = await emitChannelWithThreadsAfterCursor(channelId, rootResumeTs, remaining, seen);
    emitted.push(...r.emitted);
    hasMore = r.hasMore;
  } else {
    hasMore = true;
  }
  return { ok: true, messages: emitted, has_more: Boolean(hasMore) };
}

/** Resolve a target string into channel/ts/thread context + display summary. */
export async function buildTargetContext(target) {
  let [channelId, timestamp, threadTs] = parseEventId(target);
  const parsedUrl = parseSlackPermalink(target);
  if (parsedUrl && !threadTs) {
    channelId = parsedUrl.channel_id;
    timestamp = parsedUrl.timestamp;
    threadTs = parsedUrl.thread_ts;
  }
  timestamp = normalizeTs(timestamp);
  threadTs = normalizeTs(threadTs);

  const knownId = /^[CDGUW][A-Z0-9]{6,}$/.test(channelId || '');
  let userIdPattern = /^[UW][A-Z0-9]{6,}$/.test(channelId || '');

  if (channelId && !knownId) {
    const name = channelId.replace(/^@/, '');
    const matches = findUsersByKeyword(name);
    let userId = null;
    for (const u of matches) {
      const profile = u.profile || {};
      if (u.name === name || u.real_name === name || profile.display_name === name) {
        userId = u.id;
        break;
      }
    }
    if (userId === null && matches.length === 1) userId = matches[0].id;
    if (userId) {
      channelId = userId;
      userIdPattern = true;
    }
  }
  if (channelId && userIdPattern) {
    const dm = await slackApi('conversations.open', { users: channelId });
    if (dm.ok && dm.channel) channelId = dm.channel.id;
  }

  if (timestamp && !threadTs && channelId) {
    const data = await slackApi('conversations.history', {
      channel: channelId,
      oldest: timestamp,
      latest: timestamp,
      inclusive: true,
      limit: 1,
    });
    if (data.ok) {
      const msg = (data.messages || [])[0];
      if (msg && msg.ts === timestamp) {
        threadTs = msg.thread_ts || null;
        if (!threadTs && parseInt(msg.reply_count || 0, 10) > 0) threadTs = timestamp;
      }
    }
  }

  const ch = resolveChannel(channelId);
  let resolvedChannelId = ch.id || channelId;
  let channelName = ch.name || resolvedChannelId;
  if (channelName === resolvedChannelId) {
    const [name] = await getChannelNameById(resolvedChannelId);
    channelName = name || channelName;
  }

  let targetType = 'channel';
  if (parsedUrl && timestamp) targetType = 'message_permalink';
  else if (timestamp) targetType = 'message_event';

  const parts = [
    `target_type=${targetType}`,
    `channel_id=${resolvedChannelId}`,
    `channel_id_prefix_type=${channelPrefixType(resolvedChannelId)}`,
    `channel_name=${channelName}`,
  ];
  if (timestamp) parts.push(`timestamp=${timestamp}`);
  if (threadTs) parts.push(`thread_ts=${threadTs}`);
  if (timestamp) parts.push(`event_id=${formatEventId(resolvedChannelId, timestamp, threadTs)}`);

  return {
    resolvedChannelId,
    channelName,
    timestamp,
    threadTs,
    targetSummary: parts.join(', '),
  };
}

/** Fetch a context window around a target message (channel or thread). */
export async function fetchAroundMessages(channelId, timestamp, threadTs, before, after) {
  const all = [];

  if (threadTs) {
    const data = await slackApi('conversations.replies', { channel: channelId, ts: threadTs, limit: 200 });
    if (!data.ok) return [];
    const msgs = data.messages || [];
    const targetIndex = msgs.findIndex((m) => m.ts === timestamp);
    if (targetIndex < 0) return [];
    const start = Math.max(0, targetIndex - Math.max(0, before));
    const end = Math.min(msgs.length, targetIndex + 1 + Math.max(0, after));
    return msgs.slice(start, end);
  }

  const targetData = await slackApi('conversations.history', {
    channel: channelId,
    oldest: timestamp,
    inclusive: true,
    limit: 1,
  });
  const targetMsgs = targetData.ok ? targetData.messages || [] : [];
  if (!targetMsgs.length || targetMsgs[0].ts !== timestamp) return [];

  if (before > 0) {
    const beforeData = await slackApi('conversations.history', {
      channel: channelId,
      latest: timestamp,
      inclusive: false,
      limit: before,
    });
    if (beforeData.ok) all.push(...[...(beforeData.messages || [])].reverse());
  }

  all.push(targetMsgs[0]);

  if (after > 0) {
    let latest = timestamp;
    const tf = parseFloat(timestamp);
    if (Number.isFinite(tf)) latest = String(tf + 1000000);
    const afterData = await slackApi('conversations.history', {
      channel: channelId,
      latest,
      oldest: timestamp,
      inclusive: false,
      limit: after,
    });
    if (afterData.ok) all.push(...[...(afterData.messages || [])].reverse());
  }

  return all;
}
