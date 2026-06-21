// Centralized filesystem paths for slack-chat.
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
// src/lib/paths.mjs -> repo root is two levels up.
export const ROOT = join(HERE, '..', '..');

export const TOKENS_FILE = join(ROOT, '.tokens.yaml');
export const CONFIG_FILE = join(ROOT, 'config.yaml');
export const DB_DIR = join(ROOT, 'db');
export const CACHE_DIR = join(DB_DIR, 'cache');
export const USERS_CACHE = join(CACHE_DIR, 'users.yml');
export const CHANNELS_CACHE = join(CACHE_DIR, 'channels.yml');
