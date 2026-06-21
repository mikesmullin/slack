// search: paginated message search with inline @user expansion.
import yaml from 'js-yaml';
import { rgb, green, indigo, muted, yellow, blueText, label, dim } from '../lib/color.mjs';
import { parseArgs } from '../lib/args.mjs';
import { slackApi } from '../lib/api.mjs';
import { formatEventId } from '../lib/format.mjs';
import { getChannelNameById } from '../lib/resolve.mjs';
import { formatMessageText, displayUser, searchResultEventId } from '../lib/render.mjs';

export const help = `search

Usage:
    slack-chat search <query> [--count N] [--page N] [--yaml]

Description:
    Search Slack messages with paginated results.

Options:
    --count, -n   Results per page (default: 20)
    --page, -p    1-based page index (default: 1)
    --yaml        Print raw YAML payload

Example:
    slack-chat search "site reliability support" -n 10 -p 2`;

const SPEC = {
  count: { aliases: ['-n'], type: 'int', default: 20 },
  page: { aliases: ['-p'], type: 'int', default: 1 },
  yaml: { aliases: [], type: 'bool', default: false },
};

export async function run(argv) {
  const { opts, positionals } = parseArgs(argv, SPEC);
  const query = positionals.join(' ').trim();
  if (!query) {
    process.stderr.write('Error: search requires a <query>.\n');
    process.exit(1);
  }

  const data = await slackApi('search.messages', { query, count: opts.count, page: opts.page });

  if (opts.yaml) {
    console.log(yaml.dump(data, { sortKeys: false }));
    return;
  }
  if (!data.ok) {
    console.log(yaml.dump(data, { sortKeys: false }));
    return;
  }
  await printSearch(query, data);
}

async function channelDisplay(message, eventId) {
  const channel = message.channel || {};
  const channelId = channel.id || message.channel_id || '';
  let channelName = channel.name;
  if (channelId) {
    const [resolved] = await getChannelNameById(channelId);
    if (resolved) channelName = resolved;
  }
  if (channelName) return `#${channelName} (${eventId})`;
  if (channelId) return `#${channelId} (${eventId})`;
  return eventId;
}

async function printSearch(query, data) {
  const messagesObj = data.messages || {};
  const matches = messagesObj.matches || [];
  const paging = messagesObj.paging || {};
  const total = paging.total ?? matches.length;
  const page = paging.page;
  const pages = paging.pages;
  const perPage = paging.count ?? matches.length;

  console.log(`${yellow('query')}: ${blueText(query)}`);
  if (page && pages) {
    console.log(
      `${label('pagination')}: ${muted('page')} ${page}/${pages} ${dim('|')} ` +
        `${muted('per_page')} ${perPage} ${dim('|')} ${muted('total')} ${total}`
    );
  } else {
    console.log(`${label('results')}: ${matches.length} shown ${dim('|')} ${muted('total')} ${total}`);
  }

  const inlineUserCache = new Map();
  for (const msg of matches) {
    const eventId = searchResultEventId(msg, { formatEventId });
    const disp = await channelDisplay(msg, eventId);
    const who = await displayUser(msg);
    const text = await formatMessageText(msg.text || '', inlineUserCache, msg);
    console.log(`${green(disp)} ${indigo(who)}${muted(':')} ${text}`);
  }
}
