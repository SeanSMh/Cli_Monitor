#!/usr/bin/env python3
"""
CLI Monitor — 面板式 macOS 监控应用
点击状态栏图标 🛡️ 弹出/隐藏监控面板。
面板关闭不退出应用，常驻状态栏。

用法:
    python3 panel_app.py
"""

import os
import sys
import glob
import re
import json
import atexit
import signal
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import webview

# PyObjC (macOS 状态栏)
try:
    from AppKit import (
        NSStatusBar,
        NSVariableStatusItemLength,
        NSApplication,
        NSApplicationActivationPolicyAccessory,
        NSImage,
    )
    from Foundation import NSObject
    import objc

    HAS_APPKIT = True
except ImportError:
    HAS_APPKIT = False

UN_AVAILABLE = False
UNUserNotificationCenter = None
UNMutableNotificationContent = None
UNTimeIntervalNotificationTrigger = None
UNNotificationRequest = None
UNNotificationSound = None
UN_AUTH_OPTIONS = 0
UN_PRESENT_OPTIONS = 0

if HAS_APPKIT:
    try:
        objc.loadBundle(
            "UserNotifications",
            globals(),
            bundle_path="/System/Library/Frameworks/UserNotifications.framework",
        )
        UNUserNotificationCenter = objc.lookUpClass("UNUserNotificationCenter")
        UNMutableNotificationContent = objc.lookUpClass("UNMutableNotificationContent")
        UNTimeIntervalNotificationTrigger = objc.lookUpClass("UNTimeIntervalNotificationTrigger")
        UNNotificationRequest = objc.lookUpClass("UNNotificationRequest")
        try:
            UNNotificationSound = objc.lookUpClass("UNNotificationSound")
        except Exception:
            UNNotificationSound = None

        # UNAuthorizationOptions: badge(1) | sound(2) | alert(4)
        UN_AUTH_OPTIONS = 1 | 2 | 4
        # UNNotificationPresentationOptions: sound(2) | banner(16)
        UN_PRESENT_OPTIONS = 2 | 16
        UN_AVAILABLE = True
    except Exception as e:
        UN_AVAILABLE = False

# 项目路径
if getattr(sys, "frozen", False):
    SCRIPT_DIR = sys._MEIPASS
else:
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

# 启动日志 (调试打包问题)
_BOOT_LOG = "/tmp/cli_monitor_boot.log"
def _log(msg):
    try:
        with open(_BOOT_LOG, "a") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    except Exception:
        pass
_log(f"启动: frozen={getattr(sys, 'frozen', False)} SCRIPT_DIR={SCRIPT_DIR}")
_log(f"panel.html: {os.path.exists(os.path.join(SCRIPT_DIR, 'panel.html'))}")
_log(f"monitor.py: {os.path.exists(os.path.join(SCRIPT_DIR, 'monitor.py'))}")
_log(f"shell/cli_monitor.sh: {os.path.exists(os.path.join(SCRIPT_DIR, 'shell', 'cli_monitor.sh'))}")

from monitor import (
    analyze_log,
    parse_start_info,
    parse_session_meta,
    calculate_duration,
    clear_rate_history,
    DEFAULT_LOG_DIR,
)
from terminal_adapters import SessionMeta, TerminalFocusService

# === 配置 ===
LOG_DIR = os.environ.get("AI_MONITOR_DIR", DEFAULT_LOG_DIR)
MAX_TASKS = 8
PANEL_HTML = os.path.join(SCRIPT_DIR, "panel.html")

# 临时注入
TEMP_WRAPPER = "/tmp/cli_monitor_session.sh"
INJECT_MARKER = "# >>> cli-monitor-session >>>"
INJECT_END = "# <<< cli-monitor-session <<<"
SHELL_WRAPPER_SOURCE = os.path.join(SCRIPT_DIR, "shell", "cli_monitor.sh")

# Claude Code Hooks 注入
CLAUDE_SETTINGS = os.path.expanduser("~/.claude/settings.json")
HOOK_MARKER = "CLI_MONITOR_HOOK"  # 用于识别我们注入的 hook
SIGNAL_FILE = os.path.join(LOG_DIR, "_claude_idle_signal")
# Hook 命令: Claude 完成回复时写信号文件
HOOK_COMMAND = f"echo $(date +%s) > {SIGNAL_FILE}  # {HOOK_MARKER}"

_cleanup_done = False

# 全局引用
_window = None
_window_visible = True
_status_item = None
_sb_delegate = None
_resize_delegate = None
_api = None
_status_icon_image = None
_terminal_focus_service = TerminalFocusService()
E2E_MODE = os.environ.get("CLI_MONITOR_E2E", "0") == "1"
E2E_HOST = os.environ.get("CLI_MONITOR_E2E_HOST", "127.0.0.1")
E2E_PORT = int(os.environ.get("CLI_MONITOR_E2E_PORT", "18787"))
_e2e_server = None


# ===========================================
# 临时注入管理
# ===========================================


def _get_rc_file():
    shell = os.environ.get("SHELL", "")
    if "zsh" in shell:
        return os.path.expanduser("~/.zshrc")
    elif "bash" in shell:
        return os.path.expanduser("~/.bashrc")
    return os.path.expanduser("~/.zshrc")


