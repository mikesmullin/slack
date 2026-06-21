// Direct Slack web API transport over fetch. No browser, no server.
// Mirrors src2/utils/api.py call_api_direct.
import { loadTokens } from './tokens.mjs';

export class SlackAuthError extends Error {}
export class SlackApiError extends Error {
  constructor(message, payload) {
    super(message);
    this.payload = payload;
  }
}

function encodeForm(params) {
  const form = new URLSearchParams();
  for (const [k, v] of Object.entries(params || {})) {
    if (v === undefined || v === null) continue;
    if (typeof v === 'object') form.append(k, JSON.stringify(v));
    else form.append(k, String(v));
  }
  return form;
}

/**
 * Call a Slack API endpoint directly.
 * @param {string} endpoint e.g. "conversations.history"
 * @param {object} params form params (objects are JSON-encoded)
 * @param {object} [opts] { tokens, method }
 * @returns {Promise<object>} parsed JSON response
 */
export async function slackApi(endpoint, params = {}, opts = {}) {
  const tokens = opts.tokens || loadTokens();
  const token = tokens.token;
  if (!token) {
    throw new SlackAuthError(
      'No credentials in .tokens.yaml. Run `slack-chat login` to capture a session.'
    );
  }
  const base = (tokens.workspace_url || 'https://slack.com').replace(/\/+$/, '');
  const method = (opts.method || 'POST').toUpperCase();

  const headers = {
    Authorization: `Bearer ${token}`,
    'Content-Type': 'application/x-www-form-urlencoded',
  };
  if (tokens.cookie) headers.Cookie = `d=${tokens.cookie}`;

  const form = encodeForm({ token, ...params });
  const url = `${base}/api/${endpoint}`;

  let res;
  try {
    res = await fetch(url, {
      method,
      headers,
      body: method === 'GET' ? undefined : form,
    });
  } catch (e) {
    throw new SlackApiError(`network error calling ${endpoint}: ${e.message}`);
  }

  let data;
  const text = await res.text();
  try {
    data = JSON.parse(text);
  } catch {
    throw new SlackApiError(`non-JSON response from ${endpoint}: ${text.slice(0, 200)}`);
  }
  if (typeof data === 'string') {
    try {
      data = JSON.parse(data);
    } catch {
      /* leave as-is */
    }
  }

  if (data && data.ok === false) {
    const err = String(data.error || '');
    if (err === 'invalid_auth' || err === 'not_authed' || err === 'token_revoked') {
      throw new SlackAuthError(
        `Slack rejected credentials (${err}). Run \`slack-chat login\` to refresh.`
      );
    }
  }
  return data;
}

/** Throw if a response payload is not ok. */
export function requireOk(data, endpoint) {
  if (data && data.ok) return data;
  throw new SlackApiError(
    `${endpoint} failed: ${(data && data.error) || 'unknown error'}`,
    data
  );
}
