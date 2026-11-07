#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
import manifest as manifest_module
import render_env


class ExecEnvironmentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name).resolve()
        self.config = self.home / ".config/envy"
        self.config.mkdir(parents=True)
        self.environment = {"HOME": str(self.home), "PATH": "/usr/bin:/bin"}
        self.generated = self.config / "init-sh.env"
        self.generated.write_text("export TEST_GENERATED=current\n")

    def run_exec(self, *arguments: str, **kwargs) -> subprocess.CompletedProcess:
        return subprocess.run(
            [str(ROOT / "bin/envy"), "exec", *arguments],
            env=self.environment, cwd=self.home, text=True, capture_output=True,
            **kwargs,
        )

    def test_preserves_argv_stdin_cwd_and_exit_without_lock_or_profiles(self):
        (self.config / "state.lock.d").mkdir()
        (self.config / "state.lock.d/owner").write_text(f"occupied\n{os.getpid()}\n")
        for name in (".bashrc", ".bash_profile", ".profile", ".secrets"):
            (self.home / name).write_text("echo must-not-load >&2; exit 99\n")
        process = self.run_exec(
            "--", "/bin/bash", "--noprofile", "--norc", "-c",
            'printf "%s\\n" "$TEST_GENERATED" "$PWD" "$@"; cat; exit 23',
            "_", "--bundle", "a path", "--dry-run", "$(echo not-evaluated)", "",
            input="request body\n",
        )
        self.assertEqual(process.returncode, 23, process.stderr)
        self.assertEqual(process.stderr, "")
        self.assertEqual(process.stdout.splitlines(), [
            "current", str(self.home), "--bundle", "a path", "--dry-run",
            "$(echo not-evaluated)", "", "request body",
        ])
        self.assertEqual(sorted(p.name for p in self.config.iterdir()),
                         ["init-sh.env", "state.lock.d"])

    def test_missing_environment_and_command_fail_closed(self):
        self.assertEqual(self.run_exec().returncode, 2)
        self.generated.unlink()
        process = self.run_exec("/bin/echo", "must not execute")
        self.assertEqual(process.returncode, 1)
        self.assertEqual(process.stdout, "")
        self.assertIn("generated environment is missing", process.stderr)

    def test_path_precedence_order_quoting_dedup_and_no_implicit_mise(self):
        first = str(self.home / "first space's [*]")
        second = str(self.home / "second")
        self.generated.write_text(render_env.render({"features": {
            "owner": {"env": {"PATH^": [first, second]}},
        }}, {"features": {"owner": {"enabled": True, "status": "current"}}}, str(ROOT)))
        self.environment["PATH"] = f"/usr/bin:/bin:{first}:/usr/bin::"
        process = self.run_exec("bash", "-c", 'printf "%s" "$PATH"')
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(process.stdout.split(":"), [
            first, second, str(self.home / ".local/bin"), "/usr/bin", "/bin", "",
        ])

    def test_nested_exec_loads_new_environment_without_path_growth(self):
        self.generated.write_text("export TEST_GENERATED=old\nexport PATH=\"$PATH\":/extra\n")
        process = self.run_exec(
            "/bin/bash", "-c",
            'printf "export TEST_GENERATED=new\\nexport PATH=\\\"\\$PATH\\\":/extra\\n" > "$1"; '
            'exec "$2" exec /bin/bash -c \'printf "%s\\n%s\\n" "$TEST_GENERATED" "$PATH"\'',
            "_", str(self.generated), str(ROOT / "bin/envy"),
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(process.stdout.splitlines(), [
            "new", f"{self.home}/.local/bin:/usr/bin:/bin:/extra",
        ])

    def test_configured_roots_are_honored(self):
        alternate = self.home / "config space"
        alternate.mkdir()
        (alternate / "init-sh.env").write_text("export TEST_GENERATED=alternate\n")
        self.environment.update(ENVY_CONFIG_DIR=str(alternate),
                                ENVY_BIN_DIR=str(self.home / "bin space"))
        process = self.run_exec("/bin/bash", "-c", 'printf "%s\\n%s" "$TEST_GENERATED" "$PATH"')
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(process.stdout, f"alternate\n{self.home}/bin space:/usr/bin:/bin")

    def test_feature_trust_default_reaches_exec_and_preserves_caller_value(self):
        selected = str(self.home / "certificate path's [fixture].pem")
        self.generated.write_text(render_env.render({"features": {"trust": {}}},
            {"features": {"trust": {"enabled": True, "status": "current", "data": {
                "environment_defaults": {"NODE_EXTRA_CA_CERTS": selected}}}}}, str(ROOT)))
        process = self.run_exec("/bin/bash", "-uc", 'printf "%s" "$NODE_EXTRA_CA_CERTS"')
        self.assertEqual((process.returncode, process.stdout, process.stderr), (0, selected, ""))
        self.environment["NODE_EXTRA_CA_CERTS"] = str(self.home / "caller-cert.pem")
        process = self.run_exec("/bin/bash", "-uc", 'printf "%s" "$NODE_EXTRA_CA_CERTS"')
        self.assertEqual((process.returncode, process.stdout, process.stderr),
                         (0, self.environment["NODE_EXTRA_CA_CERTS"], ""))


class LifecycleEnvironmentTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="envy-lifecycle-path-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.core = self.root / "core"
        for folder in ("bin", "lib"):
            shutil.copytree(ROOT / folder, self.core / folder,
                            ignore=shutil.ignore_patterns("__pycache__"))
        self.home = self.root / "home"
        self.home.mkdir()
        self.generated = self.home / ".config/envy/init-sh.env"
        self.calls = self.home / "handler-calls.jsonl"
        self.system_bin = self.root / "system-bin"
        self.system_bin.mkdir()
        (self.system_bin / "python3").symlink_to(sys.executable)
        self.environment = {
            "HOME": str(self.home), "PATH": f"{self.system_bin}:/usr/bin:/bin",
            "SHELL": "/bin/bash", "ENVY_PLATFORM_OVERRIDE": "linux",
            "ENVY_SKIP_DEPENDENCY_PINS": "1", "PYTHONDONTWRITEBYTECODE": "1",
        }
        for name in (".bashrc", ".bash_profile", ".profile", ".secrets"):
            (self.home / name).write_text("echo must-not-load >&2; exit 99\n")
        handler = self.core / "crypto-check.py"
        handler.write_text(
            "import json, os, pathlib, sys\n"
            "sys.path.insert(0, str(pathlib.Path(os.environ['ENVY_ROOT']) / 'lib'))\n"
            "from secrets_sync.crypto import Crypto\n"
            "crypto = Crypto()\n"
            "home = pathlib.Path.home()\n"
            "assert pathlib.Path(crypto.sops) == home / 'installed/sops'\n"
            "assert pathlib.Path(crypto.age_keygen) == home / 'installed/age-keygen'\n"
            "marker = home / 'crypto-check-ready'\n"
            "if sys.argv[1] == 'verify' and not marker.exists(): sys.exit(1)\n"
            "if sys.argv[1] == 'converge': marker.touch()\n"
            "with (home / 'handler-calls.jsonl').open('a') as log:\n"
            "    log.write(json.dumps({'action': sys.argv[1], 'generated':\n"
            "        (home / '.config/envy/init-sh.env').exists()}) + '\\n')\n"
        )
        feature = {"description": "Installed crypto tool discovery", "tier": "core"}
        for action in ("converge", "verify", "doctor"):
            feature[action] = {"run": handler.name, "args": [action]}
        (self.core / "envy.json").write_text(json.dumps({
            "version": 3, "id": "envy-core", "features": {"crypto-check": feature},
        }))

    def install_tools(self, bin_dir):
        bin_dir.mkdir(parents=True)
        installed = self.home / "installed"
        installed.mkdir()
        for name in ("sops", "age-keygen"):
            tool = installed / name
            tool.write_text("#!/bin/sh\nexit 99\n")
            tool.chmod(0o755)
        mise = bin_dir / "mise"
        mise.write_text(
            f"#!{sys.executable}\n"
            "import os, pathlib, sys\n"
            "assert sys.argv[1:] in (['which', 'sops'], ['which', 'age-keygen'])\n"
            "assert os.environ['MISE_OFFLINE'] == '1'\n"
            "assert os.environ['MISE_AUTO_INSTALL'] == '0'\n"
            "print(pathlib.Path.home() / 'installed' / sys.argv[2])\n"
        )
        mise.chmod(0o755)

    def cli(self, *arguments):
        process = subprocess.run(
            [str(self.core / "bin/envy"), *arguments], env=self.environment,
            cwd=self.home, text=True, capture_output=True, timeout=45,
        )
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        return process

    def check_lifecycle(self):
        self.assertFalse(self.generated.exists())
        self.cli("install", "--preset", "core", "--yes")
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
        self.assertIn({"action": "converge", "generated": False}, calls)
        self.generated.unlink()
        self.cli("upgrade", "--only", "crypto-check", "--force")
        self.cli("verify", "--only", "crypto-check")
        self.cli("doctor", "crypto-check")
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
        self.assertEqual({item["action"] for item in calls}, {"converge", "verify", "doctor"})
        self.assertEqual(sum(item["action"] == "converge" for item in calls), 2)

    def test_installed_mise_tools_with_transport_path_and_no_generated_environment(self):
        self.install_tools(self.home / ".local/bin")
        self.check_lifecycle()

    def test_lifecycle_honors_configured_bin_directory_with_spaces(self):
        bin_dir = self.home / "managed bin's [tools]"
        self.environment["ENVY_BIN_DIR"] = str(bin_dir)
        self.install_tools(bin_dir)
        default_bin = self.home / ".local/bin"
        default_bin.mkdir(parents=True)
        (default_bin / "mise").write_text("#!/bin/sh\nexit 97\n")
        (default_bin / "mise").chmod(0o755)
        self.check_lifecycle()


class PrependEnvironmentTest(unittest.TestCase):
    def test_environment_defaults_are_quoted_data_and_only_fill_empty_values(self):
        with tempfile.TemporaryDirectory(prefix="envy-default-quoting-") as temporary:
            marker = Path(temporary) / "must-not-execute"
            value = f"literal's $HOME $(touch {marker}) `touch {marker}`\nsecond line"
            state = {"features": {"defaults": {"enabled": True, "status": "current",
                                               "data": {"environment_defaults": {"TEST_DEFAULT": value}}}}}
            script = render_env.render({"features": {"defaults": {}}}, state, str(ROOT))
            for inherited, expected in ((None, value), ("", value), ("caller value", "caller value")):
                environment = {"PATH": "/usr/bin:/bin"}
                if inherited is not None:
                    environment["TEST_DEFAULT"] = inherited
                process = subprocess.run(["/bin/bash", "-uc", script + 'printf "%s" "$TEST_DEFAULT"'],
                                         env=environment, text=True, capture_output=True)
                self.assertEqual((process.returncode, process.stdout, process.stderr), (0, expected, ""))
                self.assertFalse(marker.exists())
            for enabled, status in ((False, "current"), (True, "failed")):
                state["features"]["defaults"].update(enabled=enabled, status=status)
                self.assertNotIn("TEST_DEFAULT", render_env.render({"features": {"defaults": {}}}, state, str(ROOT)))

    def test_environment_defaults_reject_reserved_names_and_nonstring_values(self):
        invalid = [None, [], *({key: "value"} for key in
                   ("PATH", "HOME", "WORKSPACE_ROOT", "NVM_DIR", "NVM_BIN", "ENVY_ROOT",
                    "ENVY_NODE_BIN", "XDG_CONFIG_HOME", "INVALID-NAME", "1INVALID")),
                   *({"VALID_NAME": value} for value in (None, True, 23, [], {}, "bad\0value"))]
        for environment in invalid:
            with self.subTest(environment=environment):
                state = {"features": {"defaults": {"enabled": True, "status": "current",
                                                  "data": {"environment_defaults": environment}}}}
                with self.assertRaises(ValueError):
                    render_env.render({"features": {"defaults": {}}}, state, str(ROOT))

    def test_runtime_metadata_precedence_and_inactive_selection(self):
        features = {name: {} for name in ("nvm", "bundle-runtime", "disabled", "failed")}
        state = {name: {"enabled": name != "disabled",
                        "status": "failed" if name == "failed" else "current",
                        "data": {"node_runtime": {"backend": "system", "bin": f"/{name} bin's"}}}
                 for name in features}
        state["nvm"]["data"] = {"directory": "/managed/nvm", "node_runtime": {
            "backend": "nvm", "bin": "/managed/nvm/versions/node/v24.18.0/bin"}}
        script = render_env.render({"features": features}, {"features": state}, str(ROOT))
        process = subprocess.run(["/bin/bash", "-uc", script +
                                  'printf "%s\\n%s\\n%s" "$ENVY_NODE_BACKEND" "$ENVY_NODE_BIN" "$PATH"'],
                                 env={"PATH": "/system"}, text=True, capture_output=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(process.stdout.splitlines(), ["system", "/bundle-runtime bin's",
                         "/bundle-runtime bin's:/managed/nvm/versions/node/v24.18.0/bin:/system"])
        for inactive in ("disabled", "failed"):
            self.assertNotIn(f"/{inactive} bin's", process.stdout)

    def test_runtime_metadata_rejects_malformed_active_state(self):
        for invalid in (None, [], {}, {"backend": "system"},
                        {"backend": [], "bin": "/runtime"},
                        {"backend": "other", "bin": "/runtime"},
                        {"backend": "system", "bin": "/runtime", "PATH": "/override"},
                        *({"backend": "system", "bin": path} for path in
                          ("relative", "/runtime/../bin", "/runtime/./bin", "/runtime:bin",
                           "/runtime\nexport INJECTED=yes", "/runtime\x7f"))):
            with self.subTest(selection=invalid):
                state = {"features": {"runtime": {"enabled": True, "status": "current",
                                                   "data": {"node_runtime": invalid}}}}
                with self.assertRaises(ValueError):
                    render_env.render({"features": {"runtime": {}}}, state, str(ROOT))
                state["features"]["runtime"]["enabled"] = False
                render_env.render({"features": {"runtime": {}}}, state, str(ROOT))

    def test_nvm_default_is_only_rendered_while_active_and_current(self):
        for enabled, status in ((False, "current"), (True, "failed"), (True, "disabled")):
            script = render_env.render({"features": {"nvm": {}}}, {"features": {
                "nvm": {"enabled": enabled, "status": status,
                        "data": {"directory": "/managed/nvm"}}}}, str(ROOT))
            self.assertNotIn("ENVY_NVM_DIR", script)
            self.assertNotIn("ENVY_NODE_BIN", script)

    def test_later_feature_groups_win_and_disabled_or_failed_are_absent(self):
        features = {name: {"env": {key: value}} for name, key, value in [
            ("append", "PATH+", ["/append1", "/append2"]),
            ("first", "PATH^", ["/first1", "/first2"]),
            ("second", "PATH^", "/second"),
            ("disabled", "PATH^", "/disabled"),
            ("failed", "PATH^", "/failed"),
        ]}
        state = {name: {"enabled": name != "disabled", "status": "failed" if name == "failed" else "current"}
                 for name in features}
        script = render_env.render({"features": features}, {"features": state}, str(ROOT))
        process = subprocess.run(["/bin/bash", "-uc", script + 'printf "%s" "$PATH"'],
                                 env={"PATH": "/system"}, text=True, capture_output=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(process.stdout, "/second:/first1:/first2:/system:/append1:/append2")

    def test_prepend_does_not_add_empty_component_when_path_unset(self):
        script = render_env.render({"features": {"tool": {"env": {"PATH^": "/tool"}}}},
                                   {"features": {"tool": {"enabled": True, "status": "current"}}}, str(ROOT))
        process = subprocess.run(["/bin/bash", "-uc", "unset PATH\n" + script + 'printf "%s" "$PATH"'],
                                 text=True, capture_output=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(process.stdout, "/tool")

    def test_portable_prepend_validates_type_existence_and_containment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            owned = root / "bin"
            owned.mkdir()
            value = {"description": "Tool", "tier": "optional", "env": {"PATH^": [str(owned)]}}
            manifest_module.validate_feature("tool", value)
            manifest_module.validate_portable_feature("tool", value, root)
            for path, message in ((root / "missing", "missing"), (root.parent, "escapes")):
                value["env"]["PATH^"] = str(path)
                with self.assertRaisesRegex(manifest_module.ManifestError, message):
                    manifest_module.validate_portable_feature("tool", value, root)
            link = root / "escape"
            link.symlink_to(root.parent, target_is_directory=True)
            value["env"]["PATH^"] = str(link)
            with self.assertRaisesRegex(manifest_module.ManifestError, "escapes"):
                manifest_module.validate_portable_feature("tool", value, root)
            for invalid in ([], [12], {"path": "bin"}):
                value["env"]["PATH^"] = invalid
                with self.assertRaises(manifest_module.ManifestError):
                    manifest_module.validate_feature("tool", value)


if __name__ == "__main__":
    unittest.main()
