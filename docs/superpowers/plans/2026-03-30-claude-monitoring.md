# Claude Code Monitoring Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace heuristic Claude status detection with event-driven updates via Claude Code's `statusCommand` hook, add rate-limit state, precise session routing, and subagent tree monitoring.

**Architecture:** New `claude/` subpackage isolates all Claude-specific logic. `claude/receiver.py` is the `statusCommand` entry point — it reads stdin JSON from Claude Code and POSTs to the existing `monitord` daemon at `localhost:8766/events`. Subagent monitoring runs as a background watchdog thread inside `claude/subagent_watcher.py`.

**Tech Stack:** Python 3.10+, stdlib only for Phase 1–2 (`json`, `urllib.request`, `pathlib`), `watchdog` for Phase 3 (already available in project).

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `claude/__init__.py` | Create | Package marker |
| `claude/install.py` | Create | Read/write `~/.claude/settings.json` |
| `claude/receiver.py` | Create | `statusCommand` entry point → POST to daemon |
| `claude/project_locator.py` | Create | Map `session_id` → `~/.claude/projects/<hash>/` |
| `claude/subagent_watcher.py` | Create | Watchdog thread for subagents/ directory |
| `engine/models.py` | Modify | Add `RATE_LIMITED`, `rate_limit_reset_at`, `SubagentState`, `subagents` field |
| `engine/reducer.py` | Modify | Add `_map_claude_hook_event()` handler |
| `monitor.py` | Modify | Poll daemon for Claude tasks, `DisplayTask` dataclass, RATE_LIMITED row, tree rendering |
| `install.sh` | Modify | Call `claude/install.py install` after rc injection |
| `uninstall.sh` | Modify | Call `claude/install.py uninstall` before log cleanup |
| `tests/test_claude_install.py` | Create | Tests for install/uninstall of settings.json |
| `tests/test_claude_receiver.py` | Create | Tests for hook_event_name mapping and payload building |
| `tests/test_claude_reducer.py` | Create | Tests for claude_hook reducer logic |
| `tests/test_claude_project_locator.py` | Create | Tests for session_id → project dir lookup |

---

## Phase 1 — P0: statusCommand Receiver

### Task 1: `claude/install.py` — inject statusCommand into Claude settings

**Files:**
- Create: `claude/__init__.py`
- Create: `claude/install.py`
- Create: `tests/test_claude_install.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_claude_install.py
import json
import sys
import os
from pathlib import Path
import pytest

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from claude.install import install, uninstall, SETTINGS_PATH


def test_install_writes_status_command(tmp_path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr("claude.install.SETTINGS_PATH", settings_file)
    install(receiver_abs_path="/abs/path/claude/receiver.py")
    data = json.loads(settings_file.read_text())
    assert data["statusCommand"] == "python3 /abs/path/claude/receiver.py"


def test_install_preserves_existing_fields(tmp_path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({"theme": "dark", "other": 42}))
    monkeypatch.setattr("claude.install.SETTINGS_PATH", settings_file)
    install(receiver_abs_path="/abs/path/claude/receiver.py")
    data = json.loads(settings_file.read_text())
    assert data["theme"] == "dark"
    assert data["other"] == 42
    assert "statusCommand" in data


def test_install_creates_backup(tmp_path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({"existing": True}))
    monkeypatch.setattr("claude.install.SETTINGS_PATH", settings_file)
    install(receiver_abs_path="/x/receiver.py")
    backup = tmp_path / "settings.json.cli-monitor-backup"
    assert backup.exists()
    assert json.loads(backup.read_text())["existing"] is True


def test_install_works_when_settings_missing(tmp_path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr("claude.install.SETTINGS_PATH", settings_file)
    install(receiver_abs_path="/x/receiver.py")
    data = json.loads(settings_file.read_text())
    assert "statusCommand" in data


def test_uninstall_removes_status_command(tmp_path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({"statusCommand": "python3 /x/receiver.py", "theme": "dark"}))
    monkeypatch.setattr("claude.install.SETTINGS_PATH", settings_file)
    uninstall()
    data = json.loads(settings_file.read_text())
    assert "statusCommand" not in data
    assert data["theme"] == "dark"


def test_uninstall_is_noop_when_settings_missing(tmp_path, monkeypatch):
    settings_file = tmp_path / "nonexistent.json"
    monkeypatch.setattr("claude.install.SETTINGS_PATH", settings_file)
    uninstall()  # should not raise
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /Users/sqb/projects/cli-monitor
python3 -m pytest tests/test_claude_install.py -v 2>&1 | head -30
```

Expected: `ModuleNotFoundError: No module named 'claude'`

- [ ] **Step 3: Create `claude/__init__.py`**

```python
# claude/__init__.py
```

- [ ] **Step 4: Create `claude/install.py`**

