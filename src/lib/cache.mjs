// User/channel metadata cache (db/cache/*.yml). Cache-first lookups with
// API fallback handled by resolve.mjs. Mirrors src2/storage.py cache funcs.
import { readFileSync, writeFileSync, existsSync, mkdirSync, statSync } from 'node:fs';
import yaml from 'js-yaml';
import { USERS_CACHE, CHANNELS_CACHE, CACHE_DIR } from './paths.mjs';

const _memo = new Map(); // file -> { sig, data }

function loadCache(file) {
  let sig = null;
  try {
    const st = statSync(file);
    sig = `${st.mtimeMs}:${st.size}`;
  } catch {
    return {};
  }
  const cached = _memo.get(file);
  if (cached && cached.sig === sig) return cached.data;
  let data = {};
  try {
    data = yaml.load(readFileSync(file, 'utf8')) || {};
  } catch {
    data = {};
  }
  _memo.set(file, { sig, data });
  return data;
}

function saveCache(file, data) {
  mkdirSync(CACHE_DIR, { recursive: true });
  writeFileSync(file, yaml.dump(data, { sortKeys: false, lineWidth: -1 }), 'utf8');
  try {
    const st = statSync(file);
    _memo.set(file, { sig: `${st.mtimeMs}:${st.size}`, data });
  } catch {
    _memo.delete(file);
  }
}

const nowIso = () => new Date().toISOString();

// ── Users ────────────────────────────────────────────────────────────────────
export function getCachedUser(userId) {
  return loadCache(USERS_CACHE)[userId] || null;
}
export function cacheUser(userId, userData) {
  const cache = loadCache(USERS_CACHE);
  cache[userId] = { ...userData, _cached_at: nowIso() };
  saveCache(USERS_CACHE, cache);
}
export function evictCachedUser(userId) {
  const cache = loadCache(USERS_CACHE);
  if (userId in cache) {
    delete cache[userId];
    saveCache(USERS_CACHE, cache);
    return true;
  }
  return false;
}
export function allCachedUsers() {
  return loadCache(USERS_CACHE);
}

// ── Channels ─────────────────────────────────────────────────────────────────
export function getCachedChannel(channelId) {
  return loadCache(CHANNELS_CACHE)[channelId] || null;
}
export function cacheChannel(channelId, channelData) {
  const cache = loadCache(CHANNELS_CACHE);
  cache[channelId] = { ...channelData, _cached_at: nowIso() };
  saveCache(CHANNELS_CACHE, cache);
}
export function evictCachedChannel(channelId) {
  const cache = loadCache(CHANNELS_CACHE);
  if (channelId in cache) {
    delete cache[channelId];
    saveCache(CHANNELS_CACHE, cache);
    return true;
  }
  return false;
}
export function allCachedChannels() {
  return loadCache(CHANNELS_CACHE);
}

// ── Lookups ──────────────────────────────────────────────────────────────────
export function findChannelByName(name) {
  const n = name.replace(/^#/, '').toLowerCase();
  const cache = loadCache(CHANNELS_CACHE);
  for (const ch of Object.values(cache)) {
    if (
      (ch.name || '').toLowerCase() === n ||
      (ch.name_normalized || '').toLowerCase() === n
    ) {
      return ch;
    }
  }
  return null;
}

export function findChannelsByKeyword(keyword) {
  const k = keyword.toLowerCase();
  const matches = Object.values(loadCache(CHANNELS_CACHE)).filter((ch) =>
    (ch.name || '').toLowerCase().includes(k)
  );
  matches.sort((a, b) => (a.name || '').localeCompare(b.name || ''));
  return matches;
}

export function findUserByName(name) {
  const n = name.replace(/^@/, '').toLowerCase();
  const cache = loadCache(USERS_CACHE);
  for (const u of Object.values(cache)) {
    const display = (u.profile && u.profile.display_name) || '';
    if (
      (u.name || '').toLowerCase() === n ||
      (u.real_name || '').toLowerCase() === n ||
      display.toLowerCase() === n
    ) {
      return u;
    }
  }
  return null;
}

function deepContains(value, keyword) {
  if (typeof value === 'string') return value.toLowerCase().includes(keyword);
  if (Array.isArray(value)) return value.some((v) => deepContains(v, keyword));
  if (value && typeof value === 'object') {
    return Object.values(value).some((v) => deepContains(v, keyword));
  }
  return false;
}

export function findUsersByKeyword(keyword) {
  const k = keyword.toLowerCase();
  const matches = Object.values(loadCache(USERS_CACHE)).filter((u) => deepContains(u, k));
  matches.sort((a, b) =>
    (a.real_name || a.name || '').localeCompare(b.real_name || b.name || '')
  );
  return matches;
}

export { existsSync as _existsSync };
