// activity: mentions / threads / reactions feed via activity.feed.
import yaml from 'js-yaml';
import { rgb, green, indigo, yellow, muted, white } from '../lib/color.mjs';
import { parseArgs } from '../lib/args.mjs';
import { slackApi } from '../lib/api.mjs';
import { formatEventId, tsAge } from '../lib/format.mjs';
import { getUserNameById, getChannelNameById } from '../lib/resolve.mjs';

export const help = `activity

Usage:
    slack-chat activity [--tab TAB] [--limit N] [--after EVENT_ID] [--yaml]

Description:
    Show the Slack activity feed — mentions, thread replies, reactions.

Options:
    --tab, -t    all | mentions | threads | reactions (default: all)
    --limit, -n  Max items to fetch (default: 25)
    --after, -a  Only items newer than this event id or raw timestamp
    --yaml       Output raw YAML payload

Examples:
    slack-chat activity --tab mentions -n 10
    slack-chat activity --tab reactions
    slack-chat activity -n 50 --after C01ABCDEF2:1709253181.804579`;

const SPEC = {
  tab: { aliases: ['-t'], type: 'string', default: 'all' },
  limit: { aliases: ['-n'], type: 'int', default: 25 },
  after: { aliases: ['-a'], type: 'string', default: null },
  yaml: { aliases: [], type: 'bool', default: false },
};

const TAB_TYPES = {
  mentions:
    'at_user,at_user_group,at_channel,at_everyone,keyword,list_user_mentioned,unjoined_channel_mention',
  threads: 'thread_v2',
  reactions: 'message_reaction',
  all:
    'thread_v2,message_reaction,internal_channel_invite,list_record_edited,' +
    'bot_dm_bundle,at_user,at_user_group,at_channel,at_everyone,keyword,' +
    'list_record_assigned,list_user_mentioned,list_todo_notification,' +
    'list_approval_request,list_approval_reviewed,unjoined_channel_mention,' +
    'external_channel_invite,external_dm_invite',
};

const TYPE_LABELS = {
  at_user: 'mention', at_user_group: 'group-mention', at_channel: '@channel',
  at_everyone: '@everyone', keyword: 'keyword', list_user_mentioned: 'list-mention',
  unjoined_channel_mention: 'unjoined-mention', thread_v2: 'thread',
  message_reaction: 'reaction', internal_channel_invite: 'invite',
  external_channel_invite: 'ext-invite', external_dm_invite: 'ext-dm',
  bot_dm_bundle: 'bot-dm', list_record_edited: 'list-edit',
  list_record_assigned: 'list-assign', list_todo_notification: 'list-todo',
  list_approval_request: 'approval-req', list_approval_reviewed: 'approval-rev',
};

function extractParts(itemData) {
  const t = itemData.type || '';
  if (t === 'thread_v2') {
    const entry =
      ((itemData.bundle_info || {}).payload || {}).thread_entry || {};
    return [entry.channel_id, entry.latest_ts, null, entry.thread_ts];
  }
  if (t === 'message_reaction') {
    const msg = itemData.message || {};
    const reaction = itemData.reaction || {};
    return [msg.channel, msg.ts, reaction.user, null];
  }
  const msg = itemData.message || {};
  return [msg.channel, msg.ts, msg.author_user_id, null];
}

const USER_REF = /<@([UW][A-Z0-9]+)(?:\|([^>]+))?>/g;

