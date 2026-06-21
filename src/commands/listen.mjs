// listen: stream live Slack events from the WebSocket (no browser).
import { parseArgs } from '../lib/args.mjs';
import { SlackWSClient } from '../lib/ws.mjs';
import { SlackAuthError } from '../lib/api.mjs';

export const help = `listen

Usage:
    slack-chat listen [--raw] [--type TYPE,...] [--quiet]

Description:
    Connect directly to the Slack WebSocket and stream real-time events.
    No browser required — uses the credentials in .tokens.yaml.

Options:
    --raw          Print each event as a JSON line
    --type, -t     Comma-separated event types to include (e.g. message,channel_marked)
    --quiet, -q    Suppress human-readable output (still prints with --raw)

Examples:
    slack-chat listen
    slack-chat listen --raw
    slack-chat listen --type message,channel_marked`;

const SPEC = {
  raw: { aliases: [], type: 'bool', default: false },
  type: { aliases: ['-t'], type: 'string', default: null },
  quiet: { aliases: ['-q'], type: 'bool', default: false },
};

function printEvent(event) {
  const t = event.type || 'unknown';
  const channel = event.channel || '';
  const user = event.user || '';
  const ts = event.event_ts || event.ts || '';

  if (t === 'message') {
    const subtype = event.subtype || '';
    const text = (event.text || '').slice(0, 80);
    const label = subtype ? `[${subtype}] ` : '';
    console.log(`message  ${label}ch=${channel} user=${user} ts=${ts}  ${JSON.stringify(text)}`);
  } else if (t === 'channel_marked') {
    console.log(`channel_marked  ch=${channel}  unread=${event.unread_count || 0}  ts=${ts}`);
  } else if (t === 'badge_counts_updated') {
    const av2 = event.activity_v2 || {};
    console.log(
      `badge_updated  channel=${av2.channel || 0}  dm=${av2.dm || 0}  @=${av2.at_user || 0}  thread=${av2.thread_v2 || 0}`
    );
  } else if (t === 'hello') {
    process.stderr.write(`✅ WebSocket authenticated  region=${event.region || ''}\n`);
  } else if (t === 'pong') {
    process.stderr.write('✅ Ping-pong OK\n');
  } else if (['user_typing', 'presence_change', 'dnd_updated_user'].includes(t)) {
    /* suppress noisy events */
  } else {
    console.log(`${t}  ${JSON.stringify(event).slice(0, 120)}`);
  }
}

export async function run(argv) {
  const { opts } = parseArgs(argv, SPEC);
  const filterTypes = opts.type ? new Set(opts.type.split(',').map((s) => s.trim())) : null;

  if (!opts.quiet) process.stderr.write('Connecting to Slack WebSocket…\n');

  try {
    const client = new SlackWSClient();
    for await (const event of client.events()) {
      const type = event.type || 'unknown';
      if (filterTypes && !filterTypes.has(type)) continue;
      if (opts.raw) console.log(JSON.stringify(event));
      else if (!opts.quiet) printEvent(event);
    }
  } catch (e) {
    if (e instanceof SlackAuthError) {
      process.stderr.write(`Authentication failed: ${e.message}\nRun \`slack-chat login\` to refresh.\n`);
      process.exit(1);
    }
    throw e;
  }
}
