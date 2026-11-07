#!/usr/bin/env python3
from __future__ import annotations
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("nvm_feature", ROOT / "features/nvm.py")
nvm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nvm)


class NvmLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.target = self.home / ".nvm"
        self.state = self.home / "state"
        self.environment = patch.dict(os.environ, {
            "HOME": str(self.home), "ENVY_NVM_DIR": str(self.target),
            "ENVY_STATE_DIR": str(self.state), "ENVY_STATE_FILE": str(self.home / "state.json"),
            "ENVY_DRY_RUN": "false"}, clear=False)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.content = b"# synthetic nvm fixture\n"
        self.item = {"version": "1.2.3", "node_version": "24.18.0", "url": "https://fixture.invalid/nvm",
                     "files": {"nvm.sh": hashlib.sha256(self.content).hexdigest()}}
        mock = patch.object(nvm, "pin", return_value=self.item)
        mock.start()
        self.addCleanup(mock.stop)

    def install(self):
        def download(url, dest):
            dest.write_bytes(self.content)
        def run(script, *args, **kwargs):
            if kwargs.get("download"):
                (self.target / "alias").mkdir(exist_ok=True)
                (self.target / "alias/default").write_text("24.18.0\n")
        with patch.object(nvm.provision, "download", side_effect=download), patch.object(nvm, "run_nvm", side_effect=run):
            nvm.converge()

    def test_adoption_repeat_convergence_and_removal_preserve_user_data(self):
        self.target.mkdir()
        original = self.target / "nvm.sh"
        original.write_text("# previous user installation\n")
        package = self.target / "versions/node/v22/lib/node_modules/user-tool/package.json"
        package.parent.mkdir(parents=True)
        package.write_text('{}')
        self.install()
        self.install()
        nvm.remove()
        nvm.remove()
        self.assertEqual(original.read_text(), "# previous user installation\n")
        self.assertTrue(package.exists())

    def test_checksum_failure_does_not_mutate_installation(self):
        def download(url, dest):
            dest.write_bytes(b"incorrect upstream content")
        with patch.object(nvm.provision, "download", side_effect=download):
            with self.assertRaisesRegex(RuntimeError, "checksum mismatch"):
                nvm.converge()
        self.assertFalse(self.target.exists())

    def test_reenable_backs_up_the_new_user_state(self):
        self.install()
        nvm.remove()
        (self.target / "nvm.sh").write_text("# new user setup\n")
        self.install()
        nvm.remove()
        self.assertEqual((self.target / "nvm.sh").read_text(), "# new user setup\n")

    def test_remove_keeps_user_edits_and_dry_run_writes_nothing(self):
        with patch.dict(os.environ, {"ENVY_DRY_RUN": "true"}):
            nvm.converge()
        self.assertFalse(self.target.exists())
        self.install()
        (self.target / "nvm.sh").write_text("user edit")
        nvm.remove()
        self.assertEqual((self.target / "nvm.sh").read_text(), "user edit")

    def test_verified_convergence_records_default_runtime_for_rendering(self):
        state_file = self.home / "state.json"
        state_file.write_text(json.dumps({"features": {"nvm": {"enabled": True}}}))
        self.install()
        data = json.loads(state_file.read_text())["features"]["nvm"]["data"]
        self.assertEqual(data, {"directory": str(self.target), "node_runtime": {
            "backend": "nvm", "bin": str(self.target / "versions/node/v24.18.0/bin")}})

    def test_runtime_uses_project_or_default_and_refuses_missing_version(self):
        self.target.mkdir()
        (self.target / "nvm.sh").write_text('''
nvm_find_nvmrc() { [ ! -f .nvmrc ] || printf '%s/.nvmrc' "$PWD"; }
nvm() {
  case "$1" in
    use)
      if [ -f .nvmrc ]; then
        test "$(cat .nvmrc)" != missing || return 1
        export SELECTED_NODE="$(cat .nvmrc)"
      else export SELECTED_NODE=default; fi ;;
    deactivate) unset SELECTED_NODE ;;
  esac
}
''')
        env = dict(os.environ, ENVY_ROOT=str(ROOT), PATH="/usr/bin:/bin")
        command = [str(ROOT / "bin/nv-node"), "sh", "-c", 'printf "%s" "$SELECTED_NODE"']
        first = subprocess.run(command, cwd=self.home, env=env, capture_output=True, text=True)
        self.assertEqual((first.returncode, first.stdout), (0, "default"))
        (self.home / ".nvmrc").write_text("22.0.0")
        second = subprocess.run(command, cwd=self.home, env=env, capture_output=True, text=True)
        self.assertEqual((second.returncode, second.stdout), (0, "22.0.0"))
        (self.home / ".nvmrc").write_text("missing")
        third = subprocess.run(command, cwd=self.home, env=env, capture_output=True, text=True)
        self.assertNotEqual(third.returncode, 0)
        self.assertEqual(third.stdout, "")

    def test_feature_selection_and_mise_rendering(self):
        state_file = self.home / "state.json"
        self.assertFalse(nvm.selected())
        state_file.write_text(json.dumps({"features": {"nvm": {"enabled": True}}}))
        self.assertTrue(nvm.selected())
        chezmoi = shutil.which("chezmoi")
        if not chezmoi:
            self.skipTest("chezmoi is not installed")
        template = (ROOT / "chezmoi/dot_config/mise/config.toml.tmpl").read_text()
        for enabled in (True, False):
            result = subprocess.run([chezmoi, "execute-template", "--source", str(ROOT / "chezmoi"),
                                     "--override-data", json.dumps({"nvmEnabled": enabled})],
                                    input=template, capture_output=True, text=True, check=True)
            self.assertEqual('node   = "24.18.0"' in result.stdout, not enabled)


class NativeNodeEnvironmentTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="envy-native-node-")
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.target = self.home / "nvm directory's [fixture]"
        self.target.mkdir()
        tools = self.home / "tools"
        tools.mkdir()
        (tools / "python3").symlink_to(sys.executable)
        self.environment = {"HOME": str(self.home), "PATH": f"{tools}:/usr/bin:/bin",
                            "ENVY_ROOT": str(ROOT), "ENVY_NVM_DIR": str(self.target),
                            "UNRELATED_FIXTURE_SETTING": "must-not-export"}
        self.project = self.home / "project/subdirectory"
        self.project.mkdir(parents=True)
        self.default_bin = self.runtime(self.target / "versions/node/v24.18.0/bin", "v24.18.0")
        self.project_bin = self.runtime(self.target / "versions/node/v22.0.0/bin", "v22.0.0")
        (self.target / "nvm.sh").write_text('''
nvm_find_nvmrc() { nv_node_find_project; }
nvm() {
  case "$1" in
    use)
      version=24.18.0
      if [ "${3:-}" != default ]; then
        requested="$(nvm_find_nvmrc)"
        if [ -n "$requested" ]; then IFS= read -r version < "$requested"; fi
      fi
      [ -x "$NVM_DIR/versions/node/v$version/bin/node" ] || return 1
      export NVM_BIN="$NVM_DIR/versions/node/v$version/bin"
      if [ "${NVM_FIXTURE_KEEP_ORDER:-}" != 1 ]; then export PATH="$NVM_BIN:$PATH"; fi
      echo 'synthetic nvm selection diagnostic' ;;
    deactivate) unset NVM_BIN ;;
    *) return 99 ;;
  esac
}
''')

    def runtime(self, directory, version):
        directory.mkdir(parents=True)
        node = directory / "node"
        node.write_text(
            f"#!{sys.executable}\n"
            "import pathlib, sys\n"
            f"version = {version!r}\n"
            "arguments = sys.argv[1:]\n"
            "if arguments == ['--version']: print(version)\n"
            "elif arguments and pathlib.Path(arguments[0]).name == 'npm':\n"
            "    if arguments[1:] == ['--version']: print('11.16.0')\n"
            "    else:\n"
            "        print(version)\n"
            "        print(repr(arguments[1:]))\n"
            "        sys.stdout.write(sys.stdin.read())\n"
            "        print('native fixture stderr', file=sys.stderr)\n"
            "        sys.exit(23)\n"
            "else: sys.exit(98)\n"
        )
        node.chmod(0o755)
        npm = directory / "npm"
        npm.write_text("#!/usr/bin/env node\n// Synthetic npm entry point.\n")
        npm.chmod(0o755)
        return directory

    def launch(self, *arguments, **kwargs):
        return subprocess.run([str(ROOT / "bin/nv-node"), *arguments],
                              env=self.environment, cwd=self.project,
                              capture_output=True, text=True, timeout=10, **kwargs)

    def export(self, *arguments):
        result = self.launch("--env-json", *arguments)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def assert_record(self, record, backend, directory):
        self.assertEqual(set(record), {"version", "backend", "node", "npm",
                                      "node_version", "npm_version", "env"})
        self.assertEqual(record["version"], 1)
        self.assertEqual(record["backend"], backend)
        self.assertEqual(record["node"], str(directory / "node"))
        self.assertEqual(record["npm"], str(directory / "npm"))
        self.assertEqual(record["env"]["PATH"].split(":")[0], str(directory))
        self.assertEqual(set(record["env"]), {"PATH", "NVM_DIR", "NVM_BIN"}
                         if backend == "nvm" else {"PATH"})

    def test_project_and_default_export_match_native_consumer(self):
        (self.project.parent / ".nvmrc").write_text("22.0.0\n")
        record = self.export()
        self.assert_record(record, "nvm", self.project_bin)
        self.assertEqual(record["node_version"], "v22.0.0")
        self.assertEqual(record["npm_version"], "11.16.0")
        self.assertEqual(record["env"]["NVM_DIR"], str(self.target))
        self.assertEqual(record["env"]["NVM_BIN"], str(self.project_bin))
        native = subprocess.run([record["npm"], "a path", "--", "$(not-evaluated)"],
                                env={**self.environment, **record["env"]}, cwd=self.project,
                                input="native stdin\n", text=True, capture_output=True, timeout=10)
        self.assertEqual(native.returncode, 23)
        self.assertEqual(native.stdout, "v22.0.0\n['a path', '--', '$(not-evaluated)']\nnative stdin\n")
        self.assertEqual(native.stderr, "native fixture stderr\n")
        default = self.export("--default")
        self.assert_record(default, "nvm", self.default_bin)
        self.assertEqual(default["node_version"], "v24.18.0")

    def test_unavailable_project_fails_before_export_or_consumer(self):
        (self.project.parent / ".nvmrc").write_text("missing\n")
        for arguments in (("--env-json",), ("/bin/echo", "must-not-execute")):
            result = self.launch(*arguments)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            self.assertIn("Project Node is unavailable", result.stderr)
        self.assert_record(self.export("--default"), "nvm", self.default_bin)

    def test_export_prefixes_and_deduplicates_nvm_bin_after_utility_directories(self):
        inherited = self.environment["PATH"]
        self.environment.update(PATH=f"{inherited}:{self.default_bin}::{self.default_bin}",
                                NVM_FIXTURE_KEEP_ORDER="1")
        record = self.export("--default")
        self.assert_record(record, "nvm", self.default_bin)
        self.assertEqual(record["env"]["PATH"], f"{self.default_bin}:{inherited}:")

    def test_explicit_system_does_not_require_nvm_and_rejects_project_intent(self):
        system = self.runtime(self.home / "system bin", "v20.0.0")
        (self.target / "nvm.sh").unlink()
        record = self.export("--system-bin", str(system))
        self.assert_record(record, "system", system)
        (self.project.parent / ".nvmrc").write_text("20.0.0\n")
        result = self.launch("--env-json", "--system-bin", str(system))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("cannot satisfy a project .nvmrc", result.stderr)
        self.assert_record(self.export("--default", "--system-bin", str(system)), "system", system)

    def test_invalid_arguments_missing_npm_and_failed_probes_emit_no_record(self):
        for arguments in (("--system-bin",), ("--system-bin", "relative"),
                          ("--system-bin", "/bad:path"), ("node", "--version")):
            result = self.launch("--env-json", *arguments)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
        (self.default_bin / "npm").unlink()
        result = self.launch("--env-json")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        failed = self.runtime(self.home / "failed", "v20.0.0")
        (failed / "node").write_text("#!/bin/sh\necho synthetic-private-config\nexit 17\n")
        result = self.launch("--env-json", "--system-bin", str(failed))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("synthetic-private-config", result.stderr)

    def test_generated_default_path_supports_envy_exec_and_inherited_native_npm(self):
        import render_env
        configured = self.home / ".config/envy"
        configured.mkdir(parents=True)
        (self.project.parent / ".nvmrc").write_text("missing\n")
        state = {"features": {"nvm": {"enabled": True, "status": "current",
                                      "data": {"directory": str(self.target)}}}}
        (configured / "init-sh.env").write_text(render_env.render(
            {"features": {"nvm": {}}}, state, str(ROOT)))
        command = [str(ROOT / "bin/envy"), "exec", "/bin/bash", "--noprofile", "--norc", "-c",
                   'command -v npm; exec npm inherited-native', "_"]
        result = subprocess.run(command, cwd=self.project, env=self.environment,
                                input="inherited stdin\n", capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 23, result.stderr)
        self.assertEqual(result.stdout, f"{self.default_bin}/npm\nv24.18.0\n['inherited-native']\ninherited stdin\n")
        self.assertEqual(result.stderr, "native fixture stderr\n")

    def test_system_selection_survives_managed_bash_and_zsh_hooks(self):
        system = self.runtime(self.home / "system", "v20.0.0")
        for shell, name in (("/bin/bash", "bash"), (shutil.which("zsh"), "zsh")):
            if not shell:
                continue
            script = (ROOT / f"chezmoi/dot_config/envy/nvm-init.{name}").read_text()
            result = subprocess.run([shell, "-uc", script + '\ncommand -v npm'],
                                    env={**self.environment, "PATH": f"{system}:{self.environment['PATH']}",
                                         "ENVY_NVM_ENABLED": "1", "ENVY_NODE_BACKEND": "system"},
                                    cwd=self.project, text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, f"{system}/npm\n")
            self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
