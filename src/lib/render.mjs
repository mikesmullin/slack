// Message body rendering: clean text, inline <@U…> expansion, image summaries.
// Mirrors src2/commands/client.py text helpers.
import { rgb, muted } from './color.mjs';
import { getUserNameById } from './resolve.mjs';
import { parseSlackPermalink } from './format.mjs';

export function cleanText(text) {
  return (text || '').replace(/\n/g, ' ').trim();
}

/** Convert Markdown links [label](url) to Slack mrkdwn <url|label>. */
export function normalizeMarkdownLinks(text) {
  if (!text) return text;
  return text.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, (_m, label, url) => `<${url}|${label}>`);
}

function sizeKb(bytes) {
  let size = 0;
  try {
    size = parseInt(bytes || 0, 10);
  } catch {
    size = 0;
  }
  const kb = size > 0 ? Math.max(1, Math.round(size / 1024)) : 0;
  return kb ? `${kb}kb` : 'unknown';
}

function imageSummary(message) {
  const files = message && message.files;
  if (!Array.isArray(files) || !files.length) return '';
  const imageTypes = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'tiff', 'svg']);
  const parts = [];
  for (const f of files) {
    if (!f || typeof f !== 'object') continue;
    const mimetype = String(f.mimetype || '').toLowerCase();
    const filetype = String(f.filetype || '').toLowerCase();
    if (!mimetype.startsWith('image/') && !imageTypes.has(filetype)) continue;
    const url = String(
      f.url_private || f.url_private_download || f.permalink || f.id || 'image'
    );
    parts.push(`(image: ${url}, size: ${sizeKb(f.size)})`);
  }
  return parts.join(' ');
}

async function inlineUserRef(userId, userCache) {
  if (!userCache.has(userId)) {
    const [name] = await getUserNameById(userId);
    userCache.set(userId, name || userId);
  }
  const name = userCache.get(userId);
  return (
    `${muted('<')}${rgb(name, 255, 199, 95)}${muted('|')}` +
    `${rgb('@' + userId, 127, 219, 202)}${muted('>')}`
  );
}

/** Expand inline user refs and colorize a message body. */
export async function formatMessageText(text, userCache, message = null) {
  const clean = cleanText(text);
  const pattern = /<@([UW][A-Z0-9]{8,})(?:\|[^>]+)?>/g;
  const img = imageSummary(message || {});

  let base;
  if (!pattern.test(clean)) {
    base = rgb(clean, 218, 224, 232);
  } else {
    pattern.lastIndex = 0;
    const out = [];
    let cursor = 0;
    let m;
    while ((m = pattern.exec(clean)) !== null) {
      if (m.index > cursor) out.push(rgb(clean.slice(cursor, m.index), 218, 224, 232));
      out.push(await inlineUserRef(m[1], userCache));
      cursor = m.index + m[0].length;
    }
    if (cursor < clean.length) out.push(rgb(clean.slice(cursor), 218, 224, 232));
    base = out.join('');
  }

  if (!img) return base;
  const suffix = rgb(img, 169, 188, 255);
  if (!clean.trim()) return suffix;
  return `${base} ${suffix}`;
}

/** "Real Name (@U123)" or a bot/username fallback. */
export async function displayUser(message) {
  const userId = message.user;
  if (userId) {
    const [name] = await getUserNameById(userId);
    return `${name || userId} (@${userId})`;
  }
  return message.username || message.bot_id || 'unknown';
}

/** Build the event id for a search match (thread_ts via permalink fallback). */
export function searchResultEventId(message, { formatEventId }) {
  const ts = message.ts || '';
  const channel = message.channel || {};
  const channelId = channel.id || message.channel_id || '';
  let threadTs = message.thread_ts;
  if (!threadTs) {
    const parsed = parseSlackPermalink(message.permalink || '');
    if (parsed) threadTs = parsed.thread_ts;
  }
  if (channelId && ts) return formatEventId(channelId, ts, threadTs);
  return ts || 'unknown';
}
