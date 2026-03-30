import json
import sys
import os
from pathlib import Path
import pytest

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from claude.install import install, uninstall, SETTINGS_PATH


def test_install_writes_status_command(tmp_path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr("claude.install.SETTINGS_PATH", settings_file)
    install(receiver_abs_path="/abs/path/claude/receiver.py")
    data = json.loads(settings_file.read_text())
    assert data["statusCommand"] == "python3 /abs/path/claude/receiver.py"


def test_install_preserves_existing_fields(tmp_path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({"theme": "dark", "other": 42}))
    monkeypatch.setattr("claude.install.SETTINGS_PATH", settings_file)
    install(receiver_abs_path="/abs/path/claude/receiver.py")
    data = json.loads(settings_file.read_text())
    assert data["theme"] == "dark"
    assert data["other"] == 42
    assert "statusCommand" in data


def test_install_creates_backup(tmp_path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({"existing": True}))
    monkeypatch.setattr("claude.install.SETTINGS_PATH", settings_file)
    install(receiver_abs_path="/x/receiver.py")
    backup = tmp_path / "settings.json.cli-monitor-backup"
    assert backup.exists()
    assert json.loads(backup.read_text())["existing"] is True


def test_install_works_when_settings_missing(tmp_path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr("claude.install.SETTINGS_PATH", settings_file)
    install(receiver_abs_path="/x/receiver.py")
    data = json.loads(settings_file.read_text())
    assert "statusCommand" in data


def test_uninstall_removes_status_command(tmp_path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({"statusCommand": "python3 /x/receiver.py", "theme": "dark"}))
    monkeypatch.setattr("claude.install.SETTINGS_PATH", settings_file)
    uninstall()
    data = json.loads(settings_file.read_text())
    assert "statusCommand" not in data
    assert data["theme"] == "dark"


def test_uninstall_is_noop_when_settings_missing(tmp_path, monkeypatch):
    settings_file = tmp_path / "nonexistent.json"
    monkeypatch.setattr("claude.install.SETTINGS_PATH", settings_file)
    uninstall()  # should not raise
