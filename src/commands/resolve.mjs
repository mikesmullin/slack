// resolve: turn any target (name/id/event/permalink) into parsed components.
import yaml from 'js-yaml';
import { rgb } from '../lib/color.mjs';
import { parseArgs } from '../lib/args.mjs';
import { slackApi } from '../lib/api.mjs';
import {
  parseEventId,
  parseSlackPermalink,
  formatEventId,
} from '../lib/format.mjs';
import {
  resolveChannel,
  getChannelNameById,
  getUserNameById,
} from '../lib/resolve.mjs';
import {
  findUserByName,
  findChannelByName,
  cacheUser,
  cacheChannel,
  evictCachedChannel,
  evictCachedUser,
} from '../lib/cache.mjs';
import { searchUsersEdge } from '../lib/edge.mjs';

export const help = `resolve

Usage:
    slack-chat resolve <target> [--refresh]

Description:
    Resolve a target into parsed components and resolved names.
    Uses cache first; on API fallback success the cache is updated.

Options:
    --refresh, -r   Bypass cache and force fresh API lookup

Examples:
    slack-chat resolve jdoe
    slack-chat resolve #sre-team
    slack-chat resolve U01DT37Q5LG
    slack-chat resolve "C05R34P9KAA:1709253181.804579@1707924824.356449"`;

const SPEC = { refresh: { aliases: ['-r'], type: 'bool', default: false } };

const ID_TYPES = {
  U: 'user', W: 'workspace_app_user', B: 'legacy_bot', C: 'channel_public',
  G: 'channel_private_or_mpim', D: 'direct_message', T: 'workspace',
  E: 'enterprise_or_emoji', A: 'slack_app', S: 'user_group', F: 'file',
};
const idPrefixType = (id) => (id ? ID_TYPES[id[0].toUpperCase()] || 'unknown' : 'unknown');

function colorEventId(value) {
  if (!value.includes(':')) return rgb(value, 214, 224, 255);
  const [channelId, rest] = [value.slice(0, value.indexOf(':')), value.slice(value.indexOf(':') + 1)];
  if (rest.includes('@')) {
    const [ts, threadTs] = rest.split('@', 2);
    return (
      rgb(channelId, 66, 184, 131) + rgb(':', 140, 153, 173) + rgb(ts, 91, 192, 235) +
      rgb('@', 140, 153, 173) + rgb(threadTs, 255, 159, 67)
    );
  }
  return rgb(channelId, 66, 184, 131) + rgb(':', 140, 153, 173) + rgb(rest, 91, 192, 235);
}

function colorScalar(key, value) {
  if (typeof value === 'boolean') return rgb(String(value), 250, 208, 44);
  if (value === null || value === undefined) return rgb('null', 140, 153, 173);
  const text = String(value);
  if (key === 'event_id') return colorEventId(text);
  if (key.endsWith('_id') || key === 'id') return rgb(text, 127, 219, 202);
  if (key.endsWith('_type') || key === 'target_type' || key === 'resolved_kind') return rgb(text, 255, 199, 95);
  if (text.startsWith('http://') || text.startsWith('https://')) return rgb(text, 120, 180, 255);
  return rgb(text, 214, 224, 255);
}

function printOutput(output) {
  const colored = rgb('x', 1, 1, 1) !== 'x';
  if (!colored) {
    console.log(yaml.dump(output, { sortKeys: false }));
    return;
  }
  for (const [key, value] of Object.entries(output)) {
    console.log(`${rgb(String(key), 255, 105, 180)}: ${colorScalar(String(key), value)}`);
  }
}

async function inferThreadTs(channelId, timestamp) {
  const data = await slackApi('conversations.history', {
    channel: channelId, oldest: timestamp, latest: timestamp, inclusive: true, limit: 1,
  });
  if (!data.ok) return null;
  const msg = (data.messages || [])[0];
  if (!msg || msg.ts !== timestamp) return null;
  if (msg.thread_ts) return msg.thread_ts;
  if (parseInt(msg.reply_count || 0, 10) > 0) return timestamp;
  return null;
}

async function findChannelOnline(name) {
  const cached = findChannelByName(name);
  if (cached) return cached;
  const data = await slackApi('conversations.list', { exclude_archived: true, limit: 1000 });
  if (!data.ok) return null;
  const lower = name.toLowerCase();
  for (const ch of data.channels || []) {
    if ([ch.name, ch.name_normalized].some((c) => c && c.toLowerCase() === lower)) {
      if (ch.id) cacheChannel(ch.id, ch);
      return ch;
    }
  }
  return null;
}

async function resolveUserByName(name) {
  const lower = name.toLowerCase();
  const isExact = (u) => {
    const p = u.profile || {};
    return [u.name, u.real_name, p.display_name, p.real_name].some(
      (c) => c && c.toLowerCase() === lower
    );
  };

  const cached = findUserByName(name);
  if (cached) return { exact: cached, suggestions: [] };

  // Fuzzy people search (also finds deactivated + uncached users). We only
  // AUTO-resolve on an exact match; otherwise the results become suggestions.
  const edge = await searchUsersEdge(name);
  const edgeExact = edge.find(isExact);
  if (edgeExact) {
    if (edgeExact.id) cacheUser(edgeExact.id, edgeExact);
    return { exact: edgeExact, suggestions: [] };
  }

  // Workspace users.list exact fallback.
  const data = await slackApi('users.list', {});
  if (data.ok) {
    for (const u of data.members || []) {
      if (isExact(u)) {
        if (u.id) cacheUser(u.id, u);
        return { exact: u, suggestions: [] };
      }
    }
  }

  return { exact: null, suggestions: edge.slice(0, 5) };
}

