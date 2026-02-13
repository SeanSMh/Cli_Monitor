import unittest

from terminal_adapters import FocusResult, TerminalFocusService


class _Adapter:
    def __init__(self, name, result=None, error=None):
        self.name = name
        self._result = result
        self._error = error

    def focus_by_tty(self, tty):
        if self._error is not None:
            raise self._error
        return bool(self._result)


class TerminalFocusServiceTests(unittest.TestCase):
    def test_empty_tty_returns_failure(self):
        service = TerminalFocusService(adapters=[_Adapter("A", result=True)])
        result = service.focus_by_tty("")
        self.assertFalse(result.success)
        self.assertEqual(result.reason, "empty tty")

    def test_first_matching_adapter_wins(self):
        service = TerminalFocusService(
            adapters=[_Adapter("A", result=True), _Adapter("B", result=True)]
        )
        result = service.focus_by_tty("/dev/ttys001")
        self.assertTrue(result.success)
        self.assertEqual(result.provider, "A")

    def test_fallback_to_next_adapter(self):
        service = TerminalFocusService(
            adapters=[_Adapter("A", result=False), _Adapter("B", result=True)]
        )
        result = service.focus_by_tty("/dev/ttys001")
        self.assertTrue(result.success)
        self.assertEqual(result.provider, "B")

    def test_collect_adapter_errors_in_reason(self):
        service = TerminalFocusService(
            adapters=[
                _Adapter("A", error=RuntimeError("boom")),
                _Adapter("B", result=False),
            ]
        )
        result = service.focus_by_tty("/dev/ttys001")
        self.assertIsInstance(result, FocusResult)
        self.assertFalse(result.success)
        self.assertIn("A:", result.reason)
        self.assertIn("B:", result.reason)


if __name__ == "__main__":
    unittest.main()
