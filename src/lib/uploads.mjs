// File upload helpers for post-message attachments.
// Mirrors src2/commands/client.py upload helpers.
import { statSync, readFileSync, existsSync } from 'node:fs';
import { basename } from 'node:path';
import { slackApi, requireOk } from './api.mjs';
import { formatEventId } from './format.mjs';

async function uploadToSlack(uploadUrl, path) {
  const bytes = readFileSync(path);
  const res = await fetch(uploadUrl, {
    method: 'POST',
    body: bytes,
    headers: { 'Content-Type': 'application/octet-stream' },
  });
  if (!res.ok) throw new Error(`failed to upload ${path}: HTTP ${res.status}`);
}

function extractUploadedTs(data, channelId) {
  const files = [...(data.files || [])];
  if (data.file) files.push(data.file);
  for (const f of files) {
    const shares = f.shares || {};
    for (const vis of ['public', 'private']) {
      const channelShares = ((shares[vis] || {})[channelId]) || [];
      for (const share of channelShares) if (share.ts) return share.ts;
    }
  }
  return null;
}

/** Upload files and complete the external upload, returning the API payload. */
export async function completeFileUploads(channelId, initialComment, paths, threadTs = null) {
  const files = [];
  for (const path of paths) {
    if (!existsSync(path) || !statSync(path).isFile()) {
      process.stderr.write(`Error: attachment does not exist or is not a file: ${path}\n`);
      process.exit(1);
    }
    const uploadData = await slackApi('files.getUploadURLExternal', {
      filename: basename(path),
      length: statSync(path).size,
    });
    requireOk(uploadData, 'files.getUploadURLExternal');
    const { upload_url: uploadUrl, file_id: fileId } = uploadData;
    if (!uploadUrl || !fileId) {
      process.stderr.write('Error: missing upload_url or file_id from Slack.\n');
      process.exit(1);
    }
    await uploadToSlack(uploadUrl, path);
    files.push({ id: fileId, title: basename(path) });
  }

  const params = { files, channel_id: channelId, initial_comment: initialComment };
  if (threadTs) params.thread_ts = threadTs;

  const data = await slackApi('files.completeUploadExternal', params);
  requireOk(data, 'files.completeUploadExternal');

  let messageTs = extractUploadedTs(data, channelId);
  if (!messageTs && files.length) {
    const info = await slackApi('files.info', { file: files[0].id });
    if (info.ok) messageTs = extractUploadedTs(info, channelId);
  }
  if (messageTs) {
    data.message_ts = messageTs;
    data.event_id = formatEventId(channelId, messageTs, threadTs);
    const permalink = await slackApi('chat.getPermalink', { channel: channelId, message_ts: messageTs });
    if (permalink.ok && permalink.permalink) data.permalink = permalink.permalink;
  }
  return data;
}
