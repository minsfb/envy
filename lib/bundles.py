#!/usr/bin/env python3
"""Plan and persist explicit bundle attach/detach transactions."""

from __future__ import annotations

import json
import hashlib
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import manifest as manifest_module
import state as state_module


class BundleError(RuntimeError):
    pass


def load_manifest(path: Path) -> dict[str, Any]:
    return state_module.load_manifest(path)


def load_state(path: Path) -> dict[str, Any]:
    return state_module.load(path)


def external_bundles(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item
        for item in manifest.get("bundles", [])
        if isinstance(item, dict) and not item.get("core")
    ]


def bundle_by_id(
    bundles: list[dict[str, Any]], bundle_id: str
) -> dict[str, Any] | None:
    return next((item for item in bundles if item.get("id") == bundle_id), None)


def detached_by_id(state: dict[str, Any], bundle_id: str) -> dict[str, Any] | None:
    values = state.get("detached_bundles", [])
    if not isinstance(values, list):
        return None
    return next(
        (item for item in values if isinstance(item, dict) and item.get("id") == bundle_id),
        None,
    )


def emit_nul(values: list[str]) -> None:
    for value in values:
        sys.stdout.buffer.write(value.encode() + b"\0")


def bundle_manifest(raw: str) -> tuple[Path, dict[str, Any]]:
    path = manifest_module.bundle_manifest(raw)
    return path, manifest_module.load_object(path)


def active_paths(manifest: dict[str, Any], exclude: str | None = None) -> list[str]:
    return [
        str(item["path"])
        for item in external_bundles(manifest)
        if item.get("id") != exclude
    ]


def startup_paths(state: dict[str, Any], exclude: str) -> list[str]:
    values = state.get("bundles", [])
    if not isinstance(values, list):
        return []
    return [
        str(item["path"])
        for item in values
        if isinstance(item, dict)
        and not item.get("core")
        and item.get("id") != exclude
        and item.get("path")
    ]


def attach_paths(
    manifest: dict[str, Any], state: dict[str, Any], raw_path: str
) -> tuple[str, list[str]]:
    path, value = bundle_manifest(raw_path)
    bundle_id = value.get("id")
    if not isinstance(bundle_id, str) or not bundle_id:
        raise BundleError(f"manifest id is required: {path}")
    active = external_bundles(manifest)
    existing = bundle_by_id(active, bundle_id)
    if existing is not None:
        raise BundleError(
            f"bundle '{bundle_id}' is already attached at {existing.get('path', 'unknown')}"
        )
    paths = [str(item["path"]) for item in active]
    order_ids = state.get("bundle_order", [])
    if isinstance(order_ids, list) and bundle_id in order_ids:
        active_by_id = {str(item["id"]): str(item["path"]) for item in active}
        active_by_id[bundle_id] = str(path.parent.resolve())
        paths = [active_by_id[item] for item in order_ids if item in active_by_id]
        return bundle_id, paths
    historical = detached_by_id(state, bundle_id)
    order = historical.get("order") if historical else None
    if not isinstance(order, int) or order < 0:
        order = len(paths)
    paths.insert(min(order, len(paths)), str(path.parent.resolve()))
    return bundle_id, paths


def feature_enabled(state: dict[str, Any], name: str) -> bool:
    feature = state.get("features", {}).get(name, {})
    return isinstance(feature, dict) and bool(feature.get("enabled"))


def definition_in_scope(definition: dict[str, Any], hierarchy: list[str]) -> bool:
    scope = definition.get("scope")
    if not isinstance(scope, dict):
        return True
    for ancestor in hierarchy:
        if ancestor in scope:
            return bool(scope[ancestor])
    return False


def restored_feature_enabled(
    state: dict[str, Any], name: str, definition: dict[str, Any]
) -> bool:
    if definition.get("lifecycle") == "deprecated":
        return False
    historical = state.get("detached_features", {}).get(name)
    if isinstance(historical, dict) and historical.get("origin") in {
        None,
        definition.get("_origin"),
    }:
        return bool(historical.get("enabled"))
    return bool(state.get("installed", True) and definition.get("tier") == "core")


