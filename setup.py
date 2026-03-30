"""
CLI Monitor — py2app 打包配置
将 menubar_app.py 打包为独立的 macOS .app 应用。

用法:
    python3 setup.py py2app
"""

from setuptools import setup

APP = ["panel_app.py"]
DATA_FILES = [
    "panel.html",
    ("shell", ["shell/cli_monitor.sh", "shell/codex_launcher.sh"]),
    ("assets", ["assets/app_icon.icns", "assets/app_icon.png"]),
]

OPTIONS = {
    "argv_emulation": False,
    "iconfile": "assets/app_icon.icns",
    "plist": {
        "CFBundleName": "CLI Monitor",
        "CFBundleDisplayName": "CLI Monitor",
        "CFBundleIdentifier": "com.cli-monitor.panel",
        "CFBundleVersion": "0.0.10",
        "CFBundleShortVersionString": "0.0.10",
        "LSUIElement": True,  # Keep as UI element (or False if we want Dock icon, but panel_app handles status bar)
        # panel_app uses NSStatusBar so LSUIElement=True is appropriate to hide Dock icon if desired, 
        # but pywebview might need Dock icon for window?
        # panel_app.py docstring says "Click status bar icon to toggle panel". 
        # Usually these apps hide from Dock. Let's keep LSUIElement=True.
        "NSHumanReadableCopyright": "CLI Monitor v0.0.10",
    },
    "includes": [
        "webview",
        "monitor",
        "watchdog",
        "config_loader",
        "daemon_client",
        "AppKit",
        "Foundation",
        "objc",
    ],
    "packages": ["watchdog", "daemon", "engine", "proxy", "registry"],
}

setup(
    app=APP,
    name="CLI Monitor",
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
