#!/usr/bin/env python3
"""Map Claude Code session_id to its project directory under ~/.claude/projects/."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"

# Must match Claude Code src/utils/sessionStoragePortable.ts
MAX_SANITIZED_LENGTH = 200

_cache: dict[str, Path] = {}


def _clear_cache() -> None:
    _cache.clear()


def _djb2_hash(s: str) -> int:
    """
    Port of Claude Code src/utils/hash.ts djb2Hash.
    Replicates JavaScript int32 signed overflow semantics.
    """
    h = 0
    for ch in s:
        h = ((h << 5) - h + ord(ch)) & 0xFFFFFFFF
        # Convert to signed int32 (JavaScript | 0 behaviour)
        if h >= 0x80000000:
            h -= 0x100000000
    return h


def _to_base36(n: int) -> str:
    """Convert non-negative integer to base-36 string (matches JS .toString(36))."""
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    if n == 0:
        return "0"
    result: list[str] = []
    while n > 0:
        result.append(digits[n % 36])
        n //= 36
    return "".join(reversed(result))


def _sanitize_path(name: str) -> str:
    """
    Port of Claude Code src/utils/sessionStoragePortable.ts sanitizePath.
    Converts a filesystem path (cwd) to the directory name used under ~/.claude/projects/.

    Note: Claude Code CLI runs on Bun and uses Bun.hash for the suffix; the Node.js
    SDK uses simpleHash (Math.abs(djb2Hash).toString(36)). We implement simpleHash
    here. For paths ≤200 chars no suffix is appended so the fast path always works.
    For longer paths the hash may not match the CLI — the scan fallback handles that.
    """
    sanitized = re.sub(r"[^a-zA-Z0-9]", "-", name)
    if len(sanitized) <= MAX_SANITIZED_LENGTH:
        return sanitized
    # simpleHash: Math.abs(djb2Hash(name)).toString(36)
    suffix = _to_base36(abs(_djb2_hash(name)))
    return f"{sanitized[:MAX_SANITIZED_LENGTH]}-{suffix}"


def find_project_dir_from_cwd(cwd: str) -> Optional[Path]:
    """O(1) direct lookup using the deterministic cwd → dir-name mapping."""
    if not cwd:
        return None
    candidate = CLAUDE_PROJECTS_DIR / _sanitize_path(cwd)
    return candidate if candidate.is_dir() else None


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
            # Seek to last 8KB to avoid reading large transcripts into memory
            with open(transcript, "rb") as fh:
                size = fh.seek(0, 2)
                fh.seek(max(0, size - 8192))
                tail = fh.read().decode("utf-8", errors="replace")
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

    # Fast path: O(1) direct computation from cwd (available in hook payloads)
    if cwd:
        result = find_project_dir_from_cwd(cwd)
        if result is not None:
            _cache[session_id] = result
            return result

    # Slow fallback: scan all project dirs (safety net for edge cases)
    result = _scan_for_session(session_id)
    if result is not None:
        _cache[session_id] = result
    return result