```python
#!/usr/bin/env python3
"""Inject/remove statusCommand in ~/.claude/settings.json."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

SETTINGS_PATH = Path.home() / ".claude" / "settings.json"


def install(receiver_abs_path: str) -> None:
    settings = {}
    if SETTINGS_PATH.exists():
        try:
            settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except Exception:
            settings = {}
        backup = SETTINGS_PATH.with_suffix(".json.cli-monitor-backup")
        shutil.copy2(SETTINGS_PATH, backup)
    settings["statusCommand"] = f"python3 {receiver_abs_path}"
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")


def uninstall() -> None:
    if not SETTINGS_PATH.exists():
        return
    try:
        settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return
    settings.pop("statusCommand", None)
    SETTINGS_PATH.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "install":
        if len(sys.argv) < 3:
            print("Usage: claude/install.py install <receiver_abs_path>", file=sys.stderr)
            sys.exit(1)
        install(sys.argv[2])
        print(f"  ✅ statusCommand injected into {SETTINGS_PATH}")
    elif cmd == "uninstall":
        uninstall()
        print(f"  ✅ statusCommand removed from {SETTINGS_PATH}")
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)
```

- [ ] **Step 5: Run tests to confirm they pass**

```bash
cd /Users/sqb/projects/cli-monitor
python3 -m pytest tests/test_claude_install.py -v
```

Expected: all 6 tests PASS

- [ ] **Step 6: Commit**

```bash
git add claude/__init__.py claude/install.py tests/test_claude_install.py
git commit -m "feat(claude): add install.py to manage statusCommand in Claude settings"
```

---

### Task 2: `claude/receiver.py` — statusCommand entry point

**Files:**
- Create: `claude/receiver.py`
- Create: `tests/test_claude_receiver.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_claude_receiver.py
import json
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
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /Users/sqb/projects/cli-monitor
python3 -m pytest tests/test_claude_receiver.py -v 2>&1 | head -20
```

Expected: `ImportError: cannot import name 'map_hook_event'`

- [ ] **Step 3: Create `claude/receiver.py`**

```python
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
    except (urllib.error.URLError, TimeoutError, OSError):
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
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
cd /Users/sqb/projects/cli-monitor
python3 -m pytest tests/test_claude_receiver.py -v
```

Expected: all 8 tests PASS

- [ ] **Step 5: Commit**

```bash
git add claude/receiver.py tests/test_claude_receiver.py
git commit -m "feat(claude): add receiver.py as statusCommand entry point"
```

---

### Task 3: Extend `install.sh` and `uninstall.sh`

**Files:**
- Modify: `install.sh`
- Modify: `uninstall.sh`

- [ ] **Step 1: Add claude install call to `install.sh`**

Find the line `echo "${GREEN}✅ 安装成功!${RESET}"` in `install.sh` and insert the claude install call before it:

```bash
# Insert before the success echo in install.sh:

# 5. 注入 Claude Code statusCommand
RECEIVER_PATH="$SCRIPT_DIR/claude/receiver.py"
if [[ -f "$RECEIVER_PATH" ]]; then
    python3 "$SCRIPT_DIR/claude/install.py" install "$RECEIVER_PATH" 2>/dev/null || true
fi
```

The final block before `echo "${GREEN}✅ 安装成功!${RESET}"` should look like:

```bash
# 4. 写入 RC 文件
{
    echo ""
    echo "$MARKER_START"
    echo "# CLI Monitor: Logcat Mode (终端任务状态监控)"
    echo "# 项目路径: $SCRIPT_DIR"
    echo "$SOURCE_LINE"
    echo "$MARKER_END"
} >> "$RC_FILE"

# 5. 注入 Claude Code statusCommand
RECEIVER_PATH="$SCRIPT_DIR/claude/receiver.py"
if [[ -f "$RECEIVER_PATH" ]]; then
    python3 "$SCRIPT_DIR/claude/install.py" install "$RECEIVER_PATH" 2>/dev/null || true
fi

echo "${GREEN}✅ 安装成功!${RESET}"
```

- [ ] **Step 2: Add claude uninstall call to `uninstall.sh`**

Find `# 2. 清理日志目录` in `uninstall.sh` and insert before it:

```bash
# 1b. 移除 Claude Code statusCommand
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_PY="$SCRIPT_DIR/claude/install.py"
if [[ -f "$INSTALL_PY" ]]; then
    python3 "$INSTALL_PY" uninstall 2>/dev/null || true
fi
```

- [ ] **Step 3: Manual smoke test**

```bash
cd /Users/sqb/projects/cli-monitor
# Verify install.py can be called directly
python3 claude/install.py install "$(pwd)/claude/receiver.py"
# Verify settings.json was written
python3 -c "import json,os; d=json.load(open(os.path.expanduser('~/.claude/settings.json'))); print('statusCommand' in d)"
# Should print: True

# Verify uninstall
python3 claude/install.py uninstall
python3 -c "import json,os; d=json.load(open(os.path.expanduser('~/.claude/settings.json'))); print('statusCommand' in d)"
# Should print: False
```

- [ ] **Step 4: Restore the install (you actually want it installed)**

```bash
cd /Users/sqb/projects/cli-monitor
python3 claude/install.py install "$(pwd)/claude/receiver.py"
```

- [ ] **Step 5: Commit**

```bash
git add install.sh uninstall.sh
git commit -m "feat(install): inject Claude Code statusCommand on install/uninstall"
```

---

## Phase 2 — P1 + P2: Rate Limit State & Session Routing

### Task 4: Extend `engine/models.py` with RATE_LIMITED and SubagentState

