// channel subcommands: describe, tab, resolve, list, find, pending.
import { spawnSync } from 'node:child_process';
import yaml from 'js-yaml';
import { parseArgs } from '../lib/args.mjs';
import { slackApi } from '../lib/api.mjs';
import { truncate } from '../lib/format.mjs';
import { resolveChannel, getChannelNameById } from '../lib/resolve.mjs';
import {
  allCachedChannels,
  findChannelsByKeyword,
  findChannelByName,
  cacheChannel,
} from '../lib/cache.mjs';
import { resolveChannelTabs, selectChannelTab } from '../lib/tabs.mjs';
import { fetchAuthedUrl } from '../lib/fetchurl.mjs';

function channelRow(ch) {
  const desc = truncate((ch.purpose || {}).value || (ch.topic || {}).value || '', 50);
  return `${ch.id || ''}\t${ch.name || ''}\t${desc}`;
}

// ── describe ─────────────────────────────────────────────────────────────────
export const helpDescribe = `channel describe

Usage:
    slack-chat channel describe <channel>

Description:
    Fetch channel metadata, including resolved tabs (canvas/files URLs).`;

export async function runDescribe(argv) {
  const { positionals } = parseArgs(argv, {});
  const channel = positionals[0];
  if (!channel) {
    process.stderr.write('Error: channel describe requires a <channel>.\n');
    process.exit(1);
  }
  const ch = resolveChannel(channel);
  const data = await slackApi('conversations.info', { channel: ch.id });
  if (data.ok && data.channel) {
    data.channel.tabs_resolved = await resolveChannelTabs(data.channel);
  }
  console.log(yaml.dump(data, { sortKeys: false }));
}

// ── tab ──────────────────────────────────────────────────────────────────────
export const helpTab = `channel tab

Usage:
    slack-chat channel tab <channel> <tab> [--download] [--yaml]
    slack-chat channel tab <url> [--yaml]

Description:
    Fetch a channel tab body (e.g. canvas) via direct authenticated download.

Options:
    --download   Use the download URL when available (file-backed tabs)
    --yaml       Print tab metadata + fetched response instead of the raw body`;

const TAB_SPEC = {
  download: { aliases: [], type: 'bool', default: false },
  yaml: { aliases: [], type: 'bool', default: false },
};

function looksLikeUrl(value) {
  try {
    const u = new URL((value || '').trim());
    return (u.protocol === 'http:' || u.protocol === 'https:') && Boolean(u.host);
  } catch {
    return false;
  }
}

export async function runTab(argv) {
  const { opts, positionals } = parseArgs(argv, TAB_SPEC);
  const target = positionals[0];
  const tab = positionals[1];

  if (!target) {
    process.stderr.write('Error: channel tab requires <channel> <tab> or <url>.\n');
    process.exit(1);
  }

  // URL-only mode
  if (tab === undefined && looksLikeUrl(target)) {
    const r = await fetchAuthedUrl(target);
    if (!r.ok) {
      process.stderr.write(yaml.dump({ ok: false, error: r.error || 'fetch_failed', url: target }, { sortKeys: false }));
      process.exit(1);
    }
    if (opts.yaml) {
      console.log(
        yaml.dump(
          { ok: true, fetch: { status: r.status, status_text: r.status_text, url: r.url, content_type: r.content_type }, body: r.body },
          { sortKeys: false }
        )
      );
      return;
    }
    process.stdout.write(r.body);
    return;
  }

  if (tab === undefined) {
    process.stderr.write('Error: missing TAB selector. Use `channel tab <channel> <tab>` or `channel tab <url>`.\n');
    process.exit(1);
  }

  const ch = resolveChannel(target);
  const info = await slackApi('conversations.info', { channel: ch.id });
  if (!info.ok) {
    process.stderr.write(yaml.dump(info, { sortKeys: false }));
    process.exit(1);
  }
  const channelObj = info.channel && typeof info.channel === 'object' ? info.channel : {};
  const tabs = await resolveChannelTabs(channelObj);
  if (!tabs.length) {
    process.stderr.write('No tabs found for channel.\n');
    process.exit(1);
  }

  const selected = selectChannelTab(tab, tabs);
  if (!selected) {
    const listing = tabs.map((t) => ({ index: t.index, name: t.name, type: t.type, id: t.id }));
    process.stderr.write(yaml.dump({ ok: false, error: `tab_not_found: ${tab}`, available_tabs: listing }, { sortKeys: false }));
    process.exit(1);
  }

  const url = opts.download ? selected.download_url : selected.url;
  if (!url) {
    process.stderr.write(yaml.dump({ ok: false, error: 'tab_has_no_fetchable_url', tab: selected }, { sortKeys: false }));
    process.exit(1);
  }

  const r = await fetchAuthedUrl(url);
  if (!r.ok) {
    process.stderr.write(yaml.dump({ ok: false, error: r.error || 'fetch_failed', tab: selected, url }, { sortKeys: false }));
    process.exit(1);
  }
  if (opts.yaml) {
    console.log(
      yaml.dump(
        {
          ok: true,
          channel: { id: channelObj.id, name: channelObj.name },
          tab: selected,
          fetch: { status: r.status, status_text: r.status_text, url: r.url, content_type: r.content_type },
          body: r.body,
        },
        { sortKeys: false }
      )
    );
    return;
  }
  process.stdout.write(r.body);
}

