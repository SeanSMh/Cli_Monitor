import unittest

from terminal_adapters import (
    FocusResult,
    SessionMeta,
    TerminalFocusService,
    VSCodeTerminalAdapter,
    _bundle_matches_vscode_family,
)


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

    def test_vscode_adapter_skips_jetbrains_android_studio_hint(self):
        adapter = VSCodeTerminalAdapter()
        meta = SessionMeta(
            term_program="vscode",
            vscode_pid="123",
            terminal_emulator="JetBrains-JediTerm",
            jetbrains_ide_name="Android Studio",
        )
        self.assertFalse(adapter.match(meta))

    def test_vscode_adapter_skips_cursor_hint(self):
        adapter = VSCodeTerminalAdapter()
        meta = SessionMeta(
            term_program="vscode",
            vscode_pid="123",
            vscode_ipc_hook_cli="/Users/x/Library/Application Support/Cursor/cursor.sock",
        )
        self.assertFalse(adapter.match(meta))

    def test_vscode_adapter_skips_windsurf_hint(self):
        adapter = VSCodeTerminalAdapter()
        meta = SessionMeta(
            term_program="vscode",
            vscode_pid="123",
            vscode_git_askpass_main="/Applications/Windsurf.app/Contents/Resources/app/extensions/git/dist/askpass-main.js",
        )
        self.assertFalse(adapter.match(meta))

    def test_vscode_adapter_skips_trae_hint(self):
        adapter = VSCodeTerminalAdapter()
        meta = SessionMeta(
            term_program="vscode",
            vscode_pid="123",
            vscode_ipc_hook_cli="/Users/x/Library/Application Support/Trae/1.0-main.sock",
        )
        self.assertFalse(adapter.match(meta))

    def test_vscode_family_key_detects_insiders_and_codium(self):
        insiders = SessionMeta(
            term_program="vscode",
            vscode_git_askpass_main="/Applications/Visual Studio Code - Insiders.app/Contents/Resources/app/extensions/git/dist/askpass-main.js",
        )
        codium = SessionMeta(
            term_program="vscode",
            vscode_ipc_hook_cli="/Users/x/Library/Application Support/VSCodium/1.99-main.sock",
        )
        self.assertEqual(insiders.vscode_family_key, "vscode_insiders")
        self.assertEqual(codium.vscode_family_key, "vscodium")

    def test_bundle_family_match_rejects_antigravity_for_cursor(self):
        self.assertFalse(
            _bundle_matches_vscode_family(
                "/Applications/Antigravity.app",
                "cursor",
            )
        )

    def test_bundle_family_match_accepts_cursor_bundle(self):
        self.assertTrue(
            _bundle_matches_vscode_family(
                "/Applications/Cursor.app",
                "cursor",
            )
        )


if __name__ == "__main__":
    unittest.main()
