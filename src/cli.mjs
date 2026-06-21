#!/usr/bin/env bun
// slack-chat — CLI entry. Direct HTTP Slack API; offline-first credentials.
import process from 'node:process';
import { SlackAuthError, SlackApiError } from './lib/api.mjs';
import { TOP_HELP, helpFor } from './help.mjs';

// Registry: command path -> { load, group }. Subcommand groups nest one level.
const COMMANDS = {
  'read-message': () => import('./commands/read.mjs'),
  resolve: () => import('./commands/resolve.mjs'),
  search: () => import('./commands/search.mjs'),
  activity: () => import('./commands/activity.mjs'),
  'post-message': () => import('./commands/post.mjs').then((m) => ({ run: m.runPost, help: m.helpPost })),
  reply: () => import('./commands/post.mjs').then((m) => ({ run: m.runReply, help: m.helpReply })),
  react: () => import('./commands/react.mjs').then((m) => ({ run: m.runReact, help: m.helpReact })),
  'post-reaction': () => import('./commands/react.mjs').then((m) => ({ run: m.runPostReaction, help: m.helpPostReaction })),
  api: () => import('./commands/api.mjs'),
  listen: () => import('./commands/listen.mjs'),
};

// Subcommand groups: group -> { sub -> loader }.
const GROUPS = {
  channel: {
    describe: () => import('./commands/channel.mjs').then((m) => pick(m, 'Describe')),
    tab: () => import('./commands/channel.mjs').then((m) => pick(m, 'Tab')),
    resolve: () => import('./commands/channel.mjs').then((m) => pick(m, 'Resolve')),
    list: () => import('./commands/channel.mjs').then((m) => pick(m, 'List')),
    find: () => import('./commands/channel.mjs').then((m) => pick(m, 'Find')),
    pending: () => import('./commands/channel.mjs').then((m) => pick(m, 'Pending')),
  },
  user: {
    'status-get': () => import('./commands/user.mjs').then((m) => pick(m, 'StatusGet')),
    'status-set': () => import('./commands/user.mjs').then((m) => pick(m, 'StatusSet')),
    list: () => import('./commands/user.mjs').then((m) => pick(m, 'List')),
    find: () => import('./commands/user.mjs').then((m) => pick(m, 'Find')),
  },
  auth: {
    status: () => import('./commands/auth.mjs').then((m) => pick(m, 'Status')),
    login: () => import('./commands/auth.mjs').then((m) => pick(m, 'Login')),
  },
};

function pick(mod, suffix) {
  return { run: mod[`run${suffix}`], help: mod[`help${suffix}`] };
}

function isHelpFlag(a) {
  return a === '--help' || a === '-h' || a === 'help';
}

async function main() {
  const argv = process.argv.slice(2);
  if (argv.length === 0 || isHelpFlag(argv[0])) {
    console.log(TOP_HELP);
    return;
  }

  const cmd = argv[0];

  // Subcommand group?
  if (GROUPS[cmd]) {
    const sub = argv[1];
    if (!sub || isHelpFlag(sub)) {
      console.log(helpFor([cmd]));
      return;
    }
    const loader = GROUPS[cmd][sub];
    if (!loader) {
      process.stderr.write(`Unknown subcommand: ${cmd} ${sub}\n`);
      process.exit(2);
    }
    const rest = argv.slice(2);
    const mod = await loader();
    if (rest.some(isHelpFlag) && mod.help) {
      console.log(mod.help);
      return;
    }
    await mod.run(rest);
    return;
  }

  const loader = COMMANDS[cmd];
  if (!loader) {
    process.stderr.write(`Unknown command: ${cmd}\nRun \`slack-chat --help\` for usage.\n`);
    process.exit(2);
  }
  const rest = argv.slice(1);
  const mod = await loader();
  if (rest.some(isHelpFlag) && mod.help) {
    console.log(mod.help);
    return;
  }
  await mod.run(rest);
}

main().catch((err) => {
  if (err instanceof SlackAuthError) {
    process.stderr.write(`Error: ${err.message}\n`);
    process.exit(1);
  }
  if (err instanceof SlackApiError) {
    process.stderr.write(`Error: ${err.message}\n`);
    process.exit(1);
  }
  process.stderr.write(`Error: ${err && err.message ? err.message : err}\n`);
  process.exit(1);
});
