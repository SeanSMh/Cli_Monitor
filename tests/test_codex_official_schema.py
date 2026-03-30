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
        self.assertEqual(status, "WAITING_INPUT")
        self.assertIn("Do you want to proceed", msg)

    def test_parse_completed_event(self):
        lines = ['{"jsonrpc":"2.0","method":"turn/completed","params":{"summary":"done"}}\n']
        result, unknown = parse_codex_official_status(lines)
        self.assertEqual(unknown, 0)
        self.assertEqual(result, ("IDLE", "done"))

    def test_parse_running_event(self):
        lines = ['{"jsonrpc":"2.0","method":"turn/plan/updated","params":{"summary":"Planning files"}}\n']
        result, unknown = parse_codex_official_status(lines)
        self.assertEqual(unknown, 0)
        self.assertEqual(result, ("RUNNING", "Planning files"))

    def test_parse_thread_status_changed_waiting_approval(self):
        lines = [
            '{"jsonrpc":"2.0","method":"thread/status/changed","params":{"thread":{"id":"thr_1","status":{"type":"active","activeFlags":["waitingOnApproval"]},"title":"server needs your approval."}}}\n'
        ]
        result, unknown = parse_codex_official_status(lines)
        self.assertEqual(unknown, 0)
        self.assertEqual(result, ("WAITING_APPROVAL", "server needs your approval."))

    def test_parse_thread_started_without_meaningful_text_uses_idle_fallback(self):
        lines = [
            '{"jsonrpc":"2.0","method":"thread/started","params":{"thread":{"id":"thr_1","preview":"","modelProvider":"openai","source":"vscode","status":{"type":"idle"}}}}\n'
        ]
        result, unknown = parse_codex_official_status(lines)
        self.assertEqual(unknown, 0)
        self.assertEqual(result, ("IDLE", "AI 已完成回复"))

    def test_parse_thread_status_not_loaded_maps_idle(self):
        lines = [
            '{"jsonrpc":"2.0","method":"thread/status/changed","params":{"threadId":"thr_1","status":{"type":"notLoaded"}}}\n'
        ]
        result, unknown = parse_codex_official_status(lines)
        self.assertEqual(unknown, 0)
        self.assertEqual(result, ("IDLE", "线程未加载"))

    def test_parse_command_approval_request(self):
        lines = [
            '{"jsonrpc":"2.0","id":8,"method":"item/commandExecution/requestApproval","params":{"command":"pwd","reason":"Need your approval to run pwd."}}\n'
        ]
        result, unknown = parse_codex_official_status(lines)
        self.assertEqual(unknown, 0)
        self.assertEqual(result, ("WAITING_APPROVAL", "Need your approval to run pwd."))

    def test_parse_mcp_elicitation_request_with_approval_meta(self):
        lines = [
            '{"jsonrpc":"2.0","id":9,"method":"mcpServer/elicitation/request","params":{"threadId":"thr_1","turnId":"turn_1","serverName":"approval-server","mode":"form","message":"Approve MCP tool call?","requestedSchema":{"type":"object"},"_meta":{"codex_approval_kind":"mcp_tool_call"}}}\n'
        ]
        result, unknown = parse_codex_official_status(lines)
        self.assertEqual(unknown, 0)
        self.assertEqual(result, ("WAITING_APPROVAL", "Approve MCP tool call?"))

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
