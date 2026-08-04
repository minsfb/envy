#!/usr/bin/env python3
from __future__ import annotations

import json
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path

import sys


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import manifest as manifest_module  # noqa: E402
import facts as facts_module  # noqa: E402
import state as state_module  # noqa: E402


class ManifestStateTest(unittest.TestCase):
    def write_bundle(
        self, root: Path, *, feature: str = "example", exit_code: int = 0
    ) -> Path:
        root.mkdir()
        handler = root / "feature.sh"
        handler.write_text(f"#!/usr/bin/env bash\nexit {exit_code}\n", encoding="utf-8")
        value = {
            "version": 1,
            "id": "example-bundle",
            "bundle_version": "1",
            "recommended_features": [feature],
            "environment": {"EXAMPLE_ROOT": "${BUNDLE_ROOT}"},
            "features": {
                feature: {
                    "description": "Example",
                    "tier": "optional",
                    "scope": {"global": True},
                    "converge": {"run": "feature.sh", "args": []},
                    "verify": {"run": "feature.sh", "args": []},
                }
            },
        }
        path = root / "envy.bundle.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_explicit_bundle_composition_and_handler(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle_root = Path(temporary).resolve() / "bundle"
            self.write_bundle(bundle_root)
            value = manifest_module.compose(ROOT / "envy.json", [str(bundle_root)])
            self.assertIn("example", value["features"])
            self.assertEqual(value["features"]["example"]["_origin"], "example-bundle")
            self.assertEqual(value["environment"]["EXAMPLE_ROOT"], str(bundle_root))
            self.assertEqual(
                manifest_module.handler_argv(value, "example", "converge"),
                [str((bundle_root / "feature.sh").resolve())],
            )

    def test_duplicate_feature_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle_root = Path(temporary) / "bundle"
            path = self.write_bundle(bundle_root, feature="path")
            with self.assertRaises(manifest_module.ManifestError):
                manifest_module.compose(ROOT / "envy.json", [str(path)])

    def test_bundle_cannot_replace_runtime_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle_root = Path(temporary) / "bundle"
            path = self.write_bundle(bundle_root)
            value = json.loads(path.read_text(encoding="utf-8"))
            value["environment"] = {"ENVY_ROOT": "/tmp/replaced"}
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(
                manifest_module.ManifestError, "reserved environment key"
            ):
                manifest_module.compose(ROOT / "envy.json", [str(path)])

    def test_portable_bundle_facts_and_containment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            bundle_root = temporary_root / "bundle"
            path = self.write_bundle(bundle_root)
            provider = bundle_root / "facts.sh"
            provider.write_text(
                "#!/usr/bin/env bash\n"
                'printf \'%s\\n\' \'{"platform":"linux-team",'
                '"hierarchy":["linux-team","linux","unix","global"],'
                '"environment":{"TEAM_PROXY":"proxy-value"}}\'\n',
                encoding="utf-8",
            )
            provider.chmod(0o755)
            value = json.loads(path.read_text(encoding="utf-8"))
            value["portable"] = True
            value["facts"] = {"run": "facts.sh"}
            path.write_text(json.dumps(value), encoding="utf-8")

            composed = manifest_module.compose(ROOT / "envy.json", [str(path)])
            resolved = facts_module.merge(composed)
            self.assertEqual(resolved["platform"], "linux-team")
            self.assertEqual(
                resolved["hierarchy"],
                ["linux-team", "linux", "unix", "global"],
            )
            self.assertEqual(resolved["environment"]["TEAM_PROXY"], "proxy-value")

            value["features"]["example"]["converge"] = {"run": "../outside.sh"}
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(
                manifest_module.ManifestError, "escapes its package root"
            ):
                manifest_module.compose(ROOT / "envy.json", [str(path)])

    def test_handler_exit_two_is_not_missing_handler(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle_root = Path(temporary) / "bundle"
            self.write_bundle(bundle_root, exit_code=2)
            script = r"""
set +e
ENVY_ROOT="$1"
source "$ENVY_ROOT/lib/features.sh"
nv_manifest_init "$2" || exit
nv_run_feature_handler example converge
handler_rc=$?
nv_run_feature_handler example remove
missing_rc=$?
printf '%s %s %s\n' "$handler_rc" "$missing_rc" "$ENVY_HANDLER_UNAVAILABLE"
nv_manifest_cleanup
"""
            process = subprocess.run(
                ["bash", "-c", script, "_", str(ROOT), str(bundle_root)],
                check=True,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(process.stdout.strip(), "2 125 125")

    def test_dependencies_are_validated_and_stably_ordered(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle_root = Path(temporary) / "bundle"
            bundle_root.mkdir()
            definition = {
                "version": 1,
                "id": "ordered-bundle",
                "bundle_version": "1",
                "features": {
                    "consumer": {
                        "description": "Consumer",
                        "tier": "optional",
                        "scope": {"global": True},
                        "depends_on": ["provider"],
                    },
                    "provider": {
                        "description": "Provider",
                        "tier": "optional",
                        "scope": {"global": True},
                    },
                },
            }
            path = bundle_root / "envy.bundle.json"
            path.write_text(json.dumps(definition), encoding="utf-8")
            composed = manifest_module.compose(ROOT / "envy.json", [str(path)])
            names = list(composed["features"])
            self.assertLess(names.index("provider"), names.index("consumer"))

            definition["features"]["provider"]["depends_on"] = ["consumer"]
            path.write_text(json.dumps(definition), encoding="utf-8")
            with self.assertRaisesRegex(
                manifest_module.ManifestError, "dependency cycle"
            ):
                manifest_module.compose(ROOT / "envy.json", [str(path)])

    def test_smart_merge_is_a_core_prerequisite_for_chezmoi(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            composed = manifest_module.compose(ROOT / "envy.json", [])
            features = composed["features"]
            names = list(features)

            self.assertEqual(features["smart-merge"]["tier"], "core")
            self.assertEqual(features["chezmoi"]["depends_on"], ["smart-merge"])
            self.assertLess(names.index("smart-merge"), names.index("chezmoi"))
            self.assertNotIn("smart-merge", composed["recommended_features"])

            manifest_path = root / "manifest.json"
            state_path = root / "state.json"
            manifest_path.write_text(json.dumps(composed), encoding="utf-8")
            state = state_module.new_state(
                manifest_path, "linux", "zsh", str(ROOT), "core"
            )
            state["features"]["smart-merge"].update(
                {"enabled": False, "status": "disabled", "tier": "recommended"}
            )
            state_module.save(state_path, state)

            state_module.migrate(state_path, manifest_path, "linux", "zsh", str(ROOT))
            migrated = state_module.load(state_path)
            self.assertTrue(migrated["features"]["smart-merge"]["enabled"])
            self.assertEqual(migrated["features"]["smart-merge"]["status"], "pending")
            self.assertEqual(migrated["features"]["smart-merge"]["tier"], "core")

    def test_remote_tmux_is_optional_and_depends_on_both_features(self) -> None:
        composed = manifest_module.compose(ROOT / "envy.json", [])
        features = composed["features"]
        names = list(features)
        definition = features["remote-development-tmux"]
        self.assertEqual(definition["tier"], "optional")
        self.assertFalse(definition["default_enabled"])
        self.assertEqual(
            definition["depends_on"],
            ["tmux-persistence", "remote-development"],
        )
        self.assertLess(
            names.index("tmux-persistence"), names.index("remote-development-tmux")
        )
        self.assertLess(
            names.index("remote-development"), names.index("remote-development-tmux")
        )

    def test_v2_alias_and_detached_feature_return(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            core_value = manifest_module.compose(ROOT / "envy.json", [])
            core_manifest = root / "core.json"
            core_manifest.write_text(json.dumps(core_value), encoding="utf-8")
            state_path = root / "state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "installed": True,
                        "machine_id": socket.gethostname(),
                        "platform": "linux",
                        "shell": "zsh",
                        "profile": "recommended",
                        "features": {
                            "claude-skills": {
                                "enabled": True,
                                "status": "current",
                                "data": {"legacy": True},
                            },
                            "example": {"enabled": True, "status": "current"},
                        },
                    }
                ),
                encoding="utf-8",
            )
            state_module.migrate(state_path, core_manifest, "linux", "zsh", str(ROOT))
            migrated = state_module.load(state_path)
            self.assertEqual(migrated["schema_version"], 3)
            self.assertTrue(migrated["features"]["agent-assets"]["enabled"])
            self.assertTrue(migrated["features"]["agent-assets"]["data"]["legacy"])
            self.assertIn("example", migrated["detached_features"])

            bundle_root = root / "bundle"
            self.write_bundle(bundle_root)
            composed = manifest_module.compose(
                ROOT / "envy.json", [str(bundle_root)]
            )
            composed_path = root / "composed.json"
            composed_path.write_text(json.dumps(composed), encoding="utf-8")
            state_module.migrate(state_path, composed_path, "linux", "zsh", str(ROOT))
            restored = state_module.load(state_path)
            self.assertTrue(restored["features"]["example"]["enabled"])
            self.assertNotIn("example", restored["detached_features"])


if __name__ == "__main__":
    unittest.main()