async function cleanSlackText(text) {
  if (!text) return '';
  let out = text.replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>');
  out = out.replace(/<#([A-Z0-9]+)\|([^>]+)>/g, '#$2');
  out = out.replace(/<([^|>]+)\|([^>]+)>/g, '$2');
  out = out.replace(/<(https?:\/\/[^>]+)>/g, '$1');

  const refs = [...out.matchAll(USER_REF)];
  for (const m of refs) {
    const [name] = await getUserNameById(m[1]);
    const display = indigo(`@${name || m[2] || m[1]} (${m[1]})`);
    out = out.replace(m[0], display);
  }
  return out
    .split('\n')
    .map((line) => line.replace(/[ \t]+/g, ' ').trim())
    .join('\n')
    .trim();
}

async function resolveChannelPlain(channelId, channelName) {
  if (channelName && channelName.startsWith('mpdm-')) {
    try {
      const result = await slackApi('conversations.info', { channel: channelId });
      if (result.ok) {
        const ids = (result.channel || {}).members || [];
        const parts = [];
        for (const uid of ids.slice(0, 6)) {
          const [name] = await getUserNameById(uid);
          parts.push(name ? `@${name} (${uid})` : `(${uid})`);
        }
        if (parts.length) return `Group DM: (${parts.join(', ')}) (${channelId})`;
      }
    } catch {
      /* ignore */
    }
  }
  return `${channelName ? '#' + channelName : channelId} (${channelId})`;
}

async function fetchMessage(channelId, ts, threadTs) {
  try {
    if (threadTs && threadTs !== ts) {
      const r = await slackApi('conversations.replies', {
        channel: channelId, ts: threadTs, latest: ts, oldest: ts, inclusive: 'true', limit: '1',
      });
      if (r.ok) {
        for (const m of r.messages || []) if (m.ts === ts) return [m.text || '', m.user];
      }
    }
    const r = await slackApi('conversations.history', {
      channel: channelId, oldest: ts, latest: ts, inclusive: 'true', limit: '1',
    });
    if (r.ok) {
      const msgs = r.messages || [];
      if (msgs.length && msgs[0].ts === ts) return [msgs[0].text || '', msgs[0].user];
    }
  } catch {
    /* ignore */
  }
  return [null, null];
}

function parseCutoffTs(after) {
  if (!after) return null;
  let a = after;
  if (a.includes(':')) a = a.split(':', 2)[1];
  if (a.includes('@')) a = a.split('@', 1)[0];
  const f = parseFloat(a);
  return Number.isFinite(f) ? f : null;
}

export async function run(argv) {
  const { opts } = parseArgs(argv, SPEC);
  const tab = String(opts.tab).toLowerCase();
  if (!(tab in TAB_TYPES)) {
    process.stderr.write(`Unknown tab '${tab}'. Valid: all, mentions, threads, reactions\n`);
    process.exit(1);
  }

  const result = await slackApi('activity.feed', {
    limit: String(opts.limit),
    types: TAB_TYPES[tab],
    mode: 'priority_reads_and_unreads_v1',
    archive_only: 'false',
    snooze_only: 'false',
    unread_only: 'false',
    priority_only: 'false',
    is_activity_inbox: 'false',
  });
  if (!result.ok) {
    process.stderr.write(`activity.feed failed: ${result.error || 'unknown error'}\n`);
    process.exit(1);
  }

  let items = result.items || [];
  const cutoff = opts.after ? parseCutoffTs(opts.after) : null;
  if (cutoff !== null) {
    items = items.filter((i) => {
      const [, ts] = extractParts(i.item || {});
      const f = parseFloat(ts || '0');
      return Number.isFinite(f) && f > cutoff;
    });
  }
  if (!items.length) {
    console.log(`No activity (${tab}).`);
    return;
  }

  const enriched = [];
  for (const item of items) {
    const itemData = item.item || {};
    const itemType = itemData.type || 'unknown';
    const labelText = TYPE_LABELS[itemType] || itemType;
    let [channelId, ts, actorId, threadTs] = extractParts(itemData);
    const isUnread = Boolean(item.is_unread);
    const ageStr = tsAge(ts);

    let chPlain = '?';
    if (channelId) {
      if (channelId.startsWith('D')) chPlain = `DM ${channelId}`;
      else {
        const [chName] = await getChannelNameById(channelId);
        chPlain = await resolveChannelPlain(channelId, chName || '');
      }
    }

    let emojiName = null;
    if (itemType === 'message_reaction') emojiName = (itemData.reaction || {}).name || '';

    let [rawText, msgUserId] = [null, null];
    if (channelId && ts) [rawText, msgUserId] = await fetchMessage(channelId, ts, threadTs);
    if (!actorId && msgUserId) actorId = msgUserId;

    const eventId = channelId && ts ? formatEventId(channelId, ts, threadTs) : '';
    const cleanText = rawText ? await cleanSlackText(rawText) : '';

    if (opts.yaml) {
      let actorDisplay = '';
      if (actorId) {
        const [name] = await getUserNameById(actorId);
        actorDisplay = `@${name || actorId} (${actorId})`;
        if (emojiName) actorDisplay += ` :${emojiName}:`;
      }
      const record = {
        type: labelText, is_unread: isUnread, age: ageStr, channel_id: channelId,
        channel_display: chPlain, actor_id: actorId, actor_display: actorDisplay,
        event_id: eventId, text: cleanText,
      };
      if (emojiName) record.emoji = emojiName;
      enriched.push(record);
      continue;
    }

    const unreadTag = isUnread ? ' ' + yellow('[unread]') : '';
    const badge = yellow(`[${labelText}]`) + unreadTag;
    const ageDisp = muted(ageStr);
    const chDisplay = chPlain !== '?' ? green(chPlain) : muted('?');
    const actorParts = [];
    if (actorId) {
      const [name] = await getUserNameById(actorId);
      actorParts.push(indigo(`@${name || actorId} (${actorId})`));
    }
    if (emojiName) actorParts.push(yellow(`:${emojiName}:`));
    const actorStr = actorParts.join(' ');
    const eventIdStr = eventId ? '  ' + rgb(eventId + ':', 100, 200, 240) : '';
    console.log(`${badge}  ${ageDisp}  ${chDisplay}  ${actorStr}${eventIdStr}`);
    if (cleanText) for (const line of cleanText.split('\n')) console.log(`  ${white(line)}`);
  }

  if (opts.yaml) {
    process.stdout.write(yaml.dump(enriched, { sortKeys: false }));
  }
}
