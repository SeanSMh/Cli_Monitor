import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import panel_app


def _write_log(path: Path, tool: str, lines: list[str], meta: dict[str, str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        f.write(f"--- MONITOR_START: {tool} | 2026-03-02 10:00:00 ---\n")
        for key, value in (meta or {}).items():
            f.write(f"--- MONITOR_META {key}: {value} ---\n")
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
        self.assertNotIn(str(log), api._semantic_waiting_cache)
        self.assertNotEqual(api._last_waiting_fingerprint[str(log)], "old")
        self.assertEqual(api._unread_notification_count, 1)
        self.assertNotIn(str(log), api._unread_by_task)

    def test_build_task_for_log_keeps_closed_duration_stable_when_end_marker_write_fails(self):
        log = self.log_dir / "codex_1710000000_99999_dead.log"
        _write_log(log, "codex", ["running...\n"])

        api = panel_app.Api()

        with mock.patch.object(
            panel_app,
            "analyze_log",
            return_value=("codex", "RUNNING", "运行中...", -1, "", 0),
        ), mock.patch.object(panel_app.os, "kill", side_effect=OSError()), mock.patch.object(
            panel_app, "_append_monitor_end_if_missing", return_value=False
        ), mock.patch.object(panel_app.time, "strftime") as mock_strftime:
            mock_strftime.return_value = "2026-03-02 10:00:05"
            first = api._build_task_for_log(str(log), "zh")
            second = api._build_task_for_log(str(log), "zh")

        self.assertEqual(first["status"], "DONE")
        self.assertEqual(second["status"], "DONE")
        self.assertEqual(first["exit_code"], 137)
        self.assertEqual(first["duration"], "5s")
        self.assertEqual(second["duration"], first["duration"])

    def test_build_task_for_log_marks_codex_mode_from_state_source(self):
        weak_log = self.log_dir / "codex_weak.log"
        monitored_log = self.log_dir / "codex_monitored.log"
        _write_log(weak_log, "codex", ["Thinking\n"], meta={"state_source": "codex_weak"})
        _write_log(monitored_log, "codex", ["Thinking\n"], meta={"state_source": "codex_proxy"})

        api = panel_app.Api()

        with mock.patch.object(
            panel_app,
            "analyze_log",
            return_value=("codex", "RUNNING", "运行中...", -1, "", 0),
        ):
            weak_task = api._build_task_for_log(str(weak_log), "zh")
            monitored_task = api._build_task_for_log(str(monitored_log), "zh")

        self.assertEqual(weak_task["mode"], "normal")
        self.assertEqual(monitored_task["mode"], "monitored")

    def test_launch_codex_uses_weak_wrapper(self):
        api = panel_app.Api()

        with mock.patch.object(panel_app, "inject_shell_wrapper", return_value=True), mock.patch.object(
            panel_app, "_new_launch_token", return_value="launch_token_1"
        ), mock.patch.object(
            panel_app, "_wait_for_launch_token", return_value=True
        ), mock.patch.object(
            panel_app, "_launch_terminal_command", return_value=True
        ) as mock_launch:
            res = api.launch_codex()

        self.assertTrue(res["ok"])
        self.assertEqual(res["mode"], "normal")
        launched = mock_launch.call_args.args[0]
        self.assertIn("source", launched)
        self.assertIn(panel_app.TEMP_WRAPPER, launched)
        self.assertIn("type codex", launched)
        self.assertIn("CLI_MONITOR_LAUNCH_TOKEN=launch_token_1", launched)
        self.assertTrue(launched.strip().endswith("codex"))

    def test_launch_codex_monitored_uses_dedicated_launcher(self):
        api = panel_app.Api()

        with mock.patch.object(panel_app, "inject_shell_wrapper", return_value=True), mock.patch.object(
            panel_app, "_new_launch_token", return_value="launch_token_2"
        ), mock.patch.object(
            panel_app, "_wait_for_launch_token", return_value=True
        ), mock.patch.object(
            panel_app, "_launch_terminal_command", return_value=True
        ) as mock_launch:
            res = api.launch_codex_monitored()

        self.assertTrue(res["ok"])
        self.assertEqual(res["mode"], "monitored")
        launched = mock_launch.call_args.args[0]
        self.assertIn("bash", launched)
        self.assertIn("CLI_MONITOR_LAUNCH_TOKEN=launch_token_2", launched)
        self.assertIn(panel_app.CODEX_MONITORED_LAUNCHER, launched)

    def test_launch_codex_returns_failure_when_wrapper_injection_fails(self):
        api = panel_app.Api()

        with mock.patch.object(panel_app, "inject_shell_wrapper", return_value=False), mock.patch.object(
            panel_app, "_launch_terminal_command"
        ) as mock_launch:
            res = api.launch_codex()

        self.assertFalse(res["ok"])
        self.assertEqual(res["mode"], "normal")
        mock_launch.assert_not_called()

    def test_launch_codex_returns_failure_when_log_does_not_appear(self):
        api = panel_app.Api()

        with mock.patch.object(panel_app, "inject_shell_wrapper", return_value=True), mock.patch.object(
            panel_app, "_new_launch_token", return_value="launch_token_3"
        ), mock.patch.object(
            panel_app, "_launch_terminal_command", return_value=True
        ), mock.patch.object(panel_app, "_wait_for_launch_token", return_value=False):
            res = api.launch_codex()

        self.assertFalse(res["ok"])
        self.assertEqual(res["mode"], "normal")

    def test_wait_for_launch_token_requires_no_fast_exit(self):
        log = self.log_dir / "codex_launch_ok.log"
        _write_log(log, "codex", ["Thinking...\n"], meta={"launch_token": "launch_ok"})

        with mock.patch.object(panel_app, "LOG_DIR", str(self.log_dir)):
            ok = panel_app._wait_for_launch_token("codex", "launch_ok", timeout_seconds=0.2)

        self.assertTrue(ok)

    def test_wait_for_launch_token_fails_on_fast_end_marker(self):
        log = self.log_dir / "codex_launch_fail.log"
        _write_log(
            log,
            "codex",
            ["Thinking...\n", "--- MONITOR_END: 127 | 2026-03-02 10:00:01 ---\n"],
            meta={"launch_token": "launch_fail"},
        )

        with mock.patch.object(panel_app, "LOG_DIR", str(self.log_dir)):
            ok = panel_app._wait_for_launch_token("codex", "launch_fail", timeout_seconds=0.2)

        self.assertFalse(ok)

    def test_show_panel_from_app_event_reveals_window_and_clears_unread(self):
        api = panel_app.Api()
        window = mock.Mock()

        old_window = panel_app._window
        old_api = panel_app._api
        old_window_visible = panel_app._window_visible
        old_app_quitting = panel_app._app_quitting
        panel_app._window = window
        panel_app._api = api
        panel_app._window_visible = False
        panel_app._app_quitting = False
        self.addCleanup(lambda: setattr(panel_app, "_window", old_window))
        self.addCleanup(lambda: setattr(panel_app, "_api", old_api))
        self.addCleanup(lambda: setattr(panel_app, "_window_visible", old_window_visible))
        self.addCleanup(lambda: setattr(panel_app, "_app_quitting", old_app_quitting))

        with mock.patch.object(panel_app, "NSApplication") as mock_nsapp:
            app = mock.Mock()
            mock_nsapp.sharedApplication.return_value = app
            ok = panel_app._show_panel_from_app_event()

        self.assertTrue(ok)
        window.show.assert_called_once()
        app.activateIgnoringOtherApps_.assert_called_once_with(True)
        self.assertTrue(panel_app._window_visible)

    def test_resolve_resource_dir_prefers_bundle_resources_when_meipass_points_frameworks(self):
        with mock.patch.object(panel_app.sys, "frozen", True, create=True), mock.patch.object(
            panel_app.sys,
            "_MEIPASS",
            "/Applications/CLI Monitor.app/Contents/Frameworks",
            create=True,
        ), mock.patch.dict(panel_app.os.environ, {}, clear=True), mock.patch.object(
            panel_app.os.path,
            "isdir",
            side_effect=lambda path: path == "/Applications/CLI Monitor.app/Contents/Resources",
        ):
            resource_dir = panel_app._resolve_resource_dir()

        self.assertEqual(resource_dir, "/Applications/CLI Monitor.app/Contents/Resources")

    def test_quit_app_cleans_injection_before_terminate(self):
        api = panel_app.Api()
        old_app_quitting = panel_app._app_quitting
        old_window_visible = panel_app._window_visible
        panel_app._app_quitting = False
        panel_app._window_visible = True
        self.addCleanup(lambda: setattr(panel_app, "_app_quitting", old_app_quitting))
        self.addCleanup(lambda: setattr(panel_app, "_window_visible", old_window_visible))

        with mock.patch.object(panel_app, "_schedule_force_exit") as mock_force_exit, mock.patch.object(
            panel_app, "cleanup_shell_wrapper"
        ) as mock_cleanup_shell, mock.patch.object(
            panel_app, "cleanup_claude_hooks"
        ) as mock_cleanup_claude, mock.patch.object(
            panel_app, "_stop_e2e_server"
        ) as mock_stop_e2e, mock.patch.object(
            panel_app, "remove_status_item_from_thread"
        ) as mock_remove_status, mock.patch.object(
            panel_app, "_request_app_terminate",
            return_value=True,
        ) as mock_terminate:
            ok = api.quit_app()

        self.assertTrue(ok)
        mock_force_exit.assert_called_once()
        mock_cleanup_shell.assert_called_once()
        mock_cleanup_claude.assert_called_once()
        mock_stop_e2e.assert_called_once()
        mock_remove_status.assert_called_once_with(wait_until_done=False)
        mock_terminate.assert_called_once()
        self.assertTrue(panel_app._app_quitting)
        self.assertFalse(panel_app._window_visible)

    def test_panel_html_bootstrap_requires_successful_refresh(self):
        panel_html = Path("/Users/sqb/projects/cli-monitor/panel.html").read_text(encoding="utf-8")
        self.assertIn("const refreshed = await refresh();", panel_html)
        self.assertIn("if (!refreshed) {", panel_html)
        self.assertIn("throw new Error('refresh_failed');", panel_html)

    def test_notification_response_delegate_removes_notification_and_completes(self):
        if not getattr(panel_app, "HAS_APPKIT", False):
            self.skipTest("AppKit unavailable")
        delegate = panel_app.StatusBarDelegate.alloc().init()
        center = mock.Mock()
        notification = mock.Mock()
        response = mock.Mock()
        response.notification.return_value = notification
        completion = mock.Mock()

        with mock.patch.object(delegate, "show_panel") as mock_show_panel:
            delegate.userNotificationCenter_didReceiveNotificationResponse_withCompletionHandler_(
                center, response, completion
            )

        mock_show_panel.assert_called_once()
        center.removeDeliveredNotification_.assert_called_once_with(notification)
        completion.assert_called_once()


if __name__ == "__main__":
    unittest.main()
