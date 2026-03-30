# Claude Code Monitoring Redesign — Design Spec

**Date:** 2026-03-30
**Scope:** P0 statusCommand hook, P1 rate-limit state, P2 session-id correlation, P3 subagent monitoring
**Approach:** Method B — new `claude/` subpackage isolating all Claude-specific logic

---

## 1. Goals

- Replace heuristic Claude status detection (file-rate + regex) with event-driven updates via Claude Code's `statusCommand` hook
- Add precise `RATE_LIMITED` state with countdown display
- Use `session_id` (UUID) as the primary key for Claude sessions, eliminating log-file-path ambiguity in multi-window scenarios
- Track subagent lifecycle and display as an indented tree under the parent session

Non-goals: token/cost tracking, context window percentage, model display.

---

## 2. Architecture

New `claude/` subpackage added to project root. All Claude-specific logic lives here; existing Codex/Gemini paths are untouched.

```
cli-monitor/
├── claude/
│   ├── __init__.py
│   ├── receiver.py          # Entry point called by Claude Code via statusCommand
│   ├── install.py           # Read/write ~/.claude/settings.json
│   ├── project_locator.py   # Map session_id → ~/.claude/projects/<hash>/
│   └── subagent_watcher.py  # Background thread: watch subagents/ directory
├── engine/
│   ├── models.py            # Extended: RATE_LIMITED status, SubagentState, rate_limit_reset_at
│   └── reducer.py           # Extended: handle rateLimitResetAt field
├── registry/
│   └── session_registry.py  # Extended: session_id as primary key for Claude sessions
├── daemon/
│   └── monitord.py          # Extended: route Claude events by session_id
├── monitor.py               # Extended: tree rendering for subagents, RATE_LIMITED display
├── install.sh               # Extended: invoke claude/install.py after rc injection
└── uninstall.sh             # Extended: invoke claude/install.py uninstall
```

### Data flow

```
Claude Code (state change)
  │  fork + exec
  ▼
claude/receiver.py          reads stdin JSON, exits <200ms
  │  POST /events
  ▼
daemon/monitord.py          updates TaskStore via reducer
  │
  ├─► monitor.py TUI
  └─► panel_app.py Web UI

claude/subagent_watcher.py  background thread, watchdog
  │  POST /events
  ▼
daemon/monitord.py
```

The existing `monitor.py` log-analysis path for Claude is retained as a **silent fallback** (daemon unreachable). It is not removed.

---

## 3. P0 — statusCommand Receiver

### claude/receiver.py

Called by Claude Code on every state change:
```
echo '<json>' | python3 /abs/path/claude/receiver.py
```

Must exit within 200ms. Any exception is caught and logged to `/tmp/cli_monitor_claude_err.log`; Claude Code's execution is never interrupted.

**Incoming JSON fields used:**
| Field | Type | Use |
|---|---|---|
| `session_id` | str | Session key |
| `hook_event_name` | str | Map to internal status |
| `cwd` | str | Stored in session registry |
| `rateLimitResetAt` | str \| null | Triggers RATE_LIMITED state |

**`hook_event_name` → status mapping:**
| hook_event_name | Internal status |
|---|---|
| `Stop` | `idle` |
| `PreToolUse` | `running` |
| `PostToolUse` | `running` |
| `SubagentStop` | triggers subagent event (not a top-level status change) |
| *(others)* | `running` |

**POST payload to `localhost:8766/events`:**
```json
{
  "tool": "claude",
  "session_id": "<uuid>",
  "status": "idle",
  "cwd": "/Users/sqb/project",
  "rateLimitResetAt": null
}
```

### claude/install.py

```python
def install(receiver_abs_path: str) -> None:
    # 1. Read ~/.claude/settings.json (create {} if missing)
    # 2. Backup to ~/.claude/settings.json.cli-monitor-backup
    # 3. Set settings["statusCommand"] = f"python3 {receiver_abs_path}"
    # 4. Write back, preserving all other fields

def uninstall() -> None:
    # Remove statusCommand key, write back
    # Do not restore from backup (user may have made other changes)
```

`install.sh` calls `python3 claude/install.py install "$(pwd)/claude/receiver.py"` after rc injection.
`uninstall.sh` calls `python3 claude/install.py uninstall`.

---

## 4. P1 — Rate Limit State

### engine/models.py

