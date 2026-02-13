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

# === 配置区 ===
DEFAULT_LOG_DIR = "/tmp/ai_monitor_logs"
DEFAULT_REFRESH_RATE = 1.0   # 刷新频率 (秒)
DEFAULT_MAX_TASKS = 5        # 默认显示最近 N 个任务
TAIL_BYTES = 4096            # 尾部读取字节数 (避免大文件全量加载)
IDLE_THRESHOLD_SECONDS = 60  # 通用兜底阈值: 60 秒无新输出 (仅用于无特征匹配的未知工具)
POST_BUSY_IDLE_SECONDS = 10  # 后忙碌快速检测: BUSY 特征消失后 10 秒无输出即判定 IDLE
RATE_IDLE_SECONDS = 5         # 速率检测: 从高速输出→停止 后 5 秒判定 IDLE
RATE_HIGH_THRESHOLD = 200     # 字节/秒: 超过此值视为 "正在回复" (文本流)
RATE_HISTORY_SIZE = 30        # 保留最近 N 个采样点 (默认 2秒刷新, 博 60 秒历史)
SIGNAL_FILE = os.path.join(DEFAULT_LOG_DIR, "_claude_idle_signal")  # Claude Hook 信号文件
SIGNAL_MAX_AGE = 3600         # 信号文件有效期 (秒) - 延长至 1 小时以支持 Signal Timestamp Latching

# 状态正则匹配规则 (根据你的工具习惯调整)
WAIT_PATTERNS = [
    r"\(y/n\)",              # 匹配 (y/n)
    r"\(Y/n\)",              # 匹配 (Y/n)
    r"\(yes/no\)",           # 匹配 (yes/no)
    r"Confirm\?",            # 匹配 Confirm?
    r"\[\?\]",               # 匹配 inquirer 风格的选择框
    r"Press Enter",          # 匹配回车继续
    r"Do you want to",       # 询问句
    r"Would you like to",    # 询问句
    r"Apply changes\?",      # 应用变更
]

# AI 工具空闲特征 (表示 AI 已完成回复，等待用户输入)
IDLE_PATTERNS = [
    r"\? for shortcuts",      # codex / claude 空闲提示符
    r"context left",          # codex 空闲底栏
    r"gemini >\s*$",          # Gemini CLI 空闲提示符
]

# AI 工具工作中特征 (表示 AI 正在生成/思考)
BUSY_PATTERNS = [
    r"esc to interrupt",      # codex / claude 正在生成
    r"Working\(",             # codex 正在工作
    r"Thinking",              # claude / gemini 思考中
    r"Generating",            # gemini 生成中
    r"\u2580",                 # gemini 动画 spinner (block char)
]

# 构建工具完成特征 (匹配到即刻判定 IDLE, 并附带成功/失败信息)
BUILD_DONE_PATTERNS = [
    # gradle
    (r"BUILD SUCCESSFUL",     "✅ 构建成功"),
    (r"BUILD FAILED",         "❌ 构建失败"),
    # maven
    (r"BUILD SUCCESS",        "✅ 构建成功"),
    (r"BUILD FAILURE",        "❌ 构建失败"),
]

# ANSI 颜色/样式代码
RESET    = "\033[0m"
GREEN    = "\033[32m"
YELLOW   = "\033[33m"
GRAY     = "\033[90m"
BOLD     = "\033[1m"
BLINK    = "\033[5m"
CYAN     = "\033[36m"
DIM      = "\033[2m"


# === 核心逻辑 ===

def tail_read(filepath, num_bytes=TAIL_BYTES):
    """高效读取文件尾部内容，避免大文件全量加载。
    使用 rb 模式读取后手动解码，避免 seek 到多字节 UTF-8 字符中间导致乱码。
    """
    try:
        file_size = os.path.getsize(filepath)
        with open(filepath, "rb") as f:
            if file_size > num_bytes:
                f.seek(file_size - num_bytes)
                raw = f.read()
            else:
                raw = f.read()

        # 安全解码: 从头找到第一个合法 UTF-8 起始字节
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
        # 丢弃第一行 (可能是不完整行, 仅在 seek 过的情况下)
        if file_size > num_bytes and lines:
            lines = lines[1:]
        return lines
    except Exception:
        return []


