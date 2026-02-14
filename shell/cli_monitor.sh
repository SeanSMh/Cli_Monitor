#!/usr/bin/env bash
# ==========================================
# CLI Monitor: Logcat Mode Configuration
# 终端任务状态监控系统 - 注入层 (Shell Wrapper)
# ==========================================
# 用法: 在 ~/.zshrc 或 ~/.bashrc 中添加:
#   source /path/to/cli-monitor/shell/cli_monitor.sh

# -------------------------------------------
# 1. 日志目录配置
# -------------------------------------------
export AI_MONITOR_DIR="/tmp/ai_monitor_logs"
mkdir -p "$AI_MONITOR_DIR"

# 每次启动 Shell 时清理超过 1 天的旧日志
find "$AI_MONITOR_DIR" -name "*.log" -mtime +1 -delete 2>/dev/null

# -------------------------------------------
# 2. 平台检测
# -------------------------------------------
_CLI_MONITOR_PLATFORM=""
if [[ "$(uname)" == "Darwin" ]]; then
    _CLI_MONITOR_PLATFORM="macos"
else
    _CLI_MONITOR_PLATFORM="linux"
fi

# -------------------------------------------
# 3. 核心包装函数
# -------------------------------------------
function _ai_meta_sanitize() {
    local raw="${1:-}"
    raw="${raw//$'\n'/ }"
    raw="${raw//$'\r'/ }"
    raw="${raw//---/}"
    echo "$raw"
}

function ai_wrapper() {
    local tool_name=$1
    shift # 移除工具名，保留后续参数

    # 生成唯一会话 ID: 工具名_时间戳_PID_随机后缀
    local session_id="${tool_name}_$(date +%s)_$$_${RANDOM}"
    local log_file="$AI_MONITOR_DIR/${session_id}.log"

    # 写入起始标记 (供监控层解析)
    echo "--- MONITOR_START: $tool_name | $(date '+%Y-%m-%d %H:%M:%S') ---" > "$log_file"
    local _term_program="$(_ai_meta_sanitize "${TERM_PROGRAM:-}")"
    local _term_program_version="$(_ai_meta_sanitize "${TERM_PROGRAM_VERSION:-}")"
    local _tty="$(_ai_meta_sanitize "$(tty 2>/dev/null || true)")"
    local _cwd="$(_ai_meta_sanitize "${PWD:-}")"
    local _shell_pid="$(_ai_meta_sanitize "$$")"
    local _shell_ppid="$(_ai_meta_sanitize "$PPID")"
    local _wezterm_pane="$(_ai_meta_sanitize "${WEZTERM_PANE:-}")"
    local _warp_session="$(_ai_meta_sanitize "${WARP_SESSION_ID:-}")"
    local _vscode_pid="$(_ai_meta_sanitize "${VSCODE_PID:-}")"
    local _vscode_cwd="$(_ai_meta_sanitize "${VSCODE_CWD:-}")"
    echo "--- MONITOR_META term_program: ${_term_program} ---" >> "$log_file"
    echo "--- MONITOR_META term_program_version: ${_term_program_version} ---" >> "$log_file"
    echo "--- MONITOR_META tty: ${_tty} ---" >> "$log_file"
    echo "--- MONITOR_META cwd: ${_cwd} ---" >> "$log_file"
    echo "--- MONITOR_META shell_pid: ${_shell_pid} ---" >> "$log_file"
    echo "--- MONITOR_META shell_ppid: ${_shell_ppid} ---" >> "$log_file"
    echo "--- MONITOR_META wezterm_pane_id: ${_wezterm_pane} ---" >> "$log_file"
    echo "--- MONITOR_META warp_session_id: ${_warp_session} ---" >> "$log_file"
    echo "--- MONITOR_META vscode_pid: ${_vscode_pid} ---" >> "$log_file"
    echo "--- MONITOR_META vscode_cwd: ${_vscode_cwd} ---" >> "$log_file"

    # 根据平台选择正确的 script 命令参数
    if [[ "$_CLI_MONITOR_PLATFORM" == "macos" ]]; then
        # macOS (BSD script): -a 追加模式, -F 实时刷新, -q 静默
        script -a -F -q "$log_file" "$tool_name" "$@"
    else
        # Linux (GNU script): -a 追加模式, -f 实时刷新, -q 静默
        script -a -f -q -c "$tool_name $*" "$log_file"
    fi

    local exit_code=$?

    # 写入结束标记 (包含退出码)
    echo "--- MONITOR_END: $exit_code | $(date '+%Y-%m-%d %H:%M:%S') ---" >> "$log_file"

    return $exit_code
}

# -------------------------------------------
# 4. 动态管理监控工具
# -------------------------------------------

# 添加监控别名
# 用法: ai_monitor_add <command_name>
function ai_monitor_add() {
    if [[ -z "$1" ]]; then
        echo "用法: ai_monitor_add <command_name>"
        echo "示例: ai_monitor_add npm"
        return 1
    fi
    alias "$1"="ai_wrapper $1"
    echo "✅ 已为 '$1' 添加监控别名"
}

# 移除监控别名
# 用法: ai_monitor_remove <command_name>
function ai_monitor_remove() {
    if [[ -z "$1" ]]; then
        echo "用法: ai_monitor_remove <command_name>"
        echo "示例: ai_monitor_remove npm"
        return 1
    fi
    unalias "$1" 2>/dev/null
    echo "✅ 已移除 '$1' 的监控别名"
}

# 列出当前所有被监控的工具
function ai_monitor_list() {
    echo "🛡️  当前监控工具列表:"
    alias | grep "ai_wrapper" | sed "s/.*alias \(.*\)=.*/  • \1/" 2>/dev/null
}

# -------------------------------------------
# 5. 注册默认别名
# -------------------------------------------
alias claude="ai_wrapper claude"
alias codex="ai_wrapper codex"
alias gemini="ai_wrapper gemini"
alias gradle="ai_wrapper gradle"
alias mvn="ai_wrapper mvn"