**Files:**
- Modify: `engine/models.py`

- [ ] **Step 1: Read current `engine/models.py`** (already done; see File Map above)

- [ ] **Step 2: Add RATE_LIMITED constant, `rate_limit_reset_at` field, and `SubagentState`**

Replace the entire `engine/models.py` with:

```python
#!/usr/bin/env python3
"""Shared state models for cli-monitor."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# Status string constants
STATUS_RUNNING = "RUNNING"
STATUS_WAITING = "WAITING"
STATUS_WAITING_APPROVAL = "WAITING_APPROVAL"
STATUS_WAITING_INPUT = "WAITING_INPUT"
STATUS_IDLE = "IDLE"
STATUS_DONE = "DONE"
STATUS_ERROR = "ERROR"
STATUS_RATE_LIMITED = "RATE_LIMITED"


@dataclass
class SubagentState:
    subagent_id: str
    status: str                # STATUS_RUNNING or STATUS_IDLE
    started_at: float          # unix timestamp
    last_active_at: float      # unix timestamp

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TaskState:
    session_id: str
    tool_name: str
    status: str
    message: str = ""
    thread_id: str = ""
    source: str = ""
    updated_at_ms: int = 0
    log_file: str = ""
    meta: dict[str, Any] = field(default_factory=dict)
    rate_limit_reset_at: str | None = None          # ISO 8601, set when status == STATUS_RATE_LIMITED
    subagents: list[SubagentState] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


@dataclass
class MonitorEvent:
    source: str
    session_id: str
    tool_name: str
    event_type: str
    payload: Any
    ts_ms: int
    thread_id: str = ""
    log_file: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
```

- [ ] **Step 3: Run existing tests to confirm nothing broke**

```bash
cd /Users/sqb/projects/cli-monitor
python3 -m pytest tests/test_engine_reducer.py -v 2>&1 | tail -20
```

Expected: existing tests still pass (new fields have defaults, backward compatible)

- [ ] **Step 4: Commit**

```bash
git add engine/models.py
git commit -m "feat(engine): add RATE_LIMITED status, SubagentState, and rate_limit_reset_at field"
```

---

### Task 5: Add claude_hook handler to `engine/reducer.py`

**Files:**
- Modify: `engine/reducer.py`
- Create: `tests/test_claude_reducer.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_claude_reducer.py
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
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /Users/sqb/projects/cli-monitor
python3 -m pytest tests/test_claude_reducer.py -v 2>&1 | tail -20
```

Expected: tests for claude_hook fail (no handler yet)

- [ ] **Step 3: Add `_map_claude_hook_event` to `engine/reducer.py`**

Add after the existing imports and constants (after line 17 `WAITING_STATUSES = ...`):

```python
from engine.models import STATUS_IDLE, STATUS_RUNNING, STATUS_RATE_LIMITED

_CLAUDE_HOOK_STATUS_MAP: dict[str, str] = {
    "idle": STATUS_IDLE,
    "running": STATUS_RUNNING,
}


def _map_claude_hook_event(previous: TaskState | None, event: MonitorEvent) -> tuple[str, str] | None:
    payload = event.payload if isinstance(event.payload, dict) else {}
    rate_limit = payload.get("rateLimitResetAt")
    status_str = str(payload.get("status", "") or "").lower()

    if rate_limit:
        return STATUS_RATE_LIMITED, ""

    mapped = _CLAUDE_HOOK_STATUS_MAP.get(status_str)
    if mapped is None:
        return None
    msg = "等待输入" if mapped == STATUS_IDLE else "运行中..."
    return mapped, msg
```

Then in `reduce_event()`, add the claude_hook branch before the existing `if source == "codex_proxy":` check:

```python
def reduce_event(previous: TaskState | None, event: MonitorEvent) -> TaskState | None:
    source = str(event.source or "").strip().lower()
    mapped: tuple[str, str] | None = None

    if source == "claude_hook":                          # ← new branch
        mapped = _map_claude_hook_event(previous, event)

    elif source == "codex_proxy":
        mapped = _map_codex_proxy_event(previous, event)
        if mapped is None:
            mapped = _map_server_request(event.event_type, event.payload)

    if mapped is None:
        return previous

    status, message = mapped
    prev_meta = dict(previous.meta) if previous else {}
    merged_meta = dict(prev_meta)
    merged_meta.update(event.meta or {})
    if event.log_file:
        merged_meta["log_file"] = event.log_file

    # Determine rate_limit_reset_at
    payload = event.payload if isinstance(event.payload, dict) else {}
    new_rate_limit = payload.get("rateLimitResetAt") if source == "claude_hook" else None
    if status != STATUS_RATE_LIMITED:
        new_rate_limit = None

    # Preserve subagents from previous state
    prev_subagents = previous.subagents if previous else []

    if status in WAITING_STATUSES:
        if previous and previous.status not in WAITING_STATUSES:
            merged_meta["waiting_restore_status"] = previous.status
            merged_meta["waiting_restore_message"] = previous.message
        else:
            merged_meta.setdefault("waiting_restore_status", "RUNNING")
            merged_meta.setdefault("waiting_restore_message", "运行中...")
        request_id = _extract_request_id(event.payload, event.meta)
        if request_id:
            merged_meta["pending_request_id"] = request_id
    else:
        merged_meta.pop("pending_request_id", None)
        merged_meta.pop("waiting_restore_status", None)
        merged_meta.pop("waiting_restore_message", None)

    return TaskState(
        session_id=event.session_id,
        tool_name=event.tool_name,
        status=status,
        message=_clip_message(message, limit=200),
        thread_id=event.thread_id or _extract_thread_id(event.payload) or (previous.thread_id if previous else ""),
        source=source,
        updated_at_ms=int(event.ts_ms or now_ms()),
        log_file=event.log_file or (previous.log_file if previous else ""),
        meta=merged_meta,
        rate_limit_reset_at=new_rate_limit,
        subagents=list(prev_subagents),
    )
```

