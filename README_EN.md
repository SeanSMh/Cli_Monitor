# 🛡️ CLI Monitor

A **zero-intrusion**, **zero-dependency** CLI task status monitoring tool.

When executing long-running tasks across multiple terminal windows concurrently (such as AI code generation, compilation, packaging, etc.), CLI Monitor helps you perceive the real-time status of each task—especially the "Waiting for Confirmation" and "Task Completed" states.

## 📥 Download

**Latest macOS Application:** [Download monitor.dmg.zip](https://bril.top/monitor.dmg.zip)

## ✨ Core Features & Characteristics

- **Zero Source Modifications:** No need to modify any underlying CLI tools (Claude, Codex, Gradle, etc.).
- **Zero Habit Changes:** Continue using your original commands exactly as before (e.g., `claude ...`, `gradle ...`).
- **Zero Dependency Installation:** Built with pure Python standard libraries and the native shell `script` command.
- **Cross-Platform Compatibility:** Automatically detects macOS / Linux and applies the corresponding script parameters.
- **Real-time Visual Dashboard:** Instantly view states: 🟢 Running / 🟡 Waiting for Confirmation (blinking) / ⚪ Completed.
- **macOS Menu Bar Widget:** A convenient status bar app that pushes system notifications when a task requires your attention.
- **Terminal Jump Integration:** Built-in support to jump directly back to the active terminal (supports iTerm2, macOS Terminal, Warp, WezTerm, and VS Code).
