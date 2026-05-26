import typer
import sys
import os
import subprocess
import time
import signal

from ..utils import (
    get_client,
    get_server_pid,
    handle_response,
    SERVER_URL,
    PID_FILE,
    LOG_FILE,
    TOKENS_FILE,
)

# Create Typer app for server commands
app = typer.Typer(help="Manage the Slack browser server")


@app.command("status")
def server_status():
    """Check the status of the Slack browser server."""
    import httpx
    
    pid = get_server_pid()
    is_running = False
    if pid:
        try:
            os.kill(pid, 0)
            is_running = True
        except OSError:
            pass

    try:
        with get_client() as client:
            response = client.get(f"{SERVER_URL}/status")
            try:
                data = response.json()
            except Exception:
                data = {}

            status = "🟢 running" if is_running else "🔴 stopped"
            url = data.get("url", "N/A")
            has_token = "✅" if data.get("has_token") else "❌"
            tokens_file = "✅ saved" if data.get("tokens_file") else "❌ not saved"

            print(f"Server: {status} (PID {pid})" if pid else f"Server: {status}")
            print(f"URL: {url}")
            print(f"Token (browser): {has_token}")
            print(f"Tokens file:     {tokens_file}")
    except httpx.ConnectError:
        if is_running:
            print(f"Server: 🟡 starting (PID {pid})")
        else:
            print("Server: 🔴 stopped")


@app.command("start")
def server_start(
    background: bool = typer.Option(
        True, "--background", "-b", help="Run the server in the background"
    ),
    headless: bool = typer.Option(
        False, "--headless", help="Run the browser in headless mode (no visible window)"
    ),
):
    """Start the Slack browser server."""
    import httpx
    
    # Check if already running
    pid = get_server_pid()
    if pid:
        try:
            os.kill(pid, 0)
            print(f"❌ Server is already running (PID {pid})")
            print("Stop it first with: slack-chat server stop")
            sys.exit(1)
        except OSError:
            # Process not found, remove stale PID file
            PID_FILE.unlink()

    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "src.server:app",
        "--port",
        "3002",
        "--log-level",
        "info",
    ]

    env = os.environ.copy()
    if headless:
        env["SLACK_HEADLESS"] = "1"

    if background:
        print(f"Starting server in background... logs at {LOG_FILE}")
        with open(LOG_FILE, "a") as f:
            process = subprocess.Popen(cmd, stdout=f, stderr=f, start_new_session=True, env=env)

        PID_FILE.write_text(str(process.pid))

        # Wait for server to be ready (max 5 seconds)
        for _ in range(5):
            try:
                with get_client() as client:
                    resp = client.get(f"{SERVER_URL}/status")
                    if resp.status_code == 200:
                        print("✅ Server started")
                        return
            except httpx.ConnectError:
                time.sleep(1)
        print("⏳ Server starting... check `slack-chat server status`")
    else:
        subprocess.run(cmd, env=env)


@app.command("stop")
def server_stop():
    """Stop the Slack browser server."""
    pid = get_server_pid()

    # Try to kill the process from PID file
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
            print(f"✅ Server stopped (PID {pid})")
            if PID_FILE.exists():
                PID_FILE.unlink()
            return
        except OSError:
            print(f"Could not stop server (PID {pid}). Checking port 3002...")
            if PID_FILE.exists():
                PID_FILE.unlink()

    # If PID file doesn't exist or kill failed, check port 3002
    try:
        result = subprocess.run(
            ["lsof", "-ti", ":3002"], capture_output=True, text=True, timeout=5
        )
        if result.stdout.strip():
            pids = result.stdout.strip().split("\n")
            for p in pids:
                if p.strip():
                    try:
                        os.kill(int(p), signal.SIGKILL)
                        print(f"✅ Killed process on port 3002 (PID {p})")
                    except (OSError, ValueError):
                        pass
        else:
            print("Server is not running.")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        print("Server is not running.")

    # Kill browser-use Chrome processes
    try:
        subprocess.run(["pkill", "-f", ".browser_data"], capture_output=True)
        print("✅ Cleaned up browser-use Chrome processes")
    except Exception:
        pass


@app.command("navigate")
def server_navigate(url: str = typer.Argument(..., help="URL to navigate to")):
    """Navigate the browser to a specific URL."""
    import httpx
    
    try:
        with get_client() as client:
            response = client.post(f"{SERVER_URL}/navigate", json={"url": url})
            handle_response(response)
    except httpx.ConnectError:
        print("Server is not running.")


@app.command("reload")
def server_reload():
    """Reload config.yaml configuration without restarting the server."""
    import httpx
    
    try:
        with get_client() as client:
            response = client.post(f"{SERVER_URL}/watch/reload")
            if response.status_code == 200:
                data = response.json()
                rules_loaded = data.get('rules_loaded', 0)
                if rules_loaded > 0:
                    print(f"✅ Configuration reloaded")
                    print(f"   Rules loaded: {rules_loaded}")
                    if data.get('running'):
                        print(f"   Watch engine: running")
                else:
                    print(f"⚠️  Configuration reloaded but no rules loaded")
                    print(f"   Check config.yaml channel names can be resolved")
                    print(f"   (Server logs may have more details)")
            else:
                print(f"❌ Failed to reload: {response.text}", file=sys.stderr)
                sys.exit(1)
    except httpx.ConnectError:
        print("❌ Server is not running.", file=sys.stderr)
        sys.exit(1)


@app.command("refresh-session")
def server_refresh_session():
    """Re-extract token and cookie from the browser and update .tokens.yaml.

    Run this after logging in to Slack if the server started before you
    were authenticated, or whenever you want to refresh stored credentials.
    """
    import httpx

    try:
        with get_client() as client:
            response = client.post(f"{SERVER_URL}/refresh-session", timeout=30.0)
            if response.status_code == 200:
                data = response.json()
                if data.get("ok"):
                    print("✅ Session credentials refreshed")
                    print(f"   Token:        {data.get('token_preview', 'n/a')}")
                    print(f"   Cookie:       {'✅' if data.get('has_cookie') else '❌'}")
                    print(f"   Workspace:    {data.get('workspace_url', 'n/a')}")
                    print(f"   Enterprise:   {data.get('is_enterprise', False)}")
                else:
                    print(f"❌ {data.get('error', 'Refresh failed')}", file=sys.stderr)
                    print("   Make sure you are logged in to Slack in the browser window.")
                    sys.exit(1)
            else:
                print(f"❌ Server error: {response.text}", file=sys.stderr)
                sys.exit(1)
    except httpx.ConnectError:
        print("❌ Server is not running.", file=sys.stderr)
        sys.exit(1)
