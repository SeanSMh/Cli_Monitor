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
function ai_wrapper() {
    local tool_name=$1
    shift # 移除工具名，保留后续参数

    # 生成唯一会话 ID: 工具名_时间戳_PID_随机后缀
    local session_id="${tool_name}_$(date +%s)_$$_${RANDOM}"
    local log_file="$AI_MONITOR_DIR/${session_id}.log"

    # 写入起始标记 (供监控层解析)
    echo "--- MONITOR_START: $tool_name | $(date '+%Y-%m-%d %H:%M:%S') ---" > "$log_file"

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
alias gradle="ai_wrapper gradle"
alias mvn="ai_wrapper mvn"
