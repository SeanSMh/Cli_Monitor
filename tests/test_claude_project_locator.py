import json
import sys
import os
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from claude.project_locator import find_project_dir, _clear_cache


def _make_fake_project(tmp_path: Path, session_id: str) -> Path:
    """Create a fake ~/.claude/projects/<hash>/ with a transcript.jsonl."""
    proj_dir = tmp_path / "projects" / "fakehash123"
    proj_dir.mkdir(parents=True)
    transcript = proj_dir / "transcript.jsonl"
    lines = [
        json.dumps({"type": "other", "session_id": "old-session"}) + "\n",
        json.dumps({"type": "summary", "session_id": session_id}) + "\n",
    ]
    transcript.write_text("".join(lines))
    return proj_dir


def test_find_project_dir_by_scan(tmp_path, monkeypatch):
    _clear_cache()
    monkeypatch.setattr("claude.project_locator.CLAUDE_PROJECTS_DIR", tmp_path / "projects")
    proj_dir = _make_fake_project(tmp_path, "test-session-abc")
    result = find_project_dir(session_id="test-session-abc", cwd="/any")
    assert result == proj_dir


def test_find_project_dir_returns_none_when_not_found(tmp_path, monkeypatch):
    _clear_cache()
    monkeypatch.setattr("claude.project_locator.CLAUDE_PROJECTS_DIR", tmp_path / "empty_projects")
    result = find_project_dir(session_id="nonexistent-session", cwd="/any")
    assert result is None


def test_find_project_dir_caches_result(tmp_path, monkeypatch):
    _clear_cache()
    monkeypatch.setattr("claude.project_locator.CLAUDE_PROJECTS_DIR", tmp_path / "projects")
    proj_dir = _make_fake_project(tmp_path, "cached-session")
    r1 = find_project_dir("cached-session", "/any")
    r2 = find_project_dir("cached-session", "/any")
    assert r1 == r2 == proj_dir