- [ ] **Step 4: Run tests**

```bash
cd /Users/sqb/projects/cli-monitor
python3 -m pytest tests/test_claude_reducer.py tests/test_engine_reducer.py -v
```

Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add engine/reducer.py tests/test_claude_reducer.py
git commit -m "feat(engine): add claude_hook reducer handler with RATE_LIMITED support"
```

---

### Task 6: Extend `monitor.py` — poll daemon for Claude tasks and add RATE_LIMITED display

**Files:**
- Modify: `monitor.py`

This task introduces a `DisplayTask` dataclass to replace raw tuples, adds daemon polling for Claude sessions, and renders `RATE_LIMITED` with a countdown.

- [ ] **Step 1: Add `DisplayTask` dataclass near the top of `monitor.py`** (after the imports, before the constants):

```python
from dataclasses import dataclass, field as dc_field

@dataclass
class DisplayTask:
    tool_name: str
    status: str
    message: str
    exit_code: int = -1
    duration: str = ""
    signal_ts: int = 0
    session_id: str = ""
    rate_limit_reset_at: str | None = None
    subagents: list = dc_field(default_factory=list)
```

- [ ] **Step 2: Update `_return_status` helper to return `DisplayTask`**

Search for `def _return_status(` in `monitor.py`. It currently returns a tuple. Change its return type to `DisplayTask`. The function signature and body:

```python
def _return_status(
    tool_name, status, message, exit_code, duration, signal_ts,
    codex_source=None, codex_proxy_backed=False
) -> DisplayTask:
    if tool_name == "codex":
        _record_codex_parse_hit(codex_source or "text", proxy_backed=codex_proxy_backed)
    return DisplayTask(
        tool_name=tool_name,
        status=status,
        message=message,
        exit_code=exit_code,
        duration=duration,
        signal_ts=signal_ts,
    )
```

- [ ] **Step 3: Update `analyze_log` return annotation**

Change the return type comment in `analyze_log` from:
```python
# Returns: (tool_name, status, message, exit_code, duration, signal_ts)
```
to:
```python
# Returns: DisplayTask
```

And change all `return _return_status(...)` calls to return `DisplayTask` — they already do via the updated `_return_status`.

Also update the bare non-`_return_status` return at the start of `analyze_log`:
```python
# Find this line:
    if not lines:
        return tool_name, "RUNNING", "初始化...", -1, "", 0
# Replace with:
    if not lines:
        return DisplayTask(tool_name=tool_name, status="RUNNING", message="初始化...")
```

- [ ] **Step 4: Add `get_state` to top-level imports in `monitor.py`**

Find the existing import line:
```python
from daemon_client import get_session as get_monitord_session
```
Replace with:
```python
from daemon_client import get_session as get_monitord_session, get_state as get_daemon_state
```

- [ ] **Step 5: Update `MonitorCore._analyze_all` to merge daemon Claude tasks**

Replace the body of `_analyze_all`:

```python
def _analyze_all(self):
    """全量扫描 + 合并 daemon 中的 Claude 任务"""
    log_files = glob.glob(os.path.join(self.log_dir, "*.log"))
    log_files.sort(key=os.path.getmtime, reverse=True)
    active_files = log_files[:self.max_tasks]

    results: list[DisplayTask] = []
    for f in active_files:
        results.append(analyze_log(f))

    # Merge Claude sessions from daemon (higher priority, no log file needed)
    daemon_resp = get_daemon_state("claude")
    if daemon_resp and isinstance(daemon_resp.get("tasks"), list):
        seen_session_ids = {t.session_id for t in results if t.session_id}
        for task_dict in daemon_resp["tasks"]:
            sid = str(task_dict.get("session_id", "") or "")
            if not sid or sid in seen_session_ids:
                continue
            status_raw = str(task_dict.get("status", "") or "").upper()
            results.insert(0, DisplayTask(
                tool_name="claude",
                status=status_raw,
                message=str(task_dict.get("message", "") or ""),
                session_id=sid,
                rate_limit_reset_at=task_dict.get("rate_limit_reset_at"),
                subagents=[],
            ))

    results = results[:self.max_tasks]
    with self.lock:
        if results != self.tasks_cache:
            self.tasks_cache = results
            self.needs_render = True
```

- [ ] **Step 6: Update `render` to unpack `DisplayTask` and show RATE_LIMITED**

Find the for-loop in `render()`:
```python
for t in tasks:
    tool_name, status, msg, exit_code, duration, _ = t
```

Replace with:
```python
for t in tasks:
    tool_name = t.tool_name
    status = t.status
    msg = t.message
    exit_code = t.exit_code
    duration = t.duration
```

Add `RATE_LIMITED` to `format_status`:
```python
def format_status(status, exit_code=-1):
    if status == "WAITING": return f"{BLINK}{YELLOW}🟡 待确认{RESET}"
    if status == "WAITING_APPROVAL": return f"{BLINK}{YELLOW}🟡 待审批{RESET}"
    if status == "WAITING_INPUT": return f"{CYAN}🔵 待输入{RESET}"
    if status == "IDLE": return f"{CYAN}🔵 等待输入{RESET}"
    if status == "RUNNING": return f"{GREEN}🟢 运行中{RESET}"
    if status == "RATE_LIMITED": return f"{BLINK}{GRAY}🔴 限速中{RESET}"
    if status == "ERROR": return f"{GRAY}🔴 异常{RESET}"
    else:
        if exit_code == 0: return f"{GRAY}⚪ 已完成{RESET}"
        elif exit_code == 137: return f"{GRAY}⚪ 已关闭{RESET}"
        elif exit_code > 0: return f"{GRAY}🔴 异常退出({exit_code}){RESET}"
        else: return f"{GRAY}⚪ 已结束{RESET}"
```

Add countdown helper near `format_status`:
```python
def format_rate_limit_countdown(reset_at_iso: str | None) -> str:
    if not reset_at_iso:
        return ""
    try:
        from datetime import datetime, timezone
        reset = datetime.fromisoformat(reset_at_iso.replace("Z", "+00:00"))
        remaining = reset - datetime.now(timezone.utc)
        secs = max(0, int(remaining.total_seconds()))
        if secs == 0:
            return "解除中..."
        m, s = divmod(secs, 60)
        return f"  {m}m{s:02d}s 后解除"
    except Exception:
        return ""
```

In the render loop, update the `msg` line for RATE_LIMITED:
```python
    if status == "RATE_LIMITED" and t.rate_limit_reset_at:
        msg = format_rate_limit_countdown(t.rate_limit_reset_at)
    elif len(msg) > 24:
        msg = msg[:21] + "..."
    msg = re.sub(r'\033\[[0-9;]*m', '', msg)
    msg = re.sub(r'[\x00-\x1f\x7f]', '', msg)
```

- [ ] **Step 7: Run a quick sanity check**

```bash
cd /Users/sqb/projects/cli-monitor
python3 -c "from monitor import DisplayTask, format_status, format_rate_limit_countdown; print(format_status('RATE_LIMITED')); print(format_rate_limit_countdown('2099-01-01T12:00:00Z'))"
```

Expected: prints the RATE_LIMITED status string and a countdown.

- [ ] **Step 8: Commit**

```bash
git add monitor.py
git commit -m "feat(monitor): add DisplayTask, daemon Claude polling, and RATE_LIMITED display"
```

---

## Phase 3 — P3: Subagent Monitoring

### Task 7: `claude/project_locator.py` — map session_id to project directory

**Files:**
- Create: `claude/project_locator.py`
- Create: `tests/test_claude_project_locator.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_claude_project_locator.py
import json
import sys
import os
import time
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from claude.project_locator import find_project_dir, _clear_cache


def _make_fake_project(tmp_path: Path, session_id: str) -> Path:
    """Create a fake ~/.claude/projects/<hash>/ with a transcript.jsonl."""
    proj_dir = tmp_path / "projects" / "fakehash123"
    proj_dir.mkdir(parents=True)
    transcript = proj_dir / "transcript.jsonl"
    # Write a few lines, last one has our session_id
    lines = [
        json.dumps({"type": "other", "session_id": "old-session"}) + "\n",
        json.dumps({"type": "summary", "session_id": session_id}) + "\n",
    ]
    transcript.write_text("".join(lines))
    return proj_dir


def test_find_project_dir_by_scan(tmp_path, monkeypatch):
    _clear_cache()
    monkeypatch.setattr("claude.project_locator.CLAUDE_PROJECTS_DIR", tmp_path / "projects")
    proj_dir = _make_fake_project(tmp_path, "test-session-abc")
    result = find_project_dir(session_id="test-session-abc", cwd="/any")
    assert result == proj_dir


def test_find_project_dir_returns_none_when_not_found(tmp_path, monkeypatch):
    _clear_cache()
    monkeypatch.setattr("claude.project_locator.CLAUDE_PROJECTS_DIR", tmp_path / "empty_projects")
    result = find_project_dir(session_id="nonexistent-session", cwd="/any")
    assert result is None


def test_find_project_dir_caches_result(tmp_path, monkeypatch):
    _clear_cache()
    monkeypatch.setattr("claude.project_locator.CLAUDE_PROJECTS_DIR", tmp_path / "projects")
    proj_dir = _make_fake_project(tmp_path, "cached-session")
    r1 = find_project_dir("cached-session", "/any")
    r2 = find_project_dir("cached-session", "/any")
    assert r1 == r2 == proj_dir
```

- [ ] **Step 2: Run to confirm they fail**

```bash
cd /Users/sqb/projects/cli-monitor
python3 -m pytest tests/test_claude_project_locator.py -v 2>&1 | head -20
```

Expected: `ImportError: cannot import name 'find_project_dir'`

- [ ] **Step 3: Create `claude/project_locator.py`**

```python
#!/usr/bin/env python3
"""Map Claude Code session_id to its project directory under ~/.claude/projects/."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"

_cache: dict[str, Path] = {}


def _clear_cache() -> None:
    _cache.clear()


def _scan_for_session(session_id: str) -> Optional[Path]:
    if not CLAUDE_PROJECTS_DIR.exists():
        return None
    for proj_dir in CLAUDE_PROJECTS_DIR.iterdir():
        if not proj_dir.is_dir():
            continue
        transcript = proj_dir / "transcript.jsonl"
        if not transcript.exists():
            continue
        try:
            # Read last 40 lines efficiently
            text = transcript.read_bytes()
            tail = text[-8192:].decode("utf-8", errors="replace")
            for line in reversed(tail.splitlines()):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict) and obj.get("session_id") == session_id:
                        return proj_dir
                except (json.JSONDecodeError, ValueError):
                    continue
        except OSError:
            continue
    return None


def find_project_dir(session_id: str, cwd: str = "") -> Optional[Path]:
    if session_id in _cache:
        return _cache[session_id]
    result = _scan_for_session(session_id)
    if result is not None:
        _cache[session_id] = result
    return result
```

- [ ] **Step 4: Run tests**

```bash
cd /Users/sqb/projects/cli-monitor
python3 -m pytest tests/test_claude_project_locator.py -v
```

Expected: all 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add claude/project_locator.py tests/test_claude_project_locator.py
git commit -m "feat(claude): add project_locator to map session_id to project directory"
```

---

### Task 8: `claude/subagent_watcher.py` — background watchdog thread

**Files:**
- Create: `claude/subagent_watcher.py`

- [ ] **Step 1: Create `claude/subagent_watcher.py`**

```python
#!/usr/bin/env python3
"""
Background thread that watches ~/.claude/projects/<hash>/subagents/ for activity.
Posts subagent lifecycle events to monitord via daemon_client.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Optional

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler, FileSystemEvent
    _WATCHDOG_AVAILABLE = True
except ImportError:
    _WATCHDOG_AVAILABLE = False

_IDLE_THRESHOLD_SECS = 5.0
_IDLE_CHECK_INTERVAL = 2.0


class SubagentWatcher(threading.Thread):
    """Watches a single session's subagents/ directory."""

    def __init__(self, parent_session_id: str, subagents_dir: Path):
        super().__init__(daemon=True, name=f"subagent-watcher-{parent_session_id[:8]}")
        self.parent_session_id = parent_session_id
        self.subagents_dir = subagents_dir
        self._lock = threading.Lock()
        # subagent_id -> last_active_at (float unix ts)
        self._last_active: dict[str, float] = {}
        # subagent_id -> current status ("running" | "done")
        self._statuses: dict[str, str] = {}
        self._stop_event = threading.Event()
        self._observer: Optional[object] = None

    def _post_event(self, event_type: str, subagent_id: str, status: str = "") -> None:
        try:
            import sys, os
            sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            from daemon_client import post_event
            payload: dict = {
                "source": "claude_subagent",
                "session_id": f"{self.parent_session_id}__sub__{subagent_id}",
                "tool_name": "claude",
                "event_type": event_type,
                "payload": {
                    "parent_session_id": self.parent_session_id,
                    "subagent_id": subagent_id,
                    "status": status,
                },
                "ts_ms": int(time.time() * 1000),
            }
            post_event(payload)
        except Exception:
            pass

    def _on_subagent_active(self, subagent_id: str) -> None:
        with self._lock:
            now = time.time()
            prev_status = self._statuses.get(subagent_id)
            self._last_active[subagent_id] = now
            if prev_status != "running":
                self._statuses[subagent_id] = "running"
                event_type = "subagent_start" if prev_status is None else "subagent_status"
                self._post_event(event_type, subagent_id, "running")

    def _check_idle(self) -> None:
        now = time.time()
        with self._lock:
            for subagent_id, last_active in list(self._last_active.items()):
                if self._statuses.get(subagent_id) == "running":
                    if now - last_active >= _IDLE_THRESHOLD_SECS:
                        self._statuses[subagent_id] = "done"
                        self._post_event("subagent_done", subagent_id, "done")

    def _scan_existing(self) -> None:
        """Pick up subagents that already existed when watcher started."""
        if not self.subagents_dir.exists():
            return
        for sub_dir in self.subagents_dir.iterdir():
            if sub_dir.is_dir():
                transcript = sub_dir / "transcript.jsonl"
                if transcript.exists():
                    self._on_subagent_active(sub_dir.name)

    def stop(self) -> None:
        self._stop_event.set()
        if self._observer is not None:
            try:
                self._observer.stop()  # type: ignore[union-attr]
            except Exception:
                pass

    def run(self) -> None:
        self._scan_existing()

        if _WATCHDOG_AVAILABLE:
            self.subagents_dir.mkdir(parents=True, exist_ok=True)

            class _Handler(FileSystemEventHandler):
                def __init__(self_h):
                    pass

                def on_created(self_h, event: FileSystemEvent) -> None:
                    path = Path(event.src_path)
                    if path.suffix == "" and path.parent == self.subagents_dir:
                        # New subagent directory
                        self._on_subagent_active(path.name)
                    elif path.name == "transcript.jsonl":
                        subagent_id = path.parent.name
                        self._on_subagent_active(subagent_id)

                def on_modified(self_h, event: FileSystemEvent) -> None:
                    path = Path(event.src_path)
                    if path.name == "transcript.jsonl":
                        self._on_subagent_active(path.parent.name)

            self._observer = Observer()
            self._observer.schedule(_Handler(), str(self.subagents_dir), recursive=True)  # type: ignore[union-attr]
            self._observer.start()  # type: ignore[union-attr]

        while not self._stop_event.is_set():
            self._check_idle()
            self._stop_event.wait(timeout=_IDLE_CHECK_INTERVAL)

        if self._observer is not None:
            self._observer.join(timeout=2.0)  # type: ignore[union-attr]


# Module-level registry: session_id -> SubagentWatcher
_watchers: dict[str, SubagentWatcher] = {}
_watchers_lock = threading.Lock()


def ensure_watcher(parent_session_id: str, subagents_dir: Path) -> SubagentWatcher:
    """Start a watcher for this session if not already running."""
    with _watchers_lock:
        existing = _watchers.get(parent_session_id)
        if existing is not None and existing.is_alive():
            return existing
        watcher = SubagentWatcher(parent_session_id, subagents_dir)
        watcher.start()
        _watchers[parent_session_id] = watcher
        return watcher


def stop_all() -> None:
    with _watchers_lock:
        for w in _watchers.values():
            w.stop()
        _watchers.clear()
```

- [ ] **Step 2: Smoke test**

```bash
cd /Users/sqb/projects/cli-monitor
python3 -c "from claude.subagent_watcher import SubagentWatcher, ensure_watcher; print('import OK')"
```

Expected: `import OK`

- [ ] **Step 3: Commit**

```bash
git add claude/subagent_watcher.py
git commit -m "feat(claude): add subagent_watcher background thread for subagent lifecycle tracking"
```

---

### Task 9: Wire subagent watcher into receiver and add tree rendering to `monitor.py`

**Files:**
- Modify: `claude/receiver.py`
- Modify: `monitor.py`

- [ ] **Step 1: Update `claude/receiver.py` to start subagent watcher on claude sessions**

In `main()`, after `_post_to_daemon(payload)` succeeds, add subagent watcher startup:

```python
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
```

Add the helper before `main()`:

```python
def _maybe_start_subagent_watcher(hook_json: dict[str, Any]) -> None:
    """Start a subagent watcher for this session if project dir is locatable."""
    try:
        import os, sys
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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
```

- [ ] **Step 2: Add tree rendering to `monitor.py`**

In the `render()` method, replace the simple task row with tree-aware rendering. Find the existing print line:

```python
print(f"{BOLD}{CYAN}║{RESET} {tool_name:<10} {status_str:<25} {duration_str:<10} {msg:<26}{BOLD}{CYAN}║{RESET}")
```

Replace with:

```python
print(f"{BOLD}{CYAN}║{RESET} {tool_name:<10} {status_str:<25} {duration_str:<10} {msg:<26}{BOLD}{CYAN}║{RESET}")
# Render subagents as indented tree rows
for sub in (t.subagents or []):
    sub_id_short = str(sub.get("subagent_id", "") or "")[:8]
    sub_status = str(sub.get("status", "") or "").upper()
    sub_status_str = format_status(sub_status)
    print(f"{BOLD}{CYAN}║{RESET}    └─ {sub_id_short:<8} {sub_status_str:<22} {'':>10} {'':>26}{BOLD}{CYAN}║{RESET}")
```

- [ ] **Step 3: Update `_analyze_all` to include subagents from daemon state**

In `_analyze_all`, where daemon tasks are merged, also populate the `subagents` list:

```python
for task_dict in daemon_resp["tasks"]:
    sid = str(task_dict.get("session_id", "") or "")
    if not sid or sid in seen_session_ids:
        continue
    status_raw = str(task_dict.get("status", "") or "").upper()
    subagents_raw = task_dict.get("subagents") or []
    results.insert(0, DisplayTask(
        tool_name="claude",
        status=status_raw,
        message=str(task_dict.get("message", "") or ""),
        session_id=sid,
        rate_limit_reset_at=task_dict.get("rate_limit_reset_at"),
        subagents=subagents_raw,
    ))
```

- [ ] **Step 4: Add `apply_subagent_event` to `engine/store.py` and route in `daemon/monitord.py`**

Subagent events bypass the normal reducer and are handled directly in the store. Add `import time` to `engine/store.py` and add the new method:

```python
# engine/store.py — add import at top:
import time

# engine/store.py — add method to TaskStore class:
def apply_subagent_event(self, parent_session_id: str, subagent_id: str, status: str) -> None:
    from engine.models import SubagentState
    with self._lock:
        parent = self._states.get(parent_session_id)
        if parent is None:
            return
        now = time.time()
        existing = next((s for s in parent.subagents if s.subagent_id == subagent_id), None)
        if existing is None:
            parent.subagents.append(SubagentState(
                subagent_id=subagent_id,
                status=status,
                started_at=now,
                last_active_at=now,
            ))
        else:
            existing.status = status
            existing.last_active_at = now
```

Then in `daemon/monitord.py`'s `do_POST`, route `claude_subagent` events before the normal `MonitorEvent` creation. Insert after the fields-validation block (`if not session_id or not source ...`) and before `event = MonitorEvent(...)`:

```python
# In daemon/monitord.py do_POST, before "event = MonitorEvent(...)":
if source == "claude_subagent":
    sub_payload = body if isinstance(body, dict) else {}
    parent_sid = str(sub_payload.get("parent_session_id", "") or "")
    subagent_id = str(sub_payload.get("subagent_id", "") or "")
    sub_status = str(sub_payload.get("status", "") or "running")
    if parent_sid and subagent_id:
        STORE.apply_subagent_event(parent_sid, subagent_id, sub_status)
    self._write_json({"ok": True})
    return
```

Note: the current validation block requires `session_id`, `source`, `tool_name`, `event_type` to be non-empty. The subagent routing block must be placed **after** those fields are extracted but **before** the `if not session_id...` validation, so relax it by moving the subagent check earlier. Concretely, after extracting `source` (line `source = str(payload.get("source", "") ...)`), add:

```python
if source == "claude_subagent":
    sub_payload = payload.get("payload") or payload.get("params") or {}
    parent_sid = str(sub_payload.get("parent_session_id", "") or "")
    subagent_id = str(sub_payload.get("subagent_id", "") or "")
    sub_status = str(sub_payload.get("status", "") or "running")
    if parent_sid and subagent_id:
        STORE.apply_subagent_event(parent_sid, subagent_id, sub_status)
    self._write_json({"ok": True})
    return
```

In `engine/store.py`, add:

```python
def apply_subagent_event(self, parent_session_id: str, subagent_id: str, status: str, event_type: str) -> None:
    from engine.models import SubagentState
    with self._lock:
        parent = self._states.get(parent_session_id)
        if parent is None:
            return
        now = time.time()
        existing = next((s for s in parent.subagents if s.subagent_id == subagent_id), None)
        if existing is None:
            new_sub = SubagentState(
                subagent_id=subagent_id,
                status=status,
                started_at=now,
                last_active_at=now,
            )
            parent.subagents.append(new_sub)
        else:
            existing.status = status
            existing.last_active_at = now
```

Add `import time` at the top of `engine/store.py`.

In `daemon/monitord.py`'s `do_POST`, after extracting `source` and before creating `MonitorEvent`, add subagent routing:

```python
if source == "claude_subagent":
    sub_payload = body if isinstance(body, dict) else {}
    parent_sid = str(sub_payload.get("parent_session_id", "") or "")
    subagent_id = str(sub_payload.get("subagent_id", "") or "")
    sub_status = str(sub_payload.get("status", "") or "running")
    if parent_sid and subagent_id:
        STORE.apply_subagent_event(parent_sid, subagent_id, sub_status, event_type)
    self._write_json({"ok": True})
    return
```

- [ ] **Step 5: Run full test suite**

```bash
cd /Users/sqb/projects/cli-monitor
python3 -m pytest tests/ -v --tb=short 2>&1 | tail -40
```

Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add claude/receiver.py monitor.py engine/store.py engine/reducer.py daemon/monitord.py
git commit -m "feat(claude): wire subagent watcher, add tree rendering and subagent event routing"
```

---

## Final Verification

- [ ] **End-to-end smoke test**

```bash
cd /Users/sqb/projects/cli-monitor

# 1. Start daemon
python3 daemon/monitord.py &
DAEMON_PID=$!
sleep 0.5

# 2. Simulate a Claude Stop event
echo '{"session_id":"test-e2e-001","hook_event_name":"Stop","cwd":"/tmp","rateLimitResetAt":null}' | python3 claude/receiver.py

# 3. Verify daemon has the task
python3 -c "
from daemon_client import get_state
resp = get_state('claude')
print('tasks:', resp.get('tasks', []))
"

# 4. Simulate rate limit
echo '{"session_id":"test-e2e-002","hook_event_name":"Stop","cwd":"/tmp","rateLimitResetAt":"2099-01-01T12:00:00Z"}' | python3 claude/receiver.py
python3 -c "
from daemon_client import get_state
tasks = get_state('claude').get('tasks', [])
rl = [t for t in tasks if t.get('status') == 'RATE_LIMITED']
print('rate_limited tasks:', rl)
"

# 5. Cleanup
kill $DAEMON_PID 2>/dev/null
```

Expected: task with `status: IDLE` and separate task with `status: RATE_LIMITED` both visible.

- [ ] **Final commit with updated install**

```bash
cd /Users/sqb/projects/cli-monitor
python3 claude/install.py install "$(pwd)/claude/receiver.py"
git add -A
git status  # verify only expected files changed
git commit -m "chore: verify final state after Claude monitoring integration"
```
