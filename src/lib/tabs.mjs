// Channel tab resolution: properties.tabs/tabz + files.info + bookmarks folders.
// Mirrors src2/commands/client.py _resolve_channel_tabs / _select_channel_tab.
import { slackApi } from './api.mjs';

function collectRawTabs(props) {
  const raw = [];
  for (const key of ['tabs', 'tabz']) {
    const value = props[key];
    if (Array.isArray(value)) raw.push(...value.filter((t) => t && typeof t === 'object'));
  }
  return raw;
}

function dedupeTabs(rawTabs, meetingNotesFileId) {
  const deduped = [];
  const seen = new Set();
  for (const tab of rawTabs) {
    const tabId = String(tab.id || '');
    const tabType = String(tab.type || '');
    const label = String(tab.label || '');
    const data = tab.data || {};
    const folderBookmarkId = String(data.folder_bookmark_id || '');
    let fileId = String(data.file_id || '');
    if (!fileId && tabType === 'channel_canvas' && meetingNotesFileId) fileId = String(meetingNotesFileId);

    const normalizedId = tabId || tabType;
    const key = `${normalizedId}|${tabType}|${fileId}`;
    if (seen.has(key)) continue;
    seen.add(key);

    const name = label || (tabType === 'files' ? 'Files' : tabType || tabId);
    deduped.push({
      index: deduped.length + 1,
      id: tabId,
      type: tabType,
      label: label || (tabType === 'files' ? 'Files' : ''),
      file_id: fileId,
      folder_bookmark_id: folderBookmarkId,
      folder_path: '',
      path: name,
      name,
      url: null,
      download_url: null,
      permalink: null,
    });
  }
  return deduped;
}

async function enrichFromFile(tab, fileId, fileCache) {
  if (!fileId) return;
  if (!(fileId in fileCache)) {
    const info = await slackApi('files.info', { file: fileId });
    fileCache[fileId] = info && typeof info === 'object' ? info : {};
  }
  const info = fileCache[fileId];
  if (!info.ok) return;
  const f = info.file && typeof info.file === 'object' ? info.file : {};
  const title = (f.title || f.name || '').trim();
  if (title) {
    tab.name = title;
    if (!tab.label) tab.label = title;
    tab.path = tab.folder_path ? `${tab.folder_path}/${title}` : title;
  }
  tab.url = f.url_private;
  tab.download_url = f.url_private_download;
  tab.permalink = f.permalink;
}

async function expandBookmarkFolders(channelId, deduped, fileCache) {
  const bm = await slackApi('bookmarks.list', { channel_id: channelId });
  const bookmarks = bm.ok ? bm.bookmarks || [] : [];
  const childrenByParent = new Map();
  for (const b of bookmarks) {
    if (!b || typeof b !== 'object') continue;
    const parentId = String(b.parent_id || '');
    if (!parentId) continue;
    if (!childrenByParent.has(parentId)) childrenByParent.set(parentId, []);
    childrenByParent.get(parentId).push(b);
  }
  for (const items of childrenByParent.values()) {
    items.sort((a, b) =>
      `${a.rank || ''}${(a.title || '').toLowerCase()}`.localeCompare(
        `${b.rank || ''}${(b.title || '').toLowerCase()}`
      )
    );
  }

  const existing = new Set(
    deduped.map((t) => `${t.type || ''}|${t.id || ''}|${t.file_id || ''}|${t.folder_path || ''}`)
  );

  const walk = async (folderBookmarkId, folderPath) => {
    for (const b of childrenByParent.get(folderBookmarkId) || []) {
      const bmId = String(b.id || '');
      const bmType = String(b.type || '');
      const bmTitle = String(b.title || b.entity_id || bmId || bmType);
      if (bmType === 'folder') {
        const next = folderPath ? `${folderPath}/${bmTitle}` : bmTitle;
        await walk(bmId, next);
        continue;
      }
      const entityId = String(b.entity_id || '');
      const fileId = entityId.startsWith('F') ? entityId : '';
      const entry = {
        index: deduped.length + 1,
        id: bmId,
        bookmark_id: bmId,
        type: bmType || 'bookmark',
        label: bmTitle,
        file_id: fileId,
        folder_bookmark_id: folderBookmarkId,
        folder_path: folderPath,
        path: folderPath ? `${folderPath}/${bmTitle}` : bmTitle,
        name: bmTitle,
        url: b.link,
        download_url: null,
        permalink: b.link,
      };
      await enrichFromFile(entry, fileId, fileCache);
      const key = `${entry.type || ''}|${entry.id || ''}|${entry.file_id || ''}|${entry.folder_path || ''}`;
      if (existing.has(key)) continue;
      existing.add(key);
      deduped.push(entry);
    }
  };

  const folderRoots = deduped.filter(
    (t) => String(t.type || '') === 'folder' && String(t.folder_bookmark_id || '')
  );
  for (const root of folderRoots) {
    await walk(String(root.folder_bookmark_id || ''), String(root.label || root.name || ''));
  }

  await addVirtualFilesTab(channelId, deduped, existing);
}

