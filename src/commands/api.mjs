// api: call any Slack endpoint directly with saved credentials.
import yaml from 'js-yaml';
import { parseArgs } from '../lib/args.mjs';
import { slackApi } from '../lib/api.mjs';

export const help = `api

Usage:
    slack-chat api <endpoint> [--params JSON] [--data JSON] [--method GET|POST] [--yaml]

Description:
    Call any Slack API endpoint directly using saved credentials.

Options:
    --params, -p   Query/form parameters as JSON, e.g. '{"limit": 10}'
    --data, -d     Additional POST body parameters as JSON (merged with --params)
    --method, -X   HTTP method: GET or POST (default POST)
    --yaml         Output as YAML instead of JSON

Examples:
    slack-chat api auth.test
    slack-chat api users.list --params '{"limit": 10}'
    slack-chat api chat.postMessage --data '{"channel":"C…","text":"Hello"}'
    slack-chat api conversations.list --method GET --params '{"limit": 5}'`;

const SPEC = {
  params: { aliases: ['-p'], type: 'string', default: null },
  data: { aliases: ['-d'], type: 'string', default: null },
  method: { aliases: ['-X'], type: 'string', default: 'POST' },
  yaml: { aliases: [], type: 'bool', default: false },
};

export async function run(argv) {
  const { opts, positionals } = parseArgs(argv, SPEC);
  const endpoint = positionals[0];
  if (!endpoint) {
    process.stderr.write('Error: api requires an <endpoint>.\n');
    process.exit(1);
  }

  const merged = {};
  for (const [flag, raw] of [['--params', opts.params], ['--data', opts.data]]) {
    if (raw) {
      try {
        Object.assign(merged, JSON.parse(raw));
      } catch (e) {
        process.stderr.write(`Error: invalid ${flag} JSON: ${e.message}\n`);
        process.exit(1);
      }
    }
  }

  const result = await slackApi(endpoint, merged, { method: opts.method });
  if (opts.yaml) process.stdout.write(yaml.dump(result, { sortKeys: false }));
  else console.log(JSON.stringify(result, null, 2));
}
