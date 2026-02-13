#!/usr/bin/env python3
from dataclasses import dataclass
import subprocess


@dataclass
class FocusResult:
    success: bool
    provider: str = ""
    reason: str = ""


def _run_osascript(script: str) -> bool:
    try:
        res = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            check=False,
        )
        return res.returncode == 0 and res.stdout.strip().lower() == "true"
    except Exception:
        return False


class Iterm2Adapter:
    name = "iTerm2"

    def focus_by_tty(self, tty: str) -> bool:
        tty_short = tty.replace("/dev/", "", 1)
        script = f'''
tell application "iTerm2"
    repeat with w in windows
        repeat with t in tabs of w
            repeat with s in sessions of t
                if tty of s is "{tty}" or tty of s is "{tty_short}" then
                    set current tab of w to t
                    set current session of t to s
                    activate
                    return true
                end if
            end repeat
        end repeat
    end repeat
    return false
end tell
'''.strip()
        return _run_osascript(script)


class TerminalAppAdapter:
    name = "Terminal"

    def focus_by_tty(self, tty: str) -> bool:
        tty_short = tty.replace("/dev/", "", 1)
        script = f'''
tell application "Terminal"
    repeat with w in windows
        repeat with t in tabs of w
            if tty of t is "{tty}" or tty of t is "{tty_short}" then
                set selected tab of w to t
                set index of w to 1
                activate
                return true
            end if
        end repeat
    end repeat
    return false
end tell
'''.strip()
        return _run_osascript(script)


class TerminalFocusService:
    def __init__(self, adapters=None):
        self.adapters = adapters or [Iterm2Adapter(), TerminalAppAdapter()]

    def focus_by_tty(self, tty: str) -> FocusResult:
        if not tty:
            return FocusResult(False, reason="empty tty")

        errors = []
        for adapter in self.adapters:
            try:
                if adapter.focus_by_tty(tty):
                    return FocusResult(True, provider=adapter.name)
                errors.append(f"{adapter.name}: not matched")
            except Exception as e:
                errors.append(f"{adapter.name}: {e}")

        return FocusResult(False, reason="; ".join(errors) if errors else "no adapters")
