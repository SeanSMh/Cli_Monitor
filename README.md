# 🛡️ CLI Monitor — 终端任务状态监控系统 (Logcat 模式)

一个**零侵入**、**零依赖**的 CLI 任务状态监控工具。

在多终端并发执行长耗时任务（AI 代码生成、编译打包等）时，帮你实时感知每个任务的状态——特别是"等待确认"和"任务结束"。

## ✨ 特性

- **0 源码修改** — 不需要修改任何 CLI 工具（Claude, Codex, Gradle 等）
- **0 习惯改变** — 继续用 `claude ...`、`gradle ...` 等原有命令
- **0 依赖安装** — 纯 Python 标准库 + Shell 原生 `script` 命令
- **跨平台** — 自动识别 macOS / Linux，选用对应 `script` 参数
- **实时看板** — 🟢 运行中 / 🟡 待确认(闪烁) / ⚪ 已结束
- **macOS 状态栏** — 状态栏小工具，待确认时推送系统通知

## 📦 快速安装

```bash
# 1. 运行安装脚本
bash install.sh

# 2. 使配置生效
source ~/.zshrc
```

## 🚀 使用方法

### 启动监控面板

在一个独立的终端窗口中运行：

```bash
python3 monitor.py
```

### 正常使用 CLI 工具

在另一个终端窗口中照常使用命令：

```bash
claude "帮我生成一个 Android Activity"
gradle assembleDebug
```

监控面板会自动显示任务状态变化。

### 监控面板参数

```bash
python3 monitor.py --help

# 可用选项:
#   --sound           遇到「待确认」状态时播放提示音
#   --max-tasks N     最多显示 N 个任务 (默认: 5)
#   --log-dir DIR     自定义日志目录 (默认: /tmp/ai_monitor_logs)
#   --refresh SECS    刷新频率 (默认: 1.0 秒)
```

## 🛠️ 动态管理监控工具

```bash
# 添加新工具监控
ai_monitor_add npm

# 移除工具监控
ai_monitor_remove npm

# 查看当前监控列表
ai_monitor_list
```

## 🖥️ macOS 状态栏应用

除了终端看板，还提供 macOS 状态栏小工具：

```bash
# 安装依赖
pip3 install pyinstaller pywebview watchdog pyobjc-framework-Cocoa

# 直接运行 (开发模式)
python3 panel_app.py

# 或打包为独立 .app
./scripts/build_macos.sh
# 生成的应用在 dist/CLI Monitor.app
```

状态栏显示 🛡️ 图标，有待确认任务时自动变为 ⚠️ 并推送系统通知。

## ✅ 质量检查

```bash
# 运行状态机回归测试 (标准库 unittest)
python3 -m unittest -q tests/test_monitor_analyze_log.py

# macOS 打包并校验产物
./scripts/build_macos.sh
```

## 🗑️ 卸载

```bash
bash uninstall.sh
source ~/.zshrc
```

## 📁 项目结构

```
cli-monitor/
├── shell/
│   └── cli_monitor.sh   # 注入层: Shell 包装函数
├── monitor.py            # 监控层: Python 终端看板
├── panel_app.py          # macOS 面板 + 状态栏应用
├── terminal_adapters.py  # 终端跳转适配层 (iTerm2/Terminal)
├── CLI Monitor.spec      # PyInstaller 构建配置 (onedir)
├── scripts/
│   └── build_macos.sh    # macOS 打包脚本
├── tests/
│   └── test_monitor_analyze_log.py  # analyze_log 回归测试
├── install.sh            # 安装脚本
├── uninstall.sh          # 卸载脚本
└── README.md             # 本文件
```

## ⚙️ 工作原理

```
用户终端  ──Alias拦截──▶  Shell Wrapper  ──前台──▶  正常终端交互
                              │
                        后台流写入
                              │
                              ▼
                    临时日志文件 (/tmp/...)
                              │
                         实时读取
                              │
                              ▼
                     Python 监控看板  ──▶  状态判定
                                         🟢 运行中
                                         🟡 待确认
                                         ⚪ 已结束
```

## 📝 兼容性

| 平台 | `script` 版本 | 状态 |
|------|-------------|------|
| macOS | BSD script | ✅ 已支持 |
| Linux | GNU script | ✅ 已支持 |