async function addVirtualFilesTab(channelId, deduped, existing) {
  const hasFilesTab = deduped.some((t) => String(t.type || '') === 'files');
  const hasFolderItems = deduped.some((t) => String(t.folder_path || ''));
  if (!hasFilesTab || hasFolderItems) return;

  const filesData = await slackApi('files.list', { channel: channelId, count: 200 });
  const files = filesData.ok ? filesData.files || [] : [];
  for (const f of files) {
    if (!f || typeof f !== 'object') continue;
    const fileId = String(f.id || '');
    if (!fileId) continue;
    const mimetype = String(f.mimetype || '').toLowerCase();
    const prettyType = String(f.pretty_type || '').toLowerCase();
    if (mimetype !== 'application/vnd.slack-docs' && prettyType !== 'canvas') continue;
    const title = String(f.title || f.name || fileId);
    const entry = {
      index: deduped.length + 1,
      id: fileId,
      bookmark_id: '',
      type: 'files_canvas',
      label: title,
      file_id: fileId,
      folder_bookmark_id: '',
      folder_path: 'Files',
      path: `Files/${title}`,
      name: title,
      url: f.url_private,
      download_url: f.url_private_download,
      permalink: f.permalink,
    };
    const key = `${entry.type}|${entry.id}|${entry.file_id}|${entry.folder_path}`;
    if (existing.has(key)) continue;
    existing.add(key);
    deduped.push(entry);
  }
}

/** Build a stable, enriched tab list for a channel object. */
export async function resolveChannelTabs(channelData) {
  const props = channelData.properties || {};
  const channelId = String(channelData.id || '');
  const meetingNotesFileId = (props.meeting_notes || {}).file_id;

  const deduped = dedupeTabs(collectRawTabs(props), meetingNotesFileId);
  const fileCache = {};
  for (const tab of deduped) await enrichFromFile(tab, String(tab.file_id || ''), fileCache);
  if (channelId) await expandBookmarkFolders(channelId, deduped, fileCache);

  deduped.forEach((tab, i) => {
    tab.index = i + 1;
  });
  return deduped;
}

/** Select a tab by 1-based index or case-insensitive name/id/type match. */
export function selectChannelTab(selector, tabs) {
  const sel = (selector || '').trim();
  if (!sel) return null;

  if (/^\d+$/.test(sel)) {
    const idx = parseInt(sel, 10);
    if (idx >= 1 && idx <= tabs.length) return tabs[idx - 1];
    if (idx >= 0 && idx < tabs.length) return tabs[idx];
    return null;
  }

  const lowered = sel.toLowerCase();
  const fields = (t) => [
    t.name, t.path, t.label, t.id, t.type, t.file_id, t.url, t.permalink,
  ].map((v) => String(v || '').toLowerCase());

  const exact = tabs.filter((t) => fields(t).includes(lowered));
  if (exact.length === 1) return exact[0];
  if (exact.length > 1) {
    const fetchable = exact.filter((t) => t.url || t.download_url);
    return fetchable[0] || exact[0];
  }

  const partial = tabs.filter((t) => fields(t).join(' ').includes(lowered));
  if (partial.length === 1) return partial[0];
  if (partial.length > 1) {
    const fetchable = partial.filter((t) => t.url || t.download_url);
    return fetchable[0] || partial[0];
  }
  return null;
}
