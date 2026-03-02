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
import json
import argparse
from itertools import islice
from collections import defaultdict
from threading import Timer, Lock
from config_loader import config
from app_server_event_parser import parse_app_server_status
from codex_event_parser import parse_codex_structured_status
from parsers.codex_official_schema import parse_codex_official_status

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
STATE_DETECT_TAIL_LINES = 30
DISPLAY_TAIL_LINES = 5

RATE_HISTORY_SIZE = 30
SIGNAL_FILE = os.path.join(DEFAULT_LOG_DIR, "_claude_idle_signal")
CLAUDE_NOTIFY_SIGNAL_FILE = os.path.join(DEFAULT_LOG_DIR, "_claude_notify_signal")
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

SYSTEM_LINE_PREFIXES = (
    "--- MONITOR_START:",
    "--- MONITOR_META ",
    "--- MONITOR_END:",
    "Script started on ",
    "Script done on ",
)

DISPLAY_NOISE_PATTERNS_COMMON = (
    r"^\s*$",
    r"^(?:shell integration loaded|loading shell integration)\b",
    r"^(?:jediterm|jetbrains).{0,120}$",
    r"^(?:\]\d{1,3};\?)+$",
)

AI_AUXILIARY_BUILD_NOISE_PATTERNS = (
    # 外部构建工具（尤其是 JetBrains 终端内的 gradle）错误/摘要噪音，避免污染 AI 卡片。
    r"^\s*> Task\b.*$",
    r"^\s*FAILURE:\s+Build failed with an exception\.?\s*$",
    r"^\s*BUILD FAILED\b.*$",
    r"^\s*BUILD SUCCESSFUL\b.*$",
    r"^\s*Execution failed for task\b.*$",
    r"^\s*\* What went wrong:\s*$",
    r"^\s*\* Try:\s*$",
    r"^\s*\* Exception is:\s*$",
    r"^\s*Deprecated Gradle features were used\b.*$",
    r"^\s*Run with --(?:stacktrace|info|debug|scan)\b.*$",
    r"^\s*\d+\s+actionable tasks?:\b.*$",
    r"^\s*\S+\.(?:java|kt|kts|xml|gradle|groovy|cpp|c|cc|h|hpp):\d+(?::\d+)?:\s+(?:error|warning):\s+.+$",
)

DISPLAY_NOISE_PATTERNS_BY_TOOL = {
    "codex": (
        # Codex CLI 启动 banner / box drawing
        r"^[\s╭╮╰╯│─┌┐└┘┬┴├┤┼═║╔╗╚╝]+$",
        r"^[│\s]*(?:OpenAI\s+Codex|Codex)\b.*$",
        r"^[│\s]*(?:Model|Directory|Approval|Sandbox|Profile|Workspace|Version|Session|Agent|Config|Provider)\s*:\s*.*$",
        r"^[│\s]*You are in\s+/.+$",
        r"^[│\s]*Press .* to .*",
        # Token usage line noise (keep card subtitle concise)
        r"^\s*[\d,]{3,}\s*tokens?\b.*$",
        r"^\s*(?:total\s+)?tokens?\s*[:=]\s*[\d,]{3,}\b.*$",
    ) + AI_AUXILIARY_BUILD_NOISE_PATTERNS,
    "claude": (
        r"^\s*[\d,]{3,}\s*tokens?\b.*$",
        r"^\s*(?:total\s+)?tokens?\s*[:=]\s*[\d,]{3,}\b.*$",
        r"^\s*\??\s*for\s*shortcuts.*[\d,]{3,}\s*tokens?\b.*$",
    ) + AI_AUXILIARY_BUILD_NOISE_PATTERNS,
    "gemini": (
        r"^\s*[\d,]{3,}\s*tokens?\b.*$",
        r"^\s*(?:total\s+)?tokens?\s*[:=]\s*[\d,]{3,}\b.*$",
    ) + AI_AUXILIARY_BUILD_NOISE_PATTERNS,
}