export async function run(argv) {
  const { opts, positionals } = parseArgs(argv, SPEC);
  const target = (positionals[0] || '').trim();
  if (!target) {
    process.stderr.write('Error: resolve requires a <target>.\n');
    process.exit(1);
  }
  const refresh = opts.refresh;
  const output = { input: target };

  // Permalink form
  const parsedUrl = parseSlackPermalink(target);
  if (parsedUrl) {
    const channelId = parsedUrl.channel_id;
    let threadTs = parsedUrl.thread_ts;
    if (!threadTs) threadTs = await inferThreadTs(channelId, parsedUrl.timestamp);
    Object.assign(output, {
      target_type: 'message_permalink',
      permalink: target,
      channel_id: channelId,
      timestamp: parsedUrl.timestamp,
      thread_ts: threadTs,
      event_id: formatEventId(channelId, parsedUrl.timestamp, threadTs),
      channel_id_prefix_type: idPrefixType(channelId),
    });
    if (refresh) evictCachedChannel(channelId);
    const [name] = await getChannelNameById(channelId, { refresh });
    output.channel_name = name;
    printOutput(output);
    return;
  }

  // Event ID form
  const [channelId, timestamp, threadTs] = parseEventId(target);
  if (timestamp) {
    Object.assign(output, {
      target_type: 'message_event',
      channel_id: channelId,
      timestamp,
      thread_ts: threadTs,
      event_id: formatEventId(channelId, timestamp, threadTs),
      channel_id_prefix_type: idPrefixType(channelId),
    });
    if (refresh) evictCachedChannel(channelId);
    const [name] = await getChannelNameById(channelId, { refresh });
    output.channel_name = name;
    printOutput(output);
    return;
  }

  // Bare ID form
  const normalized = target.replace(/^[#@]+/, '');
  if (/^[A-Z][A-Z0-9]{8,}$/.test(normalized)) {
    Object.assign(output, { target_type: 'id', id: normalized, id_prefix_type: idPrefixType(normalized) });
    const first = normalized[0];
    if ('CGD'.includes(first)) {
      if (refresh) evictCachedChannel(normalized);
      const [name, data] = await getChannelNameById(normalized, { refresh });
      output.resolved_name = name;
      output.resolved_kind = 'channel';
      output.is_private = data.is_private || false;
    } else if ('UWB'.includes(first)) {
      if (refresh) evictCachedUser(normalized);
      const [name, data] = await getUserNameById(normalized);
      output.resolved_name = name;
      output.resolved_kind = 'user';
      output.is_bot = data.is_bot || false;
    }
    printOutput(output);
    return;
  }

  // Name form: channel then user
  const ch = resolveChannel(normalized);
  if (ch.id && ch.id !== normalized) {
    Object.assign(output, {
      target_type: 'name', resolved_kind: 'channel', resolved_name: ch.name || normalized,
      resolved_id: ch.id, id_prefix_type: idPrefixType(ch.id),
    });
    printOutput(output);
    return;
  }
  const channel = await findChannelOnline(normalized);
  if (channel) {
    Object.assign(output, {
      target_type: 'name', resolved_kind: 'channel', resolved_name: channel.name || normalized,
      resolved_id: channel.id || '', id_prefix_type: idPrefixType(channel.id || ''),
    });
    printOutput(output);
    return;
  }
  const user = await resolveUserByName(normalized);
  if (user.exact) {
    const u = user.exact;
    const profile = u.profile || {};
    const resolvedName =
      u.real_name || profile.real_name || profile.display_name || u.name || normalized;
    Object.assign(output, {
      target_type: 'name', resolved_kind: 'user', resolved_name: resolvedName,
      resolved_id: u.id || '', id_prefix_type: idPrefixType(u.id || ''),
      is_deactivated: Boolean(u.deleted),
    });
    printOutput(output);
    return;
  }

  // No exact match. Surface fuzzy candidates as a suggestion (do NOT auto-pick).
  if (user.suggestions.length) {
    const lines = user.suggestions.map((u) => {
      const p = u.profile || {};
      const dn = p.display_name || u.name || '?';
      const rn = p.real_name || u.real_name || '';
      const del = u.deleted ? ' [deactivated]' : '';
      return `  - ${dn}${rn ? ` (${rn})` : ''} — ${u.id}${del}`;
    });
    process.stderr.write(`User not found: '${normalized}'. Did you mean:\n${lines.join('\n')}\n`);
  }

  Object.assign(output, {
    target_type: 'unknown',
    error: 'Could not resolve target as name, id, event-id, or permalink',
  });
  printOutput(output);
}
