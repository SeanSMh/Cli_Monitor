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
        return asdict(self)


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