// ── resolve ──────────────────────────────────────────────────────────────────
export const helpResolve = `channel resolve

Usage:
    slack-chat channel resolve <identifier>

Description:
    Resolve a channel ID to name (and metadata) or a name to its ID.`;

export async function runResolve(argv) {
  const { positionals } = parseArgs(argv, {});
  let identifier = positionals[0];
  if (!identifier) {
    process.stderr.write('Error: channel resolve requires an <identifier>.\n');
    process.exit(1);
  }
  identifier = identifier.replace(/^[#@]+/, '');

  if (/^[CDG][A-Z0-9]{7,}$/.test(identifier)) {
    const [name, data] = await getChannelNameById(identifier);
    const output = {
      name,
      is_archived: data.is_archived || false,
      is_private: data.is_private || false,
      created: data.created,
      creator: data.creator,
      topic: (data.topic || {}).value || '',
      purpose: (data.purpose || {}).value || '',
    };
    if (data.is_mpim && (data.members || []).length) output.members = data.members;
    for (const k of Object.keys(output)) if (output[k] === undefined) delete output[k];
    console.log(yaml.dump(output, { sortKeys: false }));
    return;
  }

  const cached = findChannelByName(identifier);
  if (cached) {
    console.log(
      yaml.dump(
        { input: identifier, type: 'channel_name', resolved_name: cached.name, resolved_id: cached.id },
        { sortKeys: false }
      )
    );
    return;
  }
  const data = await slackApi('conversations.list', { exclude_archived: true, limit: 1000 });
  if (data.ok) {
    for (const channel of data.channels || []) {
      if (channel.name === identifier || channel.name_normalized === identifier.toLowerCase()) {
        if (channel.id) cacheChannel(channel.id, channel);
        console.log(
          yaml.dump(
            { input: identifier, type: 'channel_name', resolved_name: channel.name, resolved_id: channel.id },
            { sortKeys: false }
          )
        );
        return;
      }
    }
  }
  console.log(yaml.dump({ input: identifier, type: 'channel_name', error: 'Channel not found' }, { sortKeys: false }));
}

// ── list ─────────────────────────────────────────────────────────────────────
export const helpList = `channel list

Usage:
    slack-chat channel list

Description:
    List all cached channels (offline). Columns: id | name | description`;

export async function runList() {
  const channels = Object.values(allCachedChannels());
  if (!channels.length) {
    process.stderr.write("No channels cached. Use 'slack-chat resolve <id>' to cache channels.\n");
    return;
  }
  channels.sort((a, b) => (a.name || '').toLowerCase().localeCompare((b.name || '').toLowerCase()));
  for (const ch of channels) console.log(channelRow(ch));
}

// ── find ─────────────────────────────────────────────────────────────────────
export const helpFind = `channel find

Usage:
    slack-chat channel find <keyword>

Description:
    Find cached channels by name keyword (offline).`;

export async function runFind(argv) {
  const { positionals } = parseArgs(argv, {});
  const keyword = positionals[0];
  if (!keyword) {
    process.stderr.write('Error: channel find requires a <keyword>.\n');
    process.exit(1);
  }
  const matches = findChannelsByKeyword(keyword);
  if (!matches.length) {
    process.stderr.write(`No channels found matching '${keyword}'.\n`);
    return;
  }
  for (const ch of matches) console.log(channelRow(ch));
}

// ── pending ──────────────────────────────────────────────────────────────────
export const helpPending = `channel pending

Usage:
    slack-chat channel pending <channel>

Description:
    Check whether a channel has unread messages via the browser sidebar DOM.
    Requires the \`browser\` tool running with Slack open (browser server start).
    Prints "true" or "false".`;

export async function runPending(argv) {
  const { positionals } = parseArgs(argv, {});
  let channel = positionals[0];
  if (!channel) {
    process.stderr.write('Error: channel pending requires a <channel>.\n');
    process.exit(1);
  }
  channel = channel.replace(/^#/, '');

  const isId = /^[CDG][A-Z0-9]{7,}$/.test(channel);
  const script = isId
    ? `() => { const el = document.querySelector("[data-qa-channel-sidebar-channel-id=${channel}]"); if (!el) return { found: false, pending: false }; return { found: true, pending: el.classList.contains("p-channel_sidebar__channel--unread") }; }`
    : `() => { const channels = document.querySelectorAll("[data-qa=channel-sidebar-channel]"); for (const el of channels) { const nameEl = el.querySelector(".p-channel_sidebar__name"); if (nameEl && nameEl.textContent === "${channel}") { return { found: true, pending: el.classList.contains("p-channel_sidebar__channel--unread") }; } } return { found: false, pending: false }; }`;

  const res = spawnSync('browser', ['client', 'execute', script], { encoding: 'utf8' });
  if (res.status !== 0) {
    process.stderr.write(
      `Error: could not reach the browser tool. Start it with \`browser server start\` and open Slack.\n${res.stderr || ''}`
    );
    process.exit(1);
  }
  let result;
  try {
    const out = res.stdout.trim().replace(/^Result:\s*/, '');
    result = JSON.parse(out);
  } catch {
    process.stderr.write(`Error: unexpected browser output: ${res.stdout}\n`);
    process.exit(1);
  }
  if (!result.found) {
    process.stderr.write(`Channel '${channel}' not found in sidebar\n`);
    process.exit(1);
  }
  console.log(result.pending ? 'true' : 'false');
}
