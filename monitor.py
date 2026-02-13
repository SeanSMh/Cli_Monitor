#!/usr/bin/env python3
"""
终端任务状态监控系统 - 监控层 (Python Monitor)
Logcat 模式：实时读取日志文件，分析 CLI 任务状态并输出监控看板。

用法:
    python3 monitor.py [--sound] [--max-tasks N] [--log-dir DIR]
"""

import os
import sys
import time
import glob
import re
import argparse
from collections import defaultdict
from threading import Timer, Lock
from config_loader import config

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    WATCHDOG_AVAILABLE = True
except ImportError:
    WATCHDOG_AVAILABLE = False


# === 配置初始化 ===
CORE_CONF = config.get("core")
BEHAVIOR_CONF = config.get("behavior")
RULES_CONF = config.get("rules")
UI_CONF = config.get("ui")

# 常量定义
DEFAULT_LOG_DIR = CORE_CONF.get("log_dir", "/tmp/ai_monitor_logs")
DEFAULT_REFRESH_RATE = UI_CONF.get("refresh_rate", 2000) / 1000.0  # Polling Fallback
DEFAULT_MAX_TASKS = CORE_CONF.get("max_tasks", 10)
TAIL_BYTES = CORE_CONF.get("tail_bytes", 4096)

IDLE_THRESHOLD_SECONDS = BEHAVIOR_CONF.get("idle_threshold", 60)
RATE_HIGH_THRESHOLD = BEHAVIOR_CONF.get("rate_high_threshold", 200)
RATE_IDLE_SECONDS = BEHAVIOR_CONF.get("rate_idle_seconds", 5)

RATE_HISTORY_SIZE = 30
SIGNAL_FILE = os.path.join(DEFAULT_LOG_DIR, "_claude_idle_signal")
SIGNAL_MAX_AGE = 3600

# ANSI 颜色/样式代码
RESET    = "\033[0m"
GREEN    = "\033[32m"
YELLOW   = "\033[33m"
GRAY     = "\033[90m"
BOLD     = "\033[1m"
BLINK    = "\033[5m"
CYAN     = "\033[36m"
DIM      = "\033[2m"


# === 核心工具函数 ===

def tail_read(filepath, num_bytes=TAIL_BYTES):
    """高效读取文件尾部内容"""
    try:
        file_size = os.path.getsize(filepath)
        with open(filepath, "rb") as f:
            if file_size > num_bytes:
                f.seek(file_size - num_bytes)
                raw = f.read()
            else:
                raw = f.read()

        text = ""
        for offset in range(min(4, len(raw))):
            try:
                text = raw[offset:].decode("utf-8")
                break
            except UnicodeDecodeError:
                continue
        else:
            text = raw.decode("utf-8", errors="ignore")

        lines = text.splitlines(keepends=True)
        if file_size > num_bytes and lines:
            lines = lines[1:]
        return lines
    except Exception:
        return []


def parse_start_info(filepath):
    tool_name = "unknown"
    start_time = ""
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            first_line = f.readline()
        match = re.search(r"MONITOR_START:\s*(\S+)\s*\|\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", first_line)
        if match:
            tool_name = match.group(1)
            start_time = match.group(2).strip()
        else:
            match = re.search(r"MONITOR_START:\s*(\S+)", first_line)
            if match:
                tool_name = match.group(1)
    except Exception:
        pass

    if tool_name == "unknown":
        try:
            basename = os.path.basename(filepath)
            name_part = basename.split("_")[0]
            if name_part:
                tool_name = name_part
        except Exception:
            pass

    return tool_name, start_time


def parse_end_info(lines):
    for line in reversed(lines):
        stripped = line.strip()
        if "MONITOR_END" in stripped:
            match = re.search(r"MONITOR_END:\s*(\d+)", stripped)
            exit_code = int(match.group(1)) if match else -1
            time_match = re.search(r"\|\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", stripped)
            end_time = time_match.group(1).strip() if time_match else ""
            return True, exit_code, end_time
    return False, -1, ""