WAITING_MENU_LINE_RE = re.compile(r"^\s*(?:[❯›>•*\-]\s*)?\d+[.)]\s+\S+")
WAITING_QUESTION_HINT_RE = re.compile(
    r"(?:do you want to|would you like to|confirm\b|choose\b|select\b|save file to continue|press enter|apply changes\?)",
    re.IGNORECASE,
)
WAITING_CONFIRM_LINE_RE = re.compile(
    r"^\s*(?:[❯›>•*\-]\s*)?(?:proceed|continue)\s*(?:\?|\:|\((?:y/n|yes/no)\)|\[(?:y/n|yes/no)\])\s*$",
    re.IGNORECASE,
)

_codex_parse_stats_lock = Lock()
_codex_parse_stats = {
    "official_hit_count": 0,
    "compat_hit_count": 0,
    "text_hit_count": 0,
    "unknown_event_count": 0,
    "total_codex_samples": 0,
    "last_source": "",
}

_claude_parse_stats_lock = Lock()
_claude_parse_stats = {
    "notify_waiting_hit_count": 0,
    "notify_idle_hit_count": 0,
    "stop_idle_hit_count": 0,
    "text_fallback_hit_count": 0,
    "total_claude_samples": 0,
    "last_source": "",
}


def _record_codex_parse_hit(source: str):
    source_key = str(source or "").strip().lower()
    if source_key not in {"official", "compat", "text"}:
        return
    with _codex_parse_stats_lock:
        if source_key == "official":
            _codex_parse_stats["official_hit_count"] += 1
        elif source_key == "compat":
            _codex_parse_stats["compat_hit_count"] += 1
        else:
            _codex_parse_stats["text_hit_count"] += 1
        _codex_parse_stats["total_codex_samples"] += 1
        _codex_parse_stats["last_source"] = source_key


def _record_codex_unknown_events(count: int):
    try:
        delta = int(count)
    except Exception:
        delta = 0
    if delta <= 0:
        return
    with _codex_parse_stats_lock:
        _codex_parse_stats["unknown_event_count"] += delta


def get_codex_parse_stats():
    with _codex_parse_stats_lock:
        data = dict(_codex_parse_stats)
    total = max(1, int(data.get("total_codex_samples", 0) or 0))
    fallback_hits = int(data.get("compat_hit_count", 0) or 0) + int(
        data.get("text_hit_count", 0) or 0
    )
    data["fallback_rate"] = fallback_hits / float(total)
    return data


def _record_claude_parse_hit(source: str):
    source_key = str(source or "").strip().lower()
    if source_key not in {"notify_waiting", "notify_idle", "stop_idle", "text_fallback"}:
        return
    with _claude_parse_stats_lock:
        if source_key == "notify_waiting":
            _claude_parse_stats["notify_waiting_hit_count"] += 1
        elif source_key == "notify_idle":
            _claude_parse_stats["notify_idle_hit_count"] += 1
        elif source_key == "stop_idle":
            _claude_parse_stats["stop_idle_hit_count"] += 1
        else:
            _claude_parse_stats["text_fallback_hit_count"] += 1
        _claude_parse_stats["total_claude_samples"] += 1
        _claude_parse_stats["last_source"] = source_key


def get_claude_parse_stats():
    with _claude_parse_stats_lock:
        data = dict(_claude_parse_stats)
    total = max(1, int(data.get("total_claude_samples", 0) or 0))
    signal_hits = int(data.get("notify_waiting_hit_count", 0) or 0) + int(
        data.get("notify_idle_hit_count", 0) or 0
    ) + int(data.get("stop_idle_hit_count", 0) or 0)
    data["signal_rate"] = signal_hits / float(total)
    return data


def _get_codex_runtime_flags():
    codex_conf = config.get("codex", {}) or {}
    official_enabled = bool(codex_conf.get("official_schema_enabled", True))
    fallback_enabled = bool(codex_conf.get("fallback_enabled", True))
    strict_mode = bool(codex_conf.get("strict_mode", False))
    if strict_mode:
        fallback_enabled = False
    return official_enabled, fallback_enabled, strict_mode


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


