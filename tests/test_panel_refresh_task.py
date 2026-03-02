import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import panel_app


def _write_log(path: Path, tool: str, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        f.write(f"--- MONITOR_START: {tool} | 2026-03-02 10:00:00 ---\n")
        for line in lines:
            f.write(line)


class PanelRefreshTaskTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)
        self.log_dir = self.tmp_path / "logs"

        self._old_log_dir = panel_app.LOG_DIR
        panel_app.LOG_DIR = str(self.log_dir)
        self.addCleanup(self._restore_log_dir)

    def _restore_log_dir(self):
        panel_app.LOG_DIR = self._old_log_dir

    def test_refresh_task_rebuilds_single_task_and_clears_unread(self):
        log = self.log_dir / "codex_manual_refresh.log"
        _write_log(log, "codex", ["Apply changes? (y/n)\n"])

        api = panel_app.Api()
        api._semantic_waiting_cache[str(log)] = {"tool": "codex", "message": "stale", "fingerprint": "stale"}
        api._task_states[str(log)] = "RUNNING"
        api._last_waiting_fingerprint[str(log)] = "old"
        api._unread_notification_count = 2
        api._unread_by_task[str(log)] = 1

        with mock.patch.object(panel_app, "update_status_icon"):
            res = api.refresh_task(str(log))

        self.assertTrue(res["ok"])
        self.assertIn("task", res)
        task = res["task"]
        self.assertEqual(task["task_key"], str(log))
        self.assertEqual(task["status"], "WAITING")
        self.assertTrue(task["can_refresh"])
        self.assertEqual(api._task_states[str(log)], "WAITING")
        self.assertIn(str(log), api._semantic_waiting_cache)
        self.assertNotEqual(api._last_waiting_fingerprint[str(log)], "old")
        self.assertEqual(api._unread_notification_count, 1)
        self.assertNotIn(str(log), api._unread_by_task)


if __name__ == "__main__":
    unittest.main()
