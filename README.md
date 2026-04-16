# CLI Monitor

CLI Monitor 是一款 macOS 菜单栏应用，用于在一处集中监控 AI CLI（命令行）会话。它可以帮助您追踪像 `Codex`、`Claude Code` 和 `Gemini` 这样的工具，并直观显示任务是处于运行中、等待输入、需要确认、已完成还是已关闭状态。

## 📥 下载

**最新 macOS 安装包 (.dmg):** [点击下载 monitor.dmg.zip](https://bril.top/monitor.dmg.zip)

## 核心特性

- 自动监控 AI CLI 会话状态
- 支持 `运行中 (Running)`、`等待输入 (Awaiting Input)`、`需要操作 (Needs Action)`、`已完成 (Completed)` 和 `已关闭 (Closed)` 状态
- 将菜单栏未读计数与系统通知无缝集成
- 任务卡片支持跳转到终端、单卡片刷新和清除记录
- 支持浅色和深色主题切换
- 支持中文和英文 UI 界面
- 可打包为 macOS 的 `.app` 和 `.dmg` 文件

## 使用场景

CLI Monitor 专为并行运行多个 AI 编程工具的开发者设计。
当您使用 `Codex`、`Claude Code` 或其他基于终端的 AI 助手工作时，它可以帮您快速看清：

- 哪个任务正在运行
- 哪个任务正在等待您的输入
- 哪个任务需要确认
- 哪个任务已经完成或意外退出

## 当前支持

- Codex
- Claude Code
- Gemini
- Gradle / Maven 构建结果检测
- 多种终端和 IDE 数据源，如 Terminal、iTerm2、Cursor 和 Android Studio

## 产品特点

1. 轻量级  
作为菜单栏应用在后台运行，绝不打断您的日常工作流。

2. 专注  
使用紧凑的任务卡片，仅展示最重要的状态和摘要信息，不堆砌无用数据。

3. 实用  
专为交互式 AI CLI 工作流打造，而不仅仅是通用的终端日志查看器。

## 支持平台

- macOS
- 提供 `.app` 和 `.dmg` 分发格式

## 版本

当前版本：`0.0.10`