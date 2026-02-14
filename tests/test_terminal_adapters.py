import unittest

from terminal_adapters import FocusResult, SessionMeta, TerminalFocusService


class _Adapter:
    def __init__(self, name, result=None, error=None, matched=True):
        self.name = name
        self._result = result
        self._error = error
        self._matched = matched

    def match(self, meta):
        return self._matched

    def focus(self, meta):
        if self._error is not None:
            raise self._error
        return bool(self._result)


class TerminalFocusServiceTests(unittest.TestCase):
    def test_empty_meta_returns_failure(self):
        service = TerminalFocusService(adapters=[_Adapter("A", result=True)])
        result = service.focus(SessionMeta())
        self.assertFalse(result.success)
        self.assertEqual(result.reason, "missing tty/meta")

    def test_first_matching_adapter_wins(self):
        service = TerminalFocusService(
            adapters=[_Adapter("A", result=True), _Adapter("B", result=True)]
        )
        result = service.focus(SessionMeta(term_program="iterm.app", tty="/dev/ttys001"))
        self.assertTrue(result.success)
        self.assertEqual(result.provider, "A")

    def test_fallback_to_next_adapter_when_first_fails(self):
        service = TerminalFocusService(
            adapters=[_Adapter("A", result=False), _Adapter("B", result=True)]
        )
        result = service.focus(SessionMeta(term_program="iterm.app", tty="/dev/ttys001"))
        self.assertTrue(result.success)
        self.assertEqual(result.provider, "B")

    def test_collect_adapter_errors_in_reason(self):
        service = TerminalFocusService(
            adapters=[
                _Adapter("A", error=RuntimeError("boom")),
                _Adapter("B", result=False),
            ]
        )
        result = service.focus(SessionMeta(term_program="iterm.app", tty="/dev/ttys001"))
        self.assertIsInstance(result, FocusResult)
        self.assertFalse(result.success)
        self.assertIn("A:", result.reason)
        self.assertIn("B:", result.reason)

    def test_focus_by_tty_compatibility_entrypoint(self):
        service = TerminalFocusService(adapters=[_Adapter("A", result=True)])
        result = service.focus_by_tty("/dev/ttys001")
        self.assertTrue(result.success)
        self.assertEqual(result.provider, "A")


if __name__ == "__main__":
    unittest.main()