def inject_shell_wrapper():
    try:
        with open(SHELL_WRAPPER_SOURCE, "r") as src:
            content = src.read()
        with open(TEMP_WRAPPER, "w") as dst:
            dst.write(content)
        os.chmod(TEMP_WRAPPER, 0o644)
    except Exception as e:
        print(f"[CLI Monitor] 写入临时 wrapper 失败: {e}")
        return False

    rc_file = _get_rc_file()
    try:
        existing = ""
        if os.path.exists(rc_file):
            with open(rc_file, "r") as f:
                existing = f.read()
        if INJECT_MARKER in existing:
            return True
        inject_block = (
            f"\n{INJECT_MARKER}\n"
            f"# CLI Monitor 临时注入 (应用退出或重启后自动失效)\n"
            f'[[ -f "{TEMP_WRAPPER}" ]] && source "{TEMP_WRAPPER}"\n'
            f"{INJECT_END}\n"
        )
        with open(rc_file, "a") as f:
            f.write(inject_block)
        print(f"[CLI Monitor] ✅ 已注入到 {rc_file}")
        return True
    except Exception as e:
        print(f"[CLI Monitor] 注入失败: {e}")
        return False


def cleanup_shell_wrapper():
    global _cleanup_done
    if _cleanup_done:
        return
    _cleanup_done = True

    rc_file = _get_rc_file()
    try:
        if os.path.exists(rc_file):
            with open(rc_file, "r") as f:
                lines = f.readlines()
            new_lines, in_block = [], False
            for line in lines:
                if INJECT_MARKER in line:
                    in_block = True
                    continue
                if INJECT_END in line:
                    in_block = False
                    continue
                if not in_block:
                    new_lines.append(line)
            with open(rc_file, "w") as f:
                f.writelines(new_lines)
            print(f"[CLI Monitor] ✅ 已从 {rc_file} 清理注入内容")
    except Exception:
        pass

    try:
        if os.path.exists(TEMP_WRAPPER):
            os.remove(TEMP_WRAPPER)
    except Exception:
        pass


# ===========================================
# Claude Code Hooks 临时注入
# ===========================================


def inject_claude_hooks():
    """将 Stop hook 注入到 ~/.claude/settings.json"""
    if not os.path.exists(os.path.dirname(CLAUDE_SETTINGS)):
        print("[CLI Monitor] ⏭️  未检测到 Claude Code, 跳过 Hooks 注入")
        return False

    try:
        # 读取现有配置
        settings = {}
        if os.path.exists(CLAUDE_SETTINGS):
            with open(CLAUDE_SETTINGS, "r") as f:
                settings = json.load(f)

        # 检查是否已注入
        hooks = settings.get("hooks", {})
        stop_hooks = hooks.get("Stop", [])
        for entry in stop_hooks:
            for h in entry.get("hooks", []):
                if HOOK_MARKER in h.get("command", ""):
                    print("[CLI Monitor] ✅ Claude Hooks 已存在, 跳过")
                    return True

        # 添加 Stop hook
        our_hook = {
            "matcher": "",
            "hooks": [
                {
                    "type": "command",
                    "command": HOOK_COMMAND,
                }
            ],
        }
        stop_hooks.append(our_hook)
        hooks["Stop"] = stop_hooks
        settings["hooks"] = hooks

        # 写回
        with open(CLAUDE_SETTINGS, "w") as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)

        print("[CLI Monitor] ✅ Claude Hooks 已注入 (Stop → 写信号文件)")
        return True
    except Exception as e:
        print(f"[CLI Monitor] ⚠️  Claude Hooks 注入失败: {e}")
        return False


def cleanup_claude_hooks():
    """从 ~/.claude/settings.json 移除我们注入的 hook"""
    if not os.path.exists(CLAUDE_SETTINGS):
        return

    try:
        with open(CLAUDE_SETTINGS, "r") as f:
            settings = json.load(f)

        hooks = settings.get("hooks", {})
        stop_hooks = hooks.get("Stop", [])

        # 过滤掉包含我们标记的条目
        filtered = []
        for entry in stop_hooks:
            entry_hooks = entry.get("hooks", [])
            clean = [h for h in entry_hooks if HOOK_MARKER not in h.get("command", "")]
            if clean:
                entry["hooks"] = clean
                filtered.append(entry)

        if filtered:
            hooks["Stop"] = filtered
        else:
            hooks.pop("Stop", None)

        # 如果 hooks 为空, 移除整个字段
        if not hooks:
            settings.pop("hooks", None)

        with open(CLAUDE_SETTINGS, "w") as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)

        # 清理信号文件
        if os.path.exists(SIGNAL_FILE):
            os.remove(SIGNAL_FILE)

        print("[CLI Monitor] ✅ Claude Hooks 已清理")
    except Exception as e:
        print(f"[CLI Monitor] ⚠️  Claude Hooks 清理失败: {e}")


def _normalize_notification_text(text):
    """原生通知文本清洗，避免控制字符影响显示。"""
    s = str(text or "")
    s = s.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    s = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", s)
    return s


def send_notification(title, subtitle, message):
    title = _normalize_notification_text(title)
    subtitle = _normalize_notification_text(subtitle)
    message = _normalize_notification_text(message)
    _log(f"send_notification: title={title!r} subtitle={subtitle!r}")
    if HAS_APPKIT and _sb_delegate is not None:
        try:
            payload = json.dumps(
                {"title": title, "subtitle": subtitle, "message": message},
                ensure_ascii=False,
            )
            _sb_delegate.performSelectorOnMainThread_withObject_waitUntilDone_(
                "doDeliverNotification:", payload, False
            )
            return
        except Exception:
            pass
    # 不再回退 osascript：回调不可控，点击通知无法稳定唤起面板。
    _log("send_notification skipped: native notification unavailable")


def _extract_pid_from_log_file(log_file):
    """从日志文件名中提取 shell PID: tool_timestamp_pid_random.log"""
    try:
        basename = os.path.basename(log_file)
        parts = basename.rsplit("_", 3)
        if len(parts) >= 3:
            return int(parts[2])
    except Exception:
        pass
    return None