def parse_session_meta(filepath, max_lines=24):
    """
    解析日志头部 MONITOR_META 行，返回会话元数据。
    返回值示例:
      {
        "term_program": "iTerm.app",
        "tty": "/dev/ttys003",
        "cwd": "/Users/me/project",
        "shell_pid": "12345",
        ...
      }
    """
    meta = {
        "term_program": "",
        "term_program_version": "",
        "tty": "",
        "cwd": "",
        "shell_pid": "",
        "shell_ppid": "",
        "wezterm_pane_id": "",
        "warp_session_id": "",
        "vscode_pid": "",
        "vscode_cwd": "",
        "vscode_ipc_hook_cli": "",
        "vscode_git_askpass_main": "",
        "vscode_git_askpass_node": "",
        "vscode_git_ipc_handle": "",
        "vscode_injection": "",
        "cursor_trace_id": "",
        "terminal_emulator": "",
        "idea_initial_directory": "",
        "jetbrains_ide_name": "",
        "jetbrains_ide_product": "",
        "android_studio_version": "",
    }
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            for line in islice(f, max_lines):
                if "MONITOR_META" not in line:
                    continue
                match = re.search(r"MONITOR_META\s+([a-zA-Z0-9_]+):\s*(.*?)\s*---\s*$", line.strip())
                if not match:
                    continue
                key = match.group(1)
                value = match.group(2).strip()
                if key in meta:
                    meta[key] = value
    except Exception:
        pass

    tty = meta.get("tty", "").strip()
    if tty and tty != "not a tty" and tty != "?":
        if not tty.startswith("/dev/"):
            tty = f"/dev/{tty}"
        meta["tty"] = tty
    else:
        meta["tty"] = ""

    if not meta.get("shell_pid"):
        try:
            basename = os.path.basename(filepath)
            parts = basename.rsplit("_", 3)
            if len(parts) >= 3:
                meta["shell_pid"] = str(int(parts[2]))
        except Exception:
            pass

    return meta


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


def _claude_session_id_for_log(filepath):
    try:
        stem = os.path.splitext(os.path.basename(str(filepath or "")))[0].strip()
        return stem
    except Exception:
        return ""


def _claude_notify_signal_candidates_for_log(filepath):
    candidates = []
    try:
        stem = os.path.splitext(os.path.basename(str(filepath or "")))[0].strip()
        if stem:
            candidates.append(os.path.join(DEFAULT_LOG_DIR, f"_claude_notify_signal_{stem}"))
    except Exception:
        pass
    candidates.append(CLAUDE_NOTIFY_SIGNAL_FILE)  # backward compatibility for older hook payloads
    return candidates


def _normalize_path_for_compare(path):
    s = str(path or "").strip()
    if not s:
        return ""
    try:
        return os.path.realpath(os.path.abspath(s))
    except Exception:
        return s


def _paths_match(a, b):
    pa = _normalize_path_for_compare(a)
    pb = _normalize_path_for_compare(b)
    return bool(pa and pb and pa == pb)


def _parse_claude_event_ts(payload, file_mtime):
    ts_val = payload.get("ts_ms", 0)
    try:
        ts_ms = float(ts_val or 0)
    except Exception:
        ts_ms = 0.0
    if ts_ms > 0:
        return ts_ms / 1000.0

    ts_val = payload.get("ts", 0)
    try:
        ts = float(ts_val or 0)
    except Exception:
        ts = 0.0
    if ts > 0:
        return ts
    return float(file_mtime or 0)


def _payload_matches_claude_log(payload, expected_session_id, expected_log_file, signal_file):
    sid = str(payload.get("session_id", "") or "").strip()
    payload_log = str(payload.get("log_file", "") or "").strip()

    session_match = bool(expected_session_id and sid and sid == expected_session_id)
    log_match = bool(payload_log and expected_log_file and _paths_match(payload_log, expected_log_file))

    if sid and expected_session_id and sid != expected_session_id:
        return False
    if payload_log and expected_log_file and not log_match:
        return False
    if session_match or log_match:
        return True

    signal_basename = os.path.basename(str(signal_file or ""))
    global_basename = os.path.basename(str(CLAUDE_NOTIFY_SIGNAL_FILE or ""))
    is_global_fallback = bool(signal_basename and global_basename and signal_basename == global_basename)
    if is_global_fallback:
        # 全局 fallback 文件没有会话字段时不可信，避免多会话串扰。
        return False
    return True


def _normalize_claude_signal_message(message, fallback):
    text = strip_ansi_text(str(message or "")).strip()
    text = re.sub(r"[\x00-\x1f\x7f]", "", text)
    if not text:
        return fallback
    return text[:60]


