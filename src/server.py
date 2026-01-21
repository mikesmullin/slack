import asyncio
import logging
import os
import sys
import json
import re
import urllib.parse
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from browser_use import Browser

from contextlib import asynccontextmanager

from .watch import WatchEngine, set_watch_engine, get_watch_engine

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global state
session: Optional[Browser] = None
intercepted_token: Optional[str] = None
_watch_engine: Optional[WatchEngine] = None

WORKSPACE_ROOT = Path(__file__).parent.parent
DATA_DIR = WORKSPACE_ROOT / ".browser_data"
PID_FILE = WORKSPACE_ROOT / "slack-server.pid"

@asynccontextmanager
async def lifespan(app: FastAPI):
    global session
    logger.info("🚀 Starting Slack Browser Server...")
    
    DATA_DIR.mkdir(exist_ok=True)
    
    # Write PID file
    PID_FILE.write_text(str(os.getpid()))
    
    session = Browser(
        headless=False,
        user_data_dir=str(DATA_DIR),
    )
    
    # Start the session
    await session.start()
    
    # Navigate to Slack if not already there
    url = await session.get_current_page_url()
    if "slack.com" not in url:
        await session.navigate_to("https://app.slack.com/client/")
    
    # Initialize watch engine (loads config.yaml if present)
    await _init_watch_engine()
    
    yield
    
    logger.info("🛑 Shutting down Slack Browser Server...")
    if session:
        try:
            await session.stop()
        except Exception as e:
            logger.error(f"Error stopping session: {e}")

app = FastAPI(title="Slack Browser Server", lifespan=lifespan)

async def fetch_token_from_page():
    """Lazy-load token from localStorage when needed."""
    global intercepted_token, session
    if not session:
        return None
    
    # Return cached token if we have one
    if intercepted_token:
        return intercepted_token
    
    try:
        page = await session.get_current_page()
        
        # browser-use requires arrow function format
        script = """() => {
            const config = JSON.parse(localStorage.localConfig_v2);
            const teamId = document.location.pathname.match(/^\\/client\\/([A-Z0-9]+)/)[1];
            return config.teams[teamId].token;
        }"""
        token = await page.evaluate(script)
        logger.info(f"Token fetch result: {token[:15] + '...' if token else 'None'}")
        if token and isinstance(token, str) and token.startswith("xox"):
            intercepted_token = token
            return token

    except Exception as e:
        logger.error(f"Failed to fetch token from page: {e}")
    
    return intercepted_token



# Cache for enterprise status
_enterprise_cache = {"is_enterprise": None}

@app.get("/status")
async def get_status():
    if not session:
        return {"ready": False, "authenticated": False}
    
    try:
        url = await session.get_current_page_url()
        
        # Try to refresh token if missing
        if not intercepted_token:
            logger.info("Token not cached, fetching from page...")
            await fetch_token_from_page()
            
        # Robust auth detection:
        # 1. Must be on a slack.com/client URL
        # 2. Must NOT be on a login or landing page
        # 3. Must have a token
        is_on_client = "slack.com/client" in url
        is_on_login = "/login" in url or "/signin" in url or "get-started" in url
        
        # Check for persistence (cookies/local storage on disk)
        has_persistence = any((DATA_DIR / x).exists() for x in ["Default", "SingletonCookie", "Cookies", "storage_state.json"])
        
        # User states auth is working, so we trust being on the client URL
        authenticated = is_on_client and not is_on_login
        
        return {
            "url": url,
            "authenticated": authenticated,
            "has_token": intercepted_token is not None,
            "token_preview": intercepted_token[:10] + "..." if intercepted_token else None,
            "has_persistence": has_persistence,
            "ready": True
        }
    except Exception as e:
        return {"ready": False, "error": str(e), "authenticated": False}

