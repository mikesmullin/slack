// .tokens.yaml load/save. Holds xoxc token + d cookie + workspace_url.
import { readFileSync, writeFileSync, existsSync, chmodSync } from 'node:fs';
import yaml from 'js-yaml';
import { TOKENS_FILE } from './paths.mjs';

/** Load credentials. Returns {} when absent/unparseable. */
export function loadTokens() {
  try {
    if (existsSync(TOKENS_FILE)) {
      const data = yaml.load(readFileSync(TOKENS_FILE, 'utf8')) || {};
      if (data.token) return data;
    }
  } catch {
    /* ignore */
  }
  return {};
}

/** Merge-write credentials, preserving existing extra fields. 0600 perms. */
export function saveTokens(patch) {
  const existing = loadTokens();
  const data = { ...existing, ...patch };
  writeFileSync(TOKENS_FILE, yaml.dump(data, { sortKeys: false }), 'utf8');
  try {
    chmodSync(TOKENS_FILE, 0o600);
  } catch {
    /* ignore */
  }
  return data;
}

/** True if a token string is present. */
export function hasToken(tokens = loadTokens()) {
  return Boolean(tokens.token);
}