def _get_tty_from_pid(pid):
    """读取进程所在 TTY，如 /dev/ttys008"""
    try:
        res = subprocess.run(
            ["ps", "-o", "tty=", "-p", str(pid)],
            capture_output=True,
            text=True,
            check=False,
        )
        tty = res.stdout.strip()
        if not tty or tty == "?":
            return ""
        if not tty.startswith("/dev/"):
            tty = f"/dev/{tty}"
        return tty
    except Exception:
        return ""


def _append_monitor_end_if_missing(log_file, exit_code, end_time_str):
    """为异常终止任务补写 MONITOR_END，避免每次刷新都用当前时间重算时长。"""
    try:
        if not log_file or not os.path.exists(log_file):
            return False
        tail = ""
        with open(log_file, "rb") as f:
            try:
                f.seek(-512, os.SEEK_END)
            except OSError:
                f.seek(0)
            tail = f.read().decode("utf-8", errors="ignore")
        if "MONITOR_END:" in tail:
            return False
        with open(log_file, "a", encoding="utf-8", errors="ignore") as f:
            f.write(f"--- MONITOR_END: {int(exit_code)} | {end_time_str} ---\n")
        return True
    except Exception:
        return False


def _build_session_meta(log_file):
    meta = parse_session_meta(log_file)

    if not meta.get("shell_pid"):
        pid = _extract_pid_from_log_file(log_file)
        if pid:
            meta["shell_pid"] = str(pid)

    shell_pid = str(meta.get("shell_pid", "")).strip()
    if shell_pid and not meta.get("tty"):
        try:
            meta["tty"] = _get_tty_from_pid(int(shell_pid))
        except Exception:
            pass

    return SessionMeta.from_mapping(meta)


def _get_terminal_label(meta):
    term = str(meta.get("term_program", "") or "").strip().lower()
    term_ver = str(meta.get("term_program_version", "") or "").strip().lower()
    vscode_ipc_hook_cli = str(meta.get("vscode_ipc_hook_cli", "") or "").strip().lower()
    vscode_git_askpass_main = str(meta.get("vscode_git_askpass_main", "") or "").strip().lower()
    vscode_git_askpass_node = str(meta.get("vscode_git_askpass_node", "") or "").strip().lower()
    vscode_git_ipc_handle = str(meta.get("vscode_git_ipc_handle", "") or "").strip().lower()
    vscode_injection = str(meta.get("vscode_injection", "") or "").strip().lower()
    cursor_trace_id = str(meta.get("cursor_trace_id", "") or "").strip().lower()
    terminal_emulator = str(meta.get("terminal_emulator", "") or "").strip().lower()
    jb_name = str(meta.get("jetbrains_ide_name", "") or "").strip().lower()
    jb_product = str(meta.get("jetbrains_ide_product", "") or "").strip().lower()
    has_idea_dir = bool(str(meta.get("idea_initial_directory", "") or "").strip())
    has_as_ver = bool(str(meta.get("android_studio_version", "") or "").strip())
    vscode_family_markers = " ".join(
        [
            term,
            term_ver,
            vscode_ipc_hook_cli,
            vscode_git_askpass_main,
            vscode_git_askpass_node,
            vscode_git_ipc_handle,
            vscode_injection,
            cursor_trace_id,
        ]
    )

    jetbrains_markers = " ".join([term, term_ver, terminal_emulator, jb_name, jb_product])
    if "cursor" in vscode_family_markers:
        return "Cursor"
    if "windsurf" in vscode_family_markers or "codeium" in vscode_family_markers:
        return "Windsurf"
    if "trae" in vscode_family_markers:
        return "Trae"
    if "vscodium" in vscode_family_markers or "codium" in vscode_family_markers:
        return "VSCodium"
    if "insiders" in vscode_family_markers and ("code" in vscode_family_markers or "vscode" in vscode_family_markers):
        return "VS Code Insiders"
    if (
        "android studio" in jetbrains_markers
        or "androidstudio" in jetbrains_markers
        or has_as_ver
    ):
        return "Android Studio"
    if (
        "jediterm" in jetbrains_markers
        or "jetbrains" in jetbrains_markers
        or has_idea_dir
    ):
        return "JetBrains"

    if "iterm" in term:
        return "iTerm2"
    if term in {"apple_terminal", "terminal"}:
        return "Terminal"
    if "wezterm" in term or meta.get("wezterm_pane_id"):
        return "WezTerm"
    if "warp" in term or meta.get("warp_session_id"):
        return "Warp"
    if term in {"vscode", "code"} or meta.get("vscode_pid") or meta.get("vscode_cwd"):
        return "VS Code"
    return ""


def _append_terminal_hint(text, terminal_label):
    text = (text or "").strip()
    terminal_label = (terminal_label or "").strip()
    if not terminal_label:
        return text
    if not text:
        return terminal_label
    return f"{text} · {terminal_label}"


def _build_card_subtitle(tool_name, status, msg, exit_code, duration, signal_ts, terminal_label):
    tool_name = str(tool_name or "").strip().lower()
    msg = str(msg or "").strip()

    if status == "DONE":
        if duration:
            return _append_terminal_hint(f"⏱ {duration}", terminal_label)
        if exit_code == 137:
            return _append_terminal_hint("终端已关闭", terminal_label)
        if exit_code > 0:
            return _append_terminal_hint(f"退出码 {exit_code}", terminal_label)
        return _append_terminal_hint("任务已结束", terminal_label)

    if status == "WAITING":
        return _append_terminal_hint(msg or "等待确认输入", terminal_label)

    if status == "IDLE":
        if tool_name == "claude" and signal_ts and signal_ts > 0:
            return _append_terminal_hint("AI 已完成回复", terminal_label)
        return _append_terminal_hint("等待下一步输入", terminal_label)

    # RUNNING / unknown
    if not msg or msg == "初始化...":
        return _append_terminal_hint("运行中...", terminal_label)
    return _append_terminal_hint(msg, terminal_label)


