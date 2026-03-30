import unittest

from engine.models import MonitorEvent
from engine.reducer import reduce_event


class ReducerTests(unittest.TestCase):
    def test_thread_status_changed_maps_waiting_approval(self):
        event = MonitorEvent(
            source="codex_proxy",
            session_id="codex_1",
            tool_name="codex",
            event_type="thread/status/changed",
            payload={
                "thread": {
                    "id": "thr_1",
                    "status": {"type": "active", "activeFlags": ["waitingOnApproval"]},
                    "title": "mcp server needs your approval.",
                }
            },
            ts_ms=10,
        )
        state = reduce_event(None, event)
        self.assertIsNotNone(state)
        self.assertEqual(state.status, "WAITING_APPROVAL")
        self.assertEqual(state.thread_id, "thr_1")

    def test_thread_status_changed_maps_waiting_input(self):
        event = MonitorEvent(
            source="codex_proxy",
            session_id="codex_1",
            tool_name="codex",
            event_type="thread/status/changed",
            payload={"thread": {"status": {"type": "active", "activeFlags": ["waitingOnUserInput"]}}},
            ts_ms=10,
        )
        state = reduce_event(None, event)
        self.assertIsNotNone(state)
        self.assertEqual(state.status, "WAITING_INPUT")

    def test_item_completed_does_not_end_turn(self):
        prev = reduce_event(
            None,
            MonitorEvent(
                source="codex_proxy",
                session_id="codex_1",
                tool_name="codex",
                event_type="turn/started",
                payload={"summary": "working"},
                ts_ms=10,
            ),
        )
        state = reduce_event(
            prev,
            MonitorEvent(
                source="codex_proxy",
                session_id="codex_1",
                tool_name="codex",
                event_type="item/completed",
                payload={"summary": "item done"},
                ts_ms=20,
            ),
        )
        self.assertIsNotNone(state)
        self.assertEqual(state.status, "RUNNING")

    def test_thread_started_ignores_model_provider_as_message(self):
        event = MonitorEvent(
            source="codex_proxy",
            session_id="codex_1",
            tool_name="codex",
            event_type="thread/started",
            payload={
                "thread": {
                    "id": "thr_1",
                    "preview": "",
                    "modelProvider": "openai",
                    "source": "vscode",
                    "status": {"type": "idle"},
                }
            },
            ts_ms=10,
        )
        state = reduce_event(None, event)
        self.assertIsNotNone(state)
        self.assertEqual(state.status, "IDLE")
        self.assertEqual(state.message, "AI 已完成回复")

    def test_thread_status_not_loaded_maps_idle(self):
        event = MonitorEvent(
            source="codex_proxy",
            session_id="codex_1",
            tool_name="codex",
            event_type="thread/status/changed",
            payload={"threadId": "thr_1", "status": {"type": "notLoaded"}},
            ts_ms=10,
        )
        state = reduce_event(None, event)
        self.assertIsNotNone(state)
        self.assertEqual(state.status, "IDLE")
        self.assertEqual(state.message, "线程未加载")

    def test_command_approval_request_maps_waiting_approval(self):
        event = MonitorEvent(
            source="codex_proxy",
            session_id="codex_1",
            tool_name="codex",
            event_type="item/commandExecution/requestApproval",
            payload={"command": "pwd", "reason": "Need your approval to run pwd."},
            ts_ms=10,
        )
        state = reduce_event(None, event)
        self.assertIsNotNone(state)
        self.assertEqual(state.status, "WAITING_APPROVAL")
        self.assertIn("approval", state.message.lower())

    def test_request_user_input_maps_waiting_input(self):
        event = MonitorEvent(
            source="codex_proxy",
            session_id="codex_1",
            tool_name="codex",
            event_type="item/tool/requestUserInput",
            payload={"questions": [{"id": "q1", "question": "Which environment should I use?"}]},
            ts_ms=10,
        )
        state = reduce_event(None, event)
        self.assertIsNotNone(state)
        self.assertEqual(state.status, "WAITING_INPUT")
        self.assertIn("Which environment", state.message)

    def test_mcp_elicitation_with_approval_meta_maps_waiting_approval(self):
        event = MonitorEvent(
            source="codex_proxy",
            session_id="codex_1",
            tool_name="codex",
            event_type="mcpServer/elicitation/request",
            payload={
                "threadId": "thr_1",
                "message": "Allow MCP tool call?",
                "_meta": {"codex_approval_kind": "mcp_tool_call"},
            },
            ts_ms=10,
        )
        state = reduce_event(None, event)
        self.assertIsNotNone(state)
        self.assertEqual(state.status, "WAITING_APPROVAL")
        self.assertIn("Allow MCP tool call", state.message)

    def test_server_request_resolved_restores_previous_running_state(self):
        running = reduce_event(
            None,
            MonitorEvent(
                source="codex_proxy",
                session_id="codex_1",
                tool_name="codex",
                event_type="turn/started",
                payload={"summary": "Planning files"},
                ts_ms=10,
            ),
        )
        waiting = reduce_event(
            running,
            MonitorEvent(
                source="codex_proxy",
                session_id="codex_1",
                tool_name="codex",
                event_type="item/commandExecution/requestApproval",
                payload={"threadId": "thr_1", "command": "pwd", "reason": "Need approval."},
                meta={"request_id": 8},
                ts_ms=20,
            ),
        )
        state = reduce_event(
            waiting,
            MonitorEvent(
                source="codex_proxy",
                session_id="codex_1",
                tool_name="codex",
                event_type="serverRequest/resolved",
                payload={"threadId": "thr_1", "requestId": 8},
                ts_ms=30,
            ),
        )
        self.assertIsNotNone(state)
        self.assertEqual(state.status, "RUNNING")
        self.assertEqual(state.message, "Planning files")
        self.assertNotIn("pending_request_id", state.meta)

    def test_turn_completed_uses_idle_fallback_when_only_ids_are_present(self):
        state = reduce_event(
            None,
            MonitorEvent(
                source="codex_proxy",
                session_id="codex_1",
                tool_name="codex",
                event_type="turn/completed",
                payload={"threadId": "thr_1", "turn": {"id": "turn_1", "status": "completed"}},
                ts_ms=10,
            ),
        )
        self.assertIsNotNone(state)
        self.assertEqual(state.status, "IDLE")
        self.assertEqual(state.message, "AI 已完成回复")

    def test_turn_completed_preserves_last_meaningful_message(self):
        prev = reduce_event(
            None,
            MonitorEvent(
                source="codex_proxy",
                session_id="codex_1",
                tool_name="codex",
                event_type="item/completed",
                payload={"message": "Finished fetching status code."},
                ts_ms=10,
            ),
        )
        state = reduce_event(
            prev,
            MonitorEvent(
                source="codex_proxy",
                session_id="codex_1",
                tool_name="codex",
                event_type="turn/completed",
                payload={"threadId": "thr_1", "turn": {"id": "turn_1", "status": "completed"}},
                ts_ms=20,
            ),
        )
        self.assertIsNotNone(state)
        self.assertEqual(state.status, "IDLE")
        self.assertEqual(state.message, "Finished fetching status code.")


if __name__ == "__main__":
    unittest.main()
