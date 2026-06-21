// react + post-reaction: add an emoji reaction to a message.
import yaml from 'js-yaml';
import { parseArgs } from '../lib/args.mjs';
import { slackApi } from '../lib/api.mjs';
import { parseEventId } from '../lib/format.mjs';
import { resolveChannel } from '../lib/resolve.mjs';

// ── react ────────────────────────────────────────────────────────────────────
export const helpReact = `react

Usage:
    slack-chat react <id_or_event> <emoji>

Description:
    Add an emoji reaction. Accepts an event id (CHANNEL:TS or CHANNEL:TS@THREAD_TS).
    Emoji can be with or without colons (eyes, :eyes:, +1).

Examples:
    slack-chat react C0A7RJWRZPT:1767815267.099869 eyes
    slack-chat react C0A7RJWRZPT:1767815267.099869 white_check_mark`;

export async function runReact(argv) {
  const { positionals } = parseArgs(argv, {});
  const target = positionals[0];
  const emoji = positionals[1];
  if (!target || emoji === undefined) {
    process.stderr.write('Error: react requires <id_or_event> <emoji>.\n');
    process.exit(1);
  }
  const emojiName = emoji.replace(/^:+|:+$/g, '');

  let channelId = null;
  let timestamp = null;
  if (target.includes(':')) {
    [channelId, timestamp] = parseEventId(target);
  }
  if (!channelId || !timestamp) {
    process.stderr.write(`Error: could not resolve message: ${target}\n`);
    process.exit(1);
  }

  const data = await slackApi('reactions.add', { channel: channelId, timestamp, name: emojiName });
  if (data.ok) {
    console.log(yaml.dump({ ok: true, channel: channelId, timestamp, emoji: emojiName }, { sortKeys: false }));
  } else {
    process.stderr.write(yaml.dump({ ok: false, error: data.error || 'unknown' }, { sortKeys: false }));
    process.exit(1);
  }
}

// ── post-reaction ────────────────────────────────────────────────────────────
export const helpPostReaction = `post-reaction

Usage:
    slack-chat post-reaction <channel> <timestamp> <emoji_name>

Description:
    Add a reaction to a specific message timestamp.

Example:
    slack-chat post-reaction C05R34P9KAA 1709253181.804579 thumbsup`;

export async function runPostReaction(argv) {
  const { positionals } = parseArgs(argv, {});
  const [channel, timestamp, name] = positionals;
  if (!channel || !timestamp || !name) {
    process.stderr.write('Error: post-reaction requires <channel> <timestamp> <emoji_name>.\n');
    process.exit(1);
  }
  const ch = resolveChannel(channel);
  const data = await slackApi('reactions.add', {
    channel: ch.id,
    timestamp,
    name: name.replace(/^:+|:+$/g, ''),
  });
  console.log(yaml.dump(data, { sortKeys: false }));
}
