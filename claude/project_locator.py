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
            # Read last 8KB efficiently to find session_id
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
    """Return the project directory for the given session_id, or None if not found."""
    if session_id in _cache:
        return _cache[session_id]
    result = _scan_for_session(session_id)
    if result is not None:
        _cache[session_id] = result
    return result
