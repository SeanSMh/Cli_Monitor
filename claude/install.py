#!/usr/bin/env python3
"""Inject/remove cli-monitor hooks in ~/.claude/settings.json."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

SETTINGS_PATH = Path.home() / ".claude" / "settings.json"

_HOOK_EVENTS = ["Stop", "PreToolUse", "PostToolUse", "Notification", "SubagentStop"]
_MARKER = "cli-monitor"


def _make_hook_entry(receiver_abs_path: str) -> dict:
    return {
        "matcher": "",
        "hooks": [
            {
                "type": "command",
                "command": f'python3 "{receiver_abs_path}"',
                "_marker": _MARKER,
            }
        ],
    }


def _is_our_entry(entry: object) -> bool:
    if not isinstance(entry, dict):
        return False
    for hook in entry.get("hooks", []):
        if isinstance(hook, dict) and hook.get("_marker") == _MARKER:
            return True
    return False


def install(receiver_abs_path: str) -> None:
    if not Path(receiver_abs_path).is_absolute():
        raise ValueError(f"receiver_abs_path must be absolute, got: {receiver_abs_path!r}")

    settings: dict = {}
    if SETTINGS_PATH.exists():
        try:
            settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except Exception:
            settings = {}
        if not isinstance(settings, dict):
            settings = {}
        backup = SETTINGS_PATH.with_suffix(".json.cli-monitor-backup")
        if not backup.exists():
            shutil.copy2(SETTINGS_PATH, backup)

    hooks: dict = settings.get("hooks", {})
    entry = _make_hook_entry(receiver_abs_path)
    for event in _HOOK_EVENTS:
        existing = hooks.get(event, [])
        if not isinstance(existing, list):
            existing = []
        # Remove any previous cli-monitor entries, then append fresh one
        cleaned = [e for e in existing if not _is_our_entry(e)]
        cleaned.append(entry)
        hooks[event] = cleaned

    settings["hooks"] = hooks
    # Remove legacy statusCommand if present from a previous install
    settings.pop("statusCommand", None)

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
    hooks: dict = settings.get("hooks", {})
    changed = False
    for event in list(hooks.keys()):
        before = hooks[event]
        if not isinstance(before, list):
            continue
        after = [e for e in before if not _is_our_entry(e)]
        if len(after) != len(before):
            changed = True
        if after:
            hooks[event] = after
        else:
            del hooks[event]
    if not hooks:
        settings.pop("hooks", None)
    elif changed:
        settings["hooks"] = hooks
    SETTINGS_PATH.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "install":
        if len(sys.argv) < 3:
            print("Usage: claude/install.py install <receiver_abs_path>", file=sys.stderr)
            sys.exit(1)
        install(sys.argv[2])
        print(f"  hooks injected into {SETTINGS_PATH} for events: {', '.join(_HOOK_EVENTS)}")
    elif cmd == "uninstall":
        uninstall()
        print(f"  cli-monitor hooks removed from {SETTINGS_PATH}")
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)
