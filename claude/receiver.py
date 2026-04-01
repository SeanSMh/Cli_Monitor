#!/usr/bin/env python3
"""
Hook receiver for Claude Code.
Claude Code forks this script on every hook event and pipes JSON to stdin.
Must exit in <200ms. Never raises — errors go to /tmp/cli_monitor_claude_err.log.

Set CLI_MONITOR_RECEIVER_DIAG=1 to enable diagnostic logging to
/tmp/cli_monitor_claude_diag.log (for debugging hook payloads).
"""
from __future__ import annotations

import json
import os
import sys
import time
import http.client
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

_DAEMON_URL = "http://127.0.0.1:8766/events"
_ERR_LOG = Path("/tmp/cli_monitor_claude_err.log")
_DIAG_LOG = Path("/tmp/cli_monitor_claude_diag.log")

# Maps standard hook event names → session status
_MAIN_STATUS_MAP: dict[str, str] = {
    "Stop": "idle",
    "PreToolUse": "running",
    "PostToolUse": "running",
}

# Maps notification_type (lowercase) → internal status string
# Unknown types return None → silently skip (fail-closed)
_NOTIFICATION_TYPE_MAP: dict[str, str] = {
    "idle": "idle",
    "agent_idle": "idle",
    "rate_limit": "rate_limited",
    "permission": "waiting",
}


def _diag(label: str, data: Any) -> None:
    """Write diagnostic line if CLI_MONITOR_RECEIVER_DIAG is set."""
    if not os.environ.get("CLI_MONITOR_RECEIVER_DIAG"):
        return
    try:
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{label}] {json.dumps(data, ensure_ascii=False)}\n"
        with _DIAG_LOG.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def build_event_payload(hook_json: dict[str, Any]) -> dict[str, Any] | None:
    """Build daemon event payload from Claude hook JSON; returns None for skipped events."""
    event_name = str(hook_json.get("hook_event_name", "") or "")
    _diag("hook_received", {"event": event_name, "keys": list(hook_json.keys())})

    # --- SubagentStop ---
    if event_name == "SubagentStop":
        parent_sid = str(hook_json.get("session_id", "") or "")
        # Try multiple candidate field names in case the schema evolves
        agent_id = (
            str(hook_json.get("agent_id", "") or "")
            or str(hook_json.get("subagent_id", "") or "")
            or str(hook_json.get("child_session_id", "") or "")
        )
        if not parent_sid or not agent_id:
            _diag("SubagentStop_skip", {"reason": "missing ids", "parent_sid": parent_sid, "agent_id": agent_id})
            return None
        return {
            "source": "claude_hook",
            "session_id": parent_sid,
            "tool_name": "claude",
            "event_type": "SubagentStop",
            "payload": {
                "parent_session_id": parent_sid,
                "subagent_id": agent_id,
                "status": "done",
                "agent_type": str(hook_json.get("agent_type", "") or ""),
                "agent_transcript_path": str(hook_json.get("agent_transcript_path", "") or ""),
            },
            "ts_ms": int(time.time() * 1000),
        }

    # --- Notification ---
    if event_name == "Notification":
        ntype = str(hook_json.get("notification_type", "") or "").lower()
        status = _NOTIFICATION_TYPE_MAP.get(ntype)
        if status is None:
            _diag("Notification_skip", {"reason": "unknown notification_type", "ntype": ntype})
            return None
        return {
            "source": "claude_hook",
            "session_id": str(hook_json.get("session_id", "") or ""),
            "tool_name": "claude",
            "event_type": f"Notification_{ntype}",
            "payload": {
                "status": status,
                "message": str(hook_json.get("message", "") or ""),
                "title": str(hook_json.get("title", "") or ""),
                "notification_type": ntype,
                "rateLimitResetAt": hook_json.get("rateLimitResetAt"),
            },
            "ts_ms": int(time.time() * 1000),
        }

    # --- Standard events (Stop / PreToolUse / PostToolUse) ---
    status = _MAIN_STATUS_MAP.get(event_name)
    if status is None:
        # Unknown event — silently ignore
        _diag("event_skip", {"reason": "unknown event", "event": event_name})
        return None
    return {
        "source": "claude_hook",
        "session_id": str(hook_json.get("session_id", "") or ""),
        "tool_name": "claude",
        "event_type": event_name,
        "payload": {
            "status": status,
            "cwd": str(hook_json.get("cwd", "") or ""),
            "rateLimitResetAt": hook_json.get("rateLimitResetAt"),
        },
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


def main() -> None:
    try:
        raw = sys.stdin.read()
        hook_json = json.loads(raw) if raw.strip() else {}
        payload = build_event_payload(hook_json)
        if payload is not None:
            _post_to_daemon(payload)
    except Exception as exc:  # noqa: BLE001
        try:
            with _ERR_LOG.open("a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} ERROR: {exc}\n")
        except Exception:
            pass


if __name__ == "__main__":
    main()
