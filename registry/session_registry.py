#!/usr/bin/env python3
"""File-based session registry."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


REGISTRY_DIR = Path(os.path.expanduser("~/.cli-monitor/sessions"))


def ensure_registry_dir() -> Path:
    REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
    return REGISTRY_DIR


def session_registry_path(session_id: str) -> Path:
    safe_session_id = str(session_id or "").strip()
    return ensure_registry_dir() / f"{safe_session_id}.json"


def read_session_registry(session_id: str) -> dict[str, Any] | None:
    path = session_registry_path(session_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def write_session_registry(session_id: str, payload: dict[str, Any]) -> Path:
    path = session_registry_path(session_id)
    data = dict(payload or {})
    data["session_id"] = str(session_id or "").strip()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def merge_session_registry(session_id: str, payload: dict[str, Any]) -> Path:
    current = read_session_registry(session_id) or {"session_id": session_id}
    current.update(payload or {})
    return write_session_registry(session_id, current)


def delete_session_registry(session_id: str) -> None:
    path = session_registry_path(session_id)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
