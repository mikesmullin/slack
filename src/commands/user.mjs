// user subcommands: status-get, status-set, list, find.
import yaml from 'js-yaml';
import { parseArgs } from '../lib/args.mjs';
import { slackApi } from '../lib/api.mjs';
import { truncate } from '../lib/format.mjs';
import {
  allCachedUsers,
  findUsersByKeyword,
} from '../lib/cache.mjs';

const PROJECT_FIELD = 'XfHJKR6MPT';

function userRow(user) {
  const profile = user.profile || {};
  const name =
    `${profile.first_name || ''} ${profile.last_name || ''}`.trim() ||
    profile.display_name ||
    user.name ||
    '';
  const title = profile.title || '';
  const project = ((profile.fields || {})[PROJECT_FIELD] || {}).value || '';
  return `${user.id || ''}\t${name}\t${title}\t${project}`;
}

// ── list ─────────────────────────────────────────────────────────────────────
export const helpList = `user list

Usage:
    slack-chat user list

Description:
    List all cached users (offline). Columns: id | name | title | project`;

export async function runList() {
  const users = allCachedUsers();
  const vals = Object.values(users);
  if (!vals.length) {
    process.stderr.write("No users cached. Use 'slack-chat resolve <id>' to cache users.\n");
    return;
  }
  vals.sort((a, b) =>
    (a.real_name || a.name || '').toLowerCase().localeCompare((b.real_name || b.name || '').toLowerCase())
  );
  for (const u of vals) console.log(userRow(u));
}

// ── find ─────────────────────────────────────────────────────────────────────
export const helpFind = `user find

Usage:
    slack-chat user find <keyword>

Description:
    Find cached users by keyword (offline). Columns: id | name | title | project`;

export async function runFind(argv) {
  const { positionals } = parseArgs(argv, {});
  const keyword = positionals[0];
  if (!keyword) {
    process.stderr.write('Error: user find requires a <keyword>.\n');
    process.exit(1);
  }
  const matches = findUsersByKeyword(keyword);
  if (!matches.length) {
    process.stderr.write(`No users found matching '${keyword}'.\n`);
    return;
  }
  for (const u of matches) console.log(userRow(u));
}

// ── status-get ───────────────────────────────────────────────────────────────
export const helpStatusGet = `user status-get

Usage:
    slack-chat user status-get <identifier>

Description:
    Get the current Slack status for a user (ID, username, or @mention).`;

function resolveUserId(identifier) {
  const id = identifier.replace(/^@/, '');
  if (/^[UW][A-Z0-9]{6,}$/.test(id)) return id;
  const matches = findUsersByKeyword(id);
  for (const u of matches) {
    const prof = u.profile || {};
    if (u.name === id || u.real_name === id || prof.display_name === id) return u.id;
  }
  if (matches.length === 1) return matches[0].id;
  return null;
}

export async function runStatusGet(argv) {
  const { positionals } = parseArgs(argv, {});
  const identifier = positionals[0];
  if (!identifier) {
    process.stderr.write('Error: user status-get requires an <identifier>.\n');
    process.exit(1);
  }
  const userId = resolveUserId(identifier);
  if (!userId) {
    process.stderr.write(`error: could not resolve user '${identifier}'\n`);
    process.exit(1);
  }
  const data = await slackApi('users.profile.get', { user: userId });
  if (!data.ok) {
    process.stderr.write(`error: ${data.error || 'unknown'}\n`);
    process.exit(1);
  }
  const prof = data.profile || {};
  const expiration = prof.status_expiration || 0;
  const output = {
    user_id: userId,
    status_emoji: prof.status_emoji || '',
    status_text: prof.status_text || '',
    status_expiration: expiration ? new Date(expiration * 1000).toISOString() : null,
  };
  console.log(yaml.dump(output, { sortKeys: false }));
}

// ── status-set ───────────────────────────────────────────────────────────────
export const helpStatusSet = `user status-set

Usage:
    slack-chat user status-set <text> [--emoji :x:] [--minutes N]

Description:
    Set your own Slack status. Empty text clears it.

Options:
    --emoji, -e    Status emoji, e.g. :calendar:
    --minutes, -m  Expiry in minutes from now (0 = no expiry)

Examples:
    slack-chat user status-set "In a meeting" --emoji :calendar: --minutes 60
    slack-chat user status-set ""`;

const STATUS_SET_SPEC = {
  emoji: { aliases: ['-e'], type: 'string', default: '' },
  minutes: { aliases: ['-m'], type: 'int', default: 0 },
};

export async function runStatusSet(argv) {
  const { opts, positionals } = parseArgs(argv, STATUS_SET_SPEC);
  const text = positionals[0] ?? '';
  const expiration = opts.minutes > 0 ? Math.floor(Date.now() / 1000) + opts.minutes * 60 : 0;

  const data = await slackApi('users.profile.set', {
    profile: { status_text: text, status_emoji: opts.emoji, status_expiration: expiration },
  });
  if (!data.ok) {
    process.stderr.write(`error: ${data.error || 'unknown'}\n`);
    process.exit(1);
  }
  const prof = data.profile || {};
  const output = {
    ok: true,
    status_emoji: prof.status_emoji ?? opts.emoji,
    status_text: prof.status_text ?? text,
    status_expiration: expiration ? new Date(expiration * 1000).toISOString() : null,
  };
  console.log(yaml.dump(output, { sortKeys: false }));
}

export { truncate as _truncate };
