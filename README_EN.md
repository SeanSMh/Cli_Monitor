# 🛡️ CLI Monitor

A **zero-intrusion**, **zero-dependency** CLI task status monitoring system (Logcat mode).

When executing long-running tasks across multiple terminal windows concurrently (such as AI code generation, compilation, packing, etc.), this tool helps you sense the real-time status of each task—especially the "Waiting for Confirmation" and "Task Completed" states.

## 📥 Download

**Latest macOS DMG package:** [Download monitor.dmg.zip](https://bril.top/monitor.dmg.zip)

## ✨ Features

- **0 Source Code Modifications:** No need to modify any CLI tools (Claude, Codex, Gradle, etc.).
- **0 Habit Changes:** Continue using your original commands like `claude ...`, `gradle ...`.
- **0 Dependency Installation:** Pure Python standard library + native shell `script` command.
- **Cross-Platform:** Automatically recognizes macOS/Linux and selects the corresponding `script` parameters.
- **Real-time Dashboard:** 🟢 Running / 🟡 Waiting for confirmation (blinking) / ⚪ Completed.
- **macOS Menu Bar App:** Menu bar widget that pushes system notifications when tasks require confirmation.
- **Terminal Jump Integration:** Supports iTerm2 and native Terminal out of the box, with built-in adapter frameworks for Warp, WezTerm, and VS Code.

## 📦 Quick Installation

```bash
# 1. Run the installation script
bash install.sh

# 2. Apply the configuration
source ~/.zshrc
```

## 🚀 Usage

### Start the Monitoring Dashboard

Run the following in an independent terminal window:

```bash
python3 monitor.py
```

### Use CLI Tools Normally

Use your commands as usual in another terminal window:

```bash
claude "Help me generate an Android Activity"
gradle assembleDebug
```

The monitoring dashboard will automatically display task status changes in real-time.

### Dashboard Parameters

```bash
python3 monitor.py --help

# Available options:
#   --sound           Play an alert sound when encountering a "waiting for confirmation" state
#   --max-tasks N     Maximum number of tasks to display (default: 5)
#   --log-dir DIR     Custom log directory (default: /tmp/ai_monitor_logs)
#   --refresh SECS    Refresh frequency (default: 1.0 second)
```

## 🖥️ macOS Menu Bar App

In addition to the terminal dashboard, a macOS menu bar widget is provided.

```bash
# Install dependencies
pip3 install pyinstaller pywebview watchdog pyobjc-framework-Cocoa

# Run directly (development mode)
python3 panel_app.py

# Or package as a standalone .app
./scripts/build_macos.sh
# The generated application will be at dist/CLI Monitor.app
```

The menu bar displays a 🛡️ icon, which automatically turns into ⚠️ and pushes a system notification when there are tasks waiting for confirmation.

## ⚙️ How It Works

```
User Terminal  ──Alias Intercept──▶  Shell Wrapper  ──Foreground──▶  Normal Terminal Interaction
                                          │
                                   Background Stream
                                          │
                                          ▼
                              Temporary Log File (/tmp/...)
                                          │
                                    Real-time Read
                                          │
                                          ▼
                                Python Dashboard  ──▶  State Evaluation
                                                       🟢 Running
                                                       🟡 Waiting
                                                       ⚪ Completed
```