def _strip_terminal_hint_suffix(text, terminal_label):
    text = str(text or "").strip()
    terminal_label = str(terminal_label or "").strip()
    if not text or not terminal_label:
        return text
    suffix = f" · {terminal_label}"
    if text.endswith(suffix):
        return text[:-len(suffix)].rstrip()
    return text


def _notification_status_label(status, exit_code):
    if status == "DONE":
        if exit_code == 137:
            return "已关闭"
        if exit_code and exit_code > 0:
            return "异常退出"
        return "已完成"
    if status == "WAITING":
        return "待确认"
    if status == "IDLE":
        return "等待输入"
    return "运行中"


def _notification_compact_text(text, limit=72):
    s = str(text or "")
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"[\x00-\x1f\x7f]", "", s)
    if len(s) > limit:
        s = s[: max(0, limit - 1)] + "…"
    return s


def _build_notification_payload(task):
    status = str(task.get("status", "") or "").strip().upper()
    exit_code = int(task.get("exit_code", -1) or -1)
    terminal_label = str(task.get("terminal_label", "") or "").strip()
    badge = _notification_status_label(status, exit_code)

    subtitle_parts = [badge]
    if terminal_label:
        subtitle_parts.append(terminal_label)
    subtitle = _notification_compact_text(" · ".join(subtitle_parts), 64)

    body = _notification_compact_text(task.get("message", ""), 88)
    if not body or body in {"初始化...", "[进程已终止]"}:
        card_subtitle = _strip_terminal_hint_suffix(task.get("subtitle", ""), terminal_label)
        body = _notification_compact_text(card_subtitle, 88)

    # 避免通知副标题和正文都显示“等待输入”类文案，造成重复感。
    if status == "IDLE" and body in {"等待输入", "等待下一步输入"}:
        body = "点击通知打开面板查看详情"

    if not body:
        body = "点击状态栏打开面板查看详情"

    title = "CLI Monitor"
    return title, subtitle, body


# ===========================================
# macOS 状态栏图标
# ===========================================


def update_status_icon(alert_count):
    """更新状态栏图标"""
    display_count = alert_count
    if _is_panel_visible_and_frontmost():
        display_count = 0
    try:
        if HAS_APPKIT and _sb_delegate:
            _sb_delegate.performSelectorOnMainThread_withObject_waitUntilDone_(
                "doSetStatusIcon:", str(max(0, int(display_count))), False
            )
            return
        _do_update_status_icon(display_count)
    except Exception:
        pass


if HAS_APPKIT:

    class StatusBarDelegate(NSObject):
        """状态栏点击代理 + 主线程调度"""

        @objc.python_method
        def show_panel(self):
            global _window_visible, _api
            if _window is None:
                return
            try:
                _window.show()
            except Exception:
                pass
            _window_visible = True
            try:
                NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
            except Exception:
                pass
            if _api is not None:
                _api.clear_unread_notifications()

        @objc.python_method
        def toggle_panel(self):
            global _window_visible, _api
            if _window is None:
                return
            if _window_visible:
                _window.hide()
                _window_visible = False
            else:
                self.show_panel()

        def statusBarClicked_(self, sender):
            self.toggle_panel()

        def doTogglePanel_(self, _):
            self.toggle_panel()

        def doShowPanel_(self, _):
            self.show_panel()

        def doSetupStatusBar_(self, _):
            """ObjC selector: 在主线程上执行状态栏创建"""
            _do_setup_statusbar()

        def doSetStatusIcon_(self, payload):
            """ObjC selector: 在主线程上更新状态栏图标"""
            try:
                _do_update_status_icon(int(str(payload)))
            except Exception:
                _do_update_status_icon(0)

        def doDeliverNotification_(self, payload):
            """ObjC selector: 在主线程发送原生通知"""
            try:
                data = json.loads(str(payload or "{}"))
            except Exception:
                data = {}
            _do_send_native_notification(
                data.get("title", ""),
                data.get("subtitle", ""),
                data.get("message", ""),
            )

        def userNotificationCenter_willPresentNotification_withCompletionHandler_(self, center, notification, completionHandler):
            try:
                # 前台也允许展示通知横幅；“已读”语义由未读角标逻辑控制，不在这里 suppress。
                options = UN_PRESENT_OPTIONS
                if completionHandler:
                    completionHandler(options)
            except Exception:
                try:
                    if completionHandler:
                        completionHandler(0)
                except Exception:
                    pass

        def userNotificationCenter_didReceiveNotificationResponse_withCompletionHandler_(self, center, response, completionHandler):
            try:
                self.show_panel()
            except Exception:
                pass
            try:
                notification = response.notification() if response else None
                if notification is not None:
                    center.removeDeliveredNotification_(notification)
            except Exception:
                pass
            try:
                if completionHandler:
                    completionHandler()
            except Exception:
                pass


    class ResizeDelegate(NSObject):
        """窗口尺寸调整代理 (确保在主线程执行)"""

        def doResize_(self, payload):
            try:
                text = str(payload)
                w_str, h_str = text.split(",", 1)
                _do_resize_window(int(w_str), int(h_str))
            except Exception as e:
                _log(f"窗口调整失败: {e}")


