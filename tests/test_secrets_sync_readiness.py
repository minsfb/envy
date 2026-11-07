"""Declared provider policy must not require installed optional executables."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from secrets_sync.github_config import parse_github
from secrets_sync.git_config import parse_git
from secrets_sync.errors import ProviderUnavailable, SyncError
from secrets_sync.github_config import Executable


class ProviderPolicyTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="envy-provider-policy-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def test_missing_gh_network_helper_and_ca_are_runtime_readiness(self):
        with patch("subprocess.Popen", side_effect=AssertionError("unexpected child")):
            policy = parse_github({
                "executable": str(self.root / "missing-gh"),
                "config_directory": str(self.root / "gh"),
                "accounts": [{"host": "fixture.invalid", "account": "fixture"}],
                "network_command_prefix": [str(self.root / "missing-route")],
                "ca_file": str(self.root / "missing-ca"),
            }, repository=self.root / "repo", local=self.root / "local")
        self.assertEqual(policy.accounts, (("fixture.invalid", "fixture"),))
        self.assertFalse(policy.directory.exists())
        with self.assertRaises(ProviderUnavailable):
            policy.bind()

    def test_missing_native_git_helper_is_runtime_readiness(self):
        with patch("secrets_sync.git_config.sys.platform", "darwin"), patch(
                "secrets_sync.git_config.MAC_HELPER", str(self.root / "missing-helper")):
            policy = parse_git({"backend": "osxkeychain", "accounts": []})
        self.assertEqual(policy.backend, "osxkeychain")

    def test_missing_named_or_absolute_command_has_sanitized_diagnostic(self):
        for command in ("not-installed", str(self.root / "absent")):
            with patch("secrets_sync.github_config.shutil.which", return_value=None):
                with self.assertRaises(ProviderUnavailable) as failure:
                    Executable.resolve(command)
            self.assertNotIn(command, str(failure.exception))
            self.assertIn("install", str(failure.exception))

    def test_policy_syntax_is_rejected_even_if_tool_is_missing(self):
        for command in ("gh --flag", "./relative/gh", "/a/../b"):
            with self.assertRaises(SyncError):
                parse_github({"executable": command, "config_directory": str(self.root / "gh"),
                              "accounts": []}, repository=self.root / "repo", local=self.root / "local")

    def test_native_git_policy_is_declarative_on_linux_but_binding_is_blocked(self):
        with patch("secrets_sync.git_config.sys.platform", "linux"):
            policy = parse_git({"backend": "osxkeychain", "accounts": []})
            with self.assertRaises(ProviderUnavailable):
                policy.bind()


if __name__ == "__main__":
    unittest.main()
