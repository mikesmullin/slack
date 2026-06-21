// read-message: bounded cursor stream + around-context window.
import yaml from 'js-yaml';
import { rgb, green, indigo, muted, yellow, blueText, label, dim } from '../lib/color.mjs';
import { formatEventId } from '../lib/format.mjs';
import {
  buildTargetContext,
  fetchReadStream,
  fetchAroundMessages,
} from '../lib/stream.mjs';
import { formatMessageText, displayUser } from '../lib/render.mjs';
import { parseArgs } from '../lib/args.mjs';

export const help = `read-message

Usage:
    slack-chat read-message <target> [--count N] [--before N] [--after N] [--yaml]

Description:
    Unified read command.
    - Default mode: bounded cursor reads using target timestamp as resume marker
    - Around mode: context windows around a message via --before/--after

Options:
    --count, -n   Maximum messages to emit (default: 20)
    --before, -B  Context messages before target timestamp (around mode)
    --after, -A   Context messages after target timestamp (around mode)
    --yaml        Print raw YAML payload

Examples:
    slack-chat read-message C05R34P9KAA -n 20
    slack-chat read-message "C05R34P9KAA:1709253181.804579@1707924824.356449" -n 5
    slack-chat read-message "C05R34P9KAA:1709253181.804579@1707924824.356449" -B 2 -A 3`;

const SPEC = {
  count: { aliases: ['-n'], type: 'int', default: 20 },
  before: { aliases: ['-B'], type: 'int', default: null },
  after: { aliases: ['-A'], type: 'int', default: null },
  yaml: { aliases: [], type: 'bool', default: false },
};

export async function run(argv) {
  const { opts, positionals } = parseArgs(argv, SPEC);
  const target = positionals[0];
  if (!target) {
    process.stderr.write('Error: read-message requires a <target>.\n');
    process.exit(1);
  }

  const ctx = await buildTargetContext(target);
  const channelId = ctx.resolvedChannelId;
  const channelName = ctx.channelName;
  const timestamp = ctx.timestamp;
  const threadTs = ctx.threadTs;

  // Around mode
  if (opts.before !== null || opts.after !== null) {
    if (!timestamp) {
      process.stderr.write(
        'Error: --before/--after require a message target (CHANNEL:TIMESTAMP[@THREAD_TS] or permalink).\n'
      );
      process.exit(1);
    }
    const beforeN = Math.max(0, opts.before || 0);
    const afterN = Math.max(0, opts.after || 0);
    const messages = await fetchAroundMessages(channelId, timestamp, threadTs, beforeN, afterN);

    if (opts.yaml) {
      console.log(
        yaml.dump(
          {
            target,
            target_summary: ctx.targetSummary,
            channel: { id: channelId, name: channelName },
            context_type: threadTs ? 'thread' : 'channel',
            before: beforeN,
            after: afterN,
            message_count: messages.length,
            messages,
          },
          { sortKeys: false }
        )
      );
      return;
    }
    await printAround(ctx.targetSummary, channelName, channelId, timestamp, beforeN, afterN, messages);
    return;
  }

  // Cursor stream mode
  const data = await fetchReadStream(channelId, timestamp, threadTs, opts.count, Boolean(threadTs));

  if (opts.yaml) {
    console.log(
      yaml.dump(
        { ...data, channel: { id: channelId, name: channelName }, cursor: timestamp, thread_cursor: threadTs },
        { sortKeys: false }
      )
    );
    return;
  }
  if (!data.ok) {
    console.log(yaml.dump(data, { sortKeys: false }));
    return;
  }
  await printRead(ctx.targetSummary, channelName, channelId, threadTs, opts.count, data);
}

async function printRead(targetSummary, channelName, channelId, threadTs, count, data) {
  const messages = data.messages || [];
  const hasMore = Boolean(data.has_more);

  let totalEstimate = null;
  if (threadTs && messages.length) {
    const parent = messages[0];
    const replyCount = parseInt(parent.reply_count ?? Math.max(messages.length - 1, 0), 10) || 0;
    totalEstimate = replyCount + 1;
  }

  console.log(`${yellow('target')}: ${blueText(targetSummary)}`);
  if (totalEstimate !== null) {
    console.log(
      `${label('pagination')}: ${muted('per_page')} ${count} ${dim('|')} ` +
        `${muted('total_estimate')} ${totalEstimate} ${dim('|')} ${muted('has_more')} ${String(hasMore)}`
    );
  } else {
    console.log(
      `${label('pagination')}: ${muted('per_page')} ${count} ${dim('|')} ` +
        `${muted('returned')} ${messages.length} ${dim('|')} ${muted('has_more')} ${String(hasMore)}`
    );
  }

  const inlineUserCache = new Map();
  for (const msg of messages) {
    const msgTs = msg.ts || msg.timestamp || '';
    let msgThreadTs = msg.thread_ts || threadTs;
    if (!msgThreadTs && parseInt(msg.reply_count || 0, 10) > 0) msgThreadTs = msgTs;
    const eventId = formatEventId(channelId, msgTs, msgThreadTs);
    const channelDisplay = `#${channelName} (${eventId})`;
    const who = await displayUser(msg);
    const text = await formatMessageText(msg.text || '', inlineUserCache, msg);
    console.log(`${green(channelDisplay)} ${indigo(who)}${muted(':')} ${text}`);
  }

  if (data.thread_end) {
    const nextTs = data.next_channel_ts || '';
    const nextEventId = nextTs ? formatEventId(channelId, nextTs) : '';
    const note = nextEventId
      ? `(end of message thread. next message in channel resumes from: ${nextEventId})`
      : '(end of message thread)';
    console.log(muted(note));
  }
}

async function printAround(targetSummary, channelName, channelId, targetTs, before, after, messages) {
  console.log(`${yellow('target')}: ${blueText(targetSummary)}`);
  console.log(
    `${label('context')}: ${muted('before')} ${before} ${dim('|')} ` +
      `${muted('after')} ${after} ${dim('|')} ${muted('returned')} ${messages.length}`
  );

  const inlineUserCache = new Map();
  for (const msg of messages) {
    const msgTs = msg.ts || '';
    let msgThreadTs = msg.thread_ts || null;
    if (!msgThreadTs && parseInt(msg.reply_count || 0, 10) > 0) msgThreadTs = msgTs;
    const eventId = formatEventId(channelId, msgTs, msgThreadTs);
    const prefix = msgTs === targetTs ? '[target] ' : '';
    const channelDisplay = `${prefix}#${channelName} (${eventId})`;
    const who = await displayUser(msg);
    const text = await formatMessageText(msg.text || '', inlineUserCache, msg);
    console.log(`${green(channelDisplay)} ${indigo(who)}${muted(':')} ${text}`);
  }
}