def write_plan(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def manifest_identity(value: dict[str, Any]) -> str:
    digest = hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    )
    roots: set[Path] = set()
    for bundle in value.get("bundles", []):
        if not isinstance(bundle, dict) or not bundle.get("path"):
            continue
        root = Path(str(bundle["path"]))
        if bundle.get("core"):
            roots.update((root / "features", root / "lib"))
        else:
            roots.add(root)
    for definition in value.get("features", {}).values():
        if not isinstance(definition, dict) or not definition.get("_root"):
            continue
        root = Path(str(definition["_root"]))
        for action in manifest_module.HANDLERS:
            handler = definition.get(action)
            if isinstance(handler, dict) and handler.get("run"):
                roots.add(root / str(handler["run"]))
    for provider in value.get("fact_providers", []):
        if not isinstance(provider, dict) or not provider.get("root"):
            continue
        handler = provider.get("handler")
        if isinstance(handler, dict) and handler.get("run"):
            roots.add(Path(str(provider["root"])) / str(handler["run"]))
    for entries in value.get("resources", {}).values():
        if isinstance(entries, list):
            roots.update(Path(str(item)) for item in entries)
    for path in sorted(roots, key=str):
        update_path_identity(digest, path)
    return digest.hexdigest()


def update_path_identity(digest: Any, path: Path) -> None:
    ignored = {".git", ".hg", ".svn", "__pycache__", ".pytest_cache"}
    if path.is_symlink():
        digest.update(f"L:{path}:{path.readlink()}\n".encode())
        return
    if path.is_file():
        digest.update(f"F:{path}:{path.stat().st_mode & 0o777}\n".encode())
        digest.update(path.read_bytes())
        return
    if not path.is_dir():
        digest.update(f"M:{path}\n".encode())
        return
    digest.update(f"D:{path}\n".encode())
    for child in sorted(path.rglob("*"), key=lambda item: str(item.relative_to(path))):
        if any(part in ignored for part in child.relative_to(path).parts):
            continue
        if child.is_symlink():
            digest.update(f"L:{child}:{child.readlink()}\n".encode())
        elif child.is_file():
            digest.update(
                f"F:{child}:{child.stat().st_mode & 0o777}\n".encode()
            )
            digest.update(child.read_bytes())
        elif child.is_dir():
            digest.update(f"D:{child}\n".encode())


def bundle_order(state: dict[str, Any], active: list[dict[str, Any]]) -> list[str]:
    value = state.get("bundle_order", [])
    result = [item for item in value if isinstance(item, str)] if isinstance(value, list) else []
    for item in active:
        bundle_id = item.get("id")
        if isinstance(bundle_id, str) and bundle_id not in result:
            result.append(bundle_id)
    return result


def plan_detach(
    old_manifest: dict[str, Any],
    state: dict[str, Any],
    bundle_id: str,
    new_manifest: dict[str, Any],
    hierarchy: list[str] | None = None,
) -> dict[str, Any]:
    active = external_bundles(old_manifest)
    target = bundle_by_id(active, bundle_id)
    if target is None:
        if detached_by_id(state, bundle_id) is not None:
            raise BundleError(f"bundle '{bundle_id}' is already detached")
        raise BundleError(f"unknown active bundle: {bundle_id}")
    if bundle_by_id(external_bundles(new_manifest), bundle_id) is not None:
        raise BundleError(f"detach plan still contains bundle: {bundle_id}")

    definitions = old_manifest["features"]
    target_features = [
        name for name, definition in definitions.items() if definition.get("_origin") == bundle_id
    ]
    target_set = set(target_features)
    pending_retirements = [
        name for name in target_features
        if definitions[name].get("lifecycle") == "deprecated"
        and state.get("features", {}).get(name, {}).get("retirement")
        and (state["features"][name]["retirement"].get("status") != "retired"
             or state["features"][name]["retirement"].get("revision")
             != definitions[name].get("retirement_revision")
             or state["features"][name].get("verification") in {"drifted", "failed"})
    ]
    if pending_retirements:
        raise BundleError("complete feature retirement before detach with envy upgrade --only "
                          + ",".join(pending_retirements))
    blockers: list[str] = []
    for name, definition in definitions.items():
        if name in target_set or not feature_enabled(state, name):
            continue
        dependencies = set(definition.get("depends_on", [])) & target_set
        if dependencies:
            blockers.append(f"{name} -> {', '.join(sorted(dependencies))}")
    if blockers:
        raise BundleError(
            "enabled features depend on the departing bundle: " + "; ".join(blockers)
        )

    active_hierarchy = hierarchy or ["global"]
    departing = [
        name
        for name in target_features
        if feature_enabled(state, name)
        and definitions[name].get("lifecycle") != "deprecated"
        and definition_in_scope(definitions[name], active_hierarchy)
    ]
    irreversible = [
        name
        for name in departing
        if definitions[name].get("remove") and not definitions[name].get("converge")
    ]
    if irreversible:
        raise BundleError(
            "enabled features have removal but no converge handler for abort: "
            + ", ".join(irreversible)
        )
    departing.reverse()
    surviving = [
        name
        for name in new_manifest["features"]
        if feature_enabled(state, name)
    ]
    bundle = deepcopy(target)
    order_ids = bundle_order(state, active)
    bundle["order"] = order_ids.index(bundle_id)
    return {
        "version": 1,
        "action": "detach",
        "id": bundle_id,
        "phase": "removing",
        "bundle": bundle,
        "old_manifest_identity": manifest_identity(old_manifest),
        "new_manifest_identity": manifest_identity(new_manifest),
        "bundle_features": target_features,
        "departing_features": departing,
        "surviving_features": surviving,
        "forced_features": [],
        "baseline": {},
        "removed_features": [],
        "changed_features": [],
        "reconcile_targets": [],
        "reconciled_features": [],
        "reconcile_planned": False,
    }