def _do_setup_statusbar():
    """实际创建状态栏图标 (仅在主线程调用)"""
    global _status_item, _sb_delegate, _status_icon_image

    # 无 Dock 图标，纯状态栏应用
    NSApplication.sharedApplication().setActivationPolicy_(
        NSApplicationActivationPolicyAccessory
    )

    status_bar = NSStatusBar.systemStatusBar()
    _status_item = status_bar.statusItemWithLength_(NSVariableStatusItemLength)
    _status_icon_image = _load_statusbar_icon_image()
    if _status_item.button() is not None:
        if _status_icon_image is not None:
            try:
                _status_item.button().setImage_(_status_icon_image)
            except Exception:
                pass
        _status_item.button().setTitle_("")

    # 绑定点击事件
    _status_item.button().setAction_(
        objc.selector(_sb_delegate.statusBarClicked_, signature=b"v@:@")
    )
    _status_item.button().setTarget_(_sb_delegate)

    _setup_notification_center()

    print("[CLI Monitor] ✅ 状态栏图标已创建 (主线程)")


def _do_update_status_icon(alert_count):
    """实际更新状态栏图标 (仅在主线程调用)"""
    if _status_item:
        try:
            count = max(0, int(alert_count))
        except Exception:
            count = 0
        btn = _status_item.button()
        if btn is None:
            return
        if _status_icon_image is not None:
            try:
                btn.setImage_(_status_icon_image)
            except Exception:
                pass
            btn.setTitle_(f" {count}" if count > 0 else "")
            return
        btn.setTitle_(f"⚠️{count}" if count > 0 else "🛡️")


def _is_panel_visible_and_frontmost():
    """面板已显示且应用位于前台时，视为用户已经看到，不显示状态栏角标。"""
    try:
        if not _window_visible:
            return False
        if HAS_APPKIT:
            return bool(NSApplication.sharedApplication().isActive())
        return bool(_window_visible)
    except Exception:
        return False


def _do_resize_window(width, height):
    """实际执行窗口尺寸调整 (必须在主线程调用)"""
    global _window
    if _window is None:
        return
    _window.resize(int(width), int(height))


def _load_statusbar_icon_image():
    """加载菜单栏图标，优先使用 assets/app_icon.png，失败时回退应用图标。"""
    if not HAS_APPKIT:
        return None
    try:
        icon_path = os.path.join(SCRIPT_DIR, "assets", "app_icon.png")
        img = None
        if os.path.exists(icon_path):
            img = NSImage.alloc().initWithContentsOfFile_(icon_path)
        if img is None:
            img = NSApplication.sharedApplication().applicationIconImage()
        if img is None:
            return None
        try:
            img.setTemplate_(False)
        except Exception:
            pass
        try:
            img.setSize_((18, 18))
        except Exception:
            pass
        return img
    except Exception as e:
        _log(f"加载状态栏图标失败: {e}")
        return None


def _setup_notification_center():
    if not UN_AVAILABLE:
        _log("UserNotifications 不可用，通知将不可用")
        return
    try:
        center = UNUserNotificationCenter.currentNotificationCenter()
        center.setDelegate_(_sb_delegate)
    except Exception as e:
        _log(f"设置 UNUserNotificationCenter delegate 失败: {e}")
        return

    try:
        # PyObjC 这里传 Python 回调需要显式 block 签名；为稳定起见直接传 None。
        center.requestAuthorizationWithOptions_completionHandler_(UN_AUTH_OPTIONS, None)
        _log("已发起通知权限请求")
    except Exception as e:
        _log(f"请求通知权限失败: {e}")


def _do_send_native_notification(title, subtitle, message):
    """在主线程发送系统通知（UNUserNotificationCenter）；点击通知由 delegate 打开面板。"""
    if not UN_AVAILABLE:
        _log("原生通知发送失败: UserNotifications unavailable")
        return False
    try:
        center = UNUserNotificationCenter.currentNotificationCenter()
        content = UNMutableNotificationContent.alloc().init()
        content.setTitle_(str(title or ""))
        if subtitle:
            content.setSubtitle_(str(subtitle))
        if message:
            # UNMutableNotificationContent 使用 body，而不是 informativeText
            content.setBody_(str(message))
        if UNNotificationSound is not None:
            try:
                content.setSound_(UNNotificationSound.defaultSound())
            except Exception:
                pass

        # 使用 1s 触发，兼容系统对过短时间间隔触发器的限制。
        trigger = UNTimeIntervalNotificationTrigger.triggerWithTimeInterval_repeats_(1.0, False)
        req_id = f"cli-monitor-{int(time.time() * 1000)}"
        req = UNNotificationRequest.requestWithIdentifier_content_trigger_(req_id, content, trigger)
        center.addNotificationRequest_withCompletionHandler_(req, None)
        _log(f"通知已提交: title={title!r} subtitle={subtitle!r}")
        return True
    except Exception as e:
        _log(f"原生通知发送失败: {e}")
        return False


def setup_statusbar_from_thread():
    """
    从后台线程安全地调度状态栏创建到主线程。
    webview.start(func=) 在后台线程运行, 但 AppKit UI 必须在主线程操作。
    使用 NSObject.performSelectorOnMainThread 调度。
    """
    global _sb_delegate
    if not HAS_APPKIT:
        return

    _sb_delegate = StatusBarDelegate.alloc().init()
    _sb_delegate.performSelectorOnMainThread_withObject_waitUntilDone_(
        "doSetupStatusBar:", None, True
    )
    print("[CLI Monitor] ✅ 状态栏调度完成")


def toggle_panel_from_thread():
    if not HAS_APPKIT or _sb_delegate is None:
        return False
    _sb_delegate.performSelectorOnMainThread_withObject_waitUntilDone_(
        "doTogglePanel:", None, True
    )
    return True