@app.get("/enterprise-check")
async def check_enterprise():
    """Check if the workspace is an enterprise Slack instance."""
    global intercepted_token, session
    
    # Return cached result if available
    if _enterprise_cache["is_enterprise"] is not None:
        return {"is_enterprise": _enterprise_cache["is_enterprise"]}
    
    if not intercepted_token:
        await fetch_token_from_page()
        
    if not intercepted_token:
        raise HTTPException(status_code=401, detail="Token not captured yet")
    
    if not session:
        raise HTTPException(status_code=503, detail="Browser not initialized")

    try:
        page = await session.get_current_page()
        
        # Call team.info to check for enterprise
        script = """(token) => {
            return new Promise((resolve, reject) => {
                const timestamp = Date.now();
                const xId = 'noversion-' + timestamp + '.' + Math.floor(Math.random() * 1000);
                const versionTs = '1755340361';
                
                const queryParams = new URLSearchParams({
                    '_x_id': xId,
                    '_x_version_ts': versionTs,
                    '_x_frontend_build_type': 'current',
                    '_x_desktop_ia': '4',
                    '_x_gantry': 'true',
                    'fp': 'ec',
                    '_x_num_retries': '0'
                });
                
                const formData = new URLSearchParams();
                formData.append('token', token);
                formData.append('web_client_version', versionTs);
                formData.append('_x_sonic', 'true');
                formData.append('_x_app_name', 'client');
                
                fetch('https://slack.com/api/team.info?' + queryParams.toString(), {
                    method: 'POST',
                    body: formData,
                    credentials: 'include',
                    headers: {
                        'Accept': '*/*',
                        'Content-Type': 'application/x-www-form-urlencoded',
                        'Origin': 'https://app.slack.com',
                    }
                })
                .then(r => r.json())
                .then(data => resolve(data))
                .catch(err => reject(err.message));
            });
        }"""
        result = await page.evaluate(script, intercepted_token)
        
        # Handle case where result might be a string (JSON string from API)
        if isinstance(result, str):
            import json as json_module
            try:
                result = json_module.loads(result)
            except:
                result = {}
        
        # Check if workspace is enterprise by looking for enterprise_id or enterprise domain
        team_info = result.get("team", {}) if isinstance(result, dict) else {}
        url = team_info.get("url", "")
        is_enterprise = (
            team_info.get("enterprise_id") is not None or 
            "enterprise.slack.com" in url
        )
        
        # Cache the result
        _enterprise_cache["is_enterprise"] = is_enterprise
        
        return {"is_enterprise": is_enterprise, "team": team_info}
    except Exception as e:
        logger.error(f"Enterprise check failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api")
async def call_api(request: Request):
    global intercepted_token, session
    
    body = await request.json()
    endpoint = body.get("endpoint")
    params = body.get("params", {})
    
    if not intercepted_token:
        await fetch_token_from_page()
        
    if not intercepted_token:
        raise HTTPException(status_code=401, detail="Token not captured yet. Please ensure you are logged in and the page is fully loaded.")
    
    if not session:
        raise HTTPException(status_code=503, detail="Browser not initialized")

    try:
        page = await session.get_current_page()
        
        # We use the browser's fetch to avoid CORS and use existing cookies
        # browser-use requires arrow function format: (args) => { ... }
        
        # Make API call using browser's fetch with Slack client headers (matching .mjs)
        script = """(endpoint, token, params) => {
            return new Promise((resolve, reject) => {
                const timestamp = Date.now();
                const xId = 'noversion-' + timestamp + '.' + Math.floor(Math.random() * 1000);
                const versionTs = '1755340361';
                
                const queryParams = new URLSearchParams({
                    '_x_id': xId,
                    '_x_version_ts': versionTs,
                    '_x_frontend_build_type': 'current',
                    '_x_desktop_ia': '4',
                    '_x_gantry': 'true',
                    'fp': 'ec',
                    '_x_num_retries': '0'
                });
                
                const formData = new URLSearchParams();
                formData.append('token', token);
                formData.append('web_client_version', versionTs);
                formData.append('_x_sonic', 'true');
                formData.append('_x_app_name', 'client');
                for (const key in params) {
                    if (typeof params[key] === 'object') {
                        formData.append(key, JSON.stringify(params[key]));
                    } else {
                        formData.append(key, String(params[key]));
                    }
                }
                
                fetch('https://slack.com/api/' + endpoint + '?' + queryParams.toString(), {
                    method: 'POST',
                    body: formData,
                    credentials: 'include',
                    headers: {
                        'Accept': '*/*',
                        'Accept-Encoding': 'gzip, deflate, br, zstd',
                        'Accept-Language': 'en-US',
                        'Content-Type': 'application/x-www-form-urlencoded',
                        'Origin': 'https://app.slack.com',
                        'Sec-Ch-Ua': '"Chromium";v="139", "Not;A=Brand";v="99"',
                        'Sec-Ch-Ua-Mobile': '?0',
                        'Sec-Ch-Ua-Platform': '"Linux"',
                        'Sec-Fetch-Dest': 'empty',
                        'Sec-Fetch-Mode': 'cors',
                        'Sec-Fetch-Site': 'same-site'
                    }
                })
                .then(r => r.json())
                .then(data => resolve(data))
                .catch(err => reject(err.message));
            });
        }"""
        result = await page.evaluate(script, endpoint, intercepted_token, params)
        return result
    except Exception as e:
        logger.error(f"API call failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/execute")
