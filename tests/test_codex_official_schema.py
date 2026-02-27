import unittest

from parsers.codex_official_schema import parse_codex_official_status


class CodexOfficialSchemaTests(unittest.TestCase):
    def test_parse_waiting_event(self):
        lines = [
            '{"jsonrpc":"2.0","method":"turn/input_required","params":{"prompt":"Do you want to proceed?","choices":["1. Yes","2. No"]}}\n'
        ]
        result, unknown = parse_codex_official_status(lines)
        self.assertEqual(unknown, 0)
        self.assertIsNotNone(result)
        status, msg = result or ("", "")
        self.assertEqual(status, "WAITING")
        self.assertIn("Do you want to proceed", msg)

    def test_parse_completed_event(self):
        lines = ['{"jsonrpc":"2.0","method":"turn/completed","params":{"summary":"done"}}\n']
        result, unknown = parse_codex_official_status(lines)
        self.assertEqual(unknown, 0)
        self.assertEqual(result, ("IDLE", "AI 已完成回复"))

    def test_parse_running_event(self):
        lines = ['{"jsonrpc":"2.0","method":"turn/plan/updated","params":{"summary":"Planning files"}}\n']
        result, unknown = parse_codex_official_status(lines)
        self.assertEqual(unknown, 0)
        self.assertEqual(result, ("RUNNING", "Planning files"))

    def test_counts_unknown_official_methods(self):
        lines = ['{"jsonrpc":"2.0","method":"turn/unknown_custom","params":{"summary":"x"}}\n']
        result, unknown = parse_codex_official_status(lines)
        self.assertIsNone(result)
        self.assertEqual(unknown, 1)

    def test_ignores_non_official_json(self):
        lines = ['{"foo":"bar"}\n']
        result, unknown = parse_codex_official_status(lines)
        self.assertIsNone(result)
        self.assertEqual(unknown, 0)


if __name__ == "__main__":
    unittest.main()
