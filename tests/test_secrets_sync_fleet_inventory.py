"""FP7: typed record preservation; only owned synthetic credentials and keys."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from secrets_sync.errors import SyncError
from secrets_sync.inventory import Inventory, Ref, decode_inventory, encode_inventory
from secrets_sync.ssh_format import Material


class FleetInventoryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="envy-fleet-inventory-")
        cls.addClassCleanup(cls.temp.cleanup)
        root = Path(cls.temp.name).resolve()
        path = root / "fixture-key"
        result = subprocess.run(["/usr/bin/ssh-keygen", "-t", "ed25519", "-f", str(path), "-C", "fixture"],
                                input=b"\n\n", env={"HOME": str(root), "PATH": "/usr/bin:/bin"},
                                capture_output=True, start_new_session=True, timeout=15)
        if result.returncode:
            raise RuntimeError("owned SSH fixture generation failed")
        cls.material = Material("id_ed25519", Path(str(path) + ".pub").read_text(), path.read_text())

    def refs(self):
        return (Ref("environment", name="FIXTURE"), Ref("github", host="example.invalid", account="fixture"),
                Ref("git", host="example.invalid", account="fixture"),
                Ref("ssh", name=self.material.fingerprint))

    def inventory(self):
        env, gh, git, ssh = self.refs()
        return Inventory({env: "synthetic-env", gh: "synthetic-gh", git: "synthetic-git",
                          ssh: self.material.encode()}, 3)

    def test_schema_three_roundtrip_preserves_all_types_without_value_repr(self):
        value = self.inventory()
        decoded = decode_inventory(encode_inventory(value))
        self.assertTrue(value.entries == decoded.entries, "mixed inventory changed")
        self.assertEqual(decoded.version, 3)
        self.assertNotIn("synthetic-", repr(value))
        self.assertNotIn("PRIVATE KEY", repr(value))
        raw = json.loads(encode_inventory(value))
        record = next(record for record in raw["entries"] if record["type"] == "ssh")
        self.assertEqual(set(record), {"type", "fingerprint", "filename", "public", "private"})
        self.assertTrue(record["private"] == self.material.private, "private bytes changed")

    def test_selecting_environment_preserves_other_records(self):
        destination = self.inventory()
        env = self.refs()[0]
        source = Inventory.environment({"FIXTURE": "synthetic-updated"})
        result = destination.reconcile(source, {env: "overwrite"})
        self.assertEqual(result.version, 3)
        for ref, value in destination.entries.items():
            self.assertTrue(result.entries[ref] == ("synthetic-updated" if ref == env else value),
                            "unselected credential changed")

    def test_upgrade_only_when_selected_and_retain_version_after_removal(self):
        source = self.inventory()
        for ref in self.refs()[2:]:
            empty = Inventory({})
            self.assertIs(empty.reconcile(source, {}), empty)
            added = empty.reconcile(source, {ref: "add"})
            self.assertEqual(added.version, 3)
            removed = added.reconcile(Inventory({}), {ref: "remove"})
            self.assertEqual(removed.version, 3)
            self.assertFalse(removed.entries)

    def test_unconfigured_stores_are_blocked(self):
        value = self.inventory()
        env, gh, git, ssh = self.refs()
        self.assertEqual(value.blocked(), {gh, git, ssh})
        allowed = value.blocked(accounts=((gh.host, gh.account),), git_accounts=((git.host, git.account),),
                                ssh_names=(self.material.filename,))
        self.assertEqual(allowed, set())

    def test_ssh_identity_requires_matching_public_fingerprint(self):
        bad = json.loads(encode_inventory(self.inventory()))
        record = next(row for row in bad["entries"] if row["type"] == "ssh")
        record["fingerprint"] = "SHA256:" + "A" * 43
        with self.assertRaises(SyncError):
            decode_inventory(json.dumps(bad).encode())

    def test_versions_and_unknown_fields_refuse(self):
        good = json.loads(encode_inventory(self.inventory()))
        for version in (True, 2, 4):
            with self.subTest(version=version), self.assertRaises(SyncError):
                decode_inventory(json.dumps({**good, "version": version}).encode())
        good["entries"][-1]["command"] = "unsupported"
        with self.assertRaises(SyncError):
            decode_inventory(json.dumps(good).encode())

    def test_duplicate_fingerprints_refuse_despite_different_filenames(self):
        value = json.loads(encode_inventory(self.inventory()))
        ssh = next(row for row in value["entries"] if row["type"] == "ssh")
        value["entries"].append({**ssh, "filename": "different"})
        with self.assertRaises(SyncError):
            decode_inventory(json.dumps(value).encode())


if __name__ == "__main__":
    unittest.main()