async def execute_js(request: Request):
    """Execute arbitrary JavaScript in the browser context."""
    global session
    
    body = await request.json()
    script = body.get("script")
    
    if not script:
        raise HTTPException(status_code=400, detail="Missing 'script' in request body")
    
    if not session:
        raise HTTPException(status_code=503, detail="Browser not initialized")

    try:
        page = await session.get_current_page()
        result = await page.evaluate(script)
        return {"success": True, "result": result}
    except Exception as e:
        logger.error(f"JS execution failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/navigate")
async def navigate(request: dict):
    url = request.get("url")
    if not session:
        raise HTTPException(status_code=503, detail="Browser not initialized")
    try:
        await session.navigate_to(url)
        return {"success": True, "url": url}
    except Exception as e:
        logger.error(f"Navigation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/stop")
async def stop_server():
    # Schedule shutdown
    logger.info("Stop requested via API")
    asyncio.create_task(delayed_shutdown())
    return {"success": True, "message": "Server stopping..."}

async def delayed_shutdown():
    await asyncio.sleep(1)
    logger.info("Executing delayed shutdown...")
    
    # Manually stop session before hard exit
    global session
    if session:
        try:
            await session.stop()
        except Exception as e:
            logger.error(f"Error stopping session during delayed shutdown: {e}")
            
    # Use os._exit to bypass any uvicorn signal handling that might block
    os._exit(0)


# ============================================================================
# WEBSOCKET INTERCEPTION VIA CDP
# ============================================================================

# Store for intercepted WebSocket messages
_ws_messages: list[dict] = []
_ws_monitoring = False
_ws_callback_registered = False


async def _start_websocket_monitoring() -> bool:
    """Start WebSocket monitoring via CDP. Returns True if started successfully."""
    global session, _ws_monitoring, _ws_callback_registered, _ws_messages
    
    if not session:
        logger.warning("Cannot start WebSocket monitoring: browser not initialized")
        return False
    
    if _ws_monitoring:
        logger.debug("WebSocket monitoring already active")
        return True
    
    try:
        cdp_client = session.cdp_client
        cdp_session = await session.get_or_create_cdp_session()
        session_id = cdp_session.session_id
        
        _ws_messages = []
        
        if not _ws_callback_registered:
            def on_ws_frame_received(event: dict, sid: str | None = None):
                """Handle incoming WebSocket frame."""
                import datetime
                try:
                    request_id = event.get("requestId", "")
                    timestamp = event.get("timestamp", 0)
                    response = event.get("response", {})
                    payload_data = response.get("payloadData", "")
                    
                    try:
                        payload = json.loads(payload_data) if payload_data else {}
                    except json.JSONDecodeError:
                        payload = {"raw": payload_data}
                    
                    msg = {
                        "requestId": request_id,
                        "timestamp": datetime.datetime.now().isoformat(),
                        "cdpTimestamp": timestamp,
                        "opcode": response.get("opcode", 0),
                        "payload": payload,
                    }
                    
                    if len(_ws_messages) >= 1000:
                        _ws_messages.pop(0)
                    _ws_messages.append(msg)
                    
                    msg_type = payload.get("type", "unknown") if isinstance(payload, dict) else "raw"
                    logger.debug(f"WS frame: {msg_type}")
                    
                    # Pass message to watch engine for pattern matching
                    if isinstance(payload, dict) and _watch_engine and _watch_engine.is_running():
                        try:
                            loop = asyncio.get_event_loop()
                            loop.create_task(_watch_engine.process_message(payload))
                        except Exception as we:
                            logger.error(f"Watch engine error: {we}")
                    
                except Exception as e:
                    logger.error(f"Error processing WS frame: {e}")
            
            cdp_client.register.Network.webSocketFrameReceived(on_ws_frame_received)
            _ws_callback_registered = True
            logger.info("Registered WebSocket frame callback")
        
        await cdp_client.send.Network.enable(session_id=session_id)
        
        _ws_monitoring = True
        logger.info("WebSocket monitoring started")
        return True
        
    except Exception as e:
        logger.error(f"Failed to start WebSocket monitoring: {e}")
        return False


