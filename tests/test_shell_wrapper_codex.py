import os
import subprocess
import unittest


ROOT = "/Users/sqb/projects/cli-monitor"


class ShellWrapperCodexTests(unittest.TestCase):
    def test_sourcing_wrapper_clears_old_codex_alias(self):
        script = """
alias codex='echo old'
source shell/cli_monitor.sh
type codex
"""
        res = subprocess.run(
            ["zsh", "-fc", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            env=os.environ.copy(),
        )
        self.assertEqual(res.returncode, 0, msg=res.stderr)
        self.assertIn("shell function", res.stdout)
        self.assertNotIn("alias for", res.stdout)


if __name__ == "__main__":
    unittest.main()
