"""Typed foundation tests: owned data only; no gh or credential-store access."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from secrets_sync.crypto import Crypto, encode_payload
from secrets_sync.errors import SyncError
from secrets_sync.github_config import parse_github
from secrets_sync.inventory import Inventory, Ref, TOKEN_OVERRIDES, decode_inventory, encode_inventory


ENV = Ref("environment", name="EXAMPLE")
GH = Ref("github", host="example.invalid", account="fixture")
OTHER = Ref("github", host="other.invalid", account="other")


class InventoryTest(unittest.TestCase):
    def test_v1_encoding_is_unchanged(self):
        values = {"EXAMPLE": "synthetic"}
        inventory = decode_inventory(encode_payload(values))
        self.assertTrue(encode_inventory(inventory) == encode_payload(values))

    def test_typed_identity_and_display(self):
        self.assertNotEqual(ENV, GH)
        self.assertEqual(ENV.label, "env:EXAMPLE")
        self.assertEqual(GH.label, "gh:example.invalid/fixture")

    def test_selected_add_upgrades_without_dropping_environment(self):
        destination = Inventory({ENV: "preserved"})
        source = Inventory({GH: "selected", OTHER: "excluded"}, 2)
        result = destination.reconcile(source, {GH: "add"})
        self.assertEqual(result.version, 2)
        self.assertTrue(dict(result.entries) == {ENV: "preserved", GH: "selected"})

    def test_overwrite_preserves_unselected_types_both_directions(self):
        destination = Inventory({ENV: "destination", GH: "destination", OTHER: "unselected"}, 2)
        source = Inventory({ENV: "source", GH: "source"}, 2)
        for selected in (ENV, GH):
            result = destination.reconcile(source, {selected: "overwrite"})
            self.assertTrue(result.entries[selected] == source.entries[selected])
            self.assertTrue(all(result.entries[ref] == value for ref, value in destination.entries.items()
                                if ref != selected))

    def test_no_inferred_deletion(self):
        destination = Inventory({GH: "retained"}, 2)
        self.assertIs(destination.reconcile(Inventory({}), {}), destination)

    def test_v2_stays_v2_after_last_removal(self):
        result = Inventory({GH: "fixture"}, 2).reconcile(Inventory({}), {GH: "remove"})
        self.assertEqual(result.version, 2)
        self.assertTrue(encode_inventory(result) == b'{"entries":[],"version":2}')

    def test_keep_does_not_upgrade_v1(self):
        destination = Inventory({ENV: "fixture"})
        result = destination.reconcile(Inventory({GH: "fixture"}, 2), {GH: "keep"})
        self.assertIs(result, destination)

    def test_known_unconfigured_accounts_and_overrides_are_blocked(self):
        overrides = {Ref("environment", name=name): "fixture" for name in TOKEN_OVERRIDES}
        inventory = Inventory({GH: "fixture", OTHER: "fixture", **overrides}, 2)
        blocked = inventory.blocked([(GH.host, GH.account)], github_enabled=True)
        self.assertEqual(blocked, {OTHER, *overrides})
        with self.assertRaises(SyncError):
            inventory.reconcile(Inventory({}), {OTHER: "remove"}, blocked=blocked)

    def test_all_invalid_actions_leave_destination_unchanged(self):
        destination = Inventory({ENV: "before"})
        source = Inventory({GH: "fixture"}, 2)
        for actions in ({GH: "add", ENV: "overwrite"}, {GH: "remove"}, {GH: "invented"}):
            with self.assertRaises(SyncError):
                destination.reconcile(source, actions)
            self.assertTrue(dict(destination.entries) == {ENV: "before"})

    def test_master_identity_cannot_be_selected(self):
        source = Inventory({ENV: "prefix-PRIVATE-MARKER-suffix"})
        with self.assertRaises(SyncError):
            Inventory({}).reconcile(source, {ENV: "add"}, identity="PRIVATE-MARKER")

    def test_secret_representations_and_errors_are_sanitized(self):
        value = "SYNTHETIC-DO-NOT-REPORT"
        inventory = Inventory({GH: value}, 2)
        self.assertFalse(value in repr(inventory))
        try:
            Inventory({GH: value + "\n"}, 2)
        except SyncError as error:
            self.assertFalse(value in str(error))
        else:
            self.fail("invalid encoding accepted")

    def test_inventory_is_defensively_immutable(self):
        entries = {ENV: "before"}
        inventory = Inventory(entries)
        entries[ENV] = "after"
        self.assertTrue(inventory.entries[ENV] == "before")
        with self.assertRaises(TypeError):
            inventory.entries[ENV] = "mutation"

    def test_canonical_hosts_accounts_and_names(self):
        for host in ("", "UPPER.invalid", "a..b", "a.", "-a", "a-", "a:443", "https://a", "ü", "a" * 254):
            with self.assertRaises(SyncError):
                Ref("github", host=host, account="fixture")
        for account in ("", "UPPER", "-a", "a-", "a/b", "ü", "a" * 40):
            with self.assertRaises(SyncError):
                Ref("github", host="example.invalid", account=account)
        for name in ("", "1NAME", "NAME-X", "A" * 129):
            with self.assertRaises(SyncError):
                Ref("environment", name=name)

    def test_token_values_and_boundaries(self):
        for token in ("", "a b", "a\t", "a\n", "ü", "\x7f", "x" * 4097, None, 1):
            with self.assertRaises(SyncError):
                Inventory({GH: token}, 2)
        self.assertTrue(bool(Inventory({GH: "x" * 4096}, 2).entries))

    def test_environment_value_limits(self):
        for value in ("\x00", "\x80", "x" * 65537, "\ud800"):
            with self.assertRaises(SyncError):
                Inventory({ENV: value})
        self.assertTrue(bool(Inventory({ENV: "x" * 65536}).entries))

    def test_document_schema_refusals(self):
        valid = {"type": "github", "host": GH.host, "account": GH.account, "token": "fixture"}
        docs = [
            {"version": True, "entries": []}, {"version": 4, "entries": []},
            {"version": 2, "entries": {}}, {"version": 1, "entries": []},
            {"version": 2, "entries": [valid, valid]},
            {"version": 2, "entries": [{**valid, "extra": "field"}]},
            {"version": 2, "entries": [{**valid, "type": "unknown"}]},
            {"version": 2, "entries": [None]},
        ]
        for doc in docs:
            with self.assertRaises(SyncError):
                decode_inventory(json.dumps(doc).encode())

    def test_duplicate_keys_rejected_at_every_level(self):
        for raw in (b'{"version":2,"version":2,"entries":[]}',
                    b'{"version":2,"entries":[{"type":"environment","name":"A","name":"B","value":"x"}]}',
                    b'{"version":1,"entries":{"A":"x","A":"y"}}'):
            with self.assertRaises(SyncError):
                decode_inventory(raw)

    def test_aggregate_limits(self):
        with self.assertRaises(SyncError):
            Inventory({Ref("environment", name=f"A{i}"): "" for i in range(4097)})
        with mock.patch("secrets_sync.inventory.MAX_PLAIN", 40):
            with self.assertRaises(SyncError):
                encode_inventory(Inventory({ENV: "x" * 40}))
            with self.assertRaises(SyncError):
                decode_inventory(b" " * 41)

    def test_deterministic_serialization_and_roundtrip(self):
        values = {OTHER: "a", ENV: "b", GH: "c"}
        left = Inventory(values, 2)
        right = Inventory(dict(reversed(list(values.items()))), 2)
        self.assertTrue(encode_inventory(left) == encode_inventory(right))
        self.assertTrue(decode_inventory(encode_inventory(left)) == left)


class GitHubConfigTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="envy-gh-policy-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.command = self.root / "gh-fixture"
        self.command.write_text("#!/bin/sh\nexit 99\n")  # must never execute
        self.command.chmod(0o700)
        self.raw = {"executable": str(self.command), "config_directory": str(self.root / "auth"),
                    "accounts": [{"host": GH.host, "account": GH.account}]}

    def parse(self, raw=None):
        return parse_github(self.raw if raw is None else raw, repository=self.root / "repo",
                            local=self.root / "local.env", protected=[self.root / "recovery"])

    def test_no_credential_read_execution_or_directory_creation(self):
        with mock.patch("subprocess.Popen", side_effect=AssertionError("unexpected execution")):
            config = self.parse()
        self.assertEqual(config.accounts, ((GH.host, GH.account),))
        self.assertEqual(config.blocked_names, TOKEN_OVERRIDES)
        self.assertFalse(config.directory.exists())

    def test_empty_scope_and_prefix(self):
        config = self.parse({**self.raw, "accounts": [], "network_command_prefix": [str(self.command), "fixed"]})
        self.assertEqual(config.accounts, ())
        self.assertEqual(config.network_prefix, (str(self.command), "fixed"))

    def test_platform_policy_selects_only_public_execution_and_ca_settings(self):
        ca = self.root / "public-ca.pem"
        ca.write_text("public fixture")
        raw = {**self.raw, "platforms": {
            "darwin": {"ca_file": str(ca), "network_command_prefix": []},
            "linux": {"network_command_prefix": [str(self.command)]}}}
        with mock.patch("secrets_sync.github_config.sys.platform", "darwin"):
            mac = self.parse(raw)
        with mock.patch("secrets_sync.github_config.sys.platform", "linux"):
            linux = self.parse(raw)
        self.assertEqual(mac.ca_file, ca)
        self.assertEqual(mac.network_prefix, ())
        self.assertEqual(linux.network_prefix, (str(self.command),))
        self.assertIsNone(linux.ca_file)
        self.assertEqual(mac.accounts, linux.accounts)

    def test_platform_policy_cannot_change_accounts_or_smuggle_invalid_inactive_values(self):
        for overrides in ({"other": {}}, {"darwin": {"accounts": []}}, {"linux": {"ca_file": "relative"}},
                          {"linux": {"network_command_prefix": "shell"}}, {"linux": {"executable": None}}):
            with self.assertRaises(SyncError):
                self.parse({**self.raw, "platforms": overrides})

    def test_strict_settings(self):
        for changes in ({"extra": True}, {"accounts": None}, {"accounts": self.raw["accounts"] * 2},
                        {"accounts": [{}]}, {"accounts": [{"host": "UPPER", "account": "a"}]},
                        {"accounts": self.raw["accounts"] * 33}, {"network_command_prefix": "sh -c"},
                        {"network_command_prefix": ["bad\ncommand"]}, {"executable": "gh --flag"},
                        {"config_directory": "relative"}, {"config_directory": str(self.root / "a/../b")}):
            with self.assertRaises(SyncError):
                self.parse({**self.raw, **changes})

    def test_overlap_rejected_in_both_directions(self):
        for path in (self.root, self.root / "repo/auth", self.root / "local.env", self.root / "recovery/auth"):
            with self.assertRaises(SyncError):
                self.parse({**self.raw, "config_directory": str(path)})

    def test_private_directory_and_symlink_constraints(self):
        directory = self.root / "auth"
        directory.mkdir(mode=0o755)
        with self.assertRaises(SyncError):
            self.parse()
        directory.chmod(0o700)
        self.parse()
        alias = self.root / "alias"
        alias.symlink_to(directory)
        with self.assertRaises(SyncError):
            self.parse({**self.raw, "config_directory": str(alias)})

    def test_unsafe_executable_and_recheck(self):
        config = self.parse().bind()
        config.executable.recheck()
        self.command.chmod(0o777)
        with self.assertRaises(SyncError):
            config.executable.recheck()
        with self.assertRaises(SyncError):
            self.parse().bind()

    def test_public_ca_is_regular_and_not_writable_by_others(self):
        ca = self.root / "fixture-ca.pem"
        ca.write_text("public fixture; never loaded")
        ca.chmod(0o644)
        self.assertEqual(self.parse({**self.raw, "ca_file": str(ca)}).ca_file, ca)
        ca.chmod(0o666)
        with self.assertRaises(SyncError):
            self.parse({**self.raw, "ca_file": str(ca)}).bind()


class InventoryCryptoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.crypto = Crypto()
        raw = cls.crypto.call([cls.crypto.age_keygen], limit=4096)
        cls.identity = next(line for line in raw.decode().splitlines() if line.startswith("AGE-"))
        cls.recipient = cls.crypto.recipient(cls.identity)

    def test_real_sops_roundtrip_hides_all_typed_records(self):
        inventory = Inventory({ENV: "SYNTHETIC-ENV", GH: "SYNTHETIC-TOKEN"}, 2)
        ciphertext = self.crypto.encrypt_inventory(inventory, self.identity, self.recipient)
        self.assertTrue(self.crypto.decrypt_inventory(ciphertext, self.identity, self.recipient) == inventory)
        # Decode with the real SOPS boundary and stdlib JSON, not the product
        # inventory parser, as an independent structural/value oracle.
        raw = self.crypto.call([self.crypto.sops, "--decrypt", "--input-type", "json",
                                "--output-type", "binary", "/dev/stdin"], ciphertext, identity=self.identity)
        doc = json.loads(raw)
        self.assertEqual(doc["version"], 2)
        self.assertTrue(doc["entries"][1]["token"] == inventory.entries[GH])
        for marker in (ENV.name, GH.host, GH.account, *inventory.entries.values(), self.identity):
            self.assertFalse(marker.encode() in ciphertext)

    def test_v1_ciphertext_can_be_read_without_format_migration(self):
        ciphertext = self.crypto.encrypt({ENV.name: "fixture"}, self.identity, self.recipient)
        inventory = self.crypto.decrypt_inventory(ciphertext, self.identity, self.recipient)
        self.assertEqual(inventory.version, 1)

    def test_typed_ciphertext_stays_refused_by_environment_only_api(self):
        ciphertext = self.crypto.encrypt_inventory(Inventory({GH: "fixture"}, 2), self.identity, self.recipient)
        with self.assertRaises(SyncError):
            self.crypto.decrypt(ciphertext, self.identity, self.recipient)


if __name__ == "__main__":
    unittest.main()