def _read_latest_claude_idle_signal_ts(filepath, log_mtime):
    expected_session = _claude_session_id_for_log(filepath)
    if expected_session:
        per_session_file = os.path.join(DEFAULT_LOG_DIR, f"_claude_idle_signal_{expected_session}")
        if os.path.exists(per_session_file):
            try:
                signal_mtime = float(os.path.getmtime(per_session_file))
                age = time.time() - signal_mtime
                if age < SIGNAL_MAX_AGE and signal_mtime >= float(log_mtime) - 5:
                    return signal_mtime
            except Exception:
                pass

    # 旧版本全局 fallback: 缩短窗口，降低跨会话误判风险。
    if os.path.exists(SIGNAL_FILE):
        try:
            signal_mtime = float(os.path.getmtime(SIGNAL_FILE))
            age = time.time() - signal_mtime
            if age < min(SIGNAL_MAX_AGE, 120) and signal_mtime >= float(log_mtime) - 2:
                return signal_mtime
        except Exception:
            pass
    return 0.0


def _read_latest_claude_notify_event(filepath, log_mtime):
    best_state = ""
    best_message = ""
    best_ts = 0.0
    expected_session = _claude_session_id_for_log(filepath)
    expected_log_file = str(filepath or "")
    for signal_file in _claude_notify_signal_candidates_for_log(filepath):
        if not os.path.exists(signal_file):
            continue
        file_mtime = 0
        try:
            file_mtime = os.path.getmtime(signal_file)
        except Exception:
            file_mtime = 0
        if file_mtime <= 0:
            continue
        if time.time() - file_mtime > SIGNAL_MAX_AGE:
            continue
        if file_mtime < log_mtime - 5:
            continue
        lines = tail_read(signal_file, num_bytes=2048)
        for raw in reversed(lines):
            line = str(raw or "").strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            state = str(payload.get("state", "") or "").strip().upper()
            if state not in {"WAITING", "IDLE"}:
                continue
            if not _payload_matches_claude_log(
                payload, expected_session, expected_log_file, signal_file
            ):
                continue
            event_ts = float(_parse_claude_event_ts(payload, file_mtime))
            age = time.time() - event_ts
            if age < 0 or age > SIGNAL_MAX_AGE:
                continue
            if event_ts < log_mtime - 5:
                continue
            message = str(payload.get("message", "") or "").strip()
            if event_ts >= best_ts:
                best_ts = event_ts
                best_state = state
                best_message = message
            break
    return best_state, best_message, best_ts


def _analyze_claude_signal_status(filepath):
    """Claude: Notification 事件优先，其次 Stop-hook idle 信号。"""
    log_mtime = 0
    try:
        log_mtime = os.path.getmtime(filepath)
    except Exception:
        log_mtime = 0

    notify_state, notify_message, notify_ts = _read_latest_claude_notify_event(
        filepath, log_mtime
    )
    idle_signal_ts = _read_latest_claude_idle_signal_ts(filepath, log_mtime)

    # 事件优先: 若 Notification 事件更新, 以其状态为准；否则保留 Stop-hook 的完成信号。
    if notify_state and notify_ts >= (idle_signal_ts - 1e-3):
        if notify_state == "WAITING":
            _record_claude_parse_hit("notify_waiting")
            return (
                "WAITING",
                _normalize_claude_signal_message(notify_message, "等待确认输入"),
                0,
            )
        if notify_state == "IDLE":
            # 兼容窗口: 历史版本可能通过 Notification 直接落 IDLE。
            _record_claude_parse_hit("notify_idle")
            return (
                "IDLE",
                _normalize_claude_signal_message(notify_message, "AI 已完成回复"),
                notify_ts,
            )

    if idle_signal_ts > 0:
        _record_claude_parse_hit("stop_idle")
        return ("IDLE", "AI 已完成回复", idle_signal_ts)

    return None