def parse_start_info(filepath):
    """解析日志文件头部的 MONITOR_START 信息"""
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
            # 兼容旧格式: --- MONITOR_START: tool_name ---
            match = re.search(r"MONITOR_START:\s*(\S+)", first_line)
            if match:
                tool_name = match.group(1)
    except Exception:
        pass

    # Fallback: 从文件名提取工具名 (格式: toolname_timestamp_pid_random.log)
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
    """从日志尾部解析 MONITOR_END 信息"""
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
    """计算任务耗时"""
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


# === 输出速率跟踪器 ===
# 记录每个日志文件的 (timestamp, file_size) 采样历史
_file_size_history = defaultdict(list)  # {filepath: [(ts, size), ...]}


def track_file_rate(filepath):
    """
    跟踪文件大小变化速率。

    Returns:
        (was_fast, is_stalled, stall_duration)
        was_fast:       历史中是否出现过高速输出 (>200 B/s)
        is_stalled:     当前是否停止输出 (文件大小不再变化)
        stall_duration: 停止输出的持续秒数
    """
    try:
        now = time.time()
        size = os.path.getsize(filepath)
    except Exception:
        return False, False, 0

    history = _file_size_history[filepath]
    history.append((now, size))

    # 保留最近 N 个采样点
    if len(history) > RATE_HISTORY_SIZE:
        history[:] = history[-RATE_HISTORY_SIZE:]

    # 至少需要 2 个采样点才能计算速率
    if len(history) < 2:
        return False, False, 0

    # 检查历史中是否出现过高速输出
    was_fast = False
    for i in range(1, len(history)):
        dt = history[i][0] - history[i - 1][0]
        ds = history[i][1] - history[i - 1][1]
        if dt > 0 and ds / dt > RATE_HIGH_THRESHOLD:
            was_fast = True
            break

    # 检查是否停止输出: 文件大小不再变化
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
    """清理已结束任务的速率历史"""
    _file_size_history.pop(filepath, None)


