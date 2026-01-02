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

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global state
session: Optional[Browser] = None
intercepted_token: Optional[str] = None

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
