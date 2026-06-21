// Fetch an authenticated Slack file/URL directly using the d cookie + token.
// Replaces the old browser /xhr proxy for files.slack.com downloads.
import { loadTokens } from './tokens.mjs';

/**
 * GET a URL with Slack session credentials.
 * @returns {Promise<{ok:boolean,status:number,status_text:string,url:string,content_type:string,body:string,error?:string}>}
 */
export async function fetchAuthedUrl(url) {
  const tokens = loadTokens();
  const headers = {};
  if (tokens.token) headers.Authorization = `Bearer ${tokens.token}`;
  if (tokens.cookie) headers.Cookie = `d=${tokens.cookie}`;

  let res;
  try {
    res = await fetch(url, { method: 'GET', headers, redirect: 'follow' });
  } catch (e) {
    return { ok: false, status: 0, status_text: '', url, content_type: '', body: '', error: e.message };
  }
  const contentType = res.headers.get('content-type') || '';
  const body = await res.text();
  return {
    ok: res.ok,
    status: res.status,
    status_text: res.statusText,
    url: res.url || url,
    content_type: contentType,
    body,
    error: res.ok ? undefined : `http_${res.status}`,
  };
}
