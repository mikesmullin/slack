// SSO login + credential capture via the reusable `browser login` command.
// `browser login` opens a headed browser, waits for SSO to settle, and captures
// the requested cookies / localStorage / eval values. We ask it for the Slack
// `d` cookie and the `xoxc` token (extracted from the localConfig_v2 blob),
// validate with auth.test, write .tokens.yaml, and the browser closes itself.
import { spawnSync } from 'node:child_process';
import { slackUrl } from './config.mjs';
import { saveTokens } from './tokens.mjs';
import { slackApi } from './api.mjs';

// Arrow-fn that extracts the xoxc token from localStorage.localConfig_v2.
const TOKEN_EVAL =
  '() => { try { const c = JSON.parse(localStorage.localConfig_v2); const t = document.location.pathname.match(/^\\/client\\/([A-Z0-9]+)/)[1]; return c.teams[t].token; } catch(e) { try { const c = JSON.parse(localStorage.localConfig_v2); const teams = c.teams || {}; const f = Object.values(teams)[0]; return f ? f.token : null; } catch(e2){ return null; } } }';

/** Run the full login flow via `browser login`. Returns the saved tokens. */
export async function browserLogin({ quiet = false } = {}) {
  const log = (m) => {
    if (!quiet) process.stderr.write(m + '\n');
  };

  log('Opening browser for Slack SSO (via the `browser` tool)…');
  const args = [
    'login',
    '--url', slackUrl(),
    '--match', '*slack.com/client*',
    '--cookie', 'd',
    '--cookie-domain', 'slack.com',
    '--eval', `token=${TOKEN_EVAL}`,
  ];
  const res = spawnSync('browser', args, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'inherit'] });
  if (res.error) {
    throw new Error(`could not run \`browser\` tool: ${res.error.message}`);
  }
  if (res.status !== 0) {
    throw new Error('`browser login` did not complete successfully');
  }

  let captured;
  try {
    // stdout should be pure JSON, but extract the object defensively in case
    // any stray output leaks onto stdout.
    const raw = res.stdout || '';
    const start = raw.indexOf('{');
    const end = raw.lastIndexOf('}');
    const slice = start !== -1 && end > start ? raw.slice(start, end + 1) : raw;
    captured = JSON.parse(slice);
  } catch {
    throw new Error(`could not parse \`browser login\` output: ${res.stdout.slice(0, 200)}`);
  }

  const token = captured.eval && captured.eval.token;
  const cookie = captured.cookies && captured.cookies.d;
  if (!token) throw new Error('could not capture xoxc token from the browser');
  if (!cookie) throw new Error("could not capture the 'd' session cookie from the browser");

  // Validate before persisting.
  const probe = await slackApi('auth.test', {}, { tokens: { token, cookie, workspace_url: 'https://slack.com' } });
  if (!probe.ok) throw new Error(`captured credentials failed auth.test: ${probe.error}`);

  const workspaceUrl = (probe.url || '').replace(/\/+$/, '') || null;
  const isEnterprise = probe.enterprise_id != null || (workspaceUrl || '').includes('enterprise.slack.com');

  return saveTokens({
    token,
    cookie,
    workspace_url: workspaceUrl,
    is_enterprise: isEnterprise,
    enterprise_id: probe.enterprise_id || probe.team_id || '',
    gateway_server: null, // force ws re-probe with new token
    refreshed_at: new Date().toISOString(),
  });
}