def setup_resize_delegate():
    global _resize_delegate
    if not HAS_APPKIT:
        return
    if _resize_delegate is None:
        _resize_delegate = ResizeDelegate.alloc().init()


# ===========================================
# E2E 调试服务 (仅测试模式开启)
# ===========================================


def _start_e2e_server(api):
    global _e2e_server
    if not E2E_MODE or _e2e_server is not None:
        return

    class _E2EHandler(BaseHTTPRequestHandler):
        def _json(self, status_code, payload):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _parse(self):
            parsed = urlparse(self.path)
            return parsed.path, parse_qs(parsed.query)

        def do_GET(self):
            path, _ = self._parse()
            if path == "/state":
                self._json(200, api.debug_get_state())
                return
            self._json(404, {"ok": False, "error": "not_found"})

        def do_POST(self):
            path, query = self._parse()
            if path == "/toggle_panel":
                ok = toggle_panel_from_thread()
                self._json(200, {"ok": ok, "state": api.debug_get_state()})
                return
            if path == "/set_unread":
                count = query.get("count", ["0"])[0]
                value = api.debug_set_unread_count(count)
                self._json(200, {"ok": value >= 0, "value": value, "state": api.debug_get_state()})
                return
            if path == "/focus_task":
                log_file = query.get("log_file", [""])[0]
                ok = api.focus_task(log_file)
                self._json(200, {"ok": bool(ok), "state": api.debug_get_state()})
                return
            self._json(404, {"ok": False, "error": "not_found"})

        def log_message(self, fmt, *args):
            return

    try:
        _e2e_server = ThreadingHTTPServer((E2E_HOST, E2E_PORT), _E2EHandler)
        thread = threading.Thread(target=_e2e_server.serve_forever, daemon=True)
        thread.start()
        _log(f"E2E server started: http://{E2E_HOST}:{E2E_PORT}")
    except Exception as e:
        _log(f"E2E server start failed: {e}")


def _stop_e2e_server():
    global _e2e_server
    if _e2e_server is None:
        return
    try:
        _e2e_server.shutdown()
        _e2e_server.server_close()
    except Exception:
        pass
    _e2e_server = None


# ===========================================
# pywebview JS API
# ===========================================


