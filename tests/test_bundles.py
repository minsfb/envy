#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import sys


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import bundles as bundles_module  # noqa: E402
import state as state_module  # noqa: E402


class BundleLifecycleTest(unittest.TestCase):
    def feature(
        self,
        origin: str,
        *,
        depends_on: list[str] | None = None,
        tier: str = "optional",
    ) -> dict[str, object]:
        return {
            "description": f"Feature from {origin}",
            "tier": tier,
            "scope": {"global": True},
            "depends_on": depends_on or [],
            "_origin": origin,
        }

    def feature_state(
        self, origin: str, *, enabled: bool = True
    ) -> dict[str, object]:
        return {
            "enabled": enabled,
            "selection_explicit": True,
            "tier": "optional",
            "origin": origin,
            "status": "current" if enabled else "disabled",
            "verification": "current" if enabled else "unknown",
            "detail": "preserve me",
            "data": {"origin": origin},
        }

    def bundle(
        self, bundle_id: str, path: str, *, core: bool = False
    ) -> dict[str, object]:
        return {
            "id": bundle_id,
            "version": "1",
            "path": path,
            "manifest": f"{path}/envy.bundle.json",
            "sha256": f"sha-{bundle_id}",
            "core": core,
            "portable": not core,
        }

    def manifest(
        self,
        bundles: list[dict[str, object]],
        features: dict[str, dict[str, object]],
    ) -> dict[str, object]:
        return {
            "version": 3,
            "workspace_root": str(ROOT),
            "bundles": bundles,
            "features": features,
        }

    def state(
        self,
        manifest: dict[str, object],
        features: dict[str, dict[str, object]],
    ) -> dict[str, object]:
        return {
            "schema_version": 3,
            "installed": True,
            "platform": "linux",
            "shell": "zsh",
            "envy_root": str(ROOT),
            "workspace_root": str(ROOT),
            "bundles": manifest["bundles"],
            "bundle_order": [
                bundle["id"]
                for bundle in manifest["bundles"]
                if not bundle.get("core")
            ],
            "features": features,
            "detached_features": {},
            "detached_bundles": [],
            "ownership": {},
        }

    def write_json(self, path: Path, value: dict[str, object]) -> None:
        path.write_text(json.dumps(value), encoding="utf-8")

    def test_detach_plan_removes_enabled_features_in_reverse_order(self) -> None:
        core = self.bundle("envy-core", "/core", core=True)
        target = self.bundle("team", "/bundles/team")
        old_manifest = self.manifest(
            [core, target],
            {
                "core-feature": self.feature("envy-core", tier="core"),
                "provider": self.feature("team"),
                "consumer": self.feature("team", depends_on=["provider"]),
            },
        )
        new_manifest = self.manifest(
            [core],
            {"core-feature": self.feature("envy-core", tier="core")},
        )
        state = self.state(
            old_manifest,
            {
                "core-feature": self.feature_state("envy-core"),
                "provider": self.feature_state("team"),
                "consumer": self.feature_state("team"),
            },
        )

        plan = bundles_module.plan_detach(old_manifest, state, "team", new_manifest)

        self.assertEqual(plan["bundle_features"], ["provider", "consumer"])
        self.assertEqual(plan["departing_features"], ["consumer", "provider"])
        self.assertEqual(plan["surviving_features"], ["core-feature"])
        self.assertEqual(plan["bundle"]["order"], 0)
        self.assertEqual(plan["phase"], "removing")

    def test_detach_plan_rejects_enabled_cross_bundle_dependents(self) -> None:
        core = self.bundle("envy-core", "/core", core=True)
        target = self.bundle("team", "/bundles/team")
        consumer_bundle = self.bundle("consumer-bundle", "/bundles/consumer")
        old_manifest = self.manifest(
            [core, target, consumer_bundle],
            {
                "provider": self.feature("team"),
                "consumer": self.feature(
                    "consumer-bundle", depends_on=["provider"]
                ),
            },
        )
        new_manifest = self.manifest(
            [core, consumer_bundle],
            {"consumer": self.feature("consumer-bundle")},
        )
        state = self.state(
            old_manifest,
            {
                "provider": self.feature_state("team"),
                "consumer": self.feature_state("consumer-bundle"),
            },
        )

        with self.assertRaisesRegex(
            bundles_module.BundleError, r"consumer -> provider"
        ):
            bundles_module.plan_detach(old_manifest, state, "team", new_manifest)

        state["features"]["consumer"]["enabled"] = False
        plan = bundles_module.plan_detach(old_manifest, state, "team", new_manifest)
        self.assertEqual(plan["departing_features"], ["provider"])

    def test_attach_paths_restore_the_detached_bundle_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            first = root / "first"
            target = root / "target"
            last = root / "last"
            for path in (first, target, last):
                path.mkdir()
            self.write_json(
                target / "envy.bundle.json",
                {"version": 1, "id": "target", "features": {}},
            )
            manifest = self.manifest(
                [
                    self.bundle("envy-core", "/core", core=True),
                    self.bundle("first", str(first)),
                    self.bundle("last", str(last)),
                ],
                {},
            )
            state = self.state(manifest, {})
            state["detached_bundles"] = [{"id": "target", "order": 1}]

            bundle_id, paths = bundles_module.attach_paths(
                manifest, state, str(target)
            )

            self.assertEqual(bundle_id, "target")
            self.assertEqual(paths, [str(first), str(target.resolve()), str(last)])

    def test_bundle_order_is_stable_across_out_of_order_detach_and_attach(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            state_path = root / "state.json"
            core = self.bundle("envy-core", "/core", core=True)
            bundle_paths: dict[str, Path] = {}
            definitions: dict[str, dict[str, object]] = {}
            for bundle_id in ("a", "b"):
                path = root / bundle_id
                path.mkdir()
                self.write_json(
                    path / "envy.bundle.json",
                    {"version": 1, "id": bundle_id, "features": {}},
                )
                bundle_paths[bundle_id] = path
                definitions[bundle_id] = self.bundle(bundle_id, str(path))

            current_manifest = self.manifest(
                [core, definitions["a"], definitions["b"]], {}
            )
            state = self.state(current_manifest, {})
            self.write_json(state_path, state)

            def finish() -> None:
                current = state_module.load(state_path)
                current["bundle_transaction"]["reconcile_planned"] = True
                state_module.save(state_path, current)
                bundles_module.clear_transaction(state_path)

            def detach(bundle_id: str, remaining: list[str]) -> None:
                nonlocal current_manifest
                current = state_module.load(state_path)
                next_manifest = self.manifest(
                    [core, *(definitions[item] for item in remaining)], {}
                )
                current["bundle_transaction"] = bundles_module.plan_detach(
                    current_manifest, current, bundle_id, next_manifest
                )
                state_module.save(state_path, current)
                manifest_path = root / f"without-{bundle_id}.json"
                self.write_json(manifest_path, next_manifest)
                bundles_module.commit_detach(state_path, manifest_path)
                finish()
                current_manifest = next_manifest

            def attach(bundle_id: str, expected: list[str]) -> None:
                nonlocal current_manifest
                current = state_module.load(state_path)
                attached_id, paths = bundles_module.attach_paths(
                    current_manifest, current, str(bundle_paths[bundle_id])
                )
                self.assertEqual(attached_id, bundle_id)
                self.assertEqual(paths, [str(bundle_paths[item]) for item in expected])
                next_manifest = self.manifest(
                    [core, *(definitions[item] for item in expected)], {}
                )
                current["bundle_transaction"] = bundles_module.plan_attach(
                    current_manifest, current, next_manifest, bundle_id
                )
                state_module.save(state_path, current)
                manifest_path = root / f"with-{bundle_id}.json"
                self.write_json(manifest_path, next_manifest)
                bundles_module.commit_attach(state_path, manifest_path)
                finish()
                current_manifest = next_manifest

            detach("b", ["a"])
            detach("a", [])
            attach("b", ["b"])
            attach("a", ["a", "b"])

            final_state = state_module.load(state_path)
            self.assertEqual(final_state["bundle_order"], ["a", "b"])
            self.assertEqual(
                [bundle["id"] for bundle in final_state["bundles"]],
                ["envy-core", "a", "b"],
            )
            self.assertEqual(final_state["detached_bundles"], [])

    def test_pending_transaction_refuses_changed_manifest_identity(self) -> None:
        core = self.bundle("envy-core", "/core", core=True)
        target = self.bundle("team", "/bundles/team")
        old_manifest = self.manifest(
            [core, target], {"team-feature": self.feature("team")}
        )
        new_manifest = self.manifest([core], {})
        state = self.state(
            old_manifest, {"team-feature": self.feature_state("team")}
        )
        state["bundle_transaction"] = bundles_module.plan_detach(
            old_manifest, state, "team", new_manifest
        )

        bundles_module.validate_manifest_identity(state, old_manifest, "old")
        bundles_module.validate_manifest_identity(state, new_manifest, "new")
        changed_old = {**old_manifest, "workspace_root": "/changed"}
        changed_new = {**new_manifest, "workspace_root": "/changed"}
        for which, changed in (("old", changed_old), ("new", changed_new)):
            with self.subTest(which=which), self.assertRaisesRegex(
                bundles_module.BundleError, rf"{which} bundle manifest changed"
            ):
                bundles_module.validate_manifest_identity(state, changed, which)

    def test_attaching_transaction_can_reconstruct_paths_after_a_crash(self) -> None:
        manifest = self.manifest(
            [
                self.bundle("envy-core", "/core", core=True),
                self.bundle("first", "/bundles/first"),
                self.bundle("last", "/bundles/last"),
            ],
            {},
        )
        state = self.state(manifest, {})
        state["bundle_order"] = ["first", "target", "last"]
        state["bundle_transaction"] = {
            "action": "attach",
            "id": "target",
            "phase": "attaching",
            "bundle": {"id": "target", "path": "/bundles/target"},
        }

        self.assertEqual(
            bundles_module.resume_attach_paths(state),
            ["/bundles/first", "/bundles/target", "/bundles/last"],
        )

    def test_detach_commit_and_clear_require_completed_phases(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_path = root / "state.json"
            manifest_path = root / "manifest.json"
            core = self.bundle("envy-core", "/core", core=True)
            target = self.bundle("team", "/bundles/team")
            old_manifest = self.manifest(
                [core, target],
                {
                    "core-feature": self.feature("envy-core", tier="core"),
                    "team-feature": self.feature("team"),
                },
            )
            new_manifest = self.manifest(
                [core],
                {"core-feature": self.feature("envy-core", tier="core")},
            )
            state = self.state(
                old_manifest,
                {
                    "core-feature": self.feature_state("envy-core"),
                    "team-feature": self.feature_state("team"),
                },
            )
            state["bundle_transaction"] = bundles_module.plan_detach(
                old_manifest, state, "team", new_manifest
            )
            self.write_json(state_path, state)
            self.write_json(manifest_path, new_manifest)

            with self.assertRaisesRegex(
                bundles_module.BundleError, "removal phase is incomplete"
            ):
                bundles_module.commit_detach(state_path, manifest_path)

            bundles_module.transaction_add(
                state_path, "removed_features", "team-feature"
            )
            bundles_module.commit_detach(state_path, manifest_path)
            with self.assertRaisesRegex(
                bundles_module.BundleError, "not ready to complete"
            ):
                bundles_module.clear_transaction(state_path)

            bundles_module.set_reconcile_targets(state_path, ["core-feature"])
            with self.assertRaisesRegex(
                bundles_module.BundleError, "reconciliation is incomplete"
            ):
                bundles_module.clear_transaction(state_path)

            bundles_module.transaction_add(
                state_path, "reconciled_features", "core-feature"
            )
            bundles_module.clear_transaction(state_path)
            self.assertNotIn(
                "bundle_transaction", state_module.load(state_path)
            )

    def test_detach_rejects_remove_handler_without_abort_converge(self) -> None:
        core = self.bundle("envy-core", "/core", core=True)
        target = self.bundle("team", "/bundles/team")
        definition = self.feature("team")
        definition["remove"] = {"run": "remove.sh", "args": []}
        old_manifest = self.manifest(
            [core, target], {"irreversible": definition}
        )
        new_manifest = self.manifest([core], {})
        state = self.state(
            old_manifest, {"irreversible": self.feature_state("team")}
        )

        with self.assertRaisesRegex(
            bundles_module.BundleError, "removal but no converge handler"
        ):
            bundles_module.plan_detach(old_manifest, state, "team", new_manifest)

    def test_out_of_scope_detach_does_not_require_restore_handler(self) -> None:
        core = self.bundle("envy-core", "/core", core=True)
        target = self.bundle("team", "/bundles/team")
        definition = self.feature("team")
        definition["scope"] = {"darwin": True, "linux": False}
        definition["remove"] = {"run": "remove.sh", "args": []}
        old_manifest = self.manifest([core, target], {"darwin-only": definition})
        new_manifest = self.manifest([core], {})
        state = self.state(
            old_manifest, {"darwin-only": self.feature_state("team")}
        )

        plan = bundles_module.plan_detach(
            old_manifest, state, "team", new_manifest, ["linux", "global"]
        )

        self.assertEqual(plan["departing_features"], [])
        self.assertEqual(plan["bundle_features"], ["darwin-only"])

    def test_manifest_identity_includes_referenced_bundle_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            script = root / "handler.sh"
            script.write_text("first\n", encoding="utf-8")
            bundle = self.bundle("team", str(root))
            manifest = self.manifest(
                [self.bundle("envy-core", "/missing-core", core=True), bundle],
                {
                    "team-feature": {
                        **self.feature("team"),
                        "_root": str(root),
                        "converge": {"run": "handler.sh", "args": []},
                    }
                },
            )
            before = bundles_module.manifest_identity(manifest)
            script.write_text("second\n", encoding="utf-8")

            self.assertNotEqual(before, bundles_module.manifest_identity(manifest))

    def test_attach_restoration_is_scoped_to_the_feature_origin(self) -> None:
        core = self.bundle("envy-core", "/core", core=True)
        target = self.bundle("new-team", "/bundles/new-team")
        old_manifest = self.manifest([core], {})
        new_manifest = self.manifest(
            [core, target], {"shared-name": self.feature("new-team")}
        )
        state = self.state(old_manifest, {})
        state["detached_features"] = {
            "shared-name": self.feature_state("old-team")
        }

        with self.assertRaisesRegex(
            bundles_module.BundleError, "preserved history from another bundle"
        ):
            bundles_module.plan_attach(
                old_manifest, state, new_manifest, "new-team"
            )

    def test_attach_rejects_disabled_or_drifted_dependencies(self) -> None:
        core = self.bundle("envy-core", "/core", core=True)
        target = self.bundle("team", "/bundles/team")
        old_manifest = self.manifest(
            [core], {"provider": self.feature("envy-core", tier="core")}
        )
        new_manifest = self.manifest(
            [core, target],
            {
                "provider": self.feature("envy-core", tier="core"),
                "consumer": self.feature("team", depends_on=["provider"]),
            },
        )
        state = self.state(
            old_manifest, {"provider": self.feature_state("envy-core", enabled=False)}
        )
        state["detached_features"] = {
            "consumer": self.feature_state("team")
        }
        with self.assertRaisesRegex(
            bundles_module.BundleError, "depend on disabled features"
        ):
            bundles_module.plan_attach(old_manifest, state, new_manifest, "team")

        state["features"]["provider"]["enabled"] = True
        plan = bundles_module.plan_attach(old_manifest, state, new_manifest, "team")
        plan["baseline"] = {"provider": "drifted"}
        with self.assertRaisesRegex(
            bundles_module.BundleError, "dependency 'provider' is not current"
        ):
            bundles_module.validate_attach_plan(plan, new_manifest, ["global"])

        plan["baseline"]["provider"] = "current"
        bundles_module.validate_attach_plan(plan, new_manifest, ["global"])

    def test_commit_detach_and_attach_preserve_tombstone_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_path = root / "state.json"
            detached_manifest_path = root / "detached.json"
            attached_manifest_path = root / "attached.json"
            core = self.bundle("envy-core", "/core", core=True)
            target = self.bundle("team", "/bundles/team")
            attached_manifest = self.manifest(
                [core, target],
                {
                    "core-feature": self.feature("envy-core", tier="core"),
                    "team-feature": self.feature("team"),
                },
            )
            detached_manifest = self.manifest(
                [core],
                {"core-feature": self.feature("envy-core", tier="core")},
            )
            state = self.state(
                attached_manifest,
                {
                    "core-feature": self.feature_state("envy-core"),
                    "team-feature": self.feature_state("team"),
                },
            )
            state["bundle_transaction"] = bundles_module.plan_detach(
                attached_manifest, state, "team", detached_manifest
            )
            state["bundle_transaction"]["removed_features"] = ["team-feature"]
            self.write_json(state_path, state)
            self.write_json(detached_manifest_path, detached_manifest)
            self.write_json(attached_manifest_path, attached_manifest)

            bundles_module.commit_detach(state_path, detached_manifest_path)

            detached_state = state_module.load(state_path)
            self.assertNotIn("team-feature", detached_state["features"])
            self.assertEqual(
                detached_state["detached_features"]["team-feature"]["data"],
                {"origin": "team"},
            )
            self.assertEqual(detached_state["bundle_transaction"]["phase"], "reconciling")
            tombstone = detached_state["detached_bundles"][0]
            self.assertEqual(tombstone["id"], "team")
            self.assertEqual(tombstone["order"], 0)
            self.assertIn("detached_at", tombstone)
            self.assertEqual(
                [bundle["id"] for bundle in detached_state["bundles"]],
                ["envy-core"],
            )

            attach_plan = bundles_module.plan_attach(
                detached_manifest, detached_state, attached_manifest, "team"
            )
            self.assertEqual(attach_plan["forced_features"], ["team-feature"])
            detached_state["bundle_transaction"] = attach_plan
            state_module.save(state_path, detached_state)
            bundles_module.commit_attach(state_path, attached_manifest_path)

            reattached_state = state_module.load(state_path)
            self.assertEqual(reattached_state["detached_bundles"], [])
            self.assertEqual(
                [bundle["id"] for bundle in reattached_state["bundles"]],
                ["envy-core", "team"],
            )
            self.assertIn("team-feature", reattached_state["detached_features"])
            self.assertEqual(reattached_state["bundle_transaction"]["action"], "attach")
            self.assertEqual(
                reattached_state["bundle_transaction"]["phase"], "reconciling"
            )

    def test_forget_discards_bundle_metadata_and_feature_history(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_path = root / "state.json"
            manifest_path = root / "manifest.json"
            core = self.bundle("envy-core", "/core", core=True)
            target = self.bundle("team", "/bundles/team")
            old_manifest = self.manifest(
                [core, target],
                {
                    "core-feature": self.feature("envy-core", tier="core"),
                    "team-feature": self.feature("team"),
                },
            )
            new_manifest = self.manifest(
                [core],
                {"core-feature": self.feature("envy-core", tier="core")},
            )
            state = self.state(
                old_manifest,
                {
                    "core-feature": self.feature_state("envy-core"),
                    "team-feature": self.feature_state("team"),
                },
            )
            state["bundle_order"] = ["before", "team", "after"]
            state["detached_features"] = {
                "old-team-feature": self.feature_state("team"),
                "other-history": self.feature_state("other"),
            }
            state["detached_bundles"] = [{"id": "team", "order": 0}]
            state["bundle_transaction"] = {"action": "detach", "id": "team"}
            self.write_json(state_path, state)
            self.write_json(manifest_path, new_manifest)

            bundles_module.forget_bundle(state_path, manifest_path, "team")

            forgotten = state_module.load(state_path)
            self.assertNotIn("bundle_transaction", forgotten)
            self.assertEqual(forgotten["detached_bundles"], [])
            self.assertNotIn("team-feature", forgotten["features"])
            self.assertNotIn("team-feature", forgotten["detached_features"])
            self.assertNotIn("old-team-feature", forgotten["detached_features"])
            self.assertIn("other-history", forgotten["detached_features"])
            self.assertEqual(forgotten["bundle_order"], ["before", "after"])
            self.assertEqual(
                [bundle["id"] for bundle in forgotten["bundles"]],
                ["envy-core"],
            )


if __name__ == "__main__":
    unittest.main()
