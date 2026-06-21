// config.yaml reader (slack_url, defaults). Company-agnostic.
import { readFileSync, existsSync } from 'node:fs';
import yaml from 'js-yaml';
import { CONFIG_FILE } from './paths.mjs';

let _cache = null;

export function loadConfig() {
  if (_cache) return _cache;
  let data = {};
  try {
    if (existsSync(CONFIG_FILE)) {
      data = yaml.load(readFileSync(CONFIG_FILE, 'utf8')) || {};
    }
  } catch {
    data = {};
  }
  _cache = data;
  return data;
}

/** Workspace URL to open the browser at during login. */
export function slackUrl() {
  return loadConfig().slack_url || 'https://app.slack.com/';
}
