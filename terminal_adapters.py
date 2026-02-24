#!/usr/bin/env python3
from dataclasses import dataclass
import os
import re
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
    vscode_ipc_hook_cli: str = ""
    vscode_git_askpass_main: str = ""
    vscode_git_askpass_node: str = ""
    vscode_git_ipc_handle: str = ""
    vscode_injection: str = ""
    cursor_trace_id: str = ""
    terminal_emulator: str = ""
    idea_initial_directory: str = ""
    jetbrains_ide_name: str = ""
    jetbrains_ide_product: str = ""
    android_studio_version: str = ""

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
            vscode_ipc_hook_cli=str(data.get("vscode_ipc_hook_cli", "") or "").strip(),
            vscode_git_askpass_main=str(data.get("vscode_git_askpass_main", "") or "").strip(),
            vscode_git_askpass_node=str(data.get("vscode_git_askpass_node", "") or "").strip(),
            vscode_git_ipc_handle=str(data.get("vscode_git_ipc_handle", "") or "").strip(),
            vscode_injection=str(data.get("vscode_injection", "") or "").strip(),
            cursor_trace_id=str(data.get("cursor_trace_id", "") or "").strip(),
            terminal_emulator=str(data.get("terminal_emulator", "") or "").strip(),
            idea_initial_directory=str(data.get("idea_initial_directory", "") or "").strip(),
            jetbrains_ide_name=str(data.get("jetbrains_ide_name", "") or "").strip(),
            jetbrains_ide_product=str(data.get("jetbrains_ide_product", "") or "").strip(),
            android_studio_version=str(data.get("android_studio_version", "") or "").strip(),
        )

    @property
    def term_program_lower(self) -> str:
        return self.term_program.lower()

    def with_tty(self, tty: str):
        self.tty = str(tty or "").strip()
        return self

    @property
    def jetbrains_markers_lower(self) -> str:
        return " ".join(
            [
                self.term_program_lower,
                self.term_program_version.lower(),
                self.terminal_emulator.lower(),
                self.jetbrains_ide_name.lower(),
                self.jetbrains_ide_product.lower(),
            ]
        )

    @property
    def is_android_studio_hint(self) -> bool:
        markers = self.jetbrains_markers_lower
        return (
            "android studio" in markers
            or "androidstudio" in markers
            or bool(self.android_studio_version.strip())
        )

    @property
    def is_cursor_hint(self) -> bool:
        markers = self.vscode_family_markers_lower
        return "cursor" in markers

    @property
    def vscode_family_markers_lower(self) -> str:
        return " ".join(
            [
                self.term_program_lower,
                self.term_program_version.lower(),
                self.vscode_ipc_hook_cli.lower(),
                self.vscode_git_askpass_main.lower(),
                self.vscode_git_askpass_node.lower(),
                self.vscode_git_ipc_handle.lower(),
                self.vscode_injection.lower(),
                self.cursor_trace_id.lower(),
            ]
        )

    @property
    def vscode_family_key(self) -> str:
        markers = self.vscode_family_markers_lower
        term = self.term_program_lower

        if "cursor" in markers:
            return "cursor"
        if "windsurf" in markers or "codeium" in markers:
            return "windsurf"
        if "trae" in markers:
            return "trae"
        if "vscodium" in markers or "codium" in markers:
            return "vscodium"
        if ("insiders" in markers) and ("code" in markers or "vscode" in markers):
            return "vscode_insiders"

        if term in {"vscode", "code"} or bool(self.vscode_pid):
            return "vscode"
        return ""

    @property
    def is_jetbrains_hint(self) -> bool:
        markers = self.jetbrains_markers_lower
        return (
            self.is_android_studio_hint
            or "jediterm" in markers
            or "jetbrains" in markers
            or bool(self.idea_initial_directory.strip())
        )


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


