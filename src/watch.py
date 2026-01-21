"""Watch engine for auto-reply based on WebSocket messages.

This module handles:
- Loading config.yaml configuration
- Pattern matching on incoming messages
- Shell command execution with secure buffer file
"""

import asyncio
import json
import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Callable, Dict, Any, Set
from dataclasses import dataclass, field

import yaml

from . import storage

logger = logging.getLogger(__name__)

WORKSPACE_ROOT = Path(__file__).parent.parent
WATCH_CONFIG_FILE = WORKSPACE_ROOT / "config.yaml"
BUFFER_FILE = WORKSPACE_ROOT / "buffer.json"

# Deduplication cache
_seen_messages: set[tuple[str, str]] = set()
MAX_SEEN = 10000

# Slack identifier patterns: U=user, W=workspace user, C=channel, D=DM, G=group
SLACK_ID_PATTERN = re.compile(r'\b([UWCDG][A-Z0-9]{8,})\b')


def _extract_slack_ids(obj: Any) -> Set[str]:
    """Extract all Slack identifiers from an object.
    
    Serializes the object to JSON and finds all matches for Slack ID patterns.
    Returns a set of unique identifiers found.
    """
    try:
        serialized = json.dumps(obj)
        return set(SLACK_ID_PATTERN.findall(serialized))
    except (TypeError, ValueError):
        return set()


def _resolve_slack_ids_sync(ids: Set[str]) -> Dict[str, Dict[str, Any]]:
    """Resolve Slack identifiers from cache only (sync version).
    
    Looks up each ID in the users and channels cache.
    Returns a dict mapping ID -> resolution info.
    """
    resolutions = {}
    
    for slack_id in ids:
        prefix = slack_id[0] if slack_id else ""
        
        if prefix in ("U", "W"):
            # User ID - look up in users cache
            user_data = storage.get_cached_user(slack_id)
            if user_data:
                resolutions[slack_id] = {
                    "type": "user",
                    "name": user_data.get("name"),
                    "real_name": user_data.get("real_name"),
                    "display_name": user_data.get("profile", {}).get("display_name"),
                }
        elif prefix in ("C", "D", "G"):
            # Channel/DM/Group ID - look up in channels cache
            channel_data = storage.get_cached_channel(slack_id)
            if channel_data:
                resolutions[slack_id] = {
                    "type": "channel",
                    "name": channel_data.get("name"),
                    "is_channel": channel_data.get("is_channel"),
                    "is_group": channel_data.get("is_group"),
                    "is_im": channel_data.get("is_im"),
                    "is_mpim": channel_data.get("is_mpim"),
                }
    
    return resolutions


async def _resolve_slack_ids_async(
    ids: Set[str],
    resolve_user_func: Optional[Callable] = None,
) -> Dict[str, Dict[str, Any]]:
    """Resolve Slack identifiers, fetching uncached users via API.
    
    Looks up each ID in the cache first. For uncached users, calls
    the resolve_user_func to fetch from API if provided.
    
    Args:
        ids: Set of Slack identifiers to resolve
        resolve_user_func: Async callback to resolve uncached users
                          Signature: async (user_id: str) -> dict | None
    
    Returns a dict mapping ID -> resolution info.
    """
    resolutions = {}
    
    for slack_id in ids:
        prefix = slack_id[0] if slack_id else ""
        
        if prefix in ("U", "W"):
            # User ID - look up in users cache first
            user_data = storage.get_cached_user(slack_id)
            
            # If not cached and we have a resolve function, fetch from API
            if not user_data and resolve_user_func:
                try:
                    user_data = await resolve_user_func(slack_id)
                except Exception as e:
                    logger.warning(f"Failed to resolve user {slack_id}: {e}")
            
            if user_data:
                resolutions[slack_id] = {
                    "type": "user",
                    "name": user_data.get("name"),
                    "real_name": user_data.get("real_name"),
                    "display_name": user_data.get("profile", {}).get("display_name"),
                }
        elif prefix in ("C", "D", "G"):
            # Channel/DM/Group ID - look up in channels cache
            channel_data = storage.get_cached_channel(slack_id)
            if channel_data:
                resolutions[slack_id] = {
                    "type": "channel",
                    "name": channel_data.get("name"),
                    "is_channel": channel_data.get("is_channel"),
                    "is_group": channel_data.get("is_group"),
                    "is_im": channel_data.get("is_im"),
                    "is_mpim": channel_data.get("is_mpim"),
                }
    
    return resolutions