def _detect_waiting_prompt_from_lines(lines, line_limit=60):
    normalized = []
    for raw in lines or []:
        s = strip_ansi_text(str(raw or ""))
        s = re.sub(r"[\x00-\x1f\x7f]", "", s).strip()
        if s:
            normalized.append(s)
    if not normalized:
        return ""

    menu_lines = [line for line in normalized if WAITING_MENU_LINE_RE.search(line)]
    if len(menu_lines) >= 2:
        for line in reversed(normalized):
            if WAITING_QUESTION_HINT_RE.search(line) or WAITING_CONFIRM_LINE_RE.search(line):
                return line[:line_limit]
        return menu_lines[0][:line_limit]

    for line in reversed(normalized):
        if WAITING_QUESTION_HINT_RE.search(line) or WAITING_CONFIRM_LINE_RE.search(line):
            return line[:line_limit]
    return ""


_SUMMARY_VALUE_LINE_RE = re.compile(
    r"^[a-z0-9][a-z0-9 /()_.+\-]{0,40}:\s+\S",
    re.IGNORECASE,
)
_SUMMARY_SECTION_LINE_RE = re.compile(
    r"^[a-z0-9][a-z0-9 /()_.+\-]{0,40}:\s*$",
    re.IGNORECASE,
)
_SUMMARY_STRONG_LABEL_PREFIXES = (
    "total cost",
    "total duration",
    "total code changes",
    "usage by model",
)


def _detect_summary_completion(lines, tool_name):
    tool_key = str(tool_name or "").strip().lower()
    if tool_key != "claude":
        return False

    normalized = []
    for raw in lines or []:
        s = strip_ansi_text(str(raw or ""))
        s = re.sub(r"[\x00-\x1f\x7f]", "", s).strip()
        if not s:
            continue
        s = re.sub(r"^[\s⎿│╰╯╭╮└┘]+", "", s).strip()
        if not s or is_system_output_line(s) or is_display_noise_line(s, tool_key):
            continue
        normalized.append(s)
    if not normalized:
        return False

    summary_hits = 0
    strong_hits = 0
    for line in normalized[-10:]:
        if not (
            _SUMMARY_VALUE_LINE_RE.match(line)
            or _SUMMARY_SECTION_LINE_RE.match(line)
        ):
            continue
        summary_hits += 1
        label = line.split(":", 1)[0].strip()
        label_key = label.lower()
        if any(label_key.startswith(prefix) for prefix in _SUMMARY_STRONG_LABEL_PREFIXES):
            strong_hits += 1

    return summary_hits >= 3 and strong_hits >= 1


def strip_ansi_text(s):
    s = re.sub(r'\033\[[0-9;?]*[A-Za-z]', '', s)
    # OSC 序列可用 BEL 或 ST(ESC \\) 结束；JetBrains/JediTerm 常见颜色查询会走这里。
    s = re.sub(r'\033\][^\x07\x1b]*(?:\x07|\033\\)', '', s)
    s = re.sub(r'\033\[[=>][0-9;]*[A-Za-z]', '', s)
    s = re.sub(r'\033[()][A-Z0-9]', '', s)
    # 某些终端会留下裸露的 OSC 查询残片（如 ]10;?]11;?），显示层直接剔除。
    s = re.sub(r'(?:\]\d{1,3};\?)+', '', s)
    return s


def is_system_output_line(line):
    try:
        stripped = strip_ansi_text(line).strip()
    except Exception:
        return False
    if not stripped:
        return False
    return any(stripped.startswith(prefix) for prefix in SYSTEM_LINE_PREFIXES)


def is_display_noise_line(line, tool_name=""):
    try:
        stripped = strip_ansi_text(line).strip()
    except Exception:
        return False
    if not stripped:
        return True
    if is_system_output_line(stripped):
        return True

    for pattern in DISPLAY_NOISE_PATTERNS_COMMON:
        if re.search(pattern, stripped, re.IGNORECASE):
            return True

    tool_key = str(tool_name or "").strip().lower()
    for pattern in DISPLAY_NOISE_PATTERNS_BY_TOOL.get(tool_key, ()):
        if re.search(pattern, stripped, re.IGNORECASE):
            return True
    return False


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


def _return_status(
    tool_name,
    status,
    message,
    exit_code=-1,
    duration="",
    signal_ts=0,
    codex_source="",
):
    if tool_name == "codex" and codex_source:
        _record_codex_parse_hit(codex_source)
    return tool_name, status, message, exit_code, duration, signal_ts