class Api:
    def __init__(self):
        self._last_notify_time = {}  # Key: "filepath:status" -> timestamp
        self._last_signal_ts = {}    # Key: log_file -> last_notified_signal_ts
        self._task_states = {}       # Key: log_file -> last_status
        self._first_run = True       # 启动时不通知
        self._unread_notification_count = 0  # 未读通知数 (用于状态栏角标)
        self._unread_by_task = {}    # Key: log_file -> unread_count
        self._last_focus_result = {"success": False, "provider": "", "reason": ""}

    def get_tasks(self):
        if not os.path.exists(LOG_DIR):
            return []

        log_files = glob.glob(os.path.join(LOG_DIR, "*.log"))
        log_files.sort(key=os.path.getmtime, reverse=True)

        tasks = []

        for log_file in log_files[:MAX_TASKS]:
            meta = parse_session_meta(log_file)
            terminal_label = _get_terminal_label(meta)

            # 接收 6 个返回值 (v2.0 New Signature)
            # tool_name, status, msg, exit_code, duration, signal_ts
            tool_name, status, msg, exit_code, duration, signal_ts = analyze_log(log_file)
            
            # v2.0: parse_start_info 已被集成到 analyze_log 内部, 无需重复解析
            # tool_name, start_time = parse_start_info(log_file) 
            # 注意: calculate_duration 需要 start_time，但现在 analyze_log 内部已计算好 duration
            # 如果是异常终止(kill)，我们需要 start_time 来重新计算 duration 吗？
            # monitor.py 的 analyze_log 已经不再返回 start_time 了。
            # 这是一个潜在问题。如果进程被 kill，status 会被这里覆盖为 DONE，但 duration 需要重算。
            # 方案：monitor.py 的 analyze_log 已经处理了正常流程。
            # 对于 kill 流程，monitor.py 无法感知。
            # 为了保持逻辑简单，我们暂时 accept 如果 kill 掉，duration 可能不准或者为空。
            # 或者，我们可以让 analyze_log 总是返回 start_time? 
            # 不，为了性能，我们接受这个小瑕疵，或者再次 parse_start_info 仅在 kill 时?
            # 让我们保留 parse_start_info 调用仅用于 kill 场景的 calculate_duration?
            # 实际上，monitor.py 的 parse_start_info 也是读文件头。
            
            # 实时进程检测: 如果状态不是 DONE, 检查进程是否存活
            if status != "DONE":
                try:
                    basename = os.path.basename(log_file)
                    parts = basename.rsplit('_', 3) # [tool, timestamp, pid, random.log]
                    if len(parts) >= 3:
                        pid = int(parts[2])
                        try:
                            # 只有当 PID 存在时才不做任何事
                            os.kill(pid, 0)
                        except OSError:
                            # 进程不存在 -> 强制标记为异常结束
                            ended_at = time.strftime('%Y-%m-%d %H:%M:%S')
                            _append_monitor_end_if_missing(log_file, 137, ended_at)
                            status = "DONE"
                            exit_code = 137 # SIGKILL
                            msg = "[进程已终止]"
                            # 重新读取 start_time 以计算 duration
                            _, start_time = parse_start_info(log_file)
                            duration = calculate_duration(start_time, ended_at)
                            # 顺便清理一下速率历史
                            clear_rate_history(log_file)
                except Exception:
                    pass

            msg = re.sub(r"\033\[[0-9;?]*[A-Za-z]", "", msg)
            msg = re.sub(r"[\x00-\x1f\x7f]", "", msg)
            if len(msg) > 40:
                msg = msg[:37] + "..."
            subtitle = _build_card_subtitle(
                tool_name=tool_name,
                status=status,
                msg=msg,
                exit_code=exit_code,
                duration=duration,
                signal_ts=signal_ts,
                terminal_label=terminal_label,
            )

            tasks.append(
                {
                    "tool": tool_name,
                    "status": status,
                    "message": msg,
                    "subtitle": subtitle,
                    "terminal_label": terminal_label,
                    "exit_code": exit_code,
                    "duration": duration,
                    "log_file": log_file,
                    "signal_ts": signal_ts, # Pass signal_ts to frontend logic
                }
            )

        self._check_notifications(tasks)
        update_status_icon(self._unread_notification_count)

        return tasks

    def clear_unread_notifications(self):
        self._unread_notification_count = 0
        self._unread_by_task.clear()
        update_status_icon(0)

    def _clear_unread_for_task(self, log_file):
        log_file = str(log_file or "").strip()
        if not log_file:
            return 0
        removed = int(self._unread_by_task.pop(log_file, 0) or 0)
        if removed > 0:
            self._unread_notification_count = max(0, self._unread_notification_count - removed)
            update_status_icon(self._unread_notification_count)
        return removed

    def _mark_notification_seen_or_unread(self, log_file):
        # 面板已在前台显示时，认为用户已看到，不累加状态栏角标。
        if _is_panel_visible_and_frontmost():
            return
        log_file = str(log_file or "").strip()
        if not log_file:
            return
        self._unread_notification_count += 1
        self._unread_by_task[log_file] = int(self._unread_by_task.get(log_file, 0) or 0) + 1

    def _check_notifications(self, tasks):
        now = time.time()
        
        # 首次运行: 仅初始化状态, 不发通知
        if self._first_run:
            for task in tasks:
                self._task_states[task["log_file"]] = task["status"]
                # 记录初始信号时间戳，避免启动时重复通知
                if task.get("signal_ts", 0) > 0:
                    self._last_signal_ts[task["log_file"]] = task["signal_ts"]
            self._first_run = False
            return

        for task in tasks:
            log_file = task["log_file"]
            status = task["status"]
            prev_status = self._task_states.get(log_file)
            signal_ts = task.get("signal_ts", 0)
            
            # 更新状态记录
            self._task_states[log_file] = status

            # 用户在终端里继续操作后，任务通常会从 WAITING/IDLE 回到 RUNNING。
            # 这说明对应提醒已被处理，自动清理该任务未读计数。
            if status == "RUNNING" and prev_status in {"WAITING", "IDLE"}:
                self._clear_unread_for_task(log_file)

            if status == "WAITING":
                # WAITING 仍使用 Edge Trigger + Debounce
                if status == prev_status: continue
                
                key = f"{log_file}:WAITING"
                if now - self._last_notify_time.get(key, 0) < 5: continue

                title, subtitle, body = _build_notification_payload(task)
                send_notification(title, subtitle, body)
                self._last_notify_time[key] = now
                self._mark_notification_seen_or_unread(log_file)
            
            elif status == "IDLE":
                # IDLE 使用 Signal Timestamp 去重逻辑
                if signal_ts > 0:
                    # 拥有有效信号 (来自 Hook)
                    last_ts = self._last_signal_ts.get(log_file, 0)
                    if signal_ts > last_ts:
                        # 这是一个新的完成信号 -> 通知
                        title, subtitle, body = _build_notification_payload(task)
                        send_notification(title, subtitle, body)
                        self._last_signal_ts[log_file] = signal_ts
                        # 同时更新 notify time 用于 debounce 兼容
                        self._last_notify_time[f"{log_file}:IDLE"] = now
                        self._mark_notification_seen_or_unread(log_file)
                    else:
                        # 信号时间戳未变 -> 说明是同一个完成事件 -> 忽略
                        pass
                else:
                    # 无信号 (纯 Regex/Rate 检测)
                    # 对于 Claude: 如果 Hook 没触发 (signal_ts=0), 我们假设这是用户输入/Prompt匹配
                    # 我们不仅不通知，甚至可能想忽略它。
                    # 但为了安全，如果工具是 Claude, 且没有信号，我们 *跳过通知*
                    if task["tool"] == "claude":
                        continue 

                    # 对于其他工具 (e.g. mvn/gradle): 使用 Edge Trigger
                    if status == prev_status: continue
                    key = f"{log_file}:IDLE"
                    if now - self._last_notify_time.get(key, 0) < 5: continue

                    title, subtitle, body = _build_notification_payload(task)
                    send_notification(title, subtitle, body)
                    self._last_notify_time[key] = now
                    self._mark_notification_seen_or_unread(log_file)

    def open_logs(self):
        os.makedirs(LOG_DIR, exist_ok=True)
        subprocess.run(["open", LOG_DIR])

    def delete_task(self, log_file):
        """删除任务日志文件"""
        try:
            self._clear_unread_for_task(log_file)
            if os.path.exists(log_file) and log_file.startswith(LOG_DIR):
                os.remove(log_file)
                # 清理速率跟踪历史, 避免内存泄漏
                clear_rate_history(log_file)
        except Exception:
            pass

    def focus_task(self, log_file):
        """定位到任务对应的终端会话。"""
        try:
            if not log_file or not log_file.startswith(LOG_DIR):
                return False
            # 点击任务卡片视为用户已读该任务相关通知。
            self._clear_unread_for_task(log_file)
            meta = _build_session_meta(log_file)
            pid = 0
            try:
                pid = int(meta.shell_pid) if meta.shell_pid else 0
            except Exception:
                pid = 0

            if pid > 0:
                try:
                    os.kill(pid, 0)
                except OSError:
                    self._last_focus_result = {
                        "success": False,
                        "provider": "",
                        "reason": f"pid dead: {pid}",
                    }
                    return False

            result = _terminal_focus_service.focus(meta)
            self._last_focus_result = {
                "success": bool(result.success),
                "provider": str(result.provider or ""),
                "reason": str(result.reason or ""),
            }
            if not result.success:
                _log(
                    "focus_task 未命中 "
                    f"tty={meta.tty} term={meta.term_program} reason={result.reason}"
                )
            return result.success
        except Exception as e:
            self._last_focus_result = {"success": False, "provider": "", "reason": str(e)}
            _log(f"focus_task 失败: {e}")
            return False

    def debug_get_state(self):
        if not E2E_MODE:
            return {"enabled": False}
        return {
            "enabled": True,
            "unread_notification_count": self._unread_notification_count,
            "last_focus_result": self._last_focus_result,
            "window_visible": bool(_window_visible),
        }

    def debug_set_unread_count(self, count):
        if not E2E_MODE:
            return -1
        try:
            self._unread_notification_count = max(0, int(count))
            update_status_icon(self._unread_notification_count)
            return self._unread_notification_count
        except Exception:
            return -1

    def resize_window(self, width, height):
        """自适应调整窗口高度"""
        if not _window:
            return
        try:
            w = int(width)
            h = int(height)
            if HAS_APPKIT:
                setup_resize_delegate()
                if _resize_delegate:
                    _resize_delegate.performSelectorOnMainThread_withObject_waitUntilDone_(
                        "doResize:", f"{w},{h}", False
                    )
                    return
            _do_resize_window(w, h)
        except Exception as e:
            _log(f"resize_window 调用失败: {e}")

    def quit_app(self):
        """真正退出"""
        _stop_e2e_server()
        cleanup_shell_wrapper()
        if _status_item:
            NSStatusBar.systemStatusBar().removeStatusItem_(_status_item)
        for w in webview.windows:
            w.destroy()