@app.post("/websocket/start")
async def websocket_start():
    """Start intercepting WebSocket frames via CDP Network domain."""
    if not session:
        raise HTTPException(status_code=503, detail="Browser not initialized")
    
    if _ws_monitoring:
        return {"status": "already_running", "message": "WebSocket monitoring already active"}
    
    success = await _start_websocket_monitoring()
    if success:
        return {"status": "started", "message": "WebSocket monitoring enabled"}
    else:
        raise HTTPException(status_code=500, detail="Failed to start WebSocket monitoring")


@app.post("/websocket/stop")
async def websocket_stop():
    """Stop WebSocket frame interception."""
    global _ws_monitoring
    
    _ws_monitoring = False
    logger.info("WebSocket monitoring stopped")
    
    return {"status": "stopped", "message_count": len(_ws_messages)}


@app.get("/websocket/messages")
async def websocket_get_messages(
    since: int = 0,
    limit: int = 100,
    clear: bool = False
):
    """Get intercepted WebSocket messages.
    
    Args:
        since: Return messages after this index
        limit: Maximum number of messages to return
        clear: Clear messages after returning
    """
    global _ws_messages
    
    messages = _ws_messages[since:since + limit]
    total = len(_ws_messages)
    
    if clear:
        _ws_messages = []
    
    return {
        "messages": messages,
        "total": total,
        "returned": len(messages),
        "monitoring": _ws_monitoring
    }


@app.post("/websocket/test")
async def websocket_test():
    """Quick test to verify CDP WebSocket interception capability."""
    global session
    
    if not session:
        raise HTTPException(status_code=503, detail="Browser not initialized")
    
    try:
        # session IS a BrowserSession (Browser is an alias)
        # Check for CDP client
        has_cdp = hasattr(session, 'cdp_client')
        cdp_client = None
        try:
            cdp_client = session.cdp_client
        except AssertionError:
            # cdp_client property raises AssertionError if not initialized
            pass
        
        # Check for Network domain support
        has_network = False
        if cdp_client and hasattr(cdp_client, 'send'):
            has_network = hasattr(cdp_client.send, 'Network')
        
        # Check for event registration
        has_register = False
        if cdp_client and hasattr(cdp_client, 'register'):
            has_register = hasattr(cdp_client.register, 'Network')
        
        return {
            "success": True,
            "capabilities": {
                "has_cdp_client": has_cdp,
                "cdp_client_initialized": cdp_client is not None,
                "has_network_domain": has_network,
                "has_event_registration": has_register,
                "cdp_client_type": type(cdp_client).__name__ if cdp_client else None,
            },
            "monitoring_active": _ws_monitoring,
            "messages_captured": len(_ws_messages),
        }
        
    except Exception as e:
        logger.error(f"WebSocket test failed: {e}")
        return {"success": False, "error": str(e)}


# ============================================================================
# WATCH ENGINE FOR AUTO-REPLY
# ============================================================================

from . import storage as local_storage


