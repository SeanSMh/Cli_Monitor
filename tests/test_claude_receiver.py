import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from claude.receiver import map_hook_event, build_event_payload


def test_stop_maps_to_idle():
    assert map_hook_event("Stop") == "idle"


def test_preToolUse_maps_to_running():
    assert map_hook_event("PreToolUse") == "running"


def test_postToolUse_maps_to_running():
    assert map_hook_event("PostToolUse") == "running"


def test_unknown_hook_maps_to_running():
    assert map_hook_event("SomethingElse") == "running"


def test_subagent_stop_returns_none():
    assert map_hook_event("SubagentStop") is None


def test_build_event_payload_basic():
    hook_json = {
        "session_id": "abc-123",
        "hook_event_name": "Stop",
        "cwd": "/Users/sqb/project",
        "rateLimitResetAt": None,
    }
    payload = build_event_payload(hook_json)
    assert payload["session_id"] == "abc-123"
    assert payload["status"] == "idle"
    assert payload["tool_name"] == "claude"
    assert payload["source"] == "claude_hook"
    assert payload["event_type"] == "Stop"
    assert payload["cwd"] == "/Users/sqb/project"
    assert payload["rateLimitResetAt"] is None


def test_build_event_payload_rate_limit():
    hook_json = {
        "session_id": "abc-123",
        "hook_event_name": "Stop",
        "cwd": "/x",
        "rateLimitResetAt": "2026-03-30T10:00:00Z",
    }
    payload = build_event_payload(hook_json)
    assert payload["rateLimitResetAt"] == "2026-03-30T10:00:00Z"


def test_build_event_payload_subagent_stop_returns_none():
    hook_json = {
        "session_id": "abc-123",
        "hook_event_name": "SubagentStop",
        "cwd": "/x",
        "rateLimitResetAt": None,
    }
    assert build_event_payload(hook_json) is None
