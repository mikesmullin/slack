// post-message + reply: post to a channel or thread; optional file uploads.
import yaml from 'js-yaml';
import { yellow, blueText } from '../lib/color.mjs';
import { parseArgs } from '../lib/args.mjs';
import { slackApi } from '../lib/api.mjs';
import { parseEventId } from '../lib/format.mjs';
import { resolveChannel } from '../lib/resolve.mjs';
import { buildTargetContext } from '../lib/stream.mjs';
import { normalizeMarkdownLinks } from '../lib/render.mjs';
import { completeFileUploads } from '../lib/uploads.mjs';

// ── post-message ─────────────────────────────────────────────────────────────
export const helpPost = `post-message

Usage:
    slack-chat post-message <target> <text> [--image PATH] [--attachment PATH]

Description:
    Post a channel message, or reply when the target carries thread context.
    With --image/--attachment, uploads files using <text> as the initial comment.

Options:
    --image, -i PATH       Image to upload. Repeat for multiple.
    --attachment, -a PATH  File to upload. Repeat for multiple.

Examples:
    slack-chat post-message #sre-team "Heads up: deploy starting"
    slack-chat post-message "C05R34P9KAA:1709253181.804579@1707924824.356449" "Thanks"
    slack-chat post-message #sre-team "HTTP 500" --image ./screenshot.png`;

const POST_SPEC = {
  image: { aliases: ['-i'], type: 'list' },
  attachment: { aliases: ['-a'], type: 'list' },
};

export async function runPost(argv) {
  const { opts, positionals } = parseArgs(argv, POST_SPEC);
  const target = positionals[0];
  const text = positionals[1];
  if (!target || text === undefined) {
    process.stderr.write('Error: post-message requires <target> <text>.\n');
    process.exit(1);
  }
  const normalizedText = normalizeMarkdownLinks(text);
  const ctx = await buildTargetContext(target);
  const channelId = ctx.resolvedChannelId;
  const timestamp = ctx.timestamp;
  const threadTs = ctx.threadTs;
  const paths = [...(opts.image || []), ...(opts.attachment || [])];

  if (paths.length) {
    const data = await completeFileUploads(
      channelId,
      normalizedText,
      paths,
      threadTs || timestamp || null
    );
    console.log(`${yellow('target')}: ${blueText(ctx.targetSummary)}`);
    console.log(yaml.dump(data, { sortKeys: false }));
    return;
  }

  const params =
    threadTs || timestamp
      ? { channel: channelId, text: normalizedText, thread_ts: threadTs || timestamp }
      : { channel: channelId, text: normalizedText };
  const data = await slackApi('chat.postMessage', params);
  console.log(`${yellow('target')}: ${blueText(ctx.targetSummary)}`);
  console.log(yaml.dump(data, { sortKeys: false }));
}

// ── reply ────────────────────────────────────────────────────────────────────
export const helpReply = `reply

Usage:
    slack-chat reply <id_or_channel> <text>

Description:
    Reply to a channel or thread. Accepts a channel name/ID, or an event id
    (CHANNEL:TS or CHANNEL:TS@THREAD_TS).

Examples:
    slack-chat reply #sre-team "Following up"
    slack-chat reply C0A7RJWRZPT:1767815267.099869 "Replying to thread"`;

export async function runReply(argv) {
  const { positionals } = parseArgs(argv, {});
  const target = positionals[0];
  const message = positionals[1];
  if (!target || message === undefined) {
    process.stderr.write('Error: reply requires <id_or_channel> <text>.\n');
    process.exit(1);
  }

  let channelId = null;
  let threadTs = null;

  if (target.includes(':')) {
    const [cid, ts, parsedThread] = parseEventId(target);
    channelId = cid;
    threadTs = parsedThread || ts;
  } else {
    const ch = resolveChannel(target);
    channelId = ch.id;
    if (channelId === target.replace(/^#/, '') && !/^[CDG][A-Z0-9]{7,}$/.test(channelId)) {
      process.stderr.write(
        `Error: could not resolve channel: ${target}\n   Try 'slack-chat channel find <keyword>'.\n`
      );
      process.exit(1);
    }
  }

  const params = { channel: channelId, text: message };
  if (threadTs) params.thread_ts = threadTs;
  const data = await slackApi('chat.postMessage', params);
  if (data.ok) {
    const result = { ok: true, channel: channelId, message_ts: data.ts };
    if (threadTs) result.thread_ts = threadTs;
    if (data.message && data.message.permalink) result.permalink = data.message.permalink;
    console.log(yaml.dump(result, { sortKeys: false }));
  } else {
    process.stderr.write(yaml.dump({ ok: false, error: data.error || 'unknown' }, { sortKeys: false }));
    process.exit(1);
  }
}
