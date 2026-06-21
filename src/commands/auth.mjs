// auth subcommands: status, login.
import { green, muted, yellow, rgb } from '../lib/color.mjs';
import { loadTokens } from '../lib/tokens.mjs';
import { slackApi } from '../lib/api.mjs';
import { browserLogin } from '../lib/login.mjs';

// ── status ───────────────────────────────────────────────────────────────────
export const helpStatus = `auth status

Usage:
    slack-chat auth status

Description:
    Show whether credentials are present and still valid (live auth.test),
    plus the workspace URL and enterprise flag.`;

export async function runStatus() {
  const tokens = loadTokens();
  const has = Boolean(tokens.token);
  console.log(`${muted('token')}:        ${has ? green('present') : rgb('missing', 224, 108, 117)}`);
  if (tokens.refreshed_at) console.log(`${muted('refreshed_at')}: ${tokens.refreshed_at}`);
  if (!has) {
    console.log(muted('Run `slack-chat auth login` to capture a session.'));
    process.exit(1);
  }

  let probe;
  try {
    probe = await slackApi('auth.test', {});
  } catch (e) {
    console.log(`${muted('auth.test')}:    ${rgb('failed', 224, 108, 117)} (${e.message})`);
    process.exit(1);
  }
  if (probe.ok) {
    console.log(`${muted('auth.test')}:    ${green('ok')}`);
    console.log(`${muted('workspace')}:    ${probe.url || tokens.workspace_url || ''}`);
    console.log(`${muted('user')}:         ${probe.user_id || ''}`);
    console.log(`${muted('enterprise')}:   ${tokens.is_enterprise ? yellow('true') : 'false'}`);
  } else {
    console.log(`${muted('auth.test')}:    ${rgb(probe.error || 'failed', 224, 108, 117)}`);
    console.log(muted('Run `slack-chat auth login` to refresh.'));
    process.exit(1);
  }
}

// ── login ────────────────────────────────────────────────────────────────────
export const helpLogin = `auth login

Usage:
    slack-chat auth login

Description:
    Open a headed browser (via the \`browser\` tool), let you complete SSO,
    capture + validate the session credentials, write .tokens.yaml, and close
    the browser. Requires the \`browser\` tool to be installed.`;

export async function runLogin() {
  const saved = await browserLogin({ quiet: false });
  console.log(`${green('✅ Session credentials saved')}`);
  console.log(`   ${muted('workspace')}:  ${saved.workspace_url || ''}`);
  console.log(`   ${muted('enterprise')}: ${saved.is_enterprise ? 'true' : 'false'}`);
  console.log(`   ${muted('cookie')}:     ${saved.cookie ? '✅' : '❌'}`);
}
