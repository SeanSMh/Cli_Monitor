#!/usr/bin/env python3
"""Shared state models for cli-monitor."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


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

