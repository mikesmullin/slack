// .tokens.yaml load/save. Holds xoxc token + d cookie + workspace_url.
import { loadConfig } from './config.mjs';

/** Load credentials. Returns {} when absent/unparseable. */
export function loadTokens() {
  const config = loadConfig();
  return {
    token: process.env.SLACK_TOKEN || '',
    cookie: process.env.SLACK_COOKIE || '',
    workspace_url: config.workspace_url || config.slack_url || 'https://slack.com',
    is_enterprise: config.is_enterprise || false,
    enterprise_id: config.enterprise_id || '',
  };
}

/** Merge-write credentials, preserving existing extra fields. 0600 perms. */
export function saveTokens(patch) {
  throw new Error('Slack credentials are managed by Tokenman; run `tokenman refresh slack` instead.');
}

/** True if a token string is present. */
export function hasToken(tokens = loadTokens()) {
  return Boolean(tokens.token);
}
