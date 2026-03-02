import unittest
from unittest import mock

import panel_app


class PanelSemanticWaitingTests(unittest.TestCase):
    def test_waiting_hint_regex_avoids_plain_continue_statement(self):
        self.assertIsNone(panel_app._WAITING_HINT_RE.search("We can continue by updating config"))
        self.assertIsNone(panel_app._WAITING_CONFIRM_LINE_RE.search("To continue: run gradlew clean"))
        self.assertIsNone(panel_app._WAITING_CONFIRM_LINE_RE.search("Proceed: update the config and retry"))
        self.assertIsNotNone(panel_app._WAITING_CONFIRM_LINE_RE.search("Continue?"))
        self.assertIsNotNone(panel_app._WAITING_CONFIRM_LINE_RE.search("Proceed (y/n)"))
        self.assertIsNotNone(panel_app._WAITING_CONFIRM_LINE_RE.search("Continue [Y/n]"))
        self.assertIsNotNone(panel_app._WAITING_CONFIRM_LINE_RE.search("Continue:"))

    def test_semantic_waiting_holds_on_generic_running(self):
        api = panel_app.Api()
        log_file = "/tmp/ai_monitor_logs/claude_test_semantic.log"
        api._semantic_waiting_cache[log_file] = {
            "tool": "claude",
            "message": "Do you want to proceed?",
            "fingerprint": "Do you want to proceed? | 1. Yes | 2. No",
        }

        with mock.patch.object(panel_app, "tail_read", return_value=[]):
            status, message = api._apply_semantic_waiting_hold(
                log_file, "claude", "RUNNING", "运行中..."
            )

        self.assertEqual(status, "WAITING")
        self.assertEqual(message, "Do you want to proceed?")

    def test_semantic_waiting_does_not_expire_without_new_output(self):
        api = panel_app.Api()
        log_file = "/tmp/ai_monitor_logs/claude_test_semantic_timeout.log"
        api._semantic_waiting_cache[log_file] = {
            "tool": "claude",
            "message": "Do you want to proceed?",
            "fingerprint": "Do you want to proceed? | 1. Yes | 2. No",
        }

        with mock.patch.object(panel_app, "tail_read", return_value=[]):
            status, message = api._apply_semantic_waiting_hold(
                log_file, "claude", "RUNNING", "运行中..."
            )

        self.assertEqual(status, "WAITING")
        self.assertEqual(message, "Do you want to proceed?")
        self.assertIn(log_file, api._semantic_waiting_cache)

    def test_semantic_waiting_releases_on_meaningful_running_message(self):
        api = panel_app.Api()
        log_file = "/tmp/ai_monitor_logs/codex_test_semantic.log"
        api._semantic_waiting_cache[log_file] = {
            "tool": "codex",
            "message": "Save file to continue",
            "fingerprint": "Save file to continue | 1. Yes | 2. No",
        }

        with mock.patch.object(panel_app, "tail_read", return_value=[]):
            status, message = api._apply_semantic_waiting_hold(
                log_file, "codex", "RUNNING", "Thinking"
            )

        self.assertEqual(status, "RUNNING")
        self.assertEqual(message, "Thinking")
        self.assertNotIn(log_file, api._semantic_waiting_cache)

    def test_semantic_waiting_updates_message_when_new_prompt_detected(self):
        api = panel_app.Api()
        log_file = "/tmp/ai_monitor_logs/claude_test_semantic_update.log"
        api._semantic_waiting_cache[log_file] = {
            "tool": "claude",
            "message": "Old prompt",
            "fingerprint": "Old prompt | 1. Yes | 2. No",
        }

        with mock.patch.object(
            panel_app,
            "tail_read",
            return_value=[
                "Do you want to continue with deploy?\n",
                "1. Yes\n",
                "2. No\n",
            ],
        ):
            status, message = api._apply_semantic_waiting_hold(
                log_file, "claude", "RUNNING", "运行中..."
            )

        self.assertEqual(status, "WAITING")
        self.assertEqual(message, "Do you want to continue with deploy?")
        self.assertEqual(
            api._semantic_waiting_cache[log_file]["message"],
            "Do you want to continue with deploy?",
        )

    def test_semantic_waiting_releases_on_generic_running_with_new_effective_output(self):
        api = panel_app.Api()
        log_file = "/tmp/ai_monitor_logs/claude_test_semantic_release.log"
        api._semantic_waiting_cache[log_file] = {
            "tool": "claude",
            "message": "Do you want to proceed?",
            "fingerprint": "Do you want to proceed? | 1. Yes | 2. No",
        }

        with mock.patch.object(
            panel_app,
            "tail_read",
            return_value=["Applying patch to globals.css\n"],
        ):
            status, message = api._apply_semantic_waiting_hold(
                log_file, "claude", "RUNNING", "运行中..."
            )

        self.assertEqual(status, "RUNNING")
        self.assertEqual(message, "运行中...")
        self.assertNotIn(log_file, api._semantic_waiting_cache)

    def test_semantic_idle_holds_codex_on_unchanged_effective_tail(self):
        api = panel_app.Api()
        log_file = "/tmp/ai_monitor_logs/codex_test_semantic_idle.log"
        api._semantic_idle_cache[log_file] = {
            "tool": "codex",
            "message": "等待输入",
            "fingerprint": "Final answer line | ? for shortcuts",
        }

        with mock.patch.object(
            panel_app,
            "tail_read",
            return_value=[
                "Final answer line\n",
                "? for shortcuts\n",
            ],
        ):
            status, message = api._apply_semantic_idle_hold(
                log_file, "codex", "RUNNING", "Final answer line"
            )

        self.assertEqual(status, "IDLE")
        self.assertEqual(message, "等待输入")

    def test_semantic_idle_releases_codex_when_new_output_arrives(self):
        api = panel_app.Api()
        log_file = "/tmp/ai_monitor_logs/codex_test_semantic_idle_release.log"
        api._semantic_idle_cache[log_file] = {
            "tool": "codex",
            "message": "等待输入",
            "fingerprint": "Final answer line | ? for shortcuts",
        }

        with mock.patch.object(
            panel_app,
            "tail_read",
            return_value=[
                "Final answer line\n",
                "Thinking about next step\n",
            ],
        ):
            status, message = api._apply_semantic_idle_hold(
                log_file, "codex", "RUNNING", "Thinking about next step"
            )

        self.assertEqual(status, "RUNNING")
        self.assertEqual(message, "Thinking about next step")
        self.assertNotIn(log_file, api._semantic_idle_cache)


if __name__ == "__main__":
    unittest.main()
