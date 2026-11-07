#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "envy_secrets", ROOT / "features" / "secrets.py"
)
assert SPEC and SPEC.loader
secrets = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = secrets
SPEC.loader.exec_module(secrets)


class SecretsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.target = self.root / "target" / ".secrets"
        self.source = self.root / "secrets.sops.env"
        self.identity = self.root / "age-identities.txt"
        self.manifest = self.root / "manifest.json"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.original_path = os.environ.get("PATH")
        os.environ["PATH"] = f"{self.bin}{os.pathsep}{self.original_path or ''}"
        os.environ["ENVY_MANIFEST"] = str(self.manifest)
        self.write_tool(
            "sops",
            """#!/usr/bin/env python3
import os
import sys
from pathlib import Path
if not os.environ.get("SOPS_AGE_KEY_FILE"):
    print("missing identity", file=sys.stderr)
    raise SystemExit(2)
if "--decrypt" not in sys.argv:
    raise SystemExit(2)
contents = Path(sys.argv[-1]).read_bytes()
if b"FAIL" in contents:
    print("bad identity AGE-SECRET-KEY-LEAKME", file=sys.stderr)
    raise SystemExit(1)
sys.stdout.buffer.write(b"EXAMPLE=private\\n")
""",
        )
        self.write_tool("age", "#!/usr/bin/env python3\n")
        self.write_tool(
            "age-keygen",
            "#!/usr/bin/env python3\nprint('age1testrecipient')\n",
        )
        self.identity.write_text("AGE-SECRET-KEY-TEST-ONLY\n", encoding="utf-8")
        os.chmod(self.identity, 0o600)
        self.write_encrypted_source()

    def tearDown(self) -> None:
        os.environ.pop("ENVY_MANIFEST", None)
        os.environ.pop("FAKE_REMOTE_HOME", None)
        os.environ.pop("FAKE_SCP_LOG", None)
        if self.original_path is None:
            os.environ.pop("PATH", None)
        else:
            os.environ["PATH"] = self.original_path
        self.temporary.cleanup()

    def write_tool(self, name: str, contents: str) -> None:
        path = self.bin / name
        contents = contents.replace("#!/usr/bin/env python3", f"#!{sys.executable}", 1)
        path.write_text(contents, encoding="utf-8")
        os.chmod(path, 0o755)

    def configure_fake_ssh(self) -> Path:
        remote_home = self.root / "remote-home"
        remote_home.mkdir()
        scp_log = self.root / "scp.log"
        os.environ["FAKE_REMOTE_HOME"] = str(remote_home)
        os.environ["FAKE_SCP_LOG"] = str(scp_log)
        self.write_tool(
            "ssh",
            """#!/usr/bin/env python3
import os
import subprocess
import sys

environment = os.environ.copy()
environment["HOME"] = environment["FAKE_REMOTE_HOME"]
process = subprocess.run(
    ["/bin/sh", "-s"],
    input=sys.stdin.buffer.read(),
    env=environment,
)
raise SystemExit(process.returncode)
""",
        )
        self.write_tool(
            "scp",
            """#!/usr/bin/env python3
import os
import shutil
import sys
from pathlib import Path

arguments = sys.argv[1:]
while arguments[:1] in (["-o"], ["-P"]):
    arguments = arguments[2:]
source, destination = arguments
remote_home = Path(os.environ["FAKE_REMOTE_HOME"])
if ":" in source:
    remote_path = source.split(":", 1)[1]
    shutil.copyfile(remote_home / remote_path, destination)
else:
    remote_path = destination.split(":", 1)[1]
    shutil.copyfile(source, remote_home / remote_path)
with open(os.environ["FAKE_SCP_LOG"], "a", encoding="utf-8") as handle:
    handle.write(source + " -> " + destination + "\\n")
""",
        )
        return remote_home

    def transfer_options(self, **changes: object) -> secrets.KeyTransferOptions:
        values: dict[str, object] = {
            "destination": "test-host",
            "remote_path": ".config/sops/age/keys.txt",
            "port": None,
            "force": False,
            "assume_yes": True,
            "restore": False,
        }
        values.update(changes)
        return secrets.KeyTransferOptions(**values)

    def create_remote_identity(
        self, remote_home: Path, contents: bytes | None = None
    ) -> Path:
        path = remote_home / ".config" / "sops" / "age" / "keys.txt"
        path.parent.mkdir(parents=True)
        path.write_bytes(
            contents if contents is not None else self.identity.read_bytes()
        )
        os.chmod(path, 0o600)
        return path

    def write_encrypted_source(self, extra: str = "") -> None:
        self.source.write_text(
            "EXAMPLE=ENC[AES256_GCM,data:test]\\n"
            "sops_age__list_0__map_recipient=age1test\\n"
            "sops_mac=ENC[AES256_GCM,data:test]\\n" + extra,
            encoding="utf-8",
        )
        os.chmod(self.source, 0o600)

    def source_config(self, path: Path | None = None) -> dict[str, str]:
        return {
            "type": "sops-age",
            "path": str(path or self.source),
            "input_type": "dotenv",
            "output_type": "dotenv",
        }

    def configure(
        self,
        sources: list[object] | None = None,
        identity: Path | None = None,
    ) -> None:
        self.manifest.write_text(
            json.dumps(
                {
                    "settings": {
                        "secrets": {
                            "target": str(self.target),
                            "age": {"identity_file": str(identity or self.identity)},
                            "sources": sources or [self.source_config()],
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

    def test_sops_decryption_is_installed_atomically(self) -> None:
        self.configure()
        self.assertEqual(secrets.converge(), 0)
        self.assertEqual(self.target.read_bytes(), b"EXAMPLE=private\n")
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o600)
        self.assertEqual(secrets.verify(False), 0)
        self.assertEqual(secrets.doctor(), 0)

    def test_first_converge_bootstraps_identity_and_source(self) -> None:
        self.identity.unlink()
        self.source.unlink()
        self.configure()
        self.target.parent.mkdir(parents=True, exist_ok=True)
        self.target.write_bytes(b"EXAMPLE=private\n")
        os.chmod(self.target, 0o600)
        self.write_tool(
            "age-keygen",
            """#!/usr/bin/env python3
import sys
from pathlib import Path
if "-o" in sys.argv:
    Path(sys.argv[sys.argv.index("-o") + 1]).write_text("AGE-SECRET-KEY-GENERATED\\n")
else:
    print("age1testrecipient")
""",
        )
        self.write_tool(
            "sops",
            """#!/usr/bin/env python3
import os
import sys
if not os.environ.get("SOPS_AGE_KEY_FILE"):
    raise SystemExit(2)
if "--encrypt" in sys.argv:
    if "--age" not in sys.argv:
        raise SystemExit(2)
    sys.stdout.buffer.write(b"sops age ENC[fixture]\\n")
    raise SystemExit(0)
if "--decrypt" in sys.argv:
    sys.stdout.buffer.write(b"EXAMPLE=private\\n")
    raise SystemExit(0)
raise SystemExit(2)
""",
        )
        self.assertEqual(secrets.converge(), 0)
        self.assertTrue(self.identity.is_file())
        self.assertEqual(stat.S_IMODE(self.identity.stat().st_mode), 0o600)
        self.assertTrue(self.source.is_file())
        self.assertEqual(stat.S_IMODE(self.source.stat().st_mode), 0o600)
        self.assertEqual(secrets.verify(False), 0)

    def test_tools_resolve_through_mise_before_shell_activation(self) -> None:
        age = self.bin / "age"
        managed_age = self.root / "managed-age"
        age.rename(managed_age)
        self.write_tool(
            "mise",
            f"""#!/usr/bin/env python3
import sys
if sys.argv[1:] == ["which", "age"]:
    print({str(managed_age)!r})
    raise SystemExit(0)
raise SystemExit(1)
""",
        )
        os.environ["PATH"] = os.pathsep.join(
            (str(self.bin), os.path.dirname(sys.executable))
        )
        self.assertEqual(secrets.required_tools()["age"], str(managed_age))

    def test_key_transfers_require_initialized_tools_before_ssh(self) -> None:
        self.configure()

        def resolve(command: str) -> str | None:
            if command == "sops":
                return None
            return str(self.bin / command)

        for transfer in (secrets.push_key, secrets.pull_key):
            with self.subTest(transfer=transfer.__name__):
                with (
                    mock.patch.object(secrets, "resolve_tool", side_effect=resolve),
                    mock.patch.object(secrets, "open_ssh_transport") as transport,
                    self.assertRaisesRegex(
                        secrets.SecretError,
                        "secrets feature is not initialized; missing managed tools: sops",
                    ),
                ):
                    transfer(self.transfer_options())
                transport.assert_not_called()

    def test_plaintext_and_legacy_sources_are_refused(self) -> None:
        self.source.write_text("EXAMPLE=private\n", encoding="utf-8")
        self.configure()
        with self.assertRaisesRegex(secrets.SecretError, "not SOPS-encrypted"):
            secrets.converge()
        self.assertFalse(self.target.exists())

        self.configure(sources=[str(self.source)])
        with self.assertRaisesRegex(secrets.SecretError, "plaintext secrets sources"):
            secrets.configuration()

    def test_sops_without_an_age_recipient_is_refused(self) -> None:
        self.source.write_text(
            "EXAMPLE=ENC[AES256_GCM,data:test]\n"
            "sops_pgp__list_0__map_fp=test\n"
            "sops_mac=ENC[AES256_GCM,data:test]\n",
            encoding="utf-8",
        )
        self.configure()
        with self.assertRaisesRegex(secrets.SecretError, "SOPS-encrypted with age"):
            secrets.converge()

    def test_existing_target_does_not_bypass_encrypted_source_requirement(self) -> None:
        self.target.parent.mkdir(parents=True)
        self.target.write_text("EXAMPLE=existing\n", encoding="utf-8")
        os.chmod(self.target, 0o600)
        self.source.write_text("EXAMPLE=private\n", encoding="utf-8")
        self.configure()
        with self.assertRaisesRegex(secrets.SecretError, "not SOPS-encrypted"):
            secrets.converge()

    def test_source_identity_and_target_symlinks_are_refused(self) -> None:
        source_link = self.root / "source-link"
        source_link.symlink_to(self.source)
        self.configure(sources=[self.source_config(source_link)])
        with self.assertRaisesRegex(secrets.SecretError, "non-regular"):
            secrets.converge()

        identity_link = self.root / "identity-link"
        identity_link.symlink_to(self.identity)
        self.configure(identity=identity_link)
        with self.assertRaisesRegex(secrets.SecretError, "non-regular"):
            secrets.converge()

        self.target.parent.mkdir(parents=True)
        target_backing = self.root / "target-backing"
        target_backing.write_text("existing\n", encoding="utf-8")
        self.target.symlink_to(target_backing)
        self.configure()
        with self.assertRaisesRegex(secrets.SecretError, "non-regular"):
            secrets.converge()
        self.assertEqual(target_backing.read_text(encoding="utf-8"), "existing\n")

    def test_decryption_error_suppresses_subprocess_output(self) -> None:
        self.write_encrypted_source("FAIL=true\n")
        self.configure()
        with self.assertRaises(secrets.SecretError) as context:
            secrets.converge()
        self.assertNotIn("AGE-SECRET-KEY-LEAKME", str(context.exception))
        self.assertIn("diagnostic output was suppressed", str(context.exception))

    def test_doctor_detects_a_stale_recovery_source(self) -> None:
        self.configure()
        self.target.parent.mkdir(parents=True)
        self.target.write_text("EXAMPLE=newer\n", encoding="utf-8")
        os.chmod(self.target, 0o600)
        with self.assertRaisesRegex(secrets.SecretError, "does not match target"):
            secrets.doctor()

    def test_identity_permissions_are_enforced(self) -> None:
        os.chmod(self.identity, 0o644)
        self.configure()
        with self.assertRaisesRegex(secrets.SecretError, "permissions are too open"):
            secrets.converge()

    def test_push_key_installs_verifies_and_is_idempotent(self) -> None:
        remote_home = self.configure_fake_ssh()
        self.configure()
        self.assertEqual(secrets.push_key(self.transfer_options()), 0)
        remote_identity = remote_home / ".config" / "sops" / "age" / "keys.txt"
        self.assertEqual(remote_identity.read_bytes(), self.identity.read_bytes())
        self.assertEqual(stat.S_IMODE(remote_identity.stat().st_mode), 0o600)
        self.assertEqual(
            Path(os.environ["FAKE_SCP_LOG"]).read_text(encoding="utf-8").count("\n"),
            1,
        )

        os.chmod(remote_identity, 0o644)
        self.assertEqual(secrets.push_key(self.transfer_options()), 0)
        self.assertEqual(stat.S_IMODE(remote_identity.stat().st_mode), 0o600)
        self.assertEqual(
            Path(os.environ["FAKE_SCP_LOG"]).read_text(encoding="utf-8").count("\n"),
            1,
        )

    def test_push_key_can_restore_and_doctor_in_the_same_command(self) -> None:
        remote_home = self.configure_fake_ssh()
        self.configure()
        remote_bin = remote_home / ".local" / "bin"
        remote_bin.mkdir(parents=True)
        restore_log = self.root / "restore.log"
        remote_cli = remote_bin / "envy"
        remote_cli.write_text(
            "#!/bin/sh\n" f"printf '%s\\n' \"$*\" >> {str(restore_log)!r}\n",
            encoding="utf-8",
        )
        os.chmod(remote_cli, 0o755)

        self.assertEqual(secrets.push_key(self.transfer_options(restore=True)), 0)
        self.assertEqual(
            restore_log.read_text(encoding="utf-8").splitlines(),
            ["upgrade --only secrets --force", "doctor secrets --verbose"],
        )

    def test_push_key_decline_leaves_remote_unchanged(self) -> None:
        remote_home = self.configure_fake_ssh()
        self.configure()
        options = self.transfer_options(assume_yes=False)
        self.assertEqual(secrets.push_key(options, lambda _: False), 0)
        self.assertFalse(
            (remote_home / ".config" / "sops" / "age" / "keys.txt").exists()
        )
        self.assertFalse(Path(os.environ["FAKE_SCP_LOG"]).exists())

    def test_push_key_requires_force_to_replace_a_different_identity(self) -> None:
        remote_home = self.configure_fake_ssh()
        self.configure()
        remote_identity = remote_home / ".config" / "sops" / "age" / "keys.txt"
        remote_identity.parent.mkdir(parents=True)
        remote_identity.write_text("AGE-SECRET-KEY-DIFFERENT\n", encoding="utf-8")
        os.chmod(remote_identity, 0o600)

        with self.assertRaisesRegex(secrets.SecretError, "rerun with --force"):
            secrets.push_key(self.transfer_options())
        self.assertEqual(
            remote_identity.read_text(encoding="utf-8"), "AGE-SECRET-KEY-DIFFERENT\n"
        )

        self.assertEqual(secrets.push_key(self.transfer_options(force=True)), 0)
        self.assertEqual(remote_identity.read_bytes(), self.identity.read_bytes())
        self.assertEqual(stat.S_IMODE(remote_identity.stat().st_mode), 0o600)

    def test_push_key_refuses_a_remote_identity_symlink(self) -> None:
        remote_home = self.configure_fake_ssh()
        self.configure()
        backing = remote_home / "backing-key"
        backing.write_text("do-not-replace\n", encoding="utf-8")
        remote_identity = remote_home / ".config" / "sops" / "age" / "keys.txt"
        remote_identity.parent.mkdir(parents=True)
        remote_identity.symlink_to(backing)

        with self.assertRaisesRegex(secrets.SecretError, "symlink or non-regular"):
            secrets.push_key(self.transfer_options(force=True))
        self.assertEqual(backing.read_text(encoding="utf-8"), "do-not-replace\n")

    def test_push_key_rejects_option_injection_and_unsafe_remote_paths(self) -> None:
        with self.assertRaisesRegex(secrets.SecretError, "SSH destination"):
            secrets.validate_destination("-oProxyCommand=bad")
        with self.assertRaisesRegex(secrets.SecretError, "remote home"):
            secrets.remote_identity_path(self.identity, "/tmp/key")
        with self.assertRaisesRegex(secrets.SecretError, "without '..'"):
            secrets.remote_identity_path(self.identity, "../key")

    def test_pull_key_installs_verifies_and_is_idempotent(self) -> None:
        remote_home = self.configure_fake_ssh()
        expected = self.identity.read_bytes()
        remote_identity = self.create_remote_identity(remote_home, expected)
        os.chmod(remote_identity, 0o644)
        self.identity.unlink()
        self.configure()

        self.assertEqual(secrets.pull_key(self.transfer_options()), 0)
        self.assertEqual(self.identity.read_bytes(), expected)
        self.assertEqual(stat.S_IMODE(self.identity.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(remote_identity.stat().st_mode), 0o600)
        self.assertEqual(
            Path(os.environ["FAKE_SCP_LOG"]).read_text(encoding="utf-8").count("\n"),
            1,
        )

        os.chmod(self.identity, 0o644)
        self.assertEqual(secrets.pull_key(self.transfer_options()), 0)
        self.assertEqual(stat.S_IMODE(self.identity.stat().st_mode), 0o600)
        self.assertEqual(
            Path(os.environ["FAKE_SCP_LOG"]).read_text(encoding="utf-8").count("\n"),
            1,
        )

    def test_pull_key_decline_leaves_local_identity_absent(self) -> None:
        remote_home = self.configure_fake_ssh()
        self.create_remote_identity(remote_home)
        self.identity.unlink()
        self.configure()

        options = self.transfer_options(assume_yes=False)
        self.assertEqual(secrets.pull_key(options, lambda _: False), 0)
        self.assertFalse(self.identity.exists())
        self.assertFalse(Path(os.environ["FAKE_SCP_LOG"]).exists())

    def test_pull_key_requires_force_to_replace_a_different_identity(self) -> None:
        remote_home = self.configure_fake_ssh()
        remote_contents = b"AGE-SECRET-KEY-REMOTE\n"
        self.create_remote_identity(remote_home, remote_contents)
        original = self.identity.read_bytes()
        self.configure()

        with self.assertRaisesRegex(secrets.SecretError, "rerun with --force"):
            secrets.pull_key(self.transfer_options())
        self.assertEqual(self.identity.read_bytes(), original)

        self.assertEqual(secrets.pull_key(self.transfer_options(force=True)), 0)
        self.assertEqual(self.identity.read_bytes(), remote_contents)
        self.assertEqual(stat.S_IMODE(self.identity.stat().st_mode), 0o600)

    def test_pull_key_refuses_missing_remote_and_local_symlinks(self) -> None:
        remote_home = self.configure_fake_ssh()
        identity_contents = self.identity.read_bytes()
        self.identity.unlink()
        self.configure()
        with self.assertRaisesRegex(
            secrets.SecretError, "remote age identity is missing"
        ):
            secrets.pull_key(self.transfer_options())

        self.create_remote_identity(remote_home, identity_contents)
        backing = self.root / "local-backing"
        backing.write_text("do-not-replace\n", encoding="utf-8")
        self.identity.symlink_to(backing)
        with self.assertRaisesRegex(secrets.SecretError, "non-regular"):
            secrets.pull_key(self.transfer_options(force=True))
        self.assertEqual(backing.read_text(encoding="utf-8"), "do-not-replace\n")

    def test_pull_key_can_restore_locally_in_the_same_command(self) -> None:
        remote_home = self.configure_fake_ssh()
        self.create_remote_identity(remote_home)
        self.identity.unlink()
        self.configure()

        with mock.patch.object(secrets, "restore_local_secrets") as restore:
            self.assertEqual(
                secrets.pull_key(self.transfer_options(restore=True)),
                0,
            )
        restore.assert_called_once_with()

    def test_local_restore_runs_upgrade_then_doctor(self) -> None:
        local_root = self.root / "local-envy"
        local_bin = local_root / "bin"
        local_bin.mkdir(parents=True)
        restore_log = self.root / "local-restore.log"
        local_cli = local_bin / "envy"
        local_cli.write_text(
            "#!/bin/sh\n" f"printf '%s\\n' \"$*\" >> {str(restore_log)!r}\n",
            encoding="utf-8",
        )
        os.chmod(local_cli, 0o755)

        with mock.patch.dict(os.environ, {"ENVY_ROOT": str(local_root)}):
            secrets.restore_local_secrets()
        self.assertEqual(
            restore_log.read_text(encoding="utf-8").splitlines(),
            ["upgrade --only secrets --force", "doctor secrets --verbose"],
        )

    def test_empty_settings_use_standalone_defaults(self) -> None:
        self.manifest.write_text(
            json.dumps({"settings": {"secrets": {"sources": []}}}),
            encoding="utf-8",
        )
        with mock.patch.dict(os.environ, {"HOME": str(self.root)}):
            config = secrets.configuration()
        self.assertEqual(config.target, self.root / ".secrets")
        self.assertEqual(
            config.identity_file, self.root / ".config" / "sops" / "age" / "keys.txt"
        )
        self.assertEqual(len(config.sources), 1)
        self.assertEqual(config.sources[0].path, self.root / ".secrets.sops")
        self.assertEqual(config.sources[0].input_type, "binary")
        self.assertEqual(config.sources[0].output_type, "binary")

    def test_standalone_defaults_restore_the_runtime_file(self) -> None:
        self.manifest.write_text(
            json.dumps({"settings": {"secrets": {"sources": []}}}),
            encoding="utf-8",
        )
        default_identity = self.root / ".config" / "sops" / "age" / "keys.txt"
        default_identity.parent.mkdir(parents=True)
        default_identity.write_bytes(self.identity.read_bytes())
        os.chmod(default_identity, 0o600)
        default_source = self.root / ".secrets.sops"
        default_source.write_bytes(self.source.read_bytes())
        os.chmod(default_source, 0o600)

        with mock.patch.dict(os.environ, {"HOME": str(self.root)}):
            self.assertEqual(secrets.converge(), 0)
        self.assertEqual((self.root / ".secrets").read_bytes(), b"EXAMPLE=private\n")


if __name__ == "__main__":
    unittest.main()
