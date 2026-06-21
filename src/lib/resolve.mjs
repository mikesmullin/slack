// User/channel resolution: cache-first, API fallback, write-back.
// Mirrors src2/utils/resolution.py.
import { slackApi } from './api.mjs';
import {
  getCachedUser,
  cacheUser,
  getCachedChannel,
  cacheChannel,
  findChannelByName,
} from './cache.mjs';

const _userMemo = new Map();

/** Resolve a user id to {real_name, display_name, email}. Cache-first. */
export async function getUserInfo(userId) {
  if (_userMemo.has(userId)) return _userMemo.get(userId);

  const cached = getCachedUser(userId);
  if (cached) {
    const profile = cached.profile || {};
    const info = {
      real_name: profile.real_name || cached.real_name || userId,
      display_name: profile.display_name || cached.name || userId,
      email: profile.email,
    };
    _userMemo.set(userId, info);
    return info;
  }

  try {
    const data = await slackApi('users.info', { user: userId });
    if (data.ok) {
      const user = data.user || {};
      cacheUser(userId, user);
      const profile = user.profile || {};
      const info = {
        real_name: profile.real_name || user.real_name || userId,
        display_name: profile.display_name || user.name || userId,
        email: profile.email,
      };
      _userMemo.set(userId, info);
      return info;
    }
  } catch {
    /* ignore */
  }
  return { real_name: userId, display_name: userId };
}

/** Return [displayName, fullUserData]. Cache-first. */
export async function getUserNameById(userId) {
  const cached = getCachedUser(userId);
  if (cached) {
    const real = (cached.real_name || '').trim();
    if (real) return [real, cached];
    const disp = ((cached.profile && cached.profile.display_name) || '').trim();
    if (disp) return [disp, cached];
    return [cached.name || userId, cached];
  }
  try {
    const data = await slackApi('users.info', { user: userId });
    if (data.ok) {
      const user = data.user || {};
      cacheUser(userId, user);
      const real = (user.real_name || '').trim();
      if (real) return [real, user];
      const disp = ((user.profile && user.profile.display_name) || '').trim();
      if (disp) return [disp, user];
      return [user.name || userId, user];
    }
  } catch {
    /* ignore */
  }
  return [userId, {}];
}

/** Return [channelName, fullChannelData]. Cache-first. */
export async function getChannelNameById(channelId, { refresh = false } = {}) {
  if (!refresh) {
    const cached = getCachedChannel(channelId);
    if (cached) return [cached.name || channelId, cached];
  }
  try {
    const data = await slackApi('conversations.info', { channel: channelId });
    if (data.ok) {
      const channel = data.channel || {};
      cacheChannel(channelId, channel);
      return [channel.name || channelId, channel];
    }
  } catch {
    /* ignore */
  }
  return [channelId, {}];
}

/** Resolve a channel name/#name/ID to {id, name, ...}. Cache-first (offline). */
export function resolveChannel(nameOrId) {
  if (/^[CDG][A-Z0-9]{7,}$/.test(nameOrId)) {
    return { id: nameOrId, name: nameOrId };
  }
  const name = nameOrId.replace(/^#/, '');
  const cached = findChannelByName(name);
  if (cached) return { ...cached, id: cached.id, name: cached.name };
  return { id: nameOrId, name: nameOrId };
}