```python
class TaskStatus(str, Enum):
    RUNNING = "running"
    WAITING = "waiting"
    IDLE = "idle"
    DONE = "done"
    ERROR = "error"
    RATE_LIMITED = "rate_limited"   # new

@dataclass
class TaskState:
    # existing fields unchanged...
    rate_limit_reset_at: str | None = None   # new, ISO 8601
```

### engine/reducer.py

```python
def reduce_event(state: TaskState, event: dict) -> TaskState:
    if event.get("rateLimitResetAt"):
        state.status = TaskStatus.RATE_LIMITED
        state.rate_limit_reset_at = event["rateLimitResetAt"]
    else:
        if state.status == TaskStatus.RATE_LIMITED:
            state.rate_limit_reset_at = None
        state.status = map_status(event["status"])
    return state
```

### monitor.py display

`RATE_LIMITED` row format:
```
🔴 claude   [rate limit]  resets in 4m32s    /Users/sqb/project
```

Countdown is computed at render time from `rate_limit_reset_at` (ISO 8601 → `datetime.fromisoformat` → `timedelta`). When countdown reaches zero, status reverts to `idle` automatically on next render cycle.

---

## 5. P2 — Session ID Correlation

### daemon/monitord.py

Events with `tool == "claude"` are keyed by `session_id`:

```python
if event["tool"] == "claude":
    key = event["session_id"]
else:
    key = derive_key_from_log(event)   # existing logic unchanged
```

### registry/session_registry.py

Claude sessions registered with `session_id` as key. Existing non-Claude sessions continue using log-file-path keys. No migration needed; the two key spaces are disjoint by tool name check.

---

## 6. P3 — Subagent Monitoring

### claude/project_locator.py

Maps `session_id` → `~/.claude/projects/<hash>/`:

```python
def find_project_dir(session_id: str, cwd: str) -> Path | None:
    # 1. Compute expected hash from cwd (same algorithm Claude Code uses)
    #    Try that path first — O(1)
    # 2. If not found, scan all dirs under ~/.claude/projects/
    #    Read last 20 lines of each transcript.jsonl
    #    Match session_id field
    # 3. Cache result: session_id -> Path
```

### engine/models.py — SubagentState

```python
@dataclass
class SubagentState:
    subagent_id: str
    status: TaskStatus          # running or done
    started_at: float           # unix timestamp
    last_active_at: float       # unix timestamp

@dataclass
class TaskState:
    # existing fields...
    subagents: list[SubagentState] = field(default_factory=list)
```

### claude/subagent_watcher.py

Runs as a daemon thread started when the first Claude session is registered.

```python
class SubagentWatcher(threading.Thread):
    # Watches: ~/.claude/projects/<hash>/subagents/
    # Uses watchdog FileSystemEventHandler

    def on_created(self, event):
        # New subagent_id directory → POST subagent_start event

    def on_modified(self, event):
        # transcript.jsonl written → update last_active_at, status = running

    def _idle_checker(self):
        # Every 2s: check all known subagents
        # If now - last_active_at > idle_threshold (5s) → POST subagent_done
```

Events posted to daemon:
```json
{ "tool": "claude", "type": "subagent_start", "parent_session_id": "...", "subagent_id": "..." }
{ "tool": "claude", "type": "subagent_status", "parent_session_id": "...", "subagent_id": "...", "status": "running" }
{ "tool": "claude", "type": "subagent_done",  "parent_session_id": "...", "subagent_id": "..." }
```

### monitor.py — tree rendering

Subagents rendered as indented rows beneath parent, not counted toward `--max-tasks`:

```
🟢 claude   [running]  /Users/sqb/project              02:14
   └─ 🟢 sub-1  [running]                              00:43
   └─ ⚪ sub-2  [done]                                 01:12
```

`panel_app.py` / `panel.html`: subagent states are included in the session card JSON payload; frontend renders them as a collapsible list within the card.

---

## 7. Rollout Order

| Phase | Work |
|---|---|
| 1 | `claude/install.py` + `claude/receiver.py` + extend `install.sh`/`uninstall.sh` |
| 2 | Extend `engine/models.py`, `engine/reducer.py`, `daemon/monitord.py` for P1+P2 |
| 3 | `claude/project_locator.py` + `claude/subagent_watcher.py` + tree rendering in `monitor.py` |

Each phase is independently deployable and testable.
