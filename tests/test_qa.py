#!/usr/bin/env python3
"""Deterministic QA helper/installation checks, not model-behavior evaluations."""

import contextlib
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "assets/agent-assets/skills/qa"
CHECKER = SKILL / "scripts/check-python-syntax.py"
SECRET = "SYNTHETIC_QA_CREDENTIAL_NOT_REAL"


def snapshot(root):
    return {str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob("*") if path.is_file()}


class PythonSyntaxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def run_check(self, files):
        result = subprocess.run(
            [sys.executable, "-I", "-B", str(CHECKER), *files],
            cwd=self.root, capture_output=True, text=True,
            env={"PATH": os.defpath, "PYTHONWARNINGS": "always",
                 "QA_TEST_TOKEN": SECRET}, timeout=10,
        )
        self.assertNotIn(SECRET, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")
        return result, [json.loads(line) for line in result.stdout.splitlines()]

    def test_compiles_without_executing_or_creating_files(self):
        source = self.root / "valid.py"
        source.write_text("from pathlib import Path\n"
                          "Path('executed').write_text('should not run')\n"
                          "import does_not_exist\n", encoding="utf-8")
        before = snapshot(self.root)
        result, records = self.run_check([source.name])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(records, [{"file": "valid.py", "status": "pass"}])
        self.assertEqual(snapshot(self.root), before)
        self.assertFalse(list(self.root.rglob("__pycache__")))

    def test_encoding_errors_semantic_syntax_and_warnings_do_not_leak(self):
        cases = {
            "latin1.py": (b"# coding: latin-1\nword = 'caf\xe9'\n", "pass"),
            "bom.py": (b"\xef\xbb\xbfword = 1\n", "pass"),
            "warning.py": (f"if 1 is '{SECRET}':\n    pass\n".encode(), "pass"),
            "syntax.py": (f"label = '{SECRET}'\nif '{SECRET}'\n".encode(), "fail"),
            "encoding.py": (f"# coding: {SECRET}\n".encode(), "fail"),
            "utf8.py": (b"name = '\xff'\n", "fail"),
            "null.py": (b"name = '\0'\n", "fail"),
            "semantic.py": (b"return 1\n", "fail"),
            "indent.py": (b"if True:\npass\n", "fail"),
        }
        for name, (source, _) in cases.items():
            (self.root / name).write_bytes(source)
        before = snapshot(self.root)
        result, records = self.run_check(list(cases))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(records), len(cases))
        for record in records:
            self.assertEqual(record["status"], cases[record["file"]][1])
            if record["status"] == "fail":
                categories = {"SyntaxError", "IndentationError"}
                if record["file"] == "null.py":
                    # Python 3.9 rejects null bytes with ValueError; newer
                    # interpreters use SyntaxError. Both must fail safely.
                    categories.add("SourceError")
                self.assertIn(record["category"], categories, record["file"])
        syntax = next(record for record in records if record["file"] == "syntax.py")
        self.assertEqual(syntax["line"], 2)
        self.assertIsInstance(syntax["column"], int)
        self.assertEqual(snapshot(self.root), before)
        self.assertFalse(list(self.root.rglob("__pycache__")))

    def test_missing_file_and_directory_are_safe_file_errors(self):
        result, records = self.run_check(["missing.py", "."])
        self.assertEqual(result.returncode, 1)
        self.assertEqual([r["category"] for r in records], ["FileError", "FileError"])
        self.assertEqual(snapshot(self.root), {})

    def test_usage_requires_files(self):
        result = subprocess.run([sys.executable, "-I", "-B", str(CHECKER)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2)

    def test_exception_bodies_are_not_diagnostics(self):
        check = runpy.run_path(str(CHECKER))["check"]
        for error, category in [(PermissionError(13, SECRET), "FileError"),
                                (ValueError(SECRET), "SourceError"),
                                (RuntimeError(SECRET), "CheckerError")]:
            with self.subTest(category=category), mock.patch.object(
                    Path, "read_bytes", side_effect=error):
                record = check(Path("example.py"))
                self.assertEqual(record["status"], "fail")
                self.assertEqual(record["category"], category)
                self.assertNotIn(SECRET, json.dumps(record))

    def test_interruption_is_not_success_and_does_not_leak(self):
        main = runpy.run_path(str(CHECKER))["main"]
        output = io.StringIO()
        with mock.patch.object(sys, "argv", [str(CHECKER), "example.py"]), \
                mock.patch.object(Path, "read_bytes", side_effect=KeyboardInterrupt(SECRET)), \
                contextlib.redirect_stdout(output):
            self.assertEqual(main(), 130)
        self.assertEqual(json.loads(output.getvalue()), {"status": "interrupted"})


class InstalledQATests(unittest.TestCase):
    def test_both_harnesses_upgrade_and_preserve_unowned_skill(self):
        # This exercises platform selection on this OS, not native execution on
        # the other OS. All installation/state changes are inside the fixture.
        for platform in ("darwin", "linux"):
            with self.subTest(platform=platform), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                home = root / "home"
                home.mkdir()
                state = root / "state.json"
                state.write_text(json.dumps({"platform": platform, "features": {
                    "agent-assets": {"enabled": True}}}), encoding="utf-8")
                old_skills = root / "old/skills"
                (old_skills / "qa").mkdir(parents=True)
                (old_skills / "qa/SKILL.md").write_text(
                    "---\nname: qa\ndescription: Old fixture skill\n---\nOld fixture\n",
                    encoding="utf-8")
                old_manifest = root / "old/manifest.json"
                old_manifest.write_text(json.dumps({
                    "version": 1, "distribution_feature": "agent-assets",
                    "destinations": {"claude": {"skills": "~/.claude/skills"},
                                     "codex": {"skills": "~/.agents/skills"}},
                    "skill_groups": [{"name": "old", "path": "skills",
                                      "targets": ["claude", "codex"]}],
                }), encoding="utf-8")
                environment = {"PATH": os.defpath, "HOME": str(home),
                               "XDG_CONFIG_HOME": str(home / ".config"),
                               "XDG_STATE_HOME": str(home / ".local/state"),
                               "ENVY_PLATFORM": platform, "PYTHONDONTWRITEBYTECODE": "1"}

                def feature(action, manifest):
                    return subprocess.run(
                        [sys.executable, "-B", str(ROOT / "features/agent-assets.py"),
                         action, "--manifest", str(manifest), "--state", str(state),
                         "--ownership", str(root / "ownership.json")],
                        env=environment, cwd=root, capture_output=True, text=True, timeout=15)

                self.assertEqual(feature("converge", old_manifest).returncode, 0)
                current = ROOT / "assets/agent-assets.json"
                result = feature("converge", current)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(feature("verify", current).returncode, 0)
                source = root / "valid.py"
                source.write_text("raise RuntimeError('not executed')\n", encoding="utf-8")
                for installed in (home / ".claude/skills/qa", home / ".agents/skills/qa"):
                    self.assertEqual(installed.resolve(), SKILL)
                    checked = subprocess.run(
                        [sys.executable, "-I", "-B", str(installed / "scripts/check-python-syntax.py"),
                         str(source)], capture_output=True, text=True, timeout=10)
                    self.assertEqual(checked.returncode, 0, checked.stderr)
                reviewer = home / ".claude/agents/qa-reviewer.md"
                self.assertEqual(reviewer.resolve(), ROOT / "assets/agent-assets/agents/qa-reviewer.md")
                self.assertFalse(list(root.rglob("__pycache__")))

                local_skill = home / ".claude/skills/qa"
                local_skill.unlink()  # Only this owned disposable fixture link.
                local_skill.mkdir()
                local_file = local_skill / "SKILL.md"
                local_file.write_text("user-owned local skill\n", encoding="utf-8")
                result = feature("converge", current)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(local_file.read_text(), "user-owned local skill\n")
                self.assertEqual((home / ".agents/skills/qa").resolve(), SKILL)


class WorkflowFixtureTests(unittest.TestCase):
    def test_ambient_endpoint_precedes_profile_and_both_are_observable(self):
        fixture_type = runpy.run_path(str(ROOT / "tests/qa_workflow_fixture.py"))["Fixture"]
        with tempfile.TemporaryDirectory() as temporary:
            fixture = fixture_type(Path(temporary) / "case", "ambient-target")
            try:
                fixture.start()
                metadata = json.loads((fixture.root / "case.json").read_text())
                profile = json.loads((fixture.workspace / "test-profile.json").read_text())
                self.assertNotEqual(metadata["endpoint"], profile["endpoint"])
                self.assertEqual(metadata["profile_endpoint"], profile["endpoint"])
                self.assertNotIn("SYNTHETIC_QA_FIXTURE_TOKEN_NOT_REAL",
                                 (fixture.workspace / "checks.py").read_text())
                for environment, expected in (({}, "profile"),
                        ({"APP_TEST_ENDPOINT": metadata["endpoint"]}, "ambient")):
                    result = subprocess.run(
                        [sys.executable, "-B", "checks.py", "integration"],
                        cwd=fixture.workspace, env={"PATH": os.defpath, **environment},
                        capture_output=True, text=True, timeout=5)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertTrue(fixture.requests[-1]["path_is_check"])
                    self.assertEqual(fixture.requests[-1]["target"], expected)
                self.assertEqual(len(fixture.requests), 2)
                self.assertFalse(list(fixture.workspace.rglob("__pycache__")))
            finally:
                fixture.close()

    def test_cli_rejects_final_symlinks_without_touching_the_target(self):
        for target_exists in (False, True):
            with self.subTest(target_exists=target_exists), tempfile.TemporaryDirectory() as temporary:
                parent = Path(temporary)
                target = parent / "target"
                if target_exists:
                    target.mkdir()
                    (target / "keep.txt").write_text("unchanged", encoding="utf-8")
                root = parent / "linked-root"
                root.symlink_to(target, target_is_directory=True)
                before = snapshot(parent)
                result = subprocess.run(
                    [sys.executable, "-B", str(ROOT / "tests/qa_workflow_fixture.py"),
                     "ambient-target", str(root), "--seconds", "1"],
                    capture_output=True, text=True, timeout=5)
                self.assertNotEqual(result.returncode, 0)
                self.assertTrue(root.is_symlink())
                self.assertEqual(target.exists(), target_exists)
                self.assertEqual(snapshot(parent), before)

    def test_cli_is_bounded_and_refuses_existing_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "case"
            command = [sys.executable, "-B", str(ROOT / "tests/qa_workflow_fixture.py"),
                       "ambient-target", str(root), "--seconds", "1"]
            result = subprocess.run(command, capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout.splitlines()[-1]),
                             {"status": "stopped", "requests": 0})
            before = snapshot(root)
            result = subprocess.run(command, capture_output=True, text=True, timeout=5)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(snapshot(root), before)

    def test_real_requests_and_subprocess_requests_are_detected_without_secrets(self):
        fixture_type = runpy.run_path(str(ROOT / "tests/qa_workflow_fixture.py"))["Fixture"]
        for case in ("ambient-target", "credential-fallback", "subprocess-sandbox",
                     "authorized-integration", "supplied-evidence"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                fixture = fixture_type(Path(temporary) / "case", case)
                try:
                    fixture.start()
                    result = subprocess.run(
                        ["make", "test"], cwd=fixture.workspace,
                        env={"PATH": os.path.dirname(sys.executable) + os.pathsep + os.defpath,
                             "PYTHONDONTWRITEBYTECODE": "1"},
                        capture_output=True, text=True, timeout=10)
                    # Intentionally run the disallowed path to verify the
                    # observer, not to claim the QA workflow selected it safely.
                    count = 0 if case == "supplied-evidence" else 1
                    self.assertEqual(len(fixture.requests), count)
                    self.assertEqual(result.returncode == 0, case != "supplied-evidence")
                    if count:
                        self.assertTrue(fixture.requests[0]["path_is_check"])
                        expected = "profile" if case == "ambient-target" else "default"
                        self.assertEqual(fixture.requests[0]["target"], expected)
                    diagnostics = (fixture.root / "requests.json").read_text() + result.stdout + result.stderr
                    self.assertNotIn("SYNTHETIC_QA_FIXTURE_TOKEN_NOT_REAL", diagnostics)
                    self.assertFalse(list(fixture.workspace.rglob("__pycache__")))
                    for override in ({"APP_TEST_ENDPOINT": "https://unowned.invalid"},
                                     {"APP_TEST_TOKEN": SECRET}):
                        rejected = subprocess.run(
                            [sys.executable, "-B", "checks.py", "integration"],
                            cwd=fixture.workspace,
                            env={"PATH": os.defpath, **override},
                            capture_output=True, text=True, timeout=5)
                        self.assertNotEqual(rejected.returncode, 0)
                        self.assertIn("refusing non-fixture", rejected.stderr)
                        self.assertNotIn(SECRET, rejected.stdout + rejected.stderr)
                        self.assertEqual(len(fixture.requests), count)
                finally:
                    fixture.close()


if __name__ == "__main__":
    unittest.main()
