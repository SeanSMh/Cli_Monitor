#!/usr/bin/env python3
"""In-process task store used by monitord."""

from __future__ import annotations

import time
from threading import RLock

from engine.models import MonitorEvent, SubagentState, TaskState
from engine.reducer import reduce_event


class TaskStore:
    def __init__(self):
        self._lock = RLock()
        self._states: dict[str, TaskState] = {}

    def apply(self, event: MonitorEvent) -> TaskState | None:
        with self._lock:
            previous = self._states.get(event.session_id)
            state = reduce_event(previous, event)
            if state is None:
                return previous
            self._states[event.session_id] = state
            return state

    def upsert_state(self, state: TaskState) -> TaskState:
        with self._lock:
            self._states[state.session_id] = state
            return state

    def get(self, session_id: str) -> TaskState | None:
        with self._lock:
            return self._states.get(session_id)

    def apply_subagent_event(self, parent_session_id: str, subagent_id: str, status: str) -> None:
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

    def snapshot(self, tool_name: str = "") -> list[TaskState]:
        tool_key = str(tool_name or "").strip().lower()
        with self._lock:
            states = []
            for s in self._states.values():
                s.subagents = list(s.subagents)
                states.append(s)
        if tool_key:
            states = [item for item in states if item.tool_name.lower() == tool_key]
        states.sort(key=lambda item: (int(item.updated_at_ms or 0), item.session_id), reverse=True)
        return states
