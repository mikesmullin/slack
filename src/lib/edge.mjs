// Slack "edge" API user discovery (edgeapi.slack.com). This is what the Slack
// client uses for people search / mention autocomplete: it does fuzzy matching
// across name/display-name/real-name AND includes DEACTIVATED users — neither of
// which the workspace `users.list` / cache surface well.
import { loadTokens } from './tokens.mjs';
import { slackApi } from './api.mjs';

let _orgId = null;

async function orgId(tokens) {
  if (tokens.enterprise_id) return tokens.enterprise_id;
  if (tokens.team_id) return tokens.team_id;
  if (_orgId) return _orgId;
  try {
    const info = await slackApi('auth.test', {}, { tokens });
    _orgId = info.enterprise_id || info.team_id || null;
  } catch {
    _orgId = null;
  }
  return _orgId;
}

/**
 * Fuzzy-search users via the edge cache API (includes deactivated users).
 * Returns an array of full user objects (same shape as users.info), or [] on
 * failure. Never throws.
 * @param {string} query
 * @param {{count?:number, fuzz?:number}} [opts]
 */
export async function searchUsersEdge(query, { count = 20, fuzz = 2 } = {}) {
  const tokens = loadTokens();
  if (!tokens.token || !query) return [];
  const org = await orgId(tokens);
  if (!org) return [];

  try {
    const res = await fetch(`https://edgeapi.slack.com/cache/${org}/users/search`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(tokens.cookie ? { Cookie: `d=${tokens.cookie}` } : {}),
      },
      body: JSON.stringify({ token: tokens.token, query, count, fuzz }),
    });
    const data = await res.json();
    if (!data.ok) return [];
    return data.results || [];
  } catch {
    return [];
  }
}
