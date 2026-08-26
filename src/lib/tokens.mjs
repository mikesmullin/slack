// Tokenman/Passman-backed Slack credential access.
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

/**
 * Session metadata is intentionally not persisted locally. Credentials and
 * their lifecycle are owned by Tokenman and Passman.
 */
export function saveTokens() {
  return null;
}

/** True if a token string is present. */
export function hasToken(tokens = loadTokens()) {
  return Boolean(tokens.token);
}
