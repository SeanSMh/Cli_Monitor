#!/usr/bin/env python3
"""
CLI Monitor — macOS 状态栏应用
基于 rumps 实现，实时监控终端任务状态。

特性:
  - 启动时自动注入 Shell Wrapper（临时生效，重启后自动失效）
  - 退出时自动清理注入内容
  - 新打开的终端自动具备监控能力

用法:
    python3 menubar_app.py
"""

import os
import sys
import glob
import re
import atexit
import signal
import subprocess

import rumps

# 将项目目录加入路径，复用 monitor.py 的核心逻辑。
# 打包后优先使用 bundle 资源目录；兼容 PyInstaller 和 py2app。
def _resolve_script_dir() -> str:
    if getattr(sys, "frozen", False):
        bundled_dir = getattr(sys, "_MEIPASS", "") or os.environ.get("RESOURCEPATH", "")
        if bundled_dir:
            return os.path.abspath(bundled_dir)
    return os.path.dirname(os.path.abspath(__file__))


SCRIPT_DIR = _resolve_script_dir()
sys.path.insert(0, SCRIPT_DIR)

from monitor import (
    analyze_log,
    parse_start_info,
    calculate_duration,
    DEFAULT_LOG_DIR,
)

# === 配置 ===
LOG_DIR = os.environ.get("AI_MONITOR_DIR", DEFAULT_LOG_DIR)
MAX_TASKS = 8          # 菜单中最多显示的任务数
REFRESH_INTERVAL = 2   # 刷新间隔 (秒)

# 临时注入相关
TEMP_WRAPPER = "/tmp/cli_monitor_session.sh"
INJECT_MARKER = "# >>> cli-monitor-session >>>"
INJECT_END    = "# <<< cli-monitor-session <<<"
SHELL_WRAPPER_SOURCE = os.path.join(SCRIPT_DIR, "shell", "cli_monitor.sh")

# 状态 emoji / 文字映射
STATUS_EMOJI = {"RUNNING": "🟢", "WAITING": "🟡", "IDLE": "🔵", "DONE": "⚪"}
STATUS_TEXT  = {"RUNNING": "运行中", "WAITING": "⚠️ 待确认", "IDLE": "💬 等待输入", "DONE": "已结束"}

# 清理幂等标志 (防止 atexit + signal + _on_quit 重复执行)
_cleanup_done = False

# =============================================
# 临时注入管理
# =============================================

def _get_rc_file():
    """获取当前 Shell 的 rc 配置文件路径"""
    shell = os.environ.get("SHELL", "")
    if "zsh" in shell:
        return os.path.expanduser("~/.zshrc")
    elif "bash" in shell:
        return os.path.expanduser("~/.bashrc")
    return os.path.expanduser("~/.zshrc")  # 默认 zsh


def inject_shell_wrapper():
    """
    临时注入 Shell Wrapper:
    1. 将 wrapper 脚本复制到 /tmp/cli_monitor_session.sh
    2. 在 ~/.zshrc 中添加一行带守卫的 source 命令
    
    重启后 /tmp 被清理，守卫条件不满足，source 行自动失效。
    """
    # 1. 复制 wrapper 到 /tmp
    try:
        with open(SHELL_WRAPPER_SOURCE, "r") as src:
            content = src.read()
        with open(TEMP_WRAPPER, "w") as dst:
            dst.write(content)
        os.chmod(TEMP_WRAPPER, 0o644)
    except Exception as e:
        print(f"[CLI Monitor] 写入临时 wrapper 失败: {e}")
        return False

    # 2. 在 rc 文件中添加带守卫的 source 行
    rc_file = _get_rc_file()
    try:
        existing = ""
        if os.path.exists(rc_file):
            with open(rc_file, "r") as f:
                existing = f.read()

        # 如果已经注入过，跳过
        if INJECT_MARKER in existing:
            print(f"[CLI Monitor] 已存在注入标记，跳过")
            return True

        inject_block = (
            f"\n{INJECT_MARKER}\n"
            f"# CLI Monitor 状态栏应用临时注入 (应用退出或重启后自动失效)\n"
            f'[[ -f "{TEMP_WRAPPER}" ]] && source "{TEMP_WRAPPER}"\n'
            f"{INJECT_END}\n"
        )

        with open(rc_file, "a") as f:
            f.write(inject_block)

        print(f"[CLI Monitor] ✅ 已注入到 {rc_file}")
        print(f"[CLI Monitor] 💡 请在新终端中使用监控的命令 (claude, codex, gradle 等)")
        return True

    except Exception as e:
        print(f"[CLI Monitor] 注入 rc 文件失败: {e}")
        return False