async def _resolve_channel_for_watch(name: str) -> Optional[str]:
    """Resolve channel name to ID for watch engine.
    
    Only uses local cache (storage/_cache/channels.yml).
    Use 'slack-chat channel resolve <id>' to populate the cache.
    """
    import re as re_module
    
    # Strip # prefix if present
    name = name.lstrip("#")
    
    # Already a channel ID? (C..., G..., D...)
    if re_module.match(r'^[CDG][A-Z0-9]{8,}$', name):
        return name
    
    # Check local cache (from storage/_cache/channels.yml)
    cached = local_storage.find_channel_by_name(name)
    if cached:
        channel_id = cached.get("id")
        if channel_id:
            logger.info(f"Resolved channel '{name}' to {channel_id} (from cache)")
            return channel_id
    
    # Not found in cache
    logger.error(f"Channel '{name}' not found in cache. Use 'slack-chat channel resolve <id>' to cache it first.")
    return None


async def _post_message_for_watch(channel: str, text: str, thread_ts: Optional[str]) -> bool:
    """Post a message to Slack for the watch engine.
    
    Args:
        channel: Channel ID
        text: Message text
        thread_ts: Thread timestamp (to reply in thread)
        
    Returns:
        True if successful, False otherwise
    """
    global intercepted_token, session
    
    if not session:
        logger.error("Cannot post message: browser session not available")
        return False
    
    # Fetch token if not already cached
    if not intercepted_token:
        await fetch_token_from_page()
    
    if not intercepted_token:
        logger.error("Cannot post message: token not available")
        return False
    
    try:
        page = await session.get_current_page()
        
        # Build params for chat.postMessage
        params = {
            "channel": channel,
            "text": text,
        }
        if thread_ts:
            params["thread_ts"] = thread_ts
        
        # Use the same API calling pattern as the /api endpoint
        script = """(endpoint, token, params) => {
            return new Promise((resolve, reject) => {
                const timestamp = Date.now();
                const xId = 'noversion-' + timestamp + '.' + Math.floor(Math.random() * 1000);
                const versionTs = '1755340361';
                
                const queryParams = new URLSearchParams({
                    '_x_id': xId,
                    '_x_version_ts': versionTs,
                    '_x_frontend_build_type': 'current',
                    '_x_desktop_ia': '4',
                    '_x_gantry': 'true',
                    'fp': 'ec',
                    '_x_num_retries': '0'
                });
                
                const formData = new URLSearchParams();
                formData.append('token', token);
                formData.append('web_client_version', versionTs);
                formData.append('_x_sonic', 'true');
                formData.append('_x_app_name', 'client');
                for (const key in params) {
                    if (typeof params[key] === 'object') {
                        formData.append(key, JSON.stringify(params[key]));
                    } else {
                        formData.append(key, String(params[key]));
                    }
                }
                
                fetch('https://slack.com/api/' + endpoint + '?' + queryParams.toString(), {
                    method: 'POST',
                    body: formData,
                    credentials: 'include',
                    headers: {
                        'Content-Type': 'application/x-www-form-urlencoded',
                        'Origin': 'https://app.slack.com',
                    }
                })
                .then(r => r.json())
                .then(data => resolve(data))
                .catch(err => reject(err.message));
            });
        }"""
        
        result = await page.evaluate(script, "chat.postMessage", intercepted_token, params)
        
        # Handle case where result is JSON string
        if isinstance(result, str):
            import json
            result = json.loads(result)
        
        if result and result.get("ok"):
            logger.info(f"Posted message to {channel}" + (f" in thread {thread_ts}" if thread_ts else ""))
            return True
        else:
            error = result.get("error", "unknown") if result else "no response"
            logger.error(f"Failed to post message: {error}")
            return False
            
    except Exception as e:
        logger.error(f"Error posting message: {e}")
        return False