# ===========================================
# 窗口关闭拦截: 关闭 → 隐藏
# ===========================================


def on_closing():
    """拦截窗口关闭，改为隐藏到状态栏"""
    global _window_visible
    if _window:
        _window.hide()
    _window_visible = False
    return False  # 阻止真正关闭


# ===========================================
# 主入口
# ===========================================


def cleanup_stale_logs():
    """启动时清理旧日志: 删除已完成(DONE)的任务, 以及僵尸进程(进程已死但日志未结束)"""
    _log("开始清理旧日志...")
    count = 0
    now = time.time()
    for log_file in glob.glob(os.path.join(LOG_DIR, "*.log")):
        try:
            # 1. 清理超过 7 天的文件 (无论状态)
            mtime = os.path.getmtime(log_file)
            if now - mtime > 7 * 86400:
                os.remove(log_file)
                count += 1
                continue

            # analyze_log returns: tool_name, status, msg, exit_code, duration, signal_ts
            _, status, _, _, _, _ = analyze_log(log_file)

            # 2. 清理状态为 DONE 的任务
            if status == "DONE":
                os.remove(log_file)
                clear_rate_history(log_file)
                count += 1
                continue

            # 3. 清理僵尸任务 (状态非 DONE, 但进程已不存在)
            # 文件名格式: tool_timestamp_pid_random.log
            # 倒序解析: [tool..., timestamp, pid, random.log]
            try:
                basename = os.path.basename(log_file)
                parts = basename.rsplit('_', 3) # ['tool_part', 'timestamp', 'pid', 'random.log']
                if len(parts) >= 3:
                    pid_str = parts[2]
                    pid = int(pid_str)
                    
                    # 检查进程是否存在
                    try:
                        os.kill(pid, 0) # 发送信号 0 检测进程
                    except OSError:
                        # 进程不存在 -> 僵尸任务
                        _log(f"发现僵尸任务 (PID {pid} 不存在): {basename}")
                        os.remove(log_file)
                        clear_rate_history(log_file)
                        count += 1
            except (ValueError, IndexError):
                pass

        except Exception as e:
            _log(f"清理失败 {log_file}: {e}")
    _log(f"清理完成, 删除了 {count} 个旧文件")


def main():
    global _window, _api
    _log("main() 开始")
    os.makedirs(LOG_DIR, exist_ok=True)
    cleanup_stale_logs()  # <--- 启动时清理

    inject_shell_wrapper()
    inject_claude_hooks()

    def _cleanup_all():
        _stop_e2e_server()
        cleanup_shell_wrapper()
        cleanup_claude_hooks()

    atexit.register(_cleanup_all)

    def _signal_handler(signum, frame):
        _cleanup_all()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    api = Api()
    _api = api
    _start_e2e_server(api)
    _log(f"PANEL_HTML={PANEL_HTML} exists={os.path.exists(PANEL_HTML)}")

    _window = webview.create_window(
        title="CLI Monitor",
        url=PANEL_HTML,
        js_api=api,
        width=640,
        height=460,
        resizable=True,
        on_top=False,
        frameless=False,
        easy_drag=True,
        background_color="#F5F6FA",
    )
    _log("webview.create_window 完成")

    # 拦截窗口关闭: 隐藏到状态栏而非退出
    _window.events.closing += on_closing

    def on_started():
        """pywebview 在后台线程调用此函数; 安全调度状态栏创建到主线程"""
        _log("on_started() 开始")
        setup_statusbar_from_thread()
        _log("on_started() 完成")

    _log("webview.start 即将调用")
    webview.start(func=on_started, debug=False)

    # webview 退出后清理
    _cleanup_all()


if __name__ == "__main__":
    main()
