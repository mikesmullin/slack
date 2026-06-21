// Direct Slack WebSocket client (no browser). Mirrors src2/ws_client.py.
// Uses Bun's WebSocket (supports custom headers).
import { loadTokens, saveTokens } from './tokens.mjs';
import { slackApi, SlackAuthError } from './api.mjs';

const WS_BASE = 'wss://wss-primary.slack.com/';
const WS_ORIGIN = 'https://app.slack.com';
const PING_INTERVAL_MS = 9000;
const START_ARGS =
  '?agent=client&org_wide_aware=true&agent_version=0' +
  '&eac_cache_ts=true&cache_ts=0&name_tagging=true' +
  '&only_self_subteams=true&connect_only=true&ms_latest=true';

function gatewayBase(enterpriseId) {
  return enterpriseId.startsWith('E') ? 'T' + enterpriseId.slice(1) : enterpriseId;
}

function buildUrl(token, enterpriseId, gatewayServer, frt = null) {
  const params = new URLSearchParams();
  if (frt) params.set('frt', frt);
  params.set('token', token);
  params.set('sync_desync', '1');
  params.set('slack_client', 'desktop');
  params.set('start_args', START_ARGS);
  params.set('no_query_on_subscribe', '1');
  params.set('flannel', '3');
  params.set('lazy_channels', '1');
  params.set('gateway_server', gatewayServer);
  params.set('enterprise_id', enterpriseId);
  params.set('batch_presence_aware', '1');
  return WS_BASE + '?' + params.toString();
}

function openSocket(url, cookie) {
  return new WebSocket(url, { headers: { Origin: WS_ORIGIN, Cookie: `d=${cookie}` } });
}

async function probeGateway(token, cookie, enterpriseId) {
  const base = gatewayBase(enterpriseId);
  for (let shard = 1; shard <= 5; shard++) {
    const gw = `${base}-${shard}`;
    const url = buildUrl(token, enterpriseId, gw);
    const ok = await new Promise((resolve) => {
      let settled = false;
      const ws = openSocket(url, cookie);
      const done = (val) => {
        if (settled) return;
        settled = true;
        try { ws.close(); } catch { /* ignore */ }
        resolve(val);
      };
      const timer = setTimeout(() => done(false), 8000);
      ws.onmessage = (e) => {
        clearTimeout(timer);
        try {
          done(JSON.parse(e.data).type === 'hello');
        } catch {
          done(false);
        }
      };
      ws.onerror = () => { clearTimeout(timer); done(false); };
    });
    if (ok) return gw;
  }
  return null;
}

/** Long-running client yielding Slack WS events. */
export class SlackWSClient {
  constructor() {
    this._frt = null;
    this._pingId = 1;
  }

  async *events() {
    const tokens = loadTokens();
    const token = tokens.token;
    const cookie = tokens.cookie || '';

    let enterpriseId = tokens.enterprise_id || '';
    if (!enterpriseId) {
      const info = await slackApi('auth.test', {});
      if (!info.ok) throw new Error(`auth.test failed: ${info.error}`);
      enterpriseId = info.enterprise_id || info.team_id || '';
      if (!enterpriseId) throw new Error('Could not determine enterprise_id');
      saveTokens({ enterprise_id: enterpriseId });
    }

    let gatewayServer = tokens.gateway_server || '';
    if (!gatewayServer) {
      gatewayServer = await probeGateway(token, cookie, enterpriseId);
      if (!gatewayServer) throw new Error('Could not discover a working gateway_server (shards 1–5)');
      saveTokens({ gateway_server: gatewayServer });
    }

    for (;;) {
      const url = buildUrl(token, enterpriseId, gatewayServer, this._frt);
      this._frt = null;
      let reconnect = false;
      try {
        yield* this._session(url, cookie, () => { reconnect = true; });
      } catch (e) {
        if (e instanceof SlackAuthError) throw e;
      }
      await new Promise((r) => setTimeout(r, reconnect ? 2000 : 5000));
    }
  }

  async *_session(url, cookie) {
    const ws = openSocket(url, cookie);
    const queue = [];
    let waiter = null;
    let closed = false;
    let firstPongDone = false;

    const push = (item) => {
      if (waiter) {
        waiter(item);
        waiter = null;
      } else {
        queue.push(item);
      }
    };
    const next = () =>
      queue.length ? Promise.resolve(queue.shift()) : new Promise((r) => (waiter = r));

    ws.onmessage = (e) => {
      let event;
      try {
        event = JSON.parse(e.data);
      } catch {
        return;
      }
      const t = event.type;
      if (t === 'reconnect_url') {
        const u = event.url || '';
        const i = u.indexOf('frt=');
        if (i !== -1) {
          const start = i + 4;
          const amp = u.indexOf('&', start);
          this._frt = u.slice(start, amp === -1 ? u.length : amp);
        }
        return;
      }
      if (t === 'pong') {
        if (!firstPongDone) {
          firstPongDone = true;
          push({ event });
        }
        return;
      }
      push({ event });
    };
    ws.onclose = () => { closed = true; push({ closed: true }); };
    ws.onerror = () => { closed = true; push({ closed: true }); };

    await new Promise((resolve, reject) => {
      ws.onopen = resolve;
      const t = setTimeout(() => reject(new Error('ws open timeout')), 15000);
      ws.addEventListener('open', () => clearTimeout(t), { once: true });
    });

    const ping = setInterval(() => {
      if (!closed) {
        try {
          ws.send(JSON.stringify({ type: 'ping', id: this._pingId++ }));
        } catch { /* ignore */ }
      }
    }, PING_INTERVAL_MS);

    try {
      while (!closed) {
        const item = await next();
        if (item.closed) break;
        const event = item.event;
        if (event.type === 'error') {
          const err = event.error || {};
          if (err.code === 401 || err.msg === 'invalid_auth') {
            throw new SlackAuthError(`Slack rejected credentials: ${err.msg || 'invalid_auth'}`);
          }
          continue;
        }
        yield event;
      }
    } finally {
      clearInterval(ping);
      try { ws.close(); } catch { /* ignore */ }
    }
  }
}