async def _resolve_user_for_watch(user_id: str) -> Optional[dict]:
    """Resolve a user ID via Slack API and cache the result.
    
    This is called by the watch engine when a user isn't in the cache.
    Returns the user data dict on success, None on failure.
    """
    global intercepted_token, session
    
    if not intercepted_token:
        await fetch_token_from_page()
    
    if not intercepted_token or not session:
        logger.warning(f"Cannot resolve user {user_id}: no token or session")
        return None
    
    try:
        page = await session.get_current_page()
        
        # Use the same API calling pattern as /api endpoint
        script = """(endpoint, token, params) => {
            return new Promise((resolve, reject) => {
                const timestamp = Date.now();
                const xId = 'noversion-' + timestamp + '.' + Math.floor(Math.random() * 1000);
                const versionTs = '1755340361';
                
                const queryParams = new URLSearchParams({
                    '_x_id': xId,
                    '_x_version_ts': versionTs,
                    '_x_frontend_build_type': 'current',
                    '_x_desktop_ia': '4',
                    '_x_gantry': 'true',
                    'fp': 'ec',
                    '_x_num_retries': '0'
                });
                
                const formData = new URLSearchParams();
                formData.append('token', token);
                formData.append('web_client_version', versionTs);
                formData.append('_x_sonic', 'true');
                formData.append('_x_app_name', 'client');
                for (const key in params) {
                    if (typeof params[key] === 'object') {
                        formData.append(key, JSON.stringify(params[key]));
                    } else {
                        formData.append(key, String(params[key]));
                    }
                }
                
                fetch('https://slack.com/api/' + endpoint + '?' + queryParams.toString(), {
                    method: 'POST',
                    body: formData,
                    credentials: 'include',
                    headers: {
                        'Content-Type': 'application/x-www-form-urlencoded',
                        'Origin': 'https://app.slack.com',
                    }
                })
                .then(r => r.json())
                .then(data => resolve(data))
                .catch(err => reject(err.message));
            });
        }"""
        
        result = await page.evaluate(script, "users.info", intercepted_token, {"user": user_id})
        
        # Handle case where result is JSON string
        if isinstance(result, str):
            import json
            result = json.loads(result)
        
        if result and result.get("ok"):
            user_data = result.get("user", {})
            # Cache the result for future lookups
            from . import storage
            storage.cache_user(user_id, user_data)
            logger.info(f"Resolved and cached user {user_id}: {user_data.get('name', 'unknown')}")
            return user_data
        else:
            error = result.get("error", "unknown") if result else "no response"
            logger.warning(f"Failed to resolve user {user_id}: {error}")
            return None
            
    except Exception as e:
        logger.error(f"Error resolving user {user_id}: {e}")
        return None


async def _fetch_context_for_watch(
    channel: str,
    ts: str,
    thread_ts: Optional[str],
) -> list[dict]:
    """Fetch surrounding message context for a watch trigger.
    
    For threads: fetches the entire thread history
    For channel messages: fetches the prior 10 messages
    
    Returns list of message dicts (excluding the current message).
    """
    global intercepted_token, session
    
    if not intercepted_token:
        await fetch_token_from_page()
    
    if not intercepted_token or not session:
        logger.warning("Cannot fetch context: no token or session")
        return []
    
    try:
        page = await session.get_current_page()
        
        # Reusable API call script
        script = """(endpoint, token, params) => {
            return new Promise((resolve, reject) => {
                const timestamp = Date.now();
                const xId = 'noversion-' + timestamp + '.' + Math.floor(Math.random() * 1000);
                const versionTs = '1755340361';
                
                const queryParams = new URLSearchParams({
                    '_x_id': xId,
                    '_x_version_ts': versionTs,
                    '_x_frontend_build_type': 'current',
                    '_x_desktop_ia': '4',
                    '_x_gantry': 'true',
                    'fp': 'ec',
                    '_x_num_retries': '0'
                });
                
                const formData = new URLSearchParams();
                formData.append('token', token);
                formData.append('web_client_version', versionTs);
                formData.append('_x_sonic', 'true');
                formData.append('_x_app_name', 'client');
                for (const key in params) {
                    if (typeof params[key] === 'object') {
                        formData.append(key, JSON.stringify(params[key]));
                    } else {
                        formData.append(key, String(params[key]));
                    }
                }
                
                fetch('https://slack.com/api/' + endpoint + '?' + queryParams.toString(), {
                    method: 'POST',
                    body: formData,
                    credentials: 'include',
                    headers: {
                        'Content-Type': 'application/x-www-form-urlencoded',
                        'Origin': 'https://app.slack.com',
                    }
                })
                .then(r => r.json())
                .then(data => resolve(data))
                .catch(err => reject(err.message));
            });
        }"""
        
        context_messages = []
        
        if thread_ts:
            # For threads: fetch entire thread history
            result = await page.evaluate(
                script,
                "conversations.replies",
                intercepted_token,
                {"channel": channel, "ts": thread_ts, "limit": 100},
            )
            
            # Handle case where result is JSON string
            if isinstance(result, str):
                import json
                result = json.loads(result)
            
            if result and result.get("ok"):
                messages = result.get("messages", [])
                # Filter out the current message and return all others
                context_messages = [
                    m for m in messages
                    if m.get("ts") != ts
                ]
                logger.info(f"Fetched {len(context_messages)} thread messages for context")
        else:
            # For channel messages: fetch prior 10 messages
            result = await page.evaluate(
                script,
                "conversations.history",
                intercepted_token,
                {
                    "channel": channel,
                    "latest": ts,
                    "inclusive": False,  # Don't include the current message
                    "limit": 10,
                },
            )
            
            # Handle case where result is JSON string
            if isinstance(result, str):
                import json
                result = json.loads(result)
            
            if result and result.get("ok"):
                messages = result.get("messages", [])
                # Reverse to get chronological order (oldest first)
                context_messages = list(reversed(messages))
                logger.info(f"Fetched {len(context_messages)} prior messages for context")
        
        return context_messages
        
    except Exception as e:
        logger.error(f"Error fetching context: {e}")
        return []