def analyze_log(filepath):
    """
    分析日志文件以确定任务状态。

    Returns:
        (status, message, exit_code, duration, signal_ts)
        status:    "RUNNING" | "WAITING" | "IDLE" | "DONE"
        message:   最新输出内容摘要
        exit_code: 退出码 (仅 DONE 状态有效, 否则 -1)
        duration:  耗时字符串 (仅 DONE 状态有效)
        signal_ts: 信号文件时间戳 (仅 Signal IDLE 有效, 否则 0)
    """
    lines = tail_read(filepath)

    if not lines:
        return "RUNNING", "初始化...", -1, "", 0

    # 解析起始信息
    tool_name, start_time = parse_start_info(filepath)

    # 检查是否已结束
    is_done, exit_code, end_time = parse_end_info(lines)
    if is_done:
        duration = calculate_duration(start_time, end_time)
        clear_rate_history(filepath)
        return "DONE", "任务完成", exit_code, duration, 0

    # ── 层 0: Claude Hook 信号文件检测 (即时, 0 延迟) ──
    # 如果是 claude 任务, 且信号文件存在且新鲜, 立即判定 IDLE
    # 多会话安全: 只有当信号时间在 [log_mtime - 2, now] 范围内才关联
    if tool_name == "claude" and os.path.exists(SIGNAL_FILE):
        try:
            signal_mtime = os.path.getmtime(SIGNAL_FILE)
            log_mtime = os.path.getmtime(filepath)
            age = time.time() - signal_mtime
            # 条件 1: 信号必须新鲜 (1 小时内)
            # 条件 2: 信号时间 >= 日志最后写入时间 - 5s (容许少量后续日志/用户输入更新)
            if age < SIGNAL_MAX_AGE and signal_mtime >= log_mtime - 5:
                return "IDLE", "AI 已完成回复", -1, "", signal_mtime
        except Exception:
            pass

    # 取尾部上下文 (最后 5 行) 用于状态模式匹配
    tail_lines = lines[-5:]

    # 先清除 ANSI 转义序列 (含 CSI、OSC、kitty 扩展等)
    def strip_ansi(s):
        s = re.sub(r'\033\[[0-9;?]*[A-Za-z]', '', s)    # CSI 序列
        s = re.sub(r'\033\][^\007]*\007', '', s)          # OSC 序列 (如窗口标题)
        s = re.sub(r'\033\[[=>][0-9;]*[A-Za-z]', '', s)  # 扩展模式序列
        s = re.sub(r'\033[()][A-Z0-9]', '', s)            # 字符集切换
        return s

    clean_lines = [strip_ansi(l) for l in tail_lines]
    context = "".join(clean_lines)
    last_line = clean_lines[-1].strip() if clean_lines else ""

    # 清理控制字符
    last_line = re.sub(r'[\x00-\x1f\x7f]', '', last_line)

    # 匹配"等待确认"模式
    for pattern in WAIT_PATTERNS:
        if re.search(pattern, context, re.IGNORECASE):
            # 提取匹配到的那一行作为消息
            for line in reversed(clean_lines):
                stripped = line.strip()
                stripped = re.sub(r'[\x00-\x1f\x7f]', '', stripped)
                if re.search(pattern, stripped, re.IGNORECASE):
                    return "WAITING", stripped[:60], -1, "", 0
            return "WAITING", last_line[:60], -1, "", 0

    # 构建工具完成特征 (gradle/mvn): 匹配到即刻判定 IDLE
    for pattern, msg in BUILD_DONE_PATTERNS:
        if re.search(pattern, context, re.IGNORECASE):
            return "IDLE", msg, -1, "", 0

    # AI 工具特征匹配: 检查是否出现空闲特征 (零延迟)
    # 策略: 比较 IDLE_PATTERNS 和 BUSY_PATTERNS 在尾部的最后出现位置
    # 如果 IDLE 特征出现在 BUSY 特征之后, 则判定为空闲
    last_idle_pos = -1
    for pattern in IDLE_PATTERNS:
        for m in re.finditer(pattern, context, re.IGNORECASE):
            last_idle_pos = max(last_idle_pos, m.end())

    if last_idle_pos > 0:
        last_busy_pos = -1
        for pattern in BUSY_PATTERNS:
            for m in re.finditer(pattern, context, re.IGNORECASE):
                last_busy_pos = max(last_busy_pos, m.end())

        if last_idle_pos > last_busy_pos:
            return "IDLE", "等待输入", -1, "", 0

    # ── 输出速率检测 (最高优先级) ──
    # 不依赖文本特征, 通过文件大小变化速率检测:
    #   高速(>200B/s) → 静止(5秒) = "从回复中停下来了"
    was_fast, is_stalled, stall_secs = track_file_rate(filepath)
    if was_fast and is_stalled and stall_secs >= RATE_IDLE_SECONDS:
        return "IDLE", "AI 已完成回复", -1, "", 0

    # ── 后忙碌快速检测 (备用) ──
    # 场景: BUSY 特征消失但提示符未出现, 且速率检测未触发 (如回复太短)
    try:
        mtime = os.path.getmtime(filepath)
        idle_seconds = time.time() - mtime

        busy_in_tail = any(
            re.search(p, context, re.IGNORECASE) for p in BUSY_PATTERNS
        )

        if not busy_in_tail and idle_seconds > POST_BUSY_IDLE_SECONDS:
            broad_lines = lines[-30:] if len(lines) >= 30 else lines
            broad_context = "".join([strip_ansi(l) for l in broad_lines])
            if any(re.search(p, broad_context, re.IGNORECASE) for p in BUSY_PATTERNS):
                return "IDLE", "AI 已完成回复", -1, "", 0

        # 通用兜底: 60 秒无输出
        if idle_seconds > IDLE_THRESHOLD_SECONDS:
            return "IDLE", "等待输入", -1, "", 0
    except Exception:
        pass

    # 默认: 运行中
    return "RUNNING", last_line[:60], -1, "", 0


def clear_screen():
    """跨平台清屏 (ANSI 转义序列)"""
    sys.stdout.write("\033[H\033[J")
    sys.stdout.flush()


def format_status(status, exit_code=-1):
    """格式化状态显示 (带颜色和 emoji)"""
    if status == "WAITING":
        return f"{BLINK}{YELLOW}🟡 待确认{RESET}"
    elif status == "IDLE":
        return f"{CYAN}🔵 等待输入{RESET}"
    elif status == "RUNNING":
        return f"{GREEN}🟢 运行中{RESET}"
    else:  # DONE
        if exit_code == 0:
            return f"{GRAY}⚪ 已完成{RESET}"
        elif exit_code > 0:
            return f"{GRAY}🔴 异常退出({exit_code}){RESET}"
        else:
            return f"{GRAY}⚪ 已结束{RESET}"


