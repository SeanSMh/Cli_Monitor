import unittest

from app_server_event_parser import parse_app_server_status


class AppServerEventParserTests(unittest.TestCase):
    def test_returns_none_when_no_json_event(self):
        self.assertIsNone(parse_app_server_status(["plain terminal output\n"]))

    def test_detects_waiting_from_method_and_prompt(self):
        lines = [
            '{"jsonrpc":"2.0","method":"turn/input_required","params":{"prompt":"Do you want to proceed?","choices":["1. Yes","2. No"]}}\n'
        ]
        status, msg = parse_app_server_status(lines) or ("", "")
        self.assertEqual(status, "WAITING")
        self.assertIn("Do you want to proceed", msg)

    def test_detects_waiting_from_parenthesized_choices(self):
        lines = [
            '{"jsonrpc":"2.0","method":"turn/input_required","params":{"prompt":"Pick one","choices":["❯ 1) Yes","2) No"]}}\n'
        ]
        status, msg = parse_app_server_status(lines) or ("", "")
        self.assertEqual(status, "WAITING")
        self.assertIn("Pick one", msg)

    def test_detects_waiting_from_single_line_confirm_prompt(self):
        lines = [
            '{"jsonrpc":"2.0","method":"turn/input_required","params":{"prompt":"Proceed (y/n)"}}\n'
        ]
        status, msg = parse_app_server_status(lines) or ("", "")
        self.assertEqual(status, "WAITING")
        self.assertEqual(msg, "Proceed (y/n)")

    def test_detects_completed_event(self):
        lines = ['{"jsonrpc":"2.0","method":"turn/completed","params":{"message":"finished"}}\n']
        status, msg = parse_app_server_status(lines) or ("", "")
        self.assertEqual(status, "IDLE")
        self.assertEqual(msg, "AI 已完成回复")

    def test_detects_running_plan_update(self):
        lines = [
            '{"jsonrpc":"2.0","method":"turn/plan/updated","params":{"summary":"Planning migration steps"}}\n'
        ]
        status, msg = parse_app_server_status(lines) or ("", "")
        self.assertEqual(status, "RUNNING")
        self.assertIn("Planning migration steps", msg)

    def test_parses_data_prefix(self):
        lines = ['data: {"jsonrpc":"2.0","method":"turn/diff/updated","params":{"message":"Diff refreshed"}}\n']
        status, msg = parse_app_server_status(lines) or ("", "")
        self.assertEqual(status, "RUNNING")
        self.assertIn("Diff refreshed", msg)


if __name__ == "__main__":
    unittest.main()
