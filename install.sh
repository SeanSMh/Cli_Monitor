#!/usr/bin/env bash
# ==========================================
# CLI Monitor - 安装脚本
# ==========================================
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SHELL_SCRIPT="$SCRIPT_DIR/shell/cli_monitor.sh"
SOURCE_LINE="source \"$SHELL_SCRIPT\""
MARKER_START="# >>> cli-monitor >>>"
MARKER_END="# <<< cli-monitor <<<"

# 颜色定义
GREEN="\033[32m"
YELLOW="\033[33m"
RED="\033[31m"
RESET="\033[0m"
BOLD="\033[1m"

echo ""
echo "${BOLD}🛡️  CLI Monitor - 安装程序${RESET}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# 1. 检测 Shell 类型
RC_FILE=""
if [[ -n "$ZSH_VERSION" ]] || [[ "$SHELL" == *"zsh"* ]]; then
    RC_FILE="$HOME/.zshrc"
elif [[ -n "$BASH_VERSION" ]] || [[ "$SHELL" == *"bash"* ]]; then
    RC_FILE="$HOME/.bashrc"
else
    echo "${RED}❌ 无法识别当前 Shell 类型，请手动添加以下内容到你的 Shell 配置文件:${RESET}"
    echo "   $SOURCE_LINE"
    exit 1
fi

echo "  📂 项目路径: $SCRIPT_DIR"
echo "  🐚 Shell 配置: $RC_FILE"
echo ""

# 2. 检查是否已安装
if grep -q "cli-monitor" "$RC_FILE" 2>/dev/null; then
    echo "${YELLOW}⚠️  检测到已有安装记录，将先移除旧配置...${RESET}"
    # 移除旧的标记块
    sed -i.bak "/$MARKER_START/,/$MARKER_END/d" "$RC_FILE"
    rm -f "${RC_FILE}.bak"
    echo "  ✅ 旧配置已清理"
    echo ""
fi

# 3. 验证文件存在
if [[ ! -f "$SHELL_SCRIPT" ]]; then
    echo "${RED}❌ Shell 脚本不存在: $SHELL_SCRIPT${RESET}"
    exit 1
fi

# 4. 写入 RC 文件
{
    echo ""
    echo "$MARKER_START"
    echo "# CLI Monitor: Logcat Mode (终端任务状态监控)"
    echo "# 项目路径: $SCRIPT_DIR"
    echo "$SOURCE_LINE"
    echo "$MARKER_END"
} >> "$RC_FILE"

# 5. 注入 Claude Code statusCommand
RECEIVER_PATH="$SCRIPT_DIR/claude/receiver.py"
if [[ -f "$RECEIVER_PATH" ]]; then
    python3 "$SCRIPT_DIR/claude/install.py" install "$RECEIVER_PATH" 2>/dev/null || true
fi

echo "${GREEN}✅ 安装成功!${RESET}"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  ${BOLD}后续步骤:${RESET}"
echo ""
echo "  1. 运行以下命令使配置立即生效:"
echo "     ${BOLD}source $RC_FILE${RESET}"
echo ""
echo "  2. 在一个终端启动监控看板:"
echo "     ${BOLD}python3 $SCRIPT_DIR/monitor.py${RESET}"
echo ""
echo "  3. 在另一个终端正常使用命令 (claude, gradle 等),"
echo "     监控面板会自动显示任务状态。"
echo ""
echo "  💡 更多选项: python3 $SCRIPT_DIR/monitor.py --help"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
