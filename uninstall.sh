#!/usr/bin/env bash
# ==========================================
# CLI Monitor - 卸载脚本
# ==========================================
set -e

MARKER_START="# >>> cli-monitor >>>"
MARKER_END="# <<< cli-monitor <<<"

# 颜色定义
GREEN="\033[32m"
YELLOW="\033[33m"
RED="\033[31m"
RESET="\033[0m"
BOLD="\033[1m"

echo ""
echo "${BOLD}🛡️  CLI Monitor - 卸载程序${RESET}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# 1. 检测并清理 Shell 配置
cleaned=false

for rc_file in "$HOME/.zshrc" "$HOME/.bashrc"; do
    if [[ -f "$rc_file" ]] && grep -q "cli-monitor" "$rc_file" 2>/dev/null; then
        echo "  📄 清理: $rc_file"
        sed -i.bak "/$MARKER_START/,/$MARKER_END/d" "$rc_file"
        rm -f "${rc_file}.bak"
        cleaned=true
    fi
done

if [[ "$cleaned" == true ]]; then
    echo ""
    echo "${GREEN}  ✅ Shell 配置已清理${RESET}"
else
    echo "${YELLOW}  ⚠️  未找到已安装的配置${RESET}"
fi

# 2. 清理日志目录
LOG_DIR="/tmp/ai_monitor_logs"
if [[ -d "$LOG_DIR" ]]; then
    echo ""
    read -p "  🗑️  是否清理日志目录 ($LOG_DIR)? [y/N] " -n 1 -r
    echo ""
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        rm -rf "$LOG_DIR"
        echo "${GREEN}  ✅ 日志已清理${RESET}"
    else
        echo "  ⏭️  跳过日志清理"
    fi
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  卸载完成。请运行以下命令使变更生效:"
echo "  ${BOLD}source ~/.zshrc${RESET}  (或重新打开终端)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
