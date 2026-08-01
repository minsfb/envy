#!/usr/bin/env python3
"""Atomic, migratable machine state for mins-env."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import socket
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 3


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"state must contain an object: {path}")
    return value


def load_manifest(path: Path) -> dict[str, Any]:
    value = load(path)
    if value.get("version") != 3 or not isinstance(value.get("features"), dict):
        raise ValueError(f"unsupported resolved manifest: {path}")
    return value


def save(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = now()
    fd, temp_name = tempfile.mkstemp(prefix="state.", suffix=".json", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def decode(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def nested_get(obj: dict[str, Any], dotted: str, default: Any = None) -> Any:
    current: Any = obj
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def nested_set(obj: dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    current = obj
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    current[parts[-1]] = value


def manifest_fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bundle_state(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    fields = ("id", "version", "path", "manifest", "sha256", "core", "portable")
    return [
        {field: item[field] for field in fields if field in item}
        for item in manifest.get("bundles", [])
        if isinstance(item, dict)
    ]


def feature_record(
    definition: dict[str, Any], enabled: bool, *, explicit: bool
) -> dict[str, Any]:
    return {
        "enabled": enabled,
        "selection_explicit": explicit,
        "tier": definition.get("tier", "optional"),
        "origin": definition.get("_origin", "mins-env-core"),
        "status": "pending" if enabled else "disabled",
        "verification": "unknown",
        "detail": "",
        "data": {},
    }


def new_state(
    manifest_path: Path, platform: str, shell: str, root: str, preset: str
) -> dict[str, Any]:
    timestamp = now()
    manifest = load_manifest(manifest_path)
    state: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "installed": True,
        "installed_at": timestamp,
        "updated_at": timestamp,
        "machine_id": socket.gethostname(),
        "platform": platform,
        "shell": shell,
        "mins_env_root": root,
        "workspace_root": manifest.get("workspace_root", root),
        "preset": preset,
        "manifest_fingerprint": manifest_fingerprint(manifest_path),
        "bundles": bundle_state(manifest),
        "features": {},
        "detached_features": {},
        "ownership": {},
    }
    for name, definition in manifest["features"].items():
        enabled = definition.get("tier") == "core"
        state["features"][name] = feature_record(definition, enabled, explicit=enabled)
    return state


def apply_aliases(features: dict[str, Any], aliases: dict[str, Any]) -> bool:
    changed = False
    for old, new in aliases.items():
        if old not in features:
            continue
        if new not in features:
            features[new] = features[old]
        del features[old]
        changed = True
    return changed


def normalize_existing_feature(
    feature: dict[str, Any], definition: dict[str, Any], installed: bool
) -> bool:
    changed = False
    defaults = {
        "enabled": installed and definition.get("tier") == "core",
        "selection_explicit": True,
        "tier": definition.get("tier", "optional"),
        "origin": definition.get("_origin", "mins-env-core"),
        "status": "pending",
        "verification": "unknown",
        "detail": "",
        "data": {},
    }
    for key, value in defaults.items():
        if key not in feature:
            feature[key] = deepcopy(value)
            changed = True
    for key in ("tier", "origin"):
        if feature.get(key) != defaults[key]:
            feature[key] = defaults[key]
            changed = True
    if installed and definition.get("tier") == "core" and not feature.get("enabled"):
        feature["enabled"] = True
        feature["status"] = "pending"
        changed = True
    return changed


def migrate_v3(
    state: dict[str, Any], manifest_path: Path, platform: str, shell: str, root: str
) -> bool:
    manifest = load_manifest(manifest_path)
    definitions = manifest["features"]
    changed = False
    features = state.get("features")
    if not isinstance(features, dict):
        features = {}
        state["features"] = features
        changed = True
    detached = state.get("detached_features")
    if not isinstance(detached, dict):
        detached = {}
        state["detached_features"] = detached
        changed = True
    changed = apply_aliases(features, manifest.get("aliases", {})) or changed
    changed = apply_aliases(detached, manifest.get("aliases", {})) or changed
    facts = {
        "machine_id": socket.gethostname(),
        "platform": platform,
        "shell": shell,
        "mins_env_root": root,
        "workspace_root": manifest.get("workspace_root", root),
        "manifest_fingerprint": manifest_fingerprint(manifest_path),
        "bundles": bundle_state(manifest),
    }
    for key, value in facts.items():
        if state.get(key) != value:
            state[key] = value
            changed = True
    installed = bool(state.get("installed", True))
    for name, definition in definitions.items():
        feature = features.get(name)
        historical = detached.get(name)
        if isinstance(historical, dict) and (
            not isinstance(feature, dict)
            or not feature.get("selection_explicit")
            or (historical.get("enabled") and not feature.get("enabled"))
        ):
            feature = detached.pop(name)
            features[name] = feature
            changed = True
        elif isinstance(historical, dict):
            detached.pop(name)
            changed = True
        if not isinstance(feature, dict):
            features[name] = feature_record(
                definition,
                installed and definition.get("tier") == "core",
                explicit=definition.get("tier") == "core",
            )
            changed = True
        elif normalize_existing_feature(feature, definition, installed):
            changed = True
    for name in set(features) - set(definitions):
        detached[name] = features.pop(name)
        changed = True
    return changed


def migrate_legacy(
    old: dict[str, Any], manifest_path: Path, platform: str, shell: str, root: str
) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    definitions = manifest["features"]
    old_features = deepcopy(old.get("features", {}))
    if not isinstance(old_features, dict):
        old_features = {}
    apply_aliases(old_features, manifest.get("aliases", {}))
    migrated = new_state(
        manifest_path,
        platform,
        shell,
        root,
        str(old.get("preset", old.get("profile", "migrated"))),
    )
    migrated["installed"] = bool(old.get("installed", True))
    migrated["installed_at"] = old.get("installed_at", old.get("last_sync", now()))
    migrated["ownership"] = deepcopy(old.get("ownership", {}))
    migrated["detached_features"] = {}
    if "manifest_commit" in old:
        migrated["legacy_manifest_commit"] = old["manifest_commit"]

    for name, definition in definitions.items():
        previous = old_features.get(name)
        if not isinstance(previous, dict):
            continue
        old_status = previous.get("status", "pending")
        enabled = bool(
            previous.get("enabled", old_status in {"applied", "current", "failed"})
        )
        if definition.get("tier") == "core" and migrated["installed"]:
            enabled = True
        status = {
            "applied": "current",
            "current": "current",
            "failed": "failed",
            "disabled": "disabled",
        }.get(old_status, "pending" if enabled else "disabled")
        record = feature_record(definition, enabled, explicit=True)
        record.update(
            {
                "status": status,
                "verification": previous.get("verification", "unknown"),
                "detail": previous.get("detail", previous.get("reason", "")),
                "data": deepcopy(previous.get("data", {})),
            }
        )
        for key in ("last_converged_at", "last_verified_at", "verification_detail"):
            if key in previous:
                record[key] = previous[key]
        if previous.get("at") and "last_converged_at" not in record:
            record["last_converged_at"] = previous["at"]
        migrated["features"][name] = record
    for name, previous in old_features.items():
        if name not in definitions and isinstance(previous, dict):
            migrated["detached_features"][name] = previous
    return migrated


def migrate(
    path: Path, manifest_path: Path, platform: str, shell: str, root: str
) -> None:
    state = load(path)
    if state.get("schema_version") == SCHEMA_VERSION:
        if migrate_v3(state, manifest_path, platform, shell, root):
            save(path, state)
        return
    save(path, migrate_legacy(state, manifest_path, platform, shell, root))


def emit(value: Any) -> None:
    if isinstance(value, bool):
        print("true" if value else "false")
    elif isinstance(value, (dict, list)):
        print(json.dumps(value, separators=(",", ":")))
    elif value is not None:
        print(value)


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: state.py <state-file> <command> [args...]", file=sys.stderr)
        return 2
    path = Path(sys.argv[1]).expanduser()
    command = sys.argv[2]
    args = sys.argv[3:]

    if command == "init":
        if len(args) != 5:
            return 2
        save(path, new_state(Path(args[0]), *args[1:]))
        return 0
    if command == "migrate":
        if len(args) != 4:
            return 2
        migrate(path, Path(args[0]), *args[1:])
        return 0
    if command == "validate":
        load(path)
        return 0

    state = load(path)
    if command == "get":
        default = decode(args[1]) if len(args) > 1 else None
        value = nested_get(state, args[0], default)
        if value is None:
            return 1
        emit(value)
        return 0
    if command == "set":
        nested_set(state, args[0], decode(args[1]))
        save(path, state)
        return 0
    if command == "feature-init":
        name, enabled, tier, origin = args
        existing = state.setdefault("features", {}).get(name, {})
        existing.update(
            {
                "enabled": enabled == "true",
                "selection_explicit": True,
                "tier": tier,
                "origin": origin,
                "status": existing.get(
                    "status", "pending" if enabled == "true" else "disabled"
                ),
                "verification": existing.get("verification", "unknown"),
                "detail": existing.get("detail", ""),
                "data": existing.get("data", {}),
            }
        )
        if not existing["enabled"]:
            existing["status"] = "disabled"
        elif existing["status"] == "disabled":
            existing["status"] = "pending"
        state["features"][name] = existing
        save(path, state)
        return 0
    if command == "feature-result":
        name, status, detail = args
        feature = state.setdefault("features", {}).setdefault(name, {"data": {}})
        feature["status"] = status
        feature["detail"] = detail
        feature["last_converged_at"] = now()
        feature["verification"] = "unknown" if status == "current" else "failed"
        save(path, state)
        return 0
    if command == "feature-verify":
        name, result, detail = args
        feature = state.setdefault("features", {}).setdefault(name, {"data": {}})
        feature["verification"] = result
        feature["verification_detail"] = detail
        feature["last_verified_at"] = now()
        save(path, state)
        return 0
    if command == "list-enabled":
        for name, feature in state.get("features", {}).items():
            if feature.get("enabled"):
                print(name)
        return 0
    if command == "summary":
        counts: dict[str, int] = {}
        for feature in state.get("features", {}).values():
            if not feature.get("enabled"):
                continue
            status = feature.get("status", "pending")
            counts[status] = counts.get(status, 0) + 1
        print(json.dumps(counts, sort_keys=True))
        return 0
    if command == "export":
        print(json.dumps(state, indent=2, sort_keys=True))
        return 0
    print(f"unknown state command: {command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