def render_dashboard(log_dir, max_tasks, enable_sound):
    """渲染监控看板"""
    # 获取所有日志并按修改时间倒序排列
    log_files = glob.glob(os.path.join(log_dir, "*.log"))
    log_files.sort(key=os.path.getmtime, reverse=True)

    # 仅展示最近 N 个任务
    active_logs = log_files[:max_tasks]

    clear_screen()

    # 标题栏
    print(f"{BOLD}{CYAN}╔══════════════════════════════════════════════════════════════╗{RESET}")
    print(f"{BOLD}{CYAN}║{RESET}  🛡️  CLI 任务监控看板    {DIM}{time.strftime('%Y-%m-%d %H:%M:%S')}{RESET}  {BOLD}{CYAN}║{RESET}")
    print(f"{BOLD}{CYAN}╠══════════════════════════════════════════════════════════════╣{RESET}")
    print(f"{BOLD}{CYAN}║{RESET} {'工具':<10} {'状态':<14} {'耗时':<10} {'最新输出':<26} {BOLD}{CYAN}║{RESET}")
    print(f"{BOLD}{CYAN}╠══════════════════════════════════════════════════════════════╣{RESET}")

    if not active_logs:
        print(f"{BOLD}{CYAN}║{RESET}  {DIM}暂无活跃任务... 在其他终端运行被监控的命令即可{RESET}          {BOLD}{CYAN}║{RESET}")
    else:
        has_waiting = False
        for log_file in active_logs:
            tool_name, _ = parse_start_info(log_file)
            # ADAPTED FOR 5 RETURNS
            status, msg, exit_code, duration, _ = analyze_log(log_file)

            if status == "WAITING":
                has_waiting = True

            status_str = format_status(status, exit_code)
            duration_str = duration if duration else "—"

            # 截断过长的消息
            if len(msg) > 24:
                msg = msg[:21] + "..."

            # 清洁化消息 (移除 ANSI 转义等)
            msg = re.sub(r'\033\[[0-9;]*m', '', msg)
            msg = re.sub(r'[\x00-\x1f\x7f]', '', msg)

            print(f"{BOLD}{CYAN}║{RESET} {tool_name:<10} {status_str:<25} {duration_str:<10} {msg:<26}{BOLD}{CYAN}║{RESET}")

        # 声音提醒
        if has_waiting and enable_sound:
            sys.stdout.write('\a')
            sys.stdout.flush()

    print(f"{BOLD}{CYAN}╚══════════════════════════════════════════════════════════════╝{RESET}")
    print(f"\n  {DIM}按 Ctrl+C 退出监控  |  日志目录: {log_dir}{RESET}")


def main():
    parser = argparse.ArgumentParser(
        description="🛡️ CLI 任务状态监控看板 (Logcat Mode)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python3 monitor.py                    # 默认启动
  python3 monitor.py --sound            # 启用声音提醒
  python3 monitor.py --max-tasks 10     # 显示最近 10 个任务
  python3 monitor.py --log-dir /tmp/my  # 自定义日志目录
        """
    )
    parser.add_argument(
        "--sound", action="store_true", default=False,
        help="在检测到「待确认」状态时播放系统提示音"
    )
    parser.add_argument(
        "--max-tasks", type=int, default=DEFAULT_MAX_TASKS,
        help=f"最大显示任务数 (默认: {DEFAULT_MAX_TASKS})"
    )
    parser.add_argument(
        "--log-dir", type=str, default=DEFAULT_LOG_DIR,
        help=f"日志目录路径 (默认: {DEFAULT_LOG_DIR})"
    )
    parser.add_argument(
        "--refresh", type=float, default=DEFAULT_REFRESH_RATE,
        help=f"刷新频率，单位秒 (默认: {DEFAULT_REFRESH_RATE})"
    )

    args = parser.parse_args()

    log_dir = args.log_dir
    if not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)
        print(f"已创建日志目录: {log_dir}")
    
    print(f"🛡️  监控已启动 | 日志目录: {log_dir} | 刷新: {args.refresh}s")
    print(f"   按 Ctrl+C 退出\n")
    time.sleep(1)

    try:
        while True:
            render_dashboard(log_dir, args.max_tasks, args.sound)
            time.sleep(args.refresh)
    except KeyboardInterrupt:
        clear_screen()
        print(f"\n{GREEN}✅ 监控已退出。{RESET}")
        sys.exit(0)


if __name__ == "__main__":
    main()
