#!/usr/bin/env python3
from dataclasses import dataclass
import os
import subprocess


@dataclass
class FocusResult:
    success: bool
    provider: str = ""
    reason: str = ""


@dataclass
class SessionMeta:
    tty: str = ""
    term_program: str = ""
    term_program_version: str = ""
    cwd: str = ""
    shell_pid: str = ""
    shell_ppid: str = ""
    wezterm_pane_id: str = ""
    warp_session_id: str = ""
    vscode_pid: str = ""
    vscode_cwd: str = ""

    @classmethod
    def from_mapping(cls, data):
        data = data or {}
        return cls(
            tty=str(data.get("tty", "") or "").strip(),
            term_program=str(data.get("term_program", "") or "").strip(),
            term_program_version=str(data.get("term_program_version", "") or "").strip(),
            cwd=str(data.get("cwd", "") or "").strip(),
            shell_pid=str(data.get("shell_pid", "") or "").strip(),
            shell_ppid=str(data.get("shell_ppid", "") or "").strip(),
            wezterm_pane_id=str(data.get("wezterm_pane_id", "") or "").strip(),
            warp_session_id=str(data.get("warp_session_id", "") or "").strip(),
            vscode_pid=str(data.get("vscode_pid", "") or "").strip(),
            vscode_cwd=str(data.get("vscode_cwd", "") or "").strip(),
        )

    @property
    def term_program_lower(self) -> str:
        return self.term_program.lower()

    def with_tty(self, tty: str):
        self.tty = str(tty or "").strip()
        return self


def _run_osascript(script: str, expect_true_stdout: bool = False) -> bool:
    try:
        res = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode != 0:
            return False
        if expect_true_stdout:
            return res.stdout.strip().lower() == "true"
        return True
    except Exception:
        return False


def _run_command(args) -> bool:
    try:
        res = subprocess.run(args, capture_output=True, text=True, check=False)
        return res.returncode == 0
    except Exception:
        return False


def _activate_app(app_name: str) -> bool:
    script = f'tell application "{app_name}" to activate'
    return _run_osascript(script, expect_true_stdout=False)


def _activate_any_app(app_names) -> bool:
    for app_name in app_names:
        if _activate_app(app_name):
            return True
    return False


class Iterm2Adapter:
    name = "iTerm2"

    def match(self, meta: SessionMeta) -> bool:
        term = meta.term_program_lower
        return "iterm" in term

    def focus(self, meta: SessionMeta) -> bool:
        tty = meta.tty
        if not tty:
            return False
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
        return _run_osascript(script, expect_true_stdout=True)


class TerminalAppAdapter:
    name = "Terminal"

    def match(self, meta: SessionMeta) -> bool:
        term = meta.term_program_lower
        return term in {"apple_terminal", "terminal"}

    def focus(self, meta: SessionMeta) -> bool:
        tty = meta.tty
        if not tty:
            return False
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
        return _run_osascript(script, expect_true_stdout=True)


class WezTermAdapter:
    name = "WezTerm"

    def match(self, meta: SessionMeta) -> bool:
        return "wezterm" in meta.term_program_lower or bool(meta.wezterm_pane_id)

    def focus(self, meta: SessionMeta) -> bool:
        pane_id = meta.wezterm_pane_id.strip()
        if pane_id:
            if _run_command(["wezterm", "cli", "activate-pane", "--pane-id", pane_id]):
                _activate_app("WezTerm")
                return True
        return _activate_app("WezTerm")


class WarpAdapter:
    name = "Warp"

    def match(self, meta: SessionMeta) -> bool:
        term = meta.term_program_lower
        return "warp" in term or bool(meta.warp_session_id)

    def focus(self, meta: SessionMeta) -> bool:
        if meta.cwd and os.path.isdir(meta.cwd):
            if _run_command(["open", "-a", "Warp", meta.cwd]):
                return True
        return _activate_app("Warp")


class VSCodeTerminalAdapter:
    name = "VSCode"

    def match(self, meta: SessionMeta) -> bool:
        term = meta.term_program_lower
        return term in {"vscode", "code"} or bool(meta.vscode_pid)

    def focus(self, meta: SessionMeta) -> bool:
        target_dir = meta.vscode_cwd or meta.cwd
        if target_dir and os.path.isdir(target_dir):
            if _run_command(["code", "--reuse-window", target_dir]):
                _activate_any_app(["Visual Studio Code", "Code"])
                return True
        return _activate_any_app(["Visual Studio Code", "Code"])


class TerminalFocusService:
    def __init__(self, adapters=None):
        self.adapters = adapters or [
            Iterm2Adapter(),
            TerminalAppAdapter(),
            WezTermAdapter(),
            WarpAdapter(),
            VSCodeTerminalAdapter(),
        ]

    def _resolve_order(self, meta: SessionMeta):
        matched = [adapter for adapter in self.adapters if adapter.match(meta)]
        if not matched:
            return list(self.adapters)

        ordered = list(matched)
        if meta.tty:
            for adapter in self.adapters:
                if (
                    adapter not in ordered
                    and adapter.name in {"iTerm2", "Terminal"}
                ):
                    ordered.append(adapter)
        return ordered

    def focus(self, meta: SessionMeta) -> FocusResult:
        if not isinstance(meta, SessionMeta):
            if isinstance(meta, dict):
                meta = SessionMeta.from_mapping(meta)
            else:
                meta = SessionMeta.from_mapping(getattr(meta, "__dict__", {}) if meta else {})

        if not any(
            [
                meta.tty,
                meta.term_program,
                meta.wezterm_pane_id,
                meta.warp_session_id,
                meta.vscode_pid,
                meta.cwd,
            ]
        ):
            return FocusResult(False, reason="missing tty/meta")

        adapters = self._resolve_order(meta)
        if not adapters:
            return FocusResult(False, reason="no adapters")

        errors = []
        for adapter in adapters:
            try:
                if adapter.focus(meta):
                    return FocusResult(True, provider=adapter.name)
                errors.append(f"{adapter.name}: not matched")
            except Exception as e:
                errors.append(f"{adapter.name}: {e}")

        return FocusResult(False, reason="; ".join(errors))

    def focus_by_tty(self, tty: str) -> FocusResult:
        tty = str(tty or "").strip()
        if not tty:
            return FocusResult(False, reason="empty tty")
        return self.focus(SessionMeta(tty=tty))
