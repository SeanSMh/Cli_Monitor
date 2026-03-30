import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.models import MonitorEvent, TaskState, STATUS_IDLE, STATUS_RUNNING, STATUS_RATE_LIMITED
from engine.reducer import reduce_event


def _make_event(event_type: str, status: str, rate_limit_reset_at=None, session_id="sess-1") -> MonitorEvent:
    return MonitorEvent(
        source="claude_hook",
        session_id=session_id,
        tool_name="claude",
        event_type=event_type,
        payload={
            "status": status,
            "cwd": "/x",
            "rateLimitResetAt": rate_limit_reset_at,
        },
        ts_ms=int(time.time() * 1000),
    )


def test_stop_produces_idle_state():
    event = _make_event("Stop", "idle")
    state = reduce_event(None, event)
    assert state is not None
    assert state.status == STATUS_IDLE
    assert state.tool_name == "claude"
    assert state.session_id == "sess-1"


def test_preToolUse_produces_running_state():
    event = _make_event("PreToolUse", "running")
    state = reduce_event(None, event)
    assert state.status == STATUS_RUNNING


def test_rate_limit_event_produces_rate_limited_state():
    event = _make_event("Stop", "idle", rate_limit_reset_at="2026-03-30T10:00:00Z")
    state = reduce_event(None, event)
    assert state.status == STATUS_RATE_LIMITED
    assert state.rate_limit_reset_at == "2026-03-30T10:00:00Z"


def test_normal_event_after_rate_limit_clears_it():
    prev = TaskState(
        session_id="sess-1",
        tool_name="claude",
        status=STATUS_RATE_LIMITED,
        rate_limit_reset_at="2026-03-30T10:00:00Z",
    )
    event = _make_event("Stop", "idle")
    state = reduce_event(prev, event)
    assert state.status == STATUS_IDLE
    assert state.rate_limit_reset_at is None


def test_unknown_source_still_returns_previous():
    event = MonitorEvent(
        source="unknown_source",
        session_id="sess-1",
        tool_name="claude",
        event_type="Stop",
        payload={},
        ts_ms=int(time.time() * 1000),
    )
    prev = TaskState(session_id="sess-1", tool_name="claude", status=STATUS_IDLE)
    state = reduce_event(prev, event)
    assert state is prev  # unchanged