@dataclass
class WatchRule:
    """A single watch rule with pattern and shell command."""
    pattern: re.Pattern
    shell: str
    channel_id: str
    channel_name: str
    reply: bool = False  # If True, post shell output as a reply
    
    def matches(self, text: str) -> bool:
        """Check if text matches the pattern."""
        return bool(self.pattern.search(text or ""))


@dataclass
class WatchConfig:
    """Configuration for the watch engine."""
    rules: list[WatchRule] = field(default_factory=list)
    enabled: bool = False
    
    # Channel name to ID mapping (cached)
    _channel_cache: dict[str, str] = field(default_factory=dict)


class WatchEngine:
    """Engine for watching WebSocket messages and executing shell commands."""
    
    def __init__(
        self,
        resolve_channel_func: Optional[Callable] = None,
        post_message_func: Optional[Callable] = None,
        resolve_user_func: Optional[Callable] = None,
        fetch_context_func: Optional[Callable] = None,
    ):
        """Initialize the watch engine.
        
        Args:
            resolve_channel_func: Async function to resolve channel name to ID.
                                  Signature: async (name: str) -> str | None
            post_message_func: Async function to post a message to Slack.
                               Signature: async (channel: str, text: str, thread_ts: str | None) -> bool
            resolve_user_func: Async function to resolve and cache a user.
                               Signature: async (user_id: str) -> dict | None
            fetch_context_func: Async function to fetch surrounding message context.
                                Signature: async (channel: str, ts: str, thread_ts: str | None) -> list[dict]
        """
        self.config = WatchConfig()
        self._resolve_channel = resolve_channel_func
        self._post_message = post_message_func
        self._resolve_user = resolve_user_func
        self._fetch_context = fetch_context_func
        self._running = False
        self._stats = {
            "messages_processed": 0,
            "messages_matched": 0,
            "commands_executed": 0,
            "replies_posted": 0,
            "duplicates_skipped": 0,
            "errors": 0,
        }
    
    async def load_config(self) -> bool:
        """Load watch configuration from config.yaml.
        
        Returns:
            True if config loaded successfully, False otherwise.
        """
        if not WATCH_CONFIG_FILE.exists():
            logger.info(f"No watch config found at {WATCH_CONFIG_FILE}")
            return False
        
        try:
            with open(WATCH_CONFIG_FILE) as f:
                data = yaml.safe_load(f)
            
            if not data or "watch" not in data:
                logger.warning("config.yaml exists but has no 'watch' section")
                return False
            
            watch_data = data["watch"]
            rules = []
            
            for channel_name, channel_rules in watch_data.items():
                # Resolve channel name to ID
                channel_id = await self._resolve_channel_name(channel_name)
                if not channel_id:
                    logger.warning(f"Could not resolve channel '{channel_name}', skipping")
                    continue
                
                for rule_data in channel_rules:
                    pattern_str = rule_data.get("pattern", ".*")
                    shell_cmd = rule_data.get("shell", "")
                    reply_enabled = rule_data.get("reply", False)
                    
                    if not shell_cmd:
                        logger.warning(f"Rule for {channel_name} has no shell command, skipping")
                        continue
                    
                    # Compile regex pattern
                    try:
                        # Case-insensitive by default
                        flags = re.IGNORECASE if rule_data.get("case_insensitive", True) else 0
                        pattern = re.compile(pattern_str, flags)
                    except re.error as e:
                        logger.error(f"Invalid regex pattern '{pattern_str}': {e}")
                        continue
                    
                    rules.append(WatchRule(
                        pattern=pattern,
                        shell=shell_cmd,
                        channel_id=channel_id,
                        channel_name=channel_name,
                        reply=reply_enabled,
                    ))
                    reply_indicator = " [reply]" if reply_enabled else ""
                    logger.info(f"Loaded rule: {channel_name} ({channel_id}) -> {pattern_str}{reply_indicator}")
            
            self.config.rules = rules
            logger.info(f"Loaded {len(rules)} watch rules")
            return len(rules) > 0
            
        except Exception as e:
            logger.error(f"Failed to load watch config: {e}")
            return False
    
    async def _resolve_channel_name(self, name: str) -> Optional[str]:
        """Resolve a channel name to its ID.
        
        Supports:
        - Direct channel IDs (C..., G..., D...)
        - Channel names (with or without #)
        - DM usernames (dm-username)
        """
        # Check cache first
        if name in self.config._channel_cache:
            return self.config._channel_cache[name]
        
        # Already an ID?
        if re.match(r'^[CDG][A-Z0-9]{8,}$', name):
            self.config._channel_cache[name] = name
            return name
        
        # Use resolver function if provided
        if self._resolve_channel:
            try:
                channel_id = await self._resolve_channel(name)
                if channel_id:
                    self.config._channel_cache[name] = channel_id
                    return channel_id
            except Exception as e:
                logger.error(f"Failed to resolve channel '{name}': {e}")
        
        return None
    
    def start(self):
        """Start the watch engine."""
        self._running = True
        self.config.enabled = True
        logger.info("Watch engine started")
    
    def stop(self):
        """Stop the watch engine."""
        self._running = False
        self.config.enabled = False
        logger.info("Watch engine stopped")
    
    def is_running(self) -> bool:
        """Check if engine is running."""
        return self._running
    
    def get_stats(self) -> dict:
        """Get engine statistics."""
        return {
            **self._stats,
            "running": self._running,
            "rules_loaded": len(self.config.rules),
        }
    
    async def process_message(self, message: dict) -> bool:
        """Process an incoming WebSocket message.
        
        Args:
            message: The WebSocket message payload
            
        Returns:
            True if message matched a rule and was processed
        """
        if not self._running:
            return False
        
        # Only process actual messages (not subtypes like message_changed, etc.)
        msg_type = message.get("type")
        if msg_type != "message":
            return False
        
        # Skip messages with subtypes (edits, deletions, etc.)
        if message.get("subtype"):
            return False
        
        channel = message.get("channel", "")
        ts = message.get("ts", "")
        text = message.get("text", "")
        user = message.get("user", "")
        thread_ts = message.get("thread_ts")
        
        # Deduplication check
        if self._is_duplicate(channel, ts):
            self._stats["duplicates_skipped"] += 1
            return False
        
        self._stats["messages_processed"] += 1
        
        # Find matching rules
        for rule in self.config.rules:
            if rule.channel_id != channel:
                continue
            
            if rule.matches(text):
                logger.info(f"Message matched rule: {rule.channel_name} / {rule.pattern.pattern}")
                self._stats["messages_matched"] += 1
                
                # Execute shell command asynchronously
                asyncio.create_task(self._execute_shell(
                    rule=rule,
                    message=message,
                    channel=channel,
                    user=user,
                    ts=ts,
                    text=text,
                    thread_ts=thread_ts,
                ))
                return True
        
        return False
    
    def _is_duplicate(self, channel: str, ts: str) -> bool:
        """Check if message is a duplicate."""
        global _seen_messages
        
        key = (channel, ts)
        if key in _seen_messages:
            return True
        
        _seen_messages.add(key)
        
        # Trim cache if too large
        if len(_seen_messages) > MAX_SEEN:
            # Remove oldest half (simple strategy)
            to_remove = list(_seen_messages)[:MAX_SEEN // 2]
            for item in to_remove:
                _seen_messages.discard(item)
        
        return False
    
    async def _execute_shell(
        self,
        rule: WatchRule,
        message: dict,
        channel: str,
        user: str,
        ts: str,
        text: str,
        thread_ts: Optional[str],
    ):
        """Execute the shell command for a matched rule.
        
        Writes message to buffer file and executes shell with env vars.
        If rule.reply is True, posts stdout+stderr as a reply to the message.
        """
        try:
            # Write buffer file atomically
            buffer_data = {
                "type": "message",
                "channel": channel,
                "user": user,
                "text": text,
                "ts": ts,
                "thread_ts": thread_ts,
                "raw": message,
            }
            
            # Fetch surrounding context first (before resolution, so context IDs get resolved too)
            if self._fetch_context:
                try:
                    context = await self._fetch_context(channel, ts, thread_ts)
                    if context:
                        buffer_data["surrounding_context"] = context
                except Exception as e:
                    logger.warning(f"Failed to fetch surrounding context: {e}")
            
            # Extract and resolve all Slack identifiers from the full buffer (including context)
            slack_ids = _extract_slack_ids(buffer_data)
            resolutions = await _resolve_slack_ids_async(slack_ids, self._resolve_user)
            if resolutions:
                buffer_data["resolutions"] = resolutions
            
            # Write to temp file first, then rename for atomicity
            temp_path = BUFFER_FILE.with_suffix(".tmp")
            with open(temp_path, "w") as f:
                json.dump(buffer_data, f, indent=2)
            temp_path.rename(BUFFER_FILE)
            
            # Set up environment variables
            env = os.environ.copy()
            env["_BUFFER"] = str(BUFFER_FILE)
            env["_CHANNEL"] = channel
            env["_USER"] = user
            env["_TS"] = ts
            env["_TEXT"] = text
            env["_THREAD_TS"] = thread_ts or ""
            
            # Execute shell command
            logger.info(f"Executing: {rule.shell[:50]}...")
            
            process = await asyncio.create_subprocess_shell(
                rule.shell,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(WORKSPACE_ROOT),
            )
            
            stdout, stderr = await process.communicate()
            
            if process.returncode == 0:
                self._stats["commands_executed"] += 1
                if stdout:
                    logger.debug(f"Shell stdout: {stdout.decode()[:200]}")
                
                # Post reply if enabled
                if rule.reply and self._post_message:
                    output = stdout.decode().strip()
                    if stderr:
                        stderr_text = stderr.decode().strip()
                        if stderr_text:
                            output = f"{output}\n{stderr_text}".strip()
                    
                    if output:
                        # Determine reply target:
                        # - If it's a thread reply (thread_ts exists), reply in that thread
                        # - If it's a new message, reply to it (creating a thread)
                        reply_thread_ts = thread_ts if thread_ts else ts
                        
                        try:
                            success = await self._post_message(channel, output, reply_thread_ts)
                            if success:
                                self._stats["replies_posted"] += 1
                                logger.info(f"Posted reply to {channel}:{reply_thread_ts}")
                            else:
                                self._stats["errors"] += 1
                                logger.error(f"Failed to post reply")
                        except Exception as pe:
                            self._stats["errors"] += 1
                            logger.error(f"Error posting reply: {pe}")
                    else:
                        logger.debug("Shell produced no output, skipping reply")
            else:
                self._stats["errors"] += 1
                logger.error(f"Shell command failed (exit {process.returncode})")
                if stderr:
                    logger.error(f"Shell stderr: {stderr.decode()[:500]}")
                    
        except Exception as e:
            self._stats["errors"] += 1
            logger.error(f"Failed to execute shell command: {e}")


# Global watch engine instance (initialized by server)
_watch_engine: Optional[WatchEngine] = None


def get_watch_engine() -> Optional[WatchEngine]:
    """Get the global watch engine instance."""
    return _watch_engine


def set_watch_engine(engine: WatchEngine):
    """Set the global watch engine instance."""
    global _watch_engine
    _watch_engine = engine
