#!/usr/bin/env python3
import json
import os
import shutil

CONFIG_DIR = os.path.expanduser("~/.cli-monitor")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

DEFAULT_CONFIG = {
    "core": {
        "log_dir": "/tmp/ai_monitor_logs",
        "scan_interval": 2.0,
        "tail_bytes": 4096,
        "max_tasks": 10
    },
    "behavior": {
        "idle_threshold": 60,
        "rate_high_threshold": 200,
        "rate_idle_seconds": 5
    },
    "ui": {
        "theme": "dark",
        "refresh_rate": 2000,
        "window_size": [660, 650]
    },
    "rules": {
        "common": {
            "waiting": [
                r"\(y/n\)",
                r"\(Y/n\)",
                r"\(yes/no\)",
                r"Confirm\?",
                r"\[\?\]",
                r"Press Enter",
                r"Save file to continue",
                r"Do you want to",
                r"Would you like to",
                r"Apply changes\?"
            ],
            "idle": [
                r"input:",
                r"enter selection"
            ],
            "busy": [
                r"Working\(",
                r"Thinking",
                r"Generating",
                r"Type checking"
            ]
        },
        "tools": [
            {
                "name": "claude",
                "busy_patterns": [r"Thinking", r"esc to interrupt"],
                "idle_patterns": [r"Context left", r"Cost:"],
                "idle_threshold": 10,
                "signal_file": "_claude_idle_signal"
            },
            {
                "name": "codex",
                "busy_patterns": [r"Working\("],
                "idle_patterns": [r"\? for shortcuts", r"context left"],
                "idle_threshold": 12,
            },
            {
                "name": "gemini",
                "busy_patterns": [r"Generating", r"\u2580"],
                "idle_patterns": [r"gemini >\s*$"]
            },
            {
                "name": "maven",
                "alias": ["mvn"],
                "done_patterns": {
                    "BUILD SUCCESS": "✅ 构建成功",
                    "BUILD FAILURE": "❌ 构建失败"
                }
            },
             {
                "name": "gradle",
                "alias": ["gradlew"],
                "done_patterns": {
                    "BUILD SUCCESSFUL": "✅ 构建成功",
                    "BUILD FAILED": "❌ 构建失败"
                }
            }
        ]
    }
}

class ConfigLoader:
    def __init__(self):
        self._config = DEFAULT_CONFIG
        self._ensure_config_exists()
        self.load()

    def _ensure_config_exists(self):
        if not os.path.exists(CONFIG_DIR):
            os.makedirs(CONFIG_DIR)
        
        if not os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                    json.dump(DEFAULT_CONFIG, f, indent=2, ensure_ascii=False)
            except Exception as e:
                print(f"Warning: Failed to create default config: {e}")

    def load(self):
        try:
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    user_config = json.load(f)
                    # Deep merge could be implemented here, currently simple override
                    # Ideally we want to merge user keys into defaults
                    self._config = self._deep_merge(DEFAULT_CONFIG.copy(), user_config)
        except Exception as e:
            print(f"Error loading config: {e}. Using defaults.")
            self._config = DEFAULT_CONFIG

    def _deep_merge(self, base, update):
        for key, value in update.items():
            if isinstance(value, dict) and key in base and isinstance(base[key], dict):
                base[key] = self._deep_merge(base[key], value)
            elif (
                key == "tools"
                and isinstance(value, list)
                and isinstance(base.get(key), list)
            ):
                base[key] = self._merge_tools_list(base[key], value)
            else:
                base[key] = value
        return base

    def _merge_tools_list(self, default_tools, user_tools):
        """按 tool.name 合并，保留默认新增字段（如 idle_threshold）"""
        if not isinstance(default_tools, list):
            return user_tools
        if not isinstance(user_tools, list):
            return default_tools

        default_by_name = {}
        for item in default_tools:
            if isinstance(item, dict) and item.get("name"):
                default_by_name[item["name"]] = item

        merged = []
        for user_item in user_tools:
            if not isinstance(user_item, dict):
                merged.append(user_item)
                continue
            name = user_item.get("name")
            default_item = default_by_name.get(name)
            if isinstance(default_item, dict):
                merged.append(self._deep_merge(default_item.copy(), user_item))
            else:
                merged.append(user_item)

        user_names = {t.get("name") for t in user_tools if isinstance(t, dict)}
        for default_item in default_tools:
            if not isinstance(default_item, dict):
                continue
            if default_item.get("name") not in user_names:
                merged.append(default_item)
        return merged

    def get(self, path=None, default=None):
        """
        Get config value by dot notation path, e.g. "core.log_dir"
        """
        if not path:
            return self._config
        
        keys = path.split('.')
        value = self._config
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default
        return value

# Singleton instance
config = ConfigLoader()
