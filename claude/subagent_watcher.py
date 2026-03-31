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
            import sys
            import os
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
                self._observer.stop()
            except Exception:
                pass

    def run(self) -> None:
        self._scan_existing()

        if _WATCHDOG_AVAILABLE:
            self.subagents_dir.mkdir(parents=True, exist_ok=True)

            watcher_self = self

            class _Handler(FileSystemEventHandler):
                def on_created(self, event: FileSystemEvent) -> None:
                    path = Path(event.src_path)
                    if path.suffix == "" and path.parent == watcher_self.subagents_dir:
                        # New subagent directory
                        watcher_self._on_subagent_active(path.name)
                    elif path.name == "transcript.jsonl":
                        subagent_id = path.parent.name
                        watcher_self._on_subagent_active(subagent_id)

                def on_modified(self, event: FileSystemEvent) -> None:
                    path = Path(event.src_path)
                    if path.name == "transcript.jsonl":
                        watcher_self._on_subagent_active(path.parent.name)

            self._observer = Observer()
            self._observer.schedule(_Handler(), str(self.subagents_dir), recursive=True)
            self._observer.start()

        while not self._stop_event.is_set():
            self._check_idle()
            self._stop_event.wait(timeout=_IDLE_CHECK_INTERVAL)

        if self._observer is not None:
            self._observer.join(timeout=2.0)


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