def plan_attach(
    old_manifest: dict[str, Any],
    state: dict[str, Any],
    new_manifest: dict[str, Any],
    bundle_id: str,
    hierarchy: list[str] | None = None,
) -> dict[str, Any]:
    if bundle_by_id(external_bundles(old_manifest), bundle_id) is not None:
        raise BundleError(f"bundle '{bundle_id}' is already attached")
    active = external_bundles(new_manifest)
    target = bundle_by_id(active, bundle_id)
    if target is None:
        raise BundleError(f"attach plan does not contain bundle: {bundle_id}")
    surviving = [
        name for name in old_manifest["features"] if feature_enabled(state, name)
    ]
    target_features = [
        name
        for name, definition in new_manifest["features"].items()
        if definition.get("_origin") == bundle_id
    ]
    detached_features = state.get("detached_features", {})
    collisions = [
        name
        for name in target_features
        if isinstance(detached_features.get(name), dict)
        and detached_features[name].get("origin") not in {None, bundle_id}
    ]
    if collisions:
        raise BundleError(
            "attached feature IDs conflict with preserved history from another bundle: "
            + ", ".join(collisions)
            + "; forget the former bundle first"
        )
    forced = [
        name
        for name in target_features
        if restored_feature_enabled(state, name, new_manifest["features"][name])
    ]
    enabled_after = set(surviving) | set(forced)
    active_hierarchy = hierarchy or ["global"]
    dependency_blockers = [
        f"{name} -> {dependency}"
        for name in forced
        if definition_in_scope(new_manifest["features"][name], active_hierarchy)
        for dependency in new_manifest["features"][name].get("depends_on", [])
        if dependency not in enabled_after
    ]
    if dependency_blockers:
        raise BundleError(
            "restored features depend on disabled features: "
            + "; ".join(dependency_blockers)
        )
    bundle = deepcopy(target)
    order_ids = bundle_order(state, external_bundles(old_manifest))
    if bundle_id not in order_ids:
        order_ids.append(bundle_id)
    bundle["order"] = order_ids.index(bundle_id)
    return {
        "version": 1,
        "action": "attach",
        "id": bundle_id,
        "phase": "attaching",
        "bundle": bundle,
        "old_manifest_identity": manifest_identity(old_manifest),
        "new_manifest_identity": manifest_identity(new_manifest),
        "bundle_features": target_features,
        "departing_features": [],
        "surviving_features": surviving,
        "forced_features": forced,
        "baseline": {},
        "removed_features": [],
        "changed_features": [],
        "reconcile_targets": [],
        "reconciled_features": [],
        "reconcile_planned": False,
    }


def ensure_transaction(state: dict[str, Any]) -> dict[str, Any]:
    value = state.get("bundle_transaction")
    if not isinstance(value, dict):
        raise BundleError("no bundle transaction is pending")
    return value


