import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


def _write_log(
    path: Path,
    tool: str,
    lines: list[str],
    start_ts: str = "2026-02-13 10:00:00",
    meta: dict | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        f.write(f"--- MONITOR_START: {tool} | {start_ts} ---\n")
        for k, v in (meta or {}).items():
            f.write(f"--- MONITOR_META {k}: {v} ---\n")
        for line in lines:
            f.write(line)


class AnalyzeLogTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)

        home = self.tmp_path / "home"
        home.mkdir(parents=True, exist_ok=True)
        env_patch = mock.patch.dict(os.environ, {"HOME": str(home)}, clear=False)
        env_patch.start()
        self.addCleanup(env_patch.stop)

        for mod_name in ("monitor", "config_loader"):
            sys.modules.pop(mod_name, None)

        self.monitor = importlib.import_module("monitor")
        self.monitor.DEFAULT_LOG_DIR = str(self.tmp_path / "logs")
        self.monitor.SIGNAL_FILE = str(self.tmp_path / "_claude_idle_signal")
        self.monitor.CLAUDE_NOTIFY_SIGNAL_FILE = str(
            self.tmp_path / "_claude_notify_signal"
        )

    def test_analyze_log_done_status_and_duration(self):
        log = self.tmp_path / "logs" / "codex_1_2_3.log"
        _write_log(
            log,
            "codex",
            ["working...\n", "--- MONITOR_END: 0 | 2026-02-13 10:00:05 ---\n"],
        )

        tool, status, msg, exit_code, duration, signal_ts = self.monitor.analyze_log(str(log))
        self.assertEqual(tool, "codex")
        self.assertEqual(status, "DONE")
        self.assertEqual(msg, "任务完成")
        self.assertEqual(exit_code, 0)
        self.assertEqual(duration, "5s")
        self.assertEqual(signal_ts, 0)

    def test_analyze_log_waiting_status(self):
        log = self.tmp_path / "logs" / "codex_1_2_3.log"
        _write_log(log, "codex", ["Apply changes? (y/n)\n"])

        _, status, msg, _, _, _ = self.monitor.analyze_log(str(log))
        self.assertEqual(status, "WAITING")
        self.assertIn("(y/n)", msg)

    def test_analyze_log_gradle_done_pattern_to_idle(self):
        log = self.tmp_path / "logs" / "gradle_1_2_3.log"
        _write_log(log, "gradle", ["BUILD SUCCESSFUL in 1s\n"])

        _, status, msg, exit_code, duration, signal_ts = self.monitor.analyze_log(str(log))
        self.assertEqual(status, "IDLE")
        self.assertEqual(msg, "✅ 构建成功")
        self.assertEqual(exit_code, -1)
        self.assertEqual(duration, "")
        self.assertEqual(signal_ts, 0)

    def test_analyze_log_claude_signal_idle(self):
        log = self.tmp_path / "logs" / "claude_1_2_3.log"
        _write_log(log, "claude", ["Thinking...\n"])

        signal_file = Path(self.monitor.SIGNAL_FILE)
        signal_file.parent.mkdir(parents=True, exist_ok=True)
        signal_file.write_text("ok", encoding="utf-8")

        _, status, msg, _, _, signal_ts = self.monitor.analyze_log(str(log))
        self.assertEqual(status, "IDLE")
        self.assertEqual(msg, "AI 已完成回复")
        self.assertGreater(signal_ts, 0)

    def test_analyze_log_claude_notification_waiting(self):
        log = self.tmp_path / "logs" / "claude_1_2_3.log"
        _write_log(log, "claude", ["Thinking...\n"])

        notify_file = Path(self.monitor.CLAUDE_NOTIFY_SIGNAL_FILE)
        notify_file.parent.mkdir(parents=True, exist_ok=True)
        notify_payload = {
            "state": "WAITING",
            "message": "Do you want to proceed?",
            "session_id": "claude_1_2_3",
            "log_file": str(log),
        }
        notify_file.write_text(
            json.dumps(notify_payload, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        _, status, msg, _, _, signal_ts = self.monitor.analyze_log(str(log))
        self.assertEqual(status, "WAITING")
        self.assertIn("Do you want to proceed?", msg)
        self.assertEqual(signal_ts, 0)

    def test_analyze_log_claude_notification_overrides_idle_when_newer(self):
        log = self.tmp_path / "logs" / "claude_1_2_3.log"
        _write_log(log, "claude", ["Thinking...\n"])

        idle_signal = Path(self.monitor.SIGNAL_FILE)
        idle_signal.parent.mkdir(parents=True, exist_ok=True)
        idle_signal.write_text("ok", encoding="utf-8")
        os.utime(idle_signal, (1, 1))

        notify_file = Path(self.monitor.CLAUDE_NOTIFY_SIGNAL_FILE)
        notify_file.parent.mkdir(parents=True, exist_ok=True)
        notify_payload = {
            "state": "WAITING",
            "message": "Select an option",
            "session_id": "claude_1_2_3",
            "log_file": str(log),
        }
        notify_file.write_text(
            json.dumps(notify_payload, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        _, status, msg, _, _, signal_ts = self.monitor.analyze_log(str(log))
        self.assertEqual(status, "WAITING")
        self.assertIn("Select an option", msg)
        self.assertEqual(signal_ts, 0)

    def test_analyze_log_claude_waiting_wins_when_same_second_as_idle_signal(self):
        log = self.tmp_path / "logs" / "claude_1_2_3.log"
        _write_log(log, "claude", ["Thinking...\n"])

        idle_signal = Path(self.monitor.SIGNAL_FILE)
        idle_signal.parent.mkdir(parents=True, exist_ok=True)
        idle_signal.write_text("ok", encoding="utf-8")
        idle_mtime = os.path.getmtime(idle_signal)

        notify_file = Path(self.monitor.CLAUDE_NOTIFY_SIGNAL_FILE)
        notify_file.parent.mkdir(parents=True, exist_ok=True)
        notify_payload = {
            "state": "WAITING",
            "message": "Do you want to proceed?",
            "ts_ms": int(idle_mtime * 1000),
            "session_id": "claude_1_2_3",
            "log_file": str(log),
        }
        notify_file.write_text(
            json.dumps(notify_payload, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        _, status, msg, _, _, _ = self.monitor.analyze_log(str(log))
        self.assertEqual(status, "WAITING")
        self.assertIn("Do you want to proceed", msg)

    def test_analyze_log_ignores_global_notify_event_from_other_session(self):
        log1 = self.tmp_path / "logs" / "claude_1_2_3.log"
        log2 = self.tmp_path / "logs" / "claude_1_2_4.log"
        _write_log(log1, "claude", ["Thinking...\n"])
        _write_log(log2, "claude", ["Thinking...\n"])

        notify_file = Path(self.monitor.CLAUDE_NOTIFY_SIGNAL_FILE)
        notify_file.parent.mkdir(parents=True, exist_ok=True)
        notify_payload = {
            "state": "WAITING",
            "message": "Apply changes?",
            "session_id": "claude_1_2_4",
            "log_file": str(log2),
        }
        notify_file.write_text(
            json.dumps(notify_payload, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        _, status, _, _, _, _ = self.monitor.analyze_log(str(log1))
        self.assertEqual(status, "RUNNING")

    def test_analyze_log_claude_does_not_idle_on_cost_line_alone(self):
        log = self.tmp_path / "logs" / "claude_1_2_3.log"
        _write_log(log, "claude", ["Cost: $0.12\n"])

        _, status, msg, _, _, _ = self.monitor.analyze_log(str(log))
        self.assertEqual(status, "RUNNING")
        self.assertIn("Cost:", msg)

    def test_analyze_log_claude_notification_idle_compatibility(self):
        log = self.tmp_path / "logs" / "claude_1_2_3.log"
        _write_log(log, "claude", ["Thinking...\n"])

        notify_file = Path(self.monitor.CLAUDE_NOTIFY_SIGNAL_FILE)
        notify_file.parent.mkdir(parents=True, exist_ok=True)
        notify_payload = {
            "state": "IDLE",
            "message": "Turn completed",
            "session_id": "claude_1_2_3",
            "log_file": str(log),
        }
        notify_file.write_text(
            json.dumps(notify_payload, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        _, status, msg, _, _, signal_ts = self.monitor.analyze_log(str(log))
        self.assertEqual(status, "IDLE")
        self.assertIn("Turn completed", msg)
        self.assertGreater(signal_ts, 0)

    def test_analyze_log_records_claude_parse_stats(self):
        log = self.tmp_path / "logs" / "claude_1_2_3.log"
        _write_log(log, "claude", ["Thinking...\n"])

        notify_file = Path(self.monitor.CLAUDE_NOTIFY_SIGNAL_FILE)
        notify_file.parent.mkdir(parents=True, exist_ok=True)
        notify_payload = {
            "state": "WAITING",
            "message": "Do you want to proceed?",
            "session_id": "claude_1_2_3",
            "log_file": str(log),
        }
        notify_file.write_text(
            json.dumps(notify_payload, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        _, status, _, _, _, _ = self.monitor.analyze_log(str(log))
        self.assertEqual(status, "WAITING")

        stats = self.monitor.get_claude_parse_stats()
        self.assertEqual(stats["notify_waiting_hit_count"], 1)
        self.assertEqual(stats["notify_idle_hit_count"], 0)
        self.assertEqual(stats["stop_idle_hit_count"], 0)
        self.assertEqual(stats["text_fallback_hit_count"], 0)
        self.assertEqual(stats["last_source"], "notify_waiting")

    def test_analyze_log_rate_based_idle(self):
        log = self.tmp_path / "logs" / "npm_1_2_3.log"
        _write_log(log, "npm", ["task running...\n"])

        with mock.patch.object(
            self.monitor,
            "track_file_rate",
            return_value=(True, True, self.monitor.RATE_IDLE_SECONDS),
        ):
            _, status, msg, _, _, _ = self.monitor.analyze_log(str(log))

        self.assertEqual(status, "IDLE")
        self.assertEqual(msg, "AI 已完成回复")

    def test_analyze_log_running_strips_ansi_and_control_chars(self):
        log = self.tmp_path / "logs" / "npm_1_2_3.log"
        _write_log(log, "npm", ["\x1b[31mHello\x1b[0m\x00world\n"])

        _, status, msg, _, _, _ = self.monitor.analyze_log(str(log))
        self.assertEqual(status, "RUNNING")
        self.assertNotIn("\x1b", msg)
        self.assertNotIn("\x00", msg)
        self.assertIn("Hello", msg)

    def test_parse_session_meta_reads_header_fields(self):
        log = self.tmp_path / "logs" / "codex_1739450000_4567_99.log"
        _write_log(
            log,
            "codex",
            ["running...\n"],
            meta={
                "term_program": "iTerm.app",
                "tty": "ttys012",
                "cwd": "/tmp/work",
                "shell_pid": "4567",
                "wezterm_pane_id": "42",
            },
        )

        meta = self.monitor.parse_session_meta(str(log))
        self.assertEqual(meta["term_program"], "iTerm.app")
        self.assertEqual(meta["tty"], "/dev/ttys012")
        self.assertEqual(meta["cwd"], "/tmp/work")
        self.assertEqual(meta["shell_pid"], "4567")
        self.assertEqual(meta["wezterm_pane_id"], "42")

    def test_parse_session_meta_fallback_pid_from_filename(self):
        log = self.tmp_path / "logs" / "codex_1739450000_7654_11.log"
        _write_log(log, "codex", ["running...\n"])

        meta = self.monitor.parse_session_meta(str(log))
        self.assertEqual(meta["shell_pid"], "7654")

    def test_format_status_closed_for_137(self):
        text = self.monitor.format_status("DONE", 137)
        self.assertIn("已关闭", text)

    def test_analyze_log_ignores_monitor_meta_in_running_message(self):
        log = self.tmp_path / "logs" / "codex_1_2_3.log"
        _write_log(
            log,
            "codex",
            [],
            meta={
                "term_program": "vscode",
                "vscode_pid": "123",
            },
        )

        _, status, msg, _, _, _ = self.monitor.analyze_log(str(log))
        self.assertEqual(status, "RUNNING")
        self.assertEqual(msg, "运行中...")

    def test_analyze_log_ignores_codex_startup_banner_noise(self):
        log = self.tmp_path / "logs" / "codex_1_2_3.log"
        _write_log(
            log,
            "codex",
            [
                "╭────────────────────────╮\n",
                "│ OpenAI Codex           │\n",
                "│ Model: gpt-5           │\n",
                "│ Directory: /tmp/demo   │\n",
                "╰────────────────────────╯\n",
            ],
        )

        _, status, msg, _, _, _ = self.monitor.analyze_log(str(log))
        self.assertEqual(status, "RUNNING")
        self.assertEqual(msg, "运行中...")

    def test_analyze_log_keeps_real_output_after_codex_banner_noise(self):
        log = self.tmp_path / "logs" / "codex_1_2_3.log"
        _write_log(
            log,
            "codex",
            [
                "│ Model: gpt-5 │\n",
                "Generating patch...\n",
            ],
        )

        _, status, msg, _, _, _ = self.monitor.analyze_log(str(log))
        self.assertEqual(status, "RUNNING")
        self.assertEqual(msg, "Generating patch...")

    def test_analyze_log_strips_android_studio_osc_query_residue(self):
        log = self.tmp_path / "logs" / "codex_1_2_3.log"
        _write_log(
            log,
            "codex",
            [
                "\x1b]10;?\x1b\\\x1b]11;?\x07✨ Update applied\n",
            ],
        )

        _, status, msg, _, _, _ = self.monitor.analyze_log(str(log))
        self.assertEqual(status, "RUNNING")
        self.assertEqual(msg, "✨ Update applied")

    def test_analyze_log_treats_bare_osc_query_residue_as_noise(self):
        log = self.tmp_path / "logs" / "codex_1_2_3.log"
        _write_log(log, "codex", ["]10;?]11;?\n"])

        _, status, msg, _, _, _ = self.monitor.analyze_log(str(log))
        self.assertEqual(status, "RUNNING")
        self.assertEqual(msg, "运行中...")

    def test_analyze_log_ignores_codex_startup_version_metadata(self):
        log = self.tmp_path / "logs" / "codex_1_2_3.log"
        _write_log(
            log,
            "codex",
            [
                "│ Version: 0.42.1 │\n",
            ],
        )

        _, status, msg, _, _, _ = self.monitor.analyze_log(str(log))
        self.assertEqual(status, "RUNNING")
        self.assertEqual(msg, "运行中...")

    def test_analyze_log_ignores_codex_you_are_in_startup_hint(self):
        log = self.tmp_path / "logs" / "codex_1_2_3.log"
        _write_log(
            log,
            "codex",
            [
                "You are in /Users/sqb/projects/cli-monitor\n",
            ],
        )

        _, status, msg, _, _, _ = self.monitor.analyze_log(str(log))
        self.assertEqual(status, "RUNNING")
        self.assertEqual(msg, "运行中...")

    def test_analyze_log_detects_waiting_when_prompt_is_outside_last_five_lines(self):
        log = self.tmp_path / "logs" / "codex_1_2_3.log"
        _write_log(
            log,
            "codex",
            [
                "Do you want to proceed?\n",
                "  1. Yes\n",
                "  2. Yes, and don't ask again for: git:*\n",
                "  3. No\n",
                "extra line a\n",
                "extra line b\n",
                "extra line c\n",
                "extra line d\n",
                "extra line e\n",
            ],
        )

        _, status, msg, _, _, _ = self.monitor.analyze_log(str(log))
        self.assertEqual(status, "WAITING")
        self.assertIn("Do you want to proceed", msg)

    def test_analyze_log_keeps_runtime_version_message(self):
        log = self.tmp_path / "logs" / "codex_1_2_3.log"
        _write_log(
            log,
            "codex",
            [
                "server version mismatch detected\n",
            ],
        )

        _, status, msg, _, _, _ = self.monitor.analyze_log(str(log))
        self.assertEqual(status, "RUNNING")
        self.assertEqual(msg, "server version mismatch detected")

    def test_analyze_log_uses_codex_structured_waiting_event(self):
        log = self.tmp_path / "logs" / "codex_1_2_3.log"
        _write_log(
            log,
            "codex",
            [
                '{"type":"turn.waiting_for_input","prompt":"Do you want to proceed?","options":["1. Yes","2. No"]}\n',
            ],
        )

        _, status, msg, _, _, _ = self.monitor.analyze_log(str(log))
        self.assertEqual(status, "WAITING")
        self.assertIn("Do you want to proceed", msg)

    def test_analyze_log_uses_codex_structured_completed_event(self):
        log = self.tmp_path / "logs" / "codex_1_2_3.log"
        _write_log(
            log,
            "codex",
            [
                '{"type":"turn.completed","message":"turn finished"}\n',
            ],
        )

        _, status, msg, _, _, _ = self.monitor.analyze_log(str(log))
        self.assertEqual(status, "IDLE")
        self.assertEqual(msg, "AI 已完成回复")

    def test_analyze_log_uses_app_server_waiting_event(self):
        log = self.tmp_path / "logs" / "codex_1_2_3.log"
        _write_log(
            log,
            "codex",
            [
                '{"jsonrpc":"2.0","method":"turn/input_required","params":{"prompt":"Save file to continue","choices":["1. Yes","2. No"]}}\n',
            ],
        )

        _, status, msg, _, _, _ = self.monitor.analyze_log(str(log))
        self.assertEqual(status, "WAITING")
        self.assertIn("Save file to continue", msg)

    def test_analyze_log_records_official_parse_stats(self):
        log = self.tmp_path / "logs" / "codex_1_2_3.log"
        _write_log(
            log,
            "codex",
            [
                '{"jsonrpc":"2.0","method":"turn/completed","params":{"summary":"done"}}\n',
            ],
        )

        _, status, _, _, _, _ = self.monitor.analyze_log(str(log))
        self.assertEqual(status, "IDLE")

        stats = self.monitor.get_codex_parse_stats()
        self.assertEqual(stats["official_hit_count"], 1)
        self.assertEqual(stats["compat_hit_count"], 0)
        self.assertEqual(stats["text_hit_count"], 0)
        self.assertEqual(stats["last_source"], "official")


if __name__ == "__main__":
    unittest.main()