def _normalize_running_status_message(status: str, message: str, last_line: str) -> str:
    if status == "RUNNING" and (not message or message == "运行中..."):
        return last_line
    return message or "运行中..."


def _analyze_codex_structured_status(visible_detect_lines, common_waiting, last_line):
    official_enabled, fallback_enabled, strict_mode = _get_codex_runtime_flags()
    unknown_events = 0

    if official_enabled:
        official_status, unknown = parse_codex_official_status(
            visible_detect_lines, waiting_patterns=common_waiting
        )
        unknown_events += int(unknown or 0)
        if official_status is not None:
            status, status_msg = official_status
            status_msg = _normalize_running_status_message(status, status_msg, last_line)
            return (status, status_msg, "official"), unknown_events

    if fallback_enabled:
        app_server_status = parse_app_server_status(
            visible_detect_lines, waiting_patterns=common_waiting
        )
        if app_server_status is not None:
            status, status_msg = app_server_status
            status_msg = _normalize_running_status_message(status, status_msg, last_line)
            return (status, status_msg, "compat"), unknown_events

        structured_status = parse_codex_structured_status(
            visible_detect_lines, waiting_patterns=common_waiting
        )
        if structured_status is not None:
            status, status_msg = structured_status
            status_msg = _normalize_running_status_message(status, status_msg, last_line)
            return (status, status_msg, "compat"), unknown_events

    if strict_mode:
        return ("RUNNING", last_line[:60], "text"), unknown_events

    return None, unknown_events


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
        return _return_status(tool_name, "DONE", "任务完成", exit_code, duration, 0)

    done_patterns = tool_rules.get("done_patterns", {})
    common_waiting = RULES_CONF.get("common", {}).get("waiting", [])
    is_claude = tool_name == "claude"
    detect_tail_lines = lines[-STATE_DETECT_TAIL_LINES:]
    display_tail_lines = lines[-DISPLAY_TAIL_LINES:]

    clean_detect_lines = [strip_ansi_text(l) for l in detect_tail_lines]
    visible_detect_lines = [l for l in clean_detect_lines if not is_system_output_line(l)]
    if tool_name in {"claude", "codex", "gemini"}:
        semantic_detect_lines = [
            l for l in visible_detect_lines if not is_display_noise_line(l, tool_name)
        ]
    else:
        semantic_detect_lines = visible_detect_lines
    clean_display_lines = [strip_ansi_text(l) for l in display_tail_lines]
    visible_display_lines = [l for l in clean_display_lines if not is_system_output_line(l)]
    display_lines = [l for l in visible_display_lines if not is_display_noise_line(l, tool_name)]
    context = "".join(semantic_detect_lines)
    
    last_line = ""
    for l in reversed(display_lines):
        if l.strip():
            last_line = l.strip()
            last_line = re.sub(r'[\x00-\x1f\x7f]', '', last_line)
            break
    if not last_line:
        last_line = "运行中..."

    for pattern, msg in done_patterns.items():
        if re.search(pattern, context, re.IGNORECASE):
            return _return_status(
                tool_name, "IDLE", msg, -1, "", 0, codex_source="text"
            )

    if tool_name == "codex":
        codex_result, unknown_events = _analyze_codex_structured_status(
            semantic_detect_lines, common_waiting, last_line
        )
        _record_codex_unknown_events(unknown_events)
        if codex_result is not None:
            status, status_msg, source = codex_result
            return _return_status(
                tool_name,
                status,
                (status_msg or "运行中...")[:60],
                -1,
                "",
                0,
                codex_source=source,
            )

    if is_claude:
        signal_result = _analyze_claude_signal_status(filepath)
        if signal_result is not None:
            status, status_msg, signal_ts = signal_result
            return _return_status(tool_name, status, status_msg, -1, "", signal_ts)

    # 编号菜单/确认问句的纯文本快速识别（仅对交互式 AI CLI 启用，避免误判构建日志）。
    if tool_name in {"claude", "codex", "gemini"}:
        fast_waiting_prompt = _detect_waiting_prompt_from_lines(semantic_detect_lines[-20:])
        if fast_waiting_prompt:
            if is_claude:
                _record_claude_parse_hit("text_fallback")
            return _return_status(
                tool_name, "WAITING", fast_waiting_prompt, -1, "", 0, codex_source="text"
            )

    for pattern in common_waiting:
        if re.search(pattern, context, re.IGNORECASE):
            for line in reversed(semantic_detect_lines):
                stripped = line.strip()
                stripped = re.sub(r'[\x00-\x1f\x7f]', '', stripped)
                if re.search(pattern, stripped, re.IGNORECASE):
                    if is_claude:
                        _record_claude_parse_hit("text_fallback")
                    return _return_status(
                        tool_name,
                        "WAITING",
                        stripped[:60],
                        -1,
                        "",
                        0,
                        codex_source="text",
                    )
            if is_claude:
                _record_claude_parse_hit("text_fallback")
            return _return_status(
                tool_name, "WAITING", last_line[:60], -1, "", 0, codex_source="text"
            )

    idle_patterns = tool_rules.get("idle_patterns", []) + RULES_CONF.get("common", {}).get("idle", [])
    if is_claude:
        # "Cost:" 仅作辅助信号，不应单独触发 IDLE。
        idle_patterns = [p for p in idle_patterns if "cost" not in str(p or "").lower()]
    busy_patterns = tool_rules.get("busy_patterns", []) + RULES_CONF.get("common", {}).get("busy", [])
    tool_idle_threshold = tool_rules.get("idle_threshold", IDLE_THRESHOLD_SECONDS)
    try:
        tool_idle_threshold = float(tool_idle_threshold)
    except Exception:
        tool_idle_threshold = float(IDLE_THRESHOLD_SECONDS)

    busy_context = context
    if is_claude:
        busy_context = "".join(semantic_detect_lines[-5:])
    busy_in_tail = any(re.search(p, busy_context, re.IGNORECASE) for p in busy_patterns)

    if is_claude and not busy_in_tail and _detect_summary_completion(
        visible_detect_lines, tool_name
    ):
        _record_claude_parse_hit("text_fallback")
        return _return_status(
            tool_name, "IDLE", "等待输入", -1, "", 0, codex_source="text"
        )

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
            if is_claude:
                _record_claude_parse_hit("text_fallback")
            return _return_status(
                tool_name, "IDLE", "等待输入", -1, "", 0, codex_source="text"
            )

    was_fast, is_stalled, stall_secs = track_file_rate(filepath)
    if was_fast and is_stalled and stall_secs >= RATE_IDLE_SECONDS:
        if not is_claude:
            return _return_status(
                tool_name, "IDLE", "AI 已完成回复", -1, "", 0, codex_source="text"
            )

    try:
        mtime = os.path.getmtime(filepath)
        idle_seconds = time.time() - mtime

        if not busy_in_tail and idle_seconds > 10:
             broad_lines = lines[-30:] if len(lines) >= 30 else lines
             broad_context = "".join(
                 [
                     strip_ansi_text(l)
                     for l in broad_lines
                     if not is_system_output_line(l)
                     and not (
                         tool_name in {"claude", "codex", "gemini"}
                         and is_display_noise_line(l, tool_name)
                     )
                 ]
             )
             if any(re.search(p, broad_context, re.IGNORECASE) for p in busy_patterns):
                 if not is_claude:
                     return _return_status(
                         tool_name,
                         "IDLE",
                         "AI 已完成回复",
                         -1,
                         "",
                         0,
                         codex_source="text",
                     )

        if is_claude:
            # Claude 无 hook 信号时走更保守的纯文本 idle 兜底，减少 RUNNING/IDLE 抖动。
            if not busy_in_tail and idle_seconds > max(tool_idle_threshold, 30.0):
                _record_claude_parse_hit("text_fallback")
                return _return_status(
                    tool_name, "IDLE", "等待输入", -1, "", 0, codex_source="text"
                )
        elif idle_seconds > tool_idle_threshold:
            return _return_status(
                tool_name, "IDLE", "等待输入", -1, "", 0, codex_source="text"
            )
    except Exception:
        pass

    return _return_status(
        tool_name, "RUNNING", last_line[:60], -1, "", 0, codex_source="text"
    )


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
        elif exit_code == 137: return f"{GRAY}⚪ 已关闭{RESET}"
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