def calculate_duration(start_time, end_time):
    if not start_time or not end_time:
        return ""
    try:
        from datetime import datetime
        fmt = "%Y-%m-%d %H:%M:%S"
        start = datetime.strptime(start_time, fmt)
        end = datetime.strptime(end_time, fmt)
        delta = end - start
        total_seconds = int(delta.total_seconds())
        if total_seconds < 0:
            return ""
        minutes, seconds = divmod(total_seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours > 0:
            return f"{hours}h{minutes}m{seconds}s"
        elif minutes > 0:
            return f"{minutes}m{seconds}s"
        else:
            return f"{seconds}s"
    except Exception:
        return ""


# === 状态分析与速率跟踪 ===
_file_size_history = defaultdict(list)

def track_file_rate(filepath):
    try:
        now = time.time()
        size = os.path.getsize(filepath)
    except Exception:
        return False, False, 0

    history = _file_size_history[filepath]
    history.append((now, size))

    if len(history) > RATE_HISTORY_SIZE:
        history[:] = history[-RATE_HISTORY_SIZE:]

    if len(history) < 2:
        return False, False, 0

    was_fast = False
    for i in range(1, len(history)):
        dt = history[i][0] - history[i - 1][0]
        ds = history[i][1] - history[i - 1][1]
        if dt > 0 and ds / dt > RATE_HIGH_THRESHOLD:
            was_fast = True
            break

    current_size = history[-1][1]
    stall_start = now
    for ts, sz in reversed(history):
        if sz != current_size:
            break
        stall_start = ts
    stall_duration = now - stall_start
    is_stalled = stall_duration > 0 and current_size == history[-1][1]

    return was_fast, is_stalled, stall_duration


def clear_rate_history(filepath):
    _file_size_history.pop(filepath, None)


def _get_tool_rules(tool_name):
    tools = RULES_CONF.get("tools", [])
    for t in tools:
        if t.get("name") == tool_name or tool_name in t.get("alias", []):
            return t
    return None


def analyze_log(filepath):
    """
    Returns: (tool_name, status, message, exit_code, duration, signal_ts)
    Modified to include tool_name in return for cache efficiency
    """
    lines = tail_read(filepath)

    tool_name, start_time = parse_start_info(filepath)
    if not lines:
        return tool_name, "RUNNING", "初始化...", -1, "", 0

    tool_rules = _get_tool_rules(tool_name) or {}

    is_done, exit_code, end_time = parse_end_info(lines)
    if is_done:
        duration = calculate_duration(start_time, end_time)
        clear_rate_history(filepath)
        return tool_name, "DONE", "任务完成", exit_code, duration, 0

    done_patterns = tool_rules.get("done_patterns", {})
    tail_lines = lines[-5:]
    
    def strip_ansi(s):
        s = re.sub(r'\033\[[0-9;?]*[A-Za-z]', '', s)
        s = re.sub(r'\033\][^\007]*\007', '', s)
        s = re.sub(r'\033\[[=>][0-9;]*[A-Za-z]', '', s)
        s = re.sub(r'\033[()][A-Z0-9]', '', s)
        return s

    clean_lines = [strip_ansi(l) for l in tail_lines]
    context = "".join(clean_lines)
    
    last_line = ""
    for l in reversed(clean_lines):
        if l.strip():
            last_line = l.strip()
            last_line = re.sub(r'[\x00-\x1f\x7f]', '', last_line)
            break

    for pattern, msg in done_patterns.items():
        if re.search(pattern, context, re.IGNORECASE):
            return tool_name, "IDLE", msg, -1, "", 0

    if tool_name == "claude" and os.path.exists(SIGNAL_FILE):
        try:
            signal_mtime = os.path.getmtime(SIGNAL_FILE)
            log_mtime = os.path.getmtime(filepath)
            age = time.time() - signal_mtime
            if age < SIGNAL_MAX_AGE and signal_mtime >= log_mtime - 5:
                return tool_name, "IDLE", "AI 已完成回复", -1, "", signal_mtime
        except Exception:
            pass

    common_waiting = RULES_CONF.get("common", {}).get("waiting", [])
    for pattern in common_waiting:
        if re.search(pattern, context, re.IGNORECASE):
            for line in reversed(clean_lines):
                stripped = line.strip()
                stripped = re.sub(r'[\x00-\x1f\x7f]', '', stripped)
                if re.search(pattern, stripped, re.IGNORECASE):
                    return tool_name, "WAITING", stripped[:60], -1, "", 0
            return tool_name, "WAITING", last_line[:60], -1, "", 0

    idle_patterns = tool_rules.get("idle_patterns", []) + RULES_CONF.get("common", {}).get("idle", [])
    busy_patterns = tool_rules.get("busy_patterns", []) + RULES_CONF.get("common", {}).get("busy", [])

    last_idle_pos = -1
    for pattern in idle_patterns:
        for m in re.finditer(pattern, context, re.IGNORECASE):
            last_idle_pos = max(last_idle_pos, m.end())

    if last_idle_pos > 0:
        last_busy_pos = -1
        for pattern in busy_patterns:
            for m in re.finditer(pattern, context, re.IGNORECASE):
                last_busy_pos = max(last_busy_pos, m.end())

        if last_idle_pos > last_busy_pos:
            return tool_name, "IDLE", "等待输入", -1, "", 0

    was_fast, is_stalled, stall_secs = track_file_rate(filepath)
    if was_fast and is_stalled and stall_secs >= RATE_IDLE_SECONDS:
        return tool_name, "IDLE", "AI 已完成回复", -1, "", 0

    try:
        mtime = os.path.getmtime(filepath)
        idle_seconds = time.time() - mtime
        
        busy_in_tail = any(re.search(p, context, re.IGNORECASE) for p in busy_patterns)

        if not busy_in_tail and idle_seconds > 10: 
             broad_lines = lines[-30:] if len(lines) >= 30 else lines
             broad_context = "".join([strip_ansi(l) for l in broad_lines])
             if any(re.search(p, broad_context, re.IGNORECASE) for p in busy_patterns):
                 return tool_name, "IDLE", "AI 已完成回复", -1, "", 0

        if idle_seconds > IDLE_THRESHOLD_SECONDS:
            return tool_name, "IDLE", "等待输入", -1, "", 0
    except Exception:
        pass

    return tool_name, "RUNNING", last_line[:60], -1, "", 0


# === 监控核心 (Hybrid: Watchdog + Polling) ===

class MonitorCore:
    def __init__(self, log_dir, max_tasks, enable_sound):
        self.log_dir = log_dir
        self.max_tasks = max_tasks
        self.enable_sound = enable_sound
        
        self.tasks_cache = [] # List of task result tuples
        self.lock = Lock()
        self.needs_render = True
        self.timers = {}
        
        # Debounce/Throttle configurations
        self.debounce_ms = 0.1 

    def _analyze_all(self):
        """全量扫描"""
        log_files = glob.glob(os.path.join(self.log_dir, "*.log"))
        log_files.sort(key=os.path.getmtime, reverse=True)
        active_files = log_files[:self.max_tasks]
        
        results = []
        for f in active_files:
            # tool_name, status, msg, exit_code, duration, signal_ts
            results.append(analyze_log(f))
            
        with self.lock:
            if results != self.tasks_cache:
                self.tasks_cache = results
                self.needs_render = True

    def on_file_change(self, filepath):
        """事件回调：单文件更新"""
        if not filepath.endswith(".log"):
            return
            
        # 简单策略：只要有变动就触发一次全量简析（为了排序等），后续可优化为增量更新
        # 为了避免高频 IO，这里做 Debounce
        with self.lock:
            if filepath in self.timers:
                self.timers[filepath].cancel()
            
            t = Timer(self.debounce_ms, self._analyze_all)
            t.start()
            self.timers[filepath] = t

    def render(self):
        """渲染 UI (仅在 Dirty 时)"""
        with self.lock:
            if not self.needs_render:
                return
            tasks = list(self.tasks_cache) # Copy
            self.needs_render = False
        
        clear_screen()
        print(f"{BOLD}{CYAN}╔══════════════════════════════════════════════════════════════╗{RESET}")
        print(f"{BOLD}{CYAN}║{RESET}  🛡️  CLI 任务监控看板    {DIM}{time.strftime('%Y-%m-%d %H:%M:%S')}{RESET}  {BOLD}{CYAN}║{RESET}")
        print(f"{BOLD}{CYAN}╠══════════════════════════════════════════════════════════════╣{RESET}")
        print(f"{BOLD}{CYAN}║{RESET} {'工具':<10} {'状态':<14} {'耗时':<10} {'最新输出':<26} {BOLD}{CYAN}║{RESET}")
        print(f"{BOLD}{CYAN}╠══════════════════════════════════════════════════════════════╣{RESET}")

        if not tasks:
            print(f"{BOLD}{CYAN}║{RESET}  {DIM}暂无活跃任务... 在其他终端运行被监控的命令即可{RESET}          {BOLD}{CYAN}║{RESET}")
        else:
            has_waiting = False
            for t in tasks:
                # Unpack: tool_name, status, msg, exit_code, duration, signal_ts
                tool_name, status, msg, exit_code, duration, _ = t
                
                if status == "WAITING":
                    has_waiting = True

                status_str = format_status(status, exit_code)
                duration_str = duration if duration else "—"
                
                if len(msg) > 24:
                    msg = msg[:21] + "..."
                msg = re.sub(r'\033\[[0-9;]*m', '', msg)
                msg = re.sub(r'[\x00-\x1f\x7f]', '', msg)

                print(f"{BOLD}{CYAN}║{RESET} {tool_name:<10} {status_str:<25} {duration_str:<10} {msg:<26}{BOLD}{CYAN}║{RESET}")

            if has_waiting and self.enable_sound:
                sys.stdout.write('\a')
                sys.stdout.flush()

        print(f"{BOLD}{CYAN}╚══════════════════════════════════════════════════════════════╝{RESET}")
        mode_str = "Event-Driven" if WATCHDOG_AVAILABLE else "Polling"
        print(f"  {DIM}模式: {mode_str} | 按 Ctrl+C 退出 | {self.log_dir}{RESET}")


if WATCHDOG_AVAILABLE:
    class LogEventHandler(FileSystemEventHandler):
        def __init__(self, callback):
            self.callback = callback
            
        def on_modified(self, event):
            self.callback(event.src_path)
            
        def on_created(self, event):
            self.callback(event.src_path)
            
        def on_moved(self, event):
            self.callback(event.dest_path)


def clear_screen():
    sys.stdout.write("\033[H\033[J")
    sys.stdout.flush()

def format_status(status, exit_code=-1):
    if status == "WAITING": return f"{BLINK}{YELLOW}🟡 待确认{RESET}"
    elif status == "IDLE": return f"{CYAN}🔵 等待输入{RESET}"
    elif status == "RUNNING": return f"{GREEN}🟢 运行中{RESET}"
    else:
        if exit_code == 0: return f"{GRAY}⚪ 已完成{RESET}"
        elif exit_code > 0: return f"{GRAY}🔴 异常退出({exit_code}){RESET}"
        else: return f"{GRAY}⚪ 已结束{RESET}"


def main():
    epilog_text = (
        "示例:\n"
        "  python3 monitor.py                    # 默认启动\n"
        "  python3 monitor.py --sound            # 启用声音提醒\n"
        "  python3 monitor.py --max-tasks 10     # 显示最近 10 个任务\n"
        "  python3 monitor.py --log-dir /tmp/my  # 自定义日志目录"
    )

    parser = argparse.ArgumentParser(
        description="🛡️ CLI 任务状态监控看板 (Logcat Mode)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=epilog_text
    )
    parser.add_argument("--sound", action="store_true", default=False, help="启用提示音")
    parser.add_argument("--max-tasks", type=int, default=DEFAULT_MAX_TASKS, help="最大任务数")
    parser.add_argument("--log-dir", type=str, default=DEFAULT_LOG_DIR, help="日志目录")
    
    args = parser.parse_args()
    log_dir = args.log_dir
    os.makedirs(log_dir, exist_ok=True)
    config.load()
    
    monitor = MonitorCore(log_dir, args.max_tasks, args.sound)
    
    # 初始全量扫描
    monitor._analyze_all()
    monitor.render()

    observer = None
    if WATCHDOG_AVAILABLE:
        observer = Observer()
        handler = LogEventHandler(monitor.on_file_change)
        observer.schedule(handler, log_dir, recursive=False)
        observer.start()

    print(f"🛡️  监控已启动 | 模式: {'Event-Driven' if observer else 'Polling'}")

    try:
        last_poll = time.time()
        while True:
            # 主循环渲染 (限制 FPS, 防止渲染太快)
            monitor.render()
            time.sleep(0.1) 
            
            # 兜底轮询 (每 60s 或当 Watchdog 不可用时的常规轮询)
            now = time.time()
            poll_interval = 60.0 if observer else DEFAULT_REFRESH_RATE
            
            if now - last_poll > poll_interval:
                monitor._analyze_all()
                last_poll = now
                
    except KeyboardInterrupt:
        if observer:
            observer.stop()
            observer.join()
        clear_screen()
        print(f"\n{GREEN}✅ 监控已退出。{RESET}")
        sys.exit(0)

if __name__ == "__main__":
    main()