def start_transaction(state_path: Path, plan_path: Path) -> None:
    state = load_state(state_path)
    if isinstance(state.get("bundle_transaction"), dict):
        pending = state["bundle_transaction"]
        raise BundleError(
            f"bundle {pending.get('action', 'operation')} for "
            f"'{pending.get('id', 'unknown')}' is already pending"
        )
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if not isinstance(plan, dict):
        raise BundleError("bundle transaction plan must be an object")
    state["bundle_transaction"] = plan
    state_module.save(state_path, state)


def update_plan_baseline(plan_path: Path, pairs: list[str]) -> None:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    baseline = plan.setdefault("baseline", {})
    for pair in pairs:
        name, result = pair.split("=", 1)
        if result not in {"current", "drifted", "unknown"}:
            raise BundleError(f"invalid verification result for {name}: {result}")
        baseline[name] = result
    write_plan(plan_path, plan)


def transaction_add(state_path: Path, field: str, value: str) -> None:
    if field not in {"removed_features", "changed_features", "reconciled_features"}:
        raise BundleError(f"unsupported transaction list: {field}")
    state = load_state(state_path)
    operation = ensure_transaction(state)
    values = operation.setdefault(field, [])
    if not isinstance(values, list):
        raise BundleError(f"invalid transaction list: {field}")
    if value not in values:
        values.append(value)
        state_module.save(state_path, state)


def set_reconcile_targets(state_path: Path, values: list[str]) -> None:
    state = load_state(state_path)
    operation = ensure_transaction(state)
    operation["reconcile_targets"] = values
    operation["reconcile_planned"] = True
    state_module.save(state_path, state)


def replace_detached_bundle(
    state: dict[str, Any], bundle: dict[str, Any]
) -> None:
    values = state.setdefault("detached_bundles", [])
    if not isinstance(values, list):
        values = []
        state["detached_bundles"] = values
    values[:] = [
        item
        for item in values
        if not isinstance(item, dict) or item.get("id") != bundle.get("id")
    ]
    values.append(bundle)


def apply_manifest_state(state: dict[str, Any], manifest_path: Path) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    state["bundles"] = state_module.bundle_state(manifest)
    order = state.setdefault("bundle_order", [])
    if not isinstance(order, list):
        order = []
        state["bundle_order"] = order
    for item in external_bundles(manifest):
        if item.get("id") not in order:
            order.append(item["id"])
    state["manifest_fingerprint"] = state_module.manifest_fingerprint(manifest_path)
    state["workspace_root"] = manifest.get("workspace_root")
    return manifest


def commit_detach(state_path: Path, manifest_path: Path) -> None:
    state = load_state(state_path)
    operation = ensure_transaction(state)
    if operation.get("action") != "detach" or operation.get("phase") != "removing":
        raise BundleError("detach transaction is not ready to commit")
    pending = set(operation.get("departing_features", []))
    completed = set(operation.get("removed_features", []))
    if not pending.issubset(completed):
        raise BundleError("detach removal phase is incomplete")
    validate_manifest_identity(state, load_manifest(manifest_path), "new")
    manifest = apply_manifest_state(state, manifest_path)
    bundle_id = str(operation["id"])
    if bundle_by_id(external_bundles(manifest), bundle_id) is not None:
        raise BundleError(f"committed manifest still contains bundle: {bundle_id}")
    features = state.setdefault("features", {})
    detached = state.setdefault("detached_features", {})
    for name in operation.get("bundle_features", []):
        if name in features:
            detached[name] = features.pop(name)
    bundle = deepcopy(operation["bundle"])
    bundle["detached_at"] = state_module.now()
    replace_detached_bundle(state, bundle)
    operation["phase"] = "reconciling"
    state_module.save(state_path, state)


def commit_attach(state_path: Path, manifest_path: Path) -> None:
    state = load_state(state_path)
    operation = ensure_transaction(state)
    if operation.get("action") != "attach" or operation.get("phase") != "attaching":
        raise BundleError("attach transaction is not ready to commit")
    validate_manifest_identity(state, load_manifest(manifest_path), "new")
    manifest = apply_manifest_state(state, manifest_path)
    bundle_id = str(operation["id"])
    if bundle_by_id(external_bundles(manifest), bundle_id) is None:
        raise BundleError(f"committed manifest does not contain bundle: {bundle_id}")
    values = state.setdefault("detached_bundles", [])
    if isinstance(values, list):
        values[:] = [
            item
            for item in values
            if not isinstance(item, dict) or item.get("id") != bundle_id
        ]
    operation["phase"] = "reconciling"
    state_module.save(state_path, state)


