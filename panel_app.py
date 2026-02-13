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

import webview

# PyObjC (macOS 状态栏)
try:
    from AppKit import (
        NSStatusBar,
        NSVariableStatusItemLength,
        NSApplication,
        NSApplicationActivationPolicyAccessory,
    )
    from Foundation import NSObject
    import objc

    HAS_APPKIT = True
except ImportError:
    HAS_APPKIT = False

# 项目路径
if getattr(sys, "frozen", False):
    SCRIPT_DIR = sys._MEIPASS
else:
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from monitor import (
    analyze_log,
    parse_start_info,
    calculate_duration,
    DEFAULT_LOG_DIR,
)

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


def send_notification(title, subtitle, message):
    script = (
        f'display notification "{message}" '
        f'with title "{title}" '
        f'subtitle "{subtitle}" '
        f'sound name "default"'
    )
    try:
        subprocess.Popen(
            ["osascript", "-e", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


# ===========================================
# macOS 状态栏图标
# ===========================================


def update_status_icon(has_alert):
    """更新状态栏图标"""
    if _status_item:
        try:
            _status_item.button().setTitle_("⚠️" if has_alert else "🛡️")
        except Exception:
            pass


if HAS_APPKIT:

    class StatusBarDelegate(NSObject):
        """状态栏点击代理 + 主线程调度"""

        @objc.python_method
        def toggle_panel(self):
            global _window_visible
            if _window is None:
                return
            if _window_visible:
                _window.hide()
                _window_visible = False
            else:
                _window.show()
                _window_visible = True

        def statusBarClicked_(self, sender):
            self.toggle_panel()

        def doSetupStatusBar_(self, _):
            """ObjC selector: 在主线程上执行状态栏创建"""
            _do_setup_statusbar()


def _do_setup_statusbar():
    """实际创建状态栏图标 (仅在主线程调用)"""
    global _status_item, _sb_delegate

    # 无 Dock 图标，纯状态栏应用
    NSApplication.sharedApplication().setActivationPolicy_(
        NSApplicationActivationPolicyAccessory
    )

    status_bar = NSStatusBar.systemStatusBar()
    _status_item = status_bar.statusItemWithLength_(NSVariableStatusItemLength)
    _status_item.button().setTitle_("🛡️")

    # 绑定点击事件
    _status_item.button().setAction_(
        objc.selector(_sb_delegate.statusBarClicked_, signature=b"v@:@")
    )
    _status_item.button().setTarget_(_sb_delegate)

    print("[CLI Monitor] ✅ 状态栏图标已创建 (主线程)")


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


# ===========================================
# pywebview JS API
# ===========================================


class Api:
    def __init__(self):
        self._notified = set()

    def get_tasks(self):
        if not os.path.exists(LOG_DIR):
            return []

        log_files = glob.glob(os.path.join(LOG_DIR, "*.log"))
        log_files.sort(key=os.path.getmtime, reverse=True)

        tasks = []
        has_alert = False

        for log_file in log_files[:MAX_TASKS]:
            tool_name, start_time = parse_start_info(log_file)
            status, msg, exit_code, duration = analyze_log(log_file)

            msg = re.sub(r"\033\[[0-9;?]*[A-Za-z]", "", msg)
            msg = re.sub(r"[\x00-\x1f\x7f]", "", msg)
            if len(msg) > 40:
                msg = msg[:37] + "..."

            if status in ("WAITING", "IDLE"):
                has_alert = True

            tasks.append(
                {
                    "tool": tool_name,
                    "status": status,
                    "message": msg,
                    "exit_code": exit_code,
                    "duration": duration,
                    "log_file": log_file,
                }
            )

        self._check_notifications(tasks)
        update_status_icon(has_alert)

        return tasks

    def _check_notifications(self, tasks):
        current = set()
        for task in tasks:
            if task["status"] == "WAITING":
                key = task["log_file"] + ":WAITING"
                current.add(key)
                if key not in self._notified:
                    send_notification(
                        "⚠️ CLI 任务待确认",
                        f"工具: {task['tool']}",
                        task["message"][:60],
                    )
            elif task["status"] == "IDLE":
                key = task["log_file"] + ":IDLE"
                current.add(key)
                if key not in self._notified:
                    send_notification(
                        "🔵 AI 已完成，等待输入",
                        f"工具: {task['tool']}",
                        "AI 已完成回复，等待你的下一步指令",
                    )
        self._notified = current

    def open_logs(self):
        os.makedirs(LOG_DIR, exist_ok=True)
        subprocess.run(["open", LOG_DIR])

    def delete_task(self, log_file):
        """删除任务日志文件"""
        try:
            if os.path.exists(log_file) and log_file.startswith(LOG_DIR):
                os.remove(log_file)
        except Exception:
            pass

    def resize_window(self, width, height):
        """自适应调整窗口高度"""
        if _window:
            try:
                _window.resize(int(width), int(height))
            except Exception:
                pass

    def quit_app(self):
        """真正退出"""
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


def main():
    global _window
    os.makedirs(LOG_DIR, exist_ok=True)

    inject_shell_wrapper()
    inject_claude_hooks()

    def _cleanup_all():
        cleanup_shell_wrapper()
        cleanup_claude_hooks()

    atexit.register(_cleanup_all)

    def _signal_handler(signum, frame):
        _cleanup_all()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    api = Api()

    _window = webview.create_window(
        title="CLI Monitor",
        url=PANEL_HTML,
        js_api=api,
        width=640,
        height=460,
        resizable=True,
        on_top=True,
        frameless=False,
        easy_drag=True,
        background_color="#0F1118",
    )

    # 拦截窗口关闭: 隐藏到状态栏而非退出
    _window.events.closing += on_closing

    def on_started():
        """pywebview 在后台线程调用此函数; 安全调度状态栏创建到主线程"""
        setup_statusbar_from_thread()

    webview.start(func=on_started, debug=False)

    # webview 退出后清理
    _cleanup_all()


if __name__ == "__main__":
    main()
