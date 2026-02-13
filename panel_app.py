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
    calculate_duration,
    clear_rate_history,
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
_resize_delegate = None
_api = None


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


def _run_osascript(script):
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


def _focus_terminal_by_tty(tty):
    """根据 tty 激活对应终端 tab/session，优先 iTerm2，其次 Terminal。"""
    tty_short = tty.replace("/dev/", "", 1)

    script_iterm2 = f'''
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

    script_terminal = f'''
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

    return _run_osascript(script_iterm2) or _run_osascript(script_terminal)


# ===========================================
# macOS 状态栏图标
# ===========================================


def update_status_icon(alert_count):
    """更新状态栏图标"""
    try:
        if HAS_APPKIT and _sb_delegate:
            _sb_delegate.performSelectorOnMainThread_withObject_waitUntilDone_(
                "doSetStatusIcon:", str(max(0, int(alert_count))), False
            )
            return
        _do_update_status_icon(alert_count)
    except Exception:
        pass


if HAS_APPKIT:

    class StatusBarDelegate(NSObject):
        """状态栏点击代理 + 主线程调度"""

        @objc.python_method
        def toggle_panel(self):
            global _window_visible, _api
            if _window is None:
                return
            if _window_visible:
                _window.hide()
                _window_visible = False
            else:
                _window.show()
                _window_visible = True
                # 从状态栏打开面板时，未读通知计数清零
                if _api is not None:
                    _api.clear_unread_notifications()

        def statusBarClicked_(self, sender):
            self.toggle_panel()

        def doSetupStatusBar_(self, _):
            """ObjC selector: 在主线程上执行状态栏创建"""
            _do_setup_statusbar()

        def doSetStatusIcon_(self, payload):
            """ObjC selector: 在主线程上更新状态栏图标"""
            try:
                _do_update_status_icon(int(str(payload)))
            except Exception:
                _do_update_status_icon(0)


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


def _do_update_status_icon(alert_count):
    """实际更新状态栏图标 (仅在主线程调用)"""
    if _status_item:
        try:
            count = max(0, int(alert_count))
        except Exception:
            count = 0
        _status_item.button().setTitle_(f"⚠️{count}" if count > 0 else "🛡️")


def _do_resize_window(width, height):
    """实际执行窗口尺寸调整 (必须在主线程调用)"""
    global _window
    if _window is None:
        return
    _window.resize(int(width), int(height))


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


def setup_resize_delegate():
    global _resize_delegate
    if not HAS_APPKIT:
        return
    if _resize_delegate is None:
        _resize_delegate = ResizeDelegate.alloc().init()


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

    def get_tasks(self):
        if not os.path.exists(LOG_DIR):
            return []

        log_files = glob.glob(os.path.join(LOG_DIR, "*.log"))
        log_files.sort(key=os.path.getmtime, reverse=True)

        tasks = []

        for log_file in log_files[:MAX_TASKS]:
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
                            status = "DONE"
                            exit_code = 137 # SIGKILL
                            msg = "[进程已终止]"
                            # 重新读取 start_time 以计算 duration
                            _, start_time = parse_start_info(log_file)
                            duration = calculate_duration(start_time, time.strftime('%Y-%m-%d %H:%M:%S'))
                            # 顺便清理一下速率历史
                            clear_rate_history(log_file)
                except Exception:
                    pass

            msg = re.sub(r"\033\[[0-9;?]*[A-Za-z]", "", msg)
            msg = re.sub(r"[\x00-\x1f\x7f]", "", msg)
            if len(msg) > 40:
                msg = msg[:37] + "..."

            tasks.append(
                {
                    "tool": tool_name,
                    "status": status,
                    "message": msg,
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
        update_status_icon(0)

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

            if status == "WAITING":
                # WAITING 仍使用 Edge Trigger + Debounce
                if status == prev_status: continue
                
                key = f"{log_file}:WAITING"
                if now - self._last_notify_time.get(key, 0) < 5: continue

                send_notification("⚠️ CLI 任务待确认", f"工具: {task['tool']}", task["message"][:60])
                self._last_notify_time[key] = now
                self._unread_notification_count += 1
            
            elif status == "IDLE":
                # IDLE 使用 Signal Timestamp 去重逻辑
                if signal_ts > 0:
                    # 拥有有效信号 (来自 Hook)
                    last_ts = self._last_signal_ts.get(log_file, 0)
                    if signal_ts > last_ts:
                        # 这是一个新的完成信号 -> 通知
                        send_notification("🔵 AI 已完成，等待输入", f"工具: {task['tool']}", "AI 已完成回复，等待你的下一步指令")
                        self._last_signal_ts[log_file] = signal_ts
                        # 同时更新 notify time 用于 debounce 兼容
                        self._last_notify_time[f"{log_file}:IDLE"] = now
                        self._unread_notification_count += 1
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

                    send_notification("🔵 任务已完成", f"工具: {task['tool']}", task["message"][:60])
                    self._last_notify_time[key] = now
                    self._unread_notification_count += 1

    def open_logs(self):
        os.makedirs(LOG_DIR, exist_ok=True)
        subprocess.run(["open", LOG_DIR])

    def delete_task(self, log_file):
        """删除任务日志文件"""
        try:
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
            pid = _extract_pid_from_log_file(log_file)
            if not pid:
                return False
            try:
                os.kill(pid, 0)
            except OSError:
                return False
            tty = _get_tty_from_pid(pid)
            if not tty:
                return False
            return _focus_terminal_by_tty(tty)
        except Exception as e:
            _log(f"focus_task 失败: {e}")
            return False

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
        background_color="#0F1118",
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