def resume_attach_paths(state: dict[str, Any]) -> list[str]:
    operation = ensure_transaction(state)
    if operation.get("action") != "attach" or operation.get("phase") != "attaching":
        raise BundleError("attach transaction is not ready to commit")
    bundle = operation.get("bundle")
    if not isinstance(bundle, dict) or not bundle.get("path"):
        raise BundleError("attach transaction does not contain a bundle path")
    active = {
        str(item["id"]): str(item["path"])
        for item in state.get("bundles", [])
        if isinstance(item, dict)
        and not item.get("core")
        and item.get("id")
        and item.get("path")
    }
    bundle_id = str(operation["id"])
    active[bundle_id] = str(bundle["path"])
    order_ids = bundle_order(state, [])
    if bundle_id not in order_ids:
        order_ids.append(bundle_id)
    return [active[item] for item in order_ids if item in active]


def clear_transaction(state_path: Path, *, abort: bool = False) -> None:
    state = load_state(state_path)
    operation = ensure_transaction(state)
    if abort:
        if operation.get("action") != "detach" or operation.get("phase") != "removing":
            raise BundleError("only a pre-commit detach can be aborted")
    else:
        if operation.get("phase") != "reconciling" or not operation.get(
            "reconcile_planned"
        ):
            raise BundleError("bundle reconciliation is not ready to complete")
        pending = set(operation.get("reconcile_targets", []))
        completed = set(operation.get("reconciled_features", []))
        if not pending.issubset(completed):
            raise BundleError("bundle reconciliation is incomplete")
    state.pop("bundle_transaction", None)
    state_module.save(state_path, state)


def validate_manifest_identity(
    state: dict[str, Any], manifest: dict[str, Any], which: str
) -> None:
    operation = ensure_transaction(state)
    field = f"{which}_manifest_identity"
    expected = operation.get(field)
    if not isinstance(expected, str) or manifest_identity(manifest) != expected:
        raise BundleError(
            f"the {which} bundle manifest changed during the pending transaction; "
            "restore it or use bundle forget"
        )


def validate_attach_plan(
    plan: dict[str, Any], manifest: dict[str, Any], hierarchy: list[str]
) -> None:
    baseline = plan.get("baseline", {})
    surviving = set(plan.get("surviving_features", []))
    forced = set(plan.get("forced_features", []))
    for name in forced:
        definition = manifest.get("features", {}).get(name, {})
        if not isinstance(definition, dict) or not definition_in_scope(
            definition, hierarchy
        ):
            continue
        for dependency in definition.get("depends_on", []):
            dependency_definition = manifest.get("features", {}).get(dependency, {})
            if dependency in forced and (
                not isinstance(dependency_definition, dict)
                or not definition_in_scope(dependency_definition, hierarchy)
            ):
                raise BundleError(
                    f"cannot restore '{name}' while dependency "
                    f"'{dependency}' is out of scope"
                )
            if dependency in surviving and dependency not in forced:
                if baseline.get(dependency) != "current":
                    raise BundleError(
                        f"cannot restore '{name}' while dependency "
                        f"'{dependency}' is not current"
                    )


def validate_active_set(state: dict[str, Any], manifest: dict[str, Any]) -> None:
    if not state.get("installed", True):
        return
    expected = [
        (str(item.get("id")), str(Path(str(item.get("path"))).resolve()))
        for item in state.get("bundles", [])
        if isinstance(item, dict) and not item.get("core")
    ]
    actual = [
        (str(item.get("id")), str(Path(str(item.get("path"))).resolve()))
        for item in external_bundles(manifest)
    ]
    if actual != expected:
        raise BundleError(
            "--bundle cannot change an installed bundle set; use bundle attach/detach"
        )