async def _init_watch_engine():
    """Initialize the watch engine on server startup."""
    global _watch_engine
    
    _watch_engine = WatchEngine(
        resolve_channel_func=_resolve_channel_for_watch,
        post_message_func=_post_message_for_watch,
        resolve_user_func=_resolve_user_for_watch,
        fetch_context_func=_fetch_context_for_watch,
    )
    set_watch_engine(_watch_engine)
    
    # Try to load config (don't fail if no config exists)
    await _watch_engine.load_config()
    
    # Auto-start watch engine and WebSocket monitoring if rules are loaded
    if _watch_engine.config.rules:
        _watch_engine.start()
        logger.info(f"Watch engine auto-started with {len(_watch_engine.config.rules)} rules")
        
        # Also start WebSocket monitoring to receive messages
        await _start_websocket_monitoring()


@app.post("/watch/reload")
async def watch_reload():
    """Reload watch configuration from config.yaml."""
    global _watch_engine
    
    if not _watch_engine:
        _watch_engine = WatchEngine(
            resolve_channel_func=_resolve_channel_for_watch,
            post_message_func=_post_message_for_watch,
            resolve_user_func=_resolve_user_for_watch,
            fetch_context_func=_fetch_context_for_watch,
        )
        set_watch_engine(_watch_engine)
    
    # Ensure we have a token before trying to resolve channels
    if not intercepted_token:
        await fetch_token_from_page()
    
    success = await _watch_engine.load_config()
    rules_count = len(_watch_engine.config.rules)
    
    # Auto-start watch engine and WebSocket monitoring if we have rules
    if rules_count > 0:
        if not _watch_engine.is_running():
            _watch_engine.start()
        # Ensure WebSocket monitoring is running
        if not _ws_monitoring:
            await _start_websocket_monitoring()
    
    return {
        "success": True,  # Config was read (even if no rules loaded)
        "rules_loaded": rules_count,
        "running": _watch_engine.is_running(),
        "ws_monitoring": _ws_monitoring,
        "message": f"Loaded {rules_count} rules" if rules_count > 0 else "No rules loaded (check channel names)",
    }


@app.get("/watch/status")
async def watch_status():
    """Get watch engine status."""
    global _watch_engine
    
    if not _watch_engine:
        return {
            "running": False,
            "stats": {},
            "rules": [],
        }
    
    rules_info = [
        {
            "channel_name": r.channel_name,
            "channel_id": r.channel_id,
            "pattern": r.pattern.pattern,
            "shell": r.shell,
            "reply": r.reply,
        }
        for r in _watch_engine.config.rules
    ]
    
    return {
        "running": _watch_engine.is_running(),
        "stats": _watch_engine.get_stats(),
        "rules": rules_info,
    }
