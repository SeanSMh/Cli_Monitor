import unittest

from codex_event_parser import parse_codex_structured_status


class CodexEventParserTests(unittest.TestCase):
    def test_returns_none_when_no_json_events(self):
        result = parse_codex_structured_status(["normal output line\n"])
        self.assertIsNone(result)

    def test_detects_waiting_from_event_type_and_prompt(self):
        lines = [
            '{"type":"turn.waiting_for_input","prompt":"Do you want to proceed?","options":["1. Yes","2. No"]}\n'
        ]
        status, msg = parse_codex_structured_status(lines) or ("", "")
        self.assertEqual(status, "WAITING")
        self.assertIn("Do you want to proceed", msg)

    def test_detects_waiting_from_menu_lines(self):
        lines = [
            '{"msg":{"type":"turn.progress"},"text":"Do you want to make this edit?\\n❯ 1. Yes\\n  2. No"}\n'
        ]
        status, msg = parse_codex_structured_status(lines) or ("", "")
        self.assertEqual(status, "WAITING")
        self.assertIn("Do you want to make this edit", msg)

    def test_detects_waiting_from_parenthesized_menu_lines(self):
        lines = [
            '{"msg":{"type":"turn.progress"},"text":"Pick one\\n❯ 1) Yes\\n  2) No"}\n'
        ]
        status, msg = parse_codex_structured_status(lines) or ("", "")
        self.assertEqual(status, "WAITING")
        self.assertIn("Pick one", msg)

    def test_detects_idle_from_completed_event(self):
        lines = ['{"event":{"type":"turn.completed"},"message":"done"}\n']
        status, msg = parse_codex_structured_status(lines) or ("", "")
        self.assertEqual(status, "IDLE")
        self.assertEqual(msg, "AI 已完成回复")

    def test_parses_sse_data_prefix(self):
        lines = ['data: {"type":"turn.started","message":"Planning"}\n']
        status, msg = parse_codex_structured_status(lines) or ("", "")
        self.assertEqual(status, "RUNNING")
        self.assertIn("Planning", msg)


if __name__ == "__main__":
    unittest.main()