def cleanup_shell_wrapper():
    """
    清理临时注入 (幂等: 多次调用安全):
    1. 从 ~/.zshrc 中移除注入块
    2. 删除 /tmp 中的临时 wrapper
    """
    global _cleanup_done
    if _cleanup_done:
        return
    _cleanup_done = True

    # 1. 清理 rc 文件
    rc_file = _get_rc_file()
    try:
        if os.path.exists(rc_file):
            with open(rc_file, "r") as f:
                lines = f.readlines()

            # 过滤掉注入块
            new_lines = []
            in_block = False
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
    except Exception as e:
        print(f"[CLI Monitor] 清理 rc 文件失败: {e}")

    # 2. 删除临时 wrapper
    try:
        if os.path.exists(TEMP_WRAPPER):
            os.remove(TEMP_WRAPPER)
            print(f"[CLI Monitor] ✅ 已删除 {TEMP_WRAPPER}")
    except Exception as e:
        print(f"[CLI Monitor] 删除临时文件失败: {e}")


# =============================================
# macOS 状态栏应用
# =============================================

class CLIMonitorApp(rumps.App):
    """macOS 状态栏监控应用"""

    def __init__(self):
        super().__init__(
            name="CLI Monitor",
            title="🛡️",
            quit_button=None,
        )

        self._notified_waiting = set()
        self._last_signature = ""

        # 启动时自动注入
        inject_shell_wrapper()

        # 构建初始菜单
        self._build_menu()

        # 启动定时刷新
        self.timer = rumps.Timer(self._on_refresh, REFRESH_INTERVAL)
        self.timer.start()

    def _build_menu(self):
        """构建菜单结构"""
        self.menu.clear()
        self.menu = [
            rumps.MenuItem("CLI 任务监控", callback=None),
            None,
            rumps.MenuItem("暂无活跃任务..."),
            None,
            rumps.MenuItem("🔄 刷新", callback=self._on_manual_refresh),
            rumps.MenuItem("📂 打开日志目录", callback=self._on_open_log_dir),
            None,
            rumps.MenuItem("退出", callback=self._on_quit),
        ]

    def _get_task_list(self):
        """获取当前任务列表"""
        if not os.path.exists(LOG_DIR):
            return []

        log_files = glob.glob(os.path.join(LOG_DIR, "*.log"))
        log_files.sort(key=os.path.getmtime, reverse=True)

        tasks = []
        for log_file in log_files[:MAX_TASKS]:
            # tool_name, start_time = parse_start_info(log_file) # Integrated in analyze_log
            
            # v2.0 Signature: tool_name, status, msg, exit_code, duration, signal_ts
            tool_name, status, msg, exit_code, duration, _ = analyze_log(log_file)

            # 清理控制字符
            msg = re.sub(r'\033\[[0-9;]*m', '', msg)
            msg = re.sub(r'[\x00-\x1f\x7f]', '', msg)
            if len(msg) > 35:
                msg = msg[:32] + "..."

            tasks.append({
                "tool": tool_name,
                "status": status,
                "message": msg,
                "exit_code": exit_code,
                "duration": duration,
                "log_file": log_file,
            })

        return tasks

    def _format_task_title(self, task):
        """格式化单个任务的菜单标题"""
        emoji = STATUS_EMOJI.get(task["status"], "❓")
        text = STATUS_TEXT.get(task["status"], "未知")
        tool = task["tool"]

        suffix = ""
        if task["duration"]:
            suffix = f"  ⏱{task['duration']}"
        elif task["status"] != "DONE" and task["message"]:
            msg = task["message"]
            if len(msg) > 20:
                msg = msg[:17] + "..."
            suffix = f"  {msg}"

        if task["status"] == "DONE" and task["exit_code"] > 0:
            text = f"异常退出({task['exit_code']})"
            emoji = "🔴"

        return f"{emoji} {tool:<10} {text}{suffix}"

    def _update_menu(self, tasks):
        """更新菜单内容"""
        signature = str([(t["tool"], t["status"], t["message"]) for t in tasks])
        if signature == self._last_signature:
            return
        self._last_signature = signature

        self.menu.clear()

        header = rumps.MenuItem("CLI 任务监控")
        header.set_callback(None)
        self.menu.add(header)
        self.menu.add(None)

        if tasks:
            for task in tasks:
                title = self._format_task_title(task)
                item = rumps.MenuItem(title)
                item.set_callback(None)
                self.menu.add(item)
        else:
            no_task = rumps.MenuItem("暂无活跃任务...")
            no_task.set_callback(None)
            self.menu.add(no_task)

        self.menu.add(None)
        self.menu.add(rumps.MenuItem("🔄 刷新", callback=self._on_manual_refresh))
        self.menu.add(rumps.MenuItem("📂 打开日志目录", callback=self._on_open_log_dir))
        self.menu.add(None)
        self.menu.add(rumps.MenuItem("退出", callback=self._on_quit))

    def _check_and_notify(self, tasks):
        """检查待确认/空闲任务并发送系统通知"""
        has_alert = False
        current_alert = set()

        for task in tasks:
            if task["status"] == "WAITING":
                has_alert = True
                task_id = task["log_file"] + ":WAITING"
                current_alert.add(task_id)
                if task_id not in self._notified_waiting:
                    rumps.notification(
                        title="⚠️ CLI 任务待确认",
                        subtitle=f"工具: {task['tool']}",
                        message=task["message"][:80],
                        sound=True,
                    )
            elif task["status"] == "IDLE":
                has_alert = True
                task_id = task["log_file"] + ":IDLE"
                current_alert.add(task_id)
                if task_id not in self._notified_waiting:
                    rumps.notification(
                        title="🔵 AI 已完成，等待输入",
                        subtitle=f"工具: {task['tool']}",
                        message="AI 已完成回复，等待你的下一步指令",
                        sound=True,
                    )

        self._notified_waiting = current_alert
        self.title = "⚠️" if has_alert else "🛡️"

    def _on_refresh(self, _=None):
        """定时刷新回调"""
        try:
            tasks = self._get_task_list()
            self._update_menu(tasks)
            self._check_and_notify(tasks)
        except Exception as e:
            print(f"刷新出错: {e}")

    def _on_manual_refresh(self, _):
        """手动刷新"""
        self._last_signature = ""
        self._on_refresh()

    def _on_open_log_dir(self, _):
        """在 Finder 中打开日志目录"""
        os.makedirs(LOG_DIR, exist_ok=True)
        subprocess.run(["open", LOG_DIR])

    def _on_quit(self, _):
        """退出应用 (自动清理注入)"""
        cleanup_shell_wrapper()
        rumps.quit_application()


def main():
    os.makedirs(LOG_DIR, exist_ok=True)

    # 注册退出清理 (覆盖异常退出、信号退出等场景)
    atexit.register(cleanup_shell_wrapper)

    def _signal_handler(signum, frame):
        cleanup_shell_wrapper()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    app = CLIMonitorApp()
    app.run()


if __name__ == "__main__":
    main()