def _get_process_command(pid: str) -> str:
    try:
        pid_str = str(pid or "").strip()
        if not pid_str:
            return ""
        res = subprocess.run(
            ["ps", "-p", pid_str, "-o", "comm="],
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode != 0:
            return ""
        return (res.stdout or "").strip()
    except Exception:
        return ""


def _extract_app_bundle_path(text: str) -> str:
    s = str(text or "").strip()
    if not s:
        return ""
    m = re.search(r"(.+?\.app)(?:/|$)", s)
    if not m:
        return ""
    app_path = m.group(1)
    return app_path if os.path.exists(app_path) else ""


def _get_app_bundle_path_from_pid(pid: str) -> str:
    """从 GUI 进程 PID 反查 .app bundle 路径，避免按名称打开时命中错误应用。"""
    cmd = _get_process_command(pid)
    return _extract_app_bundle_path(cmd)


def _bundle_family_key(app_bundle_path: str) -> str:
    p = str(app_bundle_path or "").strip().lower()
    if not p:
        return ""
    if "cursor.app" in p:
        return "cursor"
    if "windsurf" in p or "codeium" in p:
        return "windsurf"
    if "trae" in p:
        return "trae"
    if "vscodium" in p or "codium" in p:
        return "vscodium"
    if "visual studio code - insiders.app" in p or "code - insiders.app" in p:
        return "vscode_insiders"
    if "visual studio code.app" in p or p.endswith("/code.app") or "/code.app/" in p:
        return "vscode"
    return ""


def _bundle_matches_vscode_family(app_bundle_path: str, family_key: str) -> bool:
    family_key = str(family_key or "").strip()
    if not family_key:
        return True
    actual = _bundle_family_key(app_bundle_path)
    return actual == family_key


def _get_app_bundle_path_from_vscode_meta(meta: SessionMeta, family_key: str) -> str:
    # 这些变量通常包含 Electron/VSCode 家族应用的实际 bundle 路径，比 VSCODE_PID 更稳定。
    for raw in (
        meta.vscode_git_askpass_main,
        meta.vscode_git_askpass_node,
        meta.vscode_injection,
    ):
        app_path = _extract_app_bundle_path(raw)
        if app_path and _bundle_matches_vscode_family(app_path, family_key):
            return app_path
    return ""


def _activate_app(app_name: str) -> bool:
    script = f'tell application "{app_name}" to activate'
    return _run_osascript(script, expect_true_stdout=False)


def _activate_any_app(app_names) -> bool:
    for app_name in app_names:
        if _activate_app(app_name):
            return True
    return False


def _open_any_app_with_dir(app_names, target_dir: str) -> bool:
    if not target_dir or not os.path.isdir(target_dir):
        return False
    for app_name in app_names:
        if _run_command(["open", "-a", app_name, target_dir]):
            return True
    return False


def _open_app_bundle_path(app_bundle_path: str, target_dir: str = "") -> bool:
    app_bundle_path = str(app_bundle_path or "").strip()
    if not app_bundle_path or not os.path.exists(app_bundle_path):
        return False
    args = ["open", "-a", app_bundle_path]
    if target_dir and os.path.isdir(target_dir):
        args.append(target_dir)
    return _run_command(args)


def _activate_app_bundle_path(app_bundle_path: str) -> bool:
    """按 bundle 路径激活应用，避免 `open -a <app> <dir>` 在 Electron 应用中额外新开窗口。"""
    app_bundle_path = str(app_bundle_path or "").strip()
    if not app_bundle_path or not os.path.exists(app_bundle_path):
        return False
    app_name = os.path.basename(app_bundle_path)
    if app_name.lower().endswith(".app"):
        app_name = app_name[:-4]
    return _activate_app(app_name)


def _focus_vscode_family(
    meta: SessionMeta,
    cli_commands,
    app_names,
    prefer_open_app=False,
) -> bool:
    family_key = meta.vscode_family_key
    target_dir = meta.vscode_cwd or meta.cwd
    # 先用环境变量里的路径线索（更接近真实应用 bundle），再用 PID 线索。
    app_bundle_from_meta = _get_app_bundle_path_from_vscode_meta(meta, family_key)

    # VSCODE_PID 可能指向另一个 VSCode 系应用；只有家族匹配时才使用。
    app_bundle_from_pid = _get_app_bundle_path_from_pid(meta.vscode_pid)
    if app_bundle_from_pid and not _bundle_matches_vscode_family(app_bundle_from_pid, family_key):
        app_bundle_from_pid = ""

    # 关键优化：如果能确认目标 App 已在运行（可从 PID 反查 bundle），只做激活，避免新开窗口。
    if app_bundle_from_pid and _activate_app_bundle_path(app_bundle_from_pid):
        return True

    # 其次尝试按元数据推断到的 bundle 激活（不带目录，减少 Electron 新窗口副作用）。
    if app_bundle_from_meta and _activate_app_bundle_path(app_bundle_from_meta):
        return True

    # 激活失败时再考虑按目录打开（可能会打开新窗口，但至少有功能回退）。
    if app_bundle_from_meta and _open_app_bundle_path(app_bundle_from_meta, target_dir):
        return True
    if app_bundle_from_meta and _open_app_bundle_path(app_bundle_from_meta):
        return True
    if app_bundle_from_pid and _open_app_bundle_path(app_bundle_from_pid, target_dir):
        return True
    if app_bundle_from_pid and _open_app_bundle_path(app_bundle_from_pid):
        return True

    if target_dir and os.path.isdir(target_dir):
        if prefer_open_app and _open_any_app_with_dir(app_names, target_dir):
            return True
        for cli in cli_commands:
            if _run_command([cli, "--reuse-window", target_dir]):
                _activate_any_app(app_names)
                return True
        if not prefer_open_app and _open_any_app_with_dir(app_names, target_dir):
            return True
    return _activate_any_app(app_names)


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


class AndroidStudioAdapter:
    name = "AndroidStudio"

    def match(self, meta: SessionMeta) -> bool:
        return meta.is_android_studio_hint

    def focus(self, meta: SessionMeta) -> bool:
        # JetBrains terminal lacks stable pane-focus API here;先激活应用避免误跳 VS Code
        return _activate_any_app(
            [
                "Android Studio",
                "Android Studio Preview",
                "Android Studio Beta",
                "Android Studio Canary",
            ]
        )


class CursorAdapter:
    name = "Cursor"

    def match(self, meta: SessionMeta) -> bool:
        return meta.vscode_family_key == "cursor"

    def focus(self, meta: SessionMeta) -> bool:
        # VS Code 家族编辑器统一禁用裸 CLI，彻底规避 PATH 同名命令误命中。
        return _focus_vscode_family(
            meta,
            [],
            ["Cursor", "Cursor Nightly"],
            prefer_open_app=True,
        )


class WindsurfAdapter:
    name = "Windsurf"

    def match(self, meta: SessionMeta) -> bool:
        return meta.vscode_family_key == "windsurf"

    def focus(self, meta: SessionMeta) -> bool:
        return _focus_vscode_family(
            meta,
            [],
            ["Windsurf", "Windsurf Next"],
            prefer_open_app=True,
        )


class TraeAdapter:
    name = "Trae"

    def match(self, meta: SessionMeta) -> bool:
        return meta.vscode_family_key == "trae"

    def focus(self, meta: SessionMeta) -> bool:
        # VS Code 家族编辑器统一禁用裸 CLI，彻底规避 PATH 同名命令误命中。
        return _focus_vscode_family(
            meta,
            [],
            ["Trae", "Trae Beta"],
            prefer_open_app=True,
        )


class VSCodeInsidersAdapter:
    name = "VSCodeInsiders"

    def match(self, meta: SessionMeta) -> bool:
        return meta.vscode_family_key == "vscode_insiders"

    def focus(self, meta: SessionMeta) -> bool:
        return _focus_vscode_family(
            meta,
            [],
            ["Visual Studio Code - Insiders", "Code - Insiders"],
            prefer_open_app=True,
        )


class VSCodiumAdapter:
    name = "VSCodium"

    def match(self, meta: SessionMeta) -> bool:
        return meta.vscode_family_key == "vscodium"

    def focus(self, meta: SessionMeta) -> bool:
        return _focus_vscode_family(meta, [], ["VSCodium"], prefer_open_app=True)


class VSCodeTerminalAdapter:
    name = "VSCode"

    def match(self, meta: SessionMeta) -> bool:
        if meta.is_jetbrains_hint:
            return False
        family = meta.vscode_family_key
        return family == "vscode"

    def focus(self, meta: SessionMeta) -> bool:
        return _focus_vscode_family(
            meta,
            [],
            ["Visual Studio Code", "Code"],
            prefer_open_app=True,
        )


class TerminalFocusService:
    def __init__(self, adapters=None):
        self.adapters = adapters or [
            Iterm2Adapter(),
            TerminalAppAdapter(),
            WezTermAdapter(),
            WarpAdapter(),
            AndroidStudioAdapter(),
            CursorAdapter(),
            WindsurfAdapter(),
            TraeAdapter(),
            VSCodeInsidersAdapter(),
            VSCodiumAdapter(),
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
                meta.vscode_ipc_hook_cli,
                meta.vscode_git_askpass_main,
                meta.vscode_git_askpass_node,
                meta.vscode_git_ipc_handle,
                meta.vscode_injection,
                meta.cursor_trace_id,
                meta.terminal_emulator,
                meta.jetbrains_ide_name,
                meta.jetbrains_ide_product,
                meta.android_studio_version,
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