def forget_bundle(state_path: Path, manifest_path: Path, bundle_id: str) -> None:
    state = load_state(state_path)
    active = [
        item
        for item in state.get("bundles", [])
        if isinstance(item, dict) and not item.get("core") and item.get("id") == bundle_id
    ]
    historical = detached_by_id(state, bundle_id)
    operation = state.get("bundle_transaction")
    pending = isinstance(operation, dict) and operation.get("id") == bundle_id
    if not active and historical is None and not pending:
        raise BundleError(f"unknown bundle: {bundle_id}")
    apply_manifest_state(state, manifest_path)
    features = state.setdefault("features", {})
    detached = state.setdefault("detached_features", {})
    for name, feature in list(features.items()):
        if isinstance(feature, dict) and feature.get("origin") == bundle_id:
            detached[name] = features.pop(name)
    values = state.setdefault("detached_bundles", [])
    if isinstance(values, list):
        values[:] = [
            item
            for item in values
            if not isinstance(item, dict) or item.get("id") != bundle_id
        ]
    state["bundle_order"] = [
        item for item in state.get("bundle_order", []) if item != bundle_id
    ]
    for name, feature in list(detached.items()):
        if isinstance(feature, dict) and feature.get("origin") == bundle_id:
            detached.pop(name)
    if pending:
        state.pop("bundle_transaction", None)
    state_module.save(state_path, state)


def main() -> int:
    if len(sys.argv) < 2:
        return 2
    command = sys.argv[1]
    args = sys.argv[2:]
    try:
        if command == "active-paths":
            manifest = load_manifest(Path(args[0]))
            emit_nul(active_paths(manifest, args[1] if len(args) > 1 else None))
            return 0
        if command == "startup-paths":
            emit_nul(startup_paths(load_state(Path(args[0])), args[1]))
            return 0
        if command == "attach-paths":
            bundle_id, paths = attach_paths(
                load_manifest(Path(args[0])), load_state(Path(args[1])), args[2]
            )
            emit_nul([bundle_id, *paths])
            return 0
        if command == "resume-attach-paths":
            emit_nul(resume_attach_paths(load_state(Path(args[0]))))
            return 0
        if command == "lookup":
            state, bundle_id = load_state(Path(args[0])), args[1]
            active = bundle_by_id(
                [item for item in state.get("bundles", []) if isinstance(item, dict)],
                bundle_id,
            )
            if active is not None and not active.get("core"):
                print("active")
            elif detached_by_id(state, bundle_id) is not None:
                print("detached")
            else:
                print("unknown")
            return 0
        if command == "plan-detach":
            write_plan(
                Path(args[4]),
                plan_detach(
                    load_manifest(Path(args[0])),
                    load_state(Path(args[1])),
                    args[2],
                    load_manifest(Path(args[3])),
                    args[5:],
                ),
            )
            return 0
        if command == "plan-attach":
            write_plan(
                Path(args[4]),
                plan_attach(
                    load_manifest(Path(args[0])),
                    load_state(Path(args[1])),
                    load_manifest(Path(args[2])),
                    args[3],
                    args[5:],
                ),
            )
            return 0
        if command == "baseline":
            update_plan_baseline(Path(args[0]), args[1:])
            return 0
        if command == "start":
            start_transaction(Path(args[0]), Path(args[1]))
            return 0
        if command == "transaction":
            operation = ensure_transaction(load_state(Path(args[0])))
            print(json.dumps(operation, separators=(",", ":")))
            return 0
        if command == "add":
            transaction_add(Path(args[0]), args[1], args[2])
            return 0
        if command == "set-targets":
            set_reconcile_targets(Path(args[0]), args[1:])
            return 0
        if command == "validate-manifest":
            validate_manifest_identity(
                load_state(Path(args[0])), load_manifest(Path(args[1])), args[2]
            )
            return 0
        if command == "validate-active-set":
            validate_active_set(load_state(Path(args[0])), load_manifest(Path(args[1])))
            return 0
        if command == "validate-attach-plan":
            validate_attach_plan(
                json.loads(Path(args[0]).read_text(encoding="utf-8")),
                load_manifest(Path(args[1])),
                args[2:],
            )
            return 0
        if command == "commit-detach":
            commit_detach(Path(args[0]), Path(args[1]))
            return 0
        if command == "commit-attach":
            commit_attach(Path(args[0]), Path(args[1]))
            return 0
        if command == "clear":
            clear_transaction(Path(args[0]), abort=len(args) > 1 and args[1] == "--abort")
            return 0
        if command == "forget":
            forget_bundle(Path(args[0]), Path(args[1]), args[2])
            return 0
        return 2
    except (
        BundleError,
        manifest_module.ManifestError,
        OSError,
        ValueError,
        json.JSONDecodeError,
        IndexError,
    ) as error:
        print(f"envy bundle: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
