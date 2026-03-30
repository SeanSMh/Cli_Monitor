#!/usr/bin/env python3
"""
statusCommand receiver for Claude Code.
Claude Code forks this script on every state change and pipes JSON to stdin.
Must exit in <200ms. Never raises — errors go to /tmp/cli_monitor_claude_err.log.
"""
from __future__ import annotations

import json
import sys
import time
import http.client
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

_DAEMON_URL = "http://127.0.0.1:8766/events"
_ERR_LOG = Path("/tmp/cli_monitor_claude_err.log")

_HOOK_STATUS_MAP: dict[str, str] = {
    "Stop": "idle",
    "PreToolUse": "running",
    "PostToolUse": "running",
}


def map_hook_event(hook_event_name: str) -> str | None:
    """Return internal status string, or None if this event should be skipped."""
    if hook_event_name == "SubagentStop":
        return None
    return _HOOK_STATUS_MAP.get(hook_event_name, "running")


def build_event_payload(hook_json: dict[str, Any]) -> dict[str, Any] | None:
    """Build daemon event payload from Claude hook JSON; returns None for skipped events."""
    hook_event_name = str(hook_json.get("hook_event_name", "") or "")
    status = map_hook_event(hook_event_name)
    if status is None:
        return None
    return {
        "session_id": str(hook_json.get("session_id", "") or ""),
        "source": "claude_hook",
        "tool_name": "claude",
        "event_type": hook_event_name,
        "status": status,
        "cwd": str(hook_json.get("cwd", "") or ""),
        "rateLimitResetAt": hook_json.get("rateLimitResetAt"),
        "ts_ms": int(time.time() * 1000),
    }


def _post_to_daemon(payload: dict[str, Any]) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        _DAEMON_URL,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=0.15) as resp:
            resp.read()
    except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError):
        pass  # daemon not running — silent fallback


def _maybe_start_subagent_watcher(hook_json: dict[str, Any]) -> None:
    """Start a subagent watcher for this session if project dir is locatable."""
    try:
        from claude.project_locator import find_project_dir
        from claude.subagent_watcher import ensure_watcher
        session_id = str(hook_json.get("session_id", "") or "")
        cwd = str(hook_json.get("cwd", "") or "")
        if not session_id:
            return
        proj_dir = find_project_dir(session_id, cwd)
        if proj_dir is None:
            return
        subagents_dir = proj_dir / "subagents"
        ensure_watcher(session_id, subagents_dir)
    except Exception:
        pass


def main() -> None:
    try:
        raw = sys.stdin.read()
        hook_json = json.loads(raw) if raw.strip() else {}
        payload = build_event_payload(hook_json)
        if payload is not None:
            _post_to_daemon(payload)
            _maybe_start_subagent_watcher(hook_json)
    except Exception as exc:  # noqa: BLE001
        try:
            with _ERR_LOG.open("a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} ERROR: {exc}\n")
        except Exception:
            pass


if __name__ == "__main__":
    main()
