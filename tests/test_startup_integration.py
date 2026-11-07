"""Real startup files in disposable homes; no user configuration or services."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
from startup_integration import Integration, IntegrationError, BEGIN, END


class StartupFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve() / "home with space"
        self.home.mkdir()
        self.config = self.home / ".config/envy"
        self.config.mkdir(parents=True)
        for name in ("bashrc", "zshrc", "tmux.conf"):
            (self.config / name).write_text("# fixture fragment\n")
        self.manager = Integration(self.home, self.home / "state", self.config,
                                   self.home / "source", zdotdir=self.home)

    def install(self):
        self.manager.prepare()
        self.manager.converge()


class StartupIntegrationTest(StartupFixture):
    def test_preserves_stock_files_and_modes_and_repeats(self):
        original = b"# Host startup\nexport HOST_BASE=present"  # no final newline
        target = self.home / ".bashrc"
        target.write_bytes(original)
        target.chmod(0o640)
        self.install()
        installed = target.read_bytes()
        self.assertTrue(installed.startswith(original + b"\n"))
        self.install()
        self.assertEqual(target.read_bytes(), installed)
        self.assertEqual(target.stat().st_mode & 0o777, 0o640)
        self.manager.verify()
        self.manager.remove()
        self.assertEqual(target.read_bytes(), original)
        self.assertFalse((self.home / ".zshrc").exists())
        self.manager.remove()

    def test_outside_edits_survive_upgrade_and_removal(self):
        self.install()
        target = self.home / ".bashrc"
        target.write_bytes(b"# Added before\n" + target.read_bytes() + b"# Added after\n")
        with self.assertRaises(IntegrationError):
            self.manager.verify()
        self.install()
        self.manager.verify()
        self.manager.remove()
        self.assertEqual(target.read_bytes(), b"# Added before\n# Added after\n")

    def test_marker_edit_or_duplicates_refuse_without_mutation(self):
        self.install()
        target = self.home / ".bashrc"
        for contents in (target.read_bytes().replace(b'_ENVY_HOST', b'_USER_HOST'),
                         target.read_bytes() + BEGIN):
            target.write_bytes(contents)
            with self.assertRaises(IntegrationError):
                self.manager.prepare()
            self.assertEqual(target.read_bytes(), contents)

    def test_symlink_refuses_and_does_not_modify_referent(self):
        outside = self.home / "original"
        outside.write_text("host\n")
        (self.home / ".bashrc").symlink_to(outside)
        with self.assertRaises(IntegrationError):
            self.manager.prepare()
        self.assertEqual(outside.read_text(), "host\n")
        self.assertFalse(self.manager.ledger.exists())

    def test_profile_precedence_does_not_mask_existing_profile(self):
        (self.home / ".profile").write_text("# POSIX host profile\n")
        self.install()
        self.assertFalse((self.home / ".bash_profile").exists())
        self.assertIn(BEGIN, (self.home / ".profile").read_bytes())
        self.manager.verify()

    def test_zdotdir_is_respected_and_relocation_is_reported(self):
        zdir = self.home / "zsh config"
        zdir.mkdir()
        self.manager.zdotdir = zdir
        self.install()
        self.assertTrue((zdir / ".zshrc").exists())
        self.assertFalse((self.home / ".zshrc").exists())
        self.manager.zdotdir = self.home
        with self.assertRaises(IntegrationError):
            self.manager.prepare()

    def test_stale_prepared_input_refuses_all_writes(self):
        self.manager.prepare()
        (self.home / ".zshrc").write_text("# User edit during apply\n")
        with self.assertRaises(IntegrationError):
            self.manager.converge()
        self.assertFalse((self.home / ".bashrc").exists())

    def test_interrupted_writes_resume_from_journal(self):
        self.manager.prepare()
        import startup_integration
        original_write = startup_integration.atomic_write
        count = 0

        def interrupted(path, contents, mode=0o600):
            nonlocal count
            count += 1
            if count == 3:
                raise OSError("fixture interruption")
            original_write(path, contents, mode)

        with patch.object(startup_integration, "atomic_write", interrupted):
            with self.assertRaises(OSError):
                self.manager.converge()
        self.manager.prepare()
        self.manager.converge()
        self.manager.verify()
        for path in self.manager.blocks():
            self.assertEqual((self.home / path).read_bytes().count(BEGIN), 1)

    def test_bundle_skeleton_only_seeds_absent_file(self):
        skeleton = self.home / "host-skeleton"
        skeleton.write_text("# host default\n")
        self.manager.defaults = {".bashrc": str(skeleton), ".zshrc": str(skeleton)}
        (self.home / ".bashrc").write_text("# existing custom base\n")
        self.install()
        self.assertTrue((self.home / ".bashrc").read_text().startswith("# existing custom base\n"))
        self.manager.remove()
        self.assertEqual((self.home / ".zshrc").read_text(), "# host default\n")

    def legacy_fixture(self, contents=b"old generated\n"):
        backup = self.manager.backup
        (backup / "files").mkdir(parents=True)
        (backup / "files/.bashrc").write_bytes(b"# original host rc\n")
        for name in ("existing.list", "created.list", "pending-absent.list"):
            (backup / name).write_text(".bashrc\n.other\n")
        self.manager.source.mkdir()
        (self.manager.source / "dot_bashrc.tmpl").write_bytes(contents)
        (self.home / ".bashrc").write_bytes(contents)

    def test_legacy_generated_file_restores_base_and_retires_old_ownership(self):
        self.legacy_fixture()
        with patch.object(self.manager, "written_targets", return_value={str(self.home / '.bashrc')}), \
             patch('startup_integration.subprocess.run', return_value=subprocess.CompletedProcess([], 0, b"old generated\n")):
            self.manager.prepare()
        # Prepared migration must survive replacement of the old composed tree.
        shutil.rmtree(self.manager.source)
        self.manager.converge()
        self.assertTrue((self.home / ".bashrc").read_bytes().startswith(b"# original host rc\n"))
        for name in ("existing.list", "created.list", "pending-absent.list"):
            self.assertEqual((self.manager.backup / name).read_text(), ".other\n")
        self.manager.remove()
        self.assertEqual((self.home / ".bashrc").read_bytes(), b"# original host rc\n")

    def test_customized_legacy_file_is_not_reset(self):
        self.legacy_fixture(b"old generated plus personal edits\n")
        with patch.object(self.manager, "written_targets", return_value={str(self.home / '.bashrc')}), \
             patch('startup_integration.subprocess.run', return_value=subprocess.CompletedProcess([], 0, b"old generated\n")):
            with self.assertRaisesRegex(IntegrationError, "Customized legacy"):
                self.manager.prepare()
        self.assertFalse(self.manager.ledger.exists())
        self.assertEqual((self.home / ".bashrc").read_bytes(), b"old generated plus personal edits\n")

    def test_first_failed_install_keeps_original(self):
        self.legacy_fixture(b"# original host rc\n")
        with patch.object(self.manager, "written_targets", return_value=set()):
            self.install()
        self.manager.verify()
        self.manager.remove()
        self.assertEqual((self.home / ".bashrc").read_bytes(), b"# original host rc\n")


@unittest.skipUnless(shutil.which("chezmoi") and shutil.which("zsh"), "chezmoi and zsh required")
class ShellOrderTest(StartupFixture):
    def render(self):
        binary = self.home / "bin"
        binary.mkdir()
        # No real mise, package manager, credentials, or services in startup.
        for command in ("mise", "bat", "glow"):
            executable = binary / command
            executable.write_text("#!/bin/sh\nexit 1\n")
            executable.chmod(0o755)
        self.env = {"HOME": str(self.home), "ZDOTDIR": str(self.home),
                    "PATH": f"{binary}:/usr/bin:/bin", "TERM": "dumb", "ENVY_NO_ZSH": "1"}
        data = {"xdgConfigHome": str(self.config.parent),
                "xdgDataHome": str(self.home / ".local/share")}
        for name in ("bashrc", "zshrc"):
            result = subprocess.run([shutil.which("chezmoi"), "execute-template", "--source", str(ROOT / "chezmoi"),
                                     "--override-data", json.dumps(data)],
                                    input=(ROOT / f"chezmoi/dot_config/envy/{name}.tmpl").read_text(),
                                    env=self.env, text=True, capture_output=True, check=True)
            (self.config / name).write_text(result.stdout)
        (self.config / "init-sh.env").write_text('export EDITOR=managed\n')
        fragments = self.config / "shell.d"
        fragments.mkdir()
        for suffix in ("bash", "zsh"):
            (fragments / f"50-private.{suffix}").write_text('export EDITOR=private\n')
        for shell in ("bash", "zsh"):
            (self.home / f".{shell}rc.local").write_text('export EDITOR=local\n')

    def run_shell(self, *args):
        result = subprocess.run(args, cwd=self.home, env=self.env, stdin=subprocess.DEVNULL,
                                text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip().splitlines()

    def test_bash_login_preserves_host_then_managed_then_local(self):
        self.render()
        (self.home / ".bashrc").write_text('case $- in *i*) ;; *) return ;; esac\nexport BASE_COUNT=$(( ${BASE_COUNT:-0} + 1 ))\n')
        (self.home / ".bash_profile").write_text('. "$HOME/.bashrc"\nexport EDITOR=host_after_rc\n')
        self.install()
        output = self.run_shell("/bin/bash", "--noprofile", "--norc", "-lic",
                                '. "$HOME/.bash_profile"; printf "%s:%s\\n" "$EDITOR" "$BASE_COUNT"')
        self.assertEqual(output, ["local:1"])

    def test_bash_nonlogin_and_noninteractive(self):
        self.render()
        (self.home / ".bashrc").write_text('export EDITOR=host\n')
        self.install()
        self.assertEqual(self.run_shell('/bin/bash', '--noprofile', '-ic', 'echo "$EDITOR"'), ['local'])
        self.assertEqual(self.run_shell('/bin/bash', '--noprofile', '-c', '. "$HOME/.bashrc"; echo "$EDITOR"'), ['host'])

    def test_zsh_login_runs_overlay_after_host_zlogin(self):
        self.render()
        (self.home / ".zshrc").write_text('export BASE_COUNT=$(( ${BASE_COUNT:-0} + 1 ))\n')
        (self.home / ".zlogin").write_text('export EDITOR=host_after_rc\n')
        self.install()
        self.assertEqual(self.run_shell(shutil.which('zsh'), '-dlic', 'echo "$EDITOR:$BASE_COUNT"'), ['local:1'])
        self.assertEqual(self.run_shell(shutil.which('zsh'), '-dic', 'echo "$EDITOR:$BASE_COUNT"'), ['local:1'])

    def test_login_handoff_waits_for_host_profile(self):
        self.render()
        fake = self.home / "bin/zsh"
        fake.write_text('#!/bin/sh\nprintf "%s:%s\\n" "$HOST_PROFILE_DONE" "$*"\n')
        fake.chmod(0o755)
        self.env.pop('ENVY_NO_ZSH')
        (self.home / '.bash_profile').write_text('. "$HOME/.bashrc"\nexport HOST_PROFILE_DONE=yes\n')
        self.install()
        self.assertEqual(self.run_shell('/bin/bash', '--noprofile', '--norc', '-lic',
                                        '. "$HOME/.bash_profile"; exit 95'), ['yes:-l'])


if __name__ == "__main__":
    unittest.main()
