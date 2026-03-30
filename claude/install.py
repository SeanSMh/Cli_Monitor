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
        print(f"  statusCommand injected into {SETTINGS_PATH}")
    elif cmd == "uninstall":
        uninstall()
        print(f"  statusCommand removed from {SETTINGS_PATH}")
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)
