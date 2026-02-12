"""
CLI Monitor — py2app 打包配置
将 menubar_app.py 打包为独立的 macOS .app 应用。

用法:
    python3 setup.py py2app
"""

from setuptools import setup

APP = ["menubar_app.py"]
DATA_FILES = []

OPTIONS = {
    "argv_emulation": False,
    "plist": {
        "CFBundleName": "CLI Monitor",
        "CFBundleDisplayName": "CLI Monitor",
        "CFBundleIdentifier": "com.cli-monitor.menubar",
        "CFBundleVersion": "1.0.0",
        "CFBundleShortVersionString": "1.0.0",
        "LSUIElement": True,  # 不在 Dock 中显示图标 (纯状态栏应用)
        "NSHumanReadableCopyright": "CLI Monitor - 终端任务状态监控",
    },
    "includes": ["rumps", "monitor"],
    "packages": [],
}

setup(
    app=APP,
    name="CLI Monitor",
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
