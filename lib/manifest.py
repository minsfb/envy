#!/usr/bin/env python3
"""Compose and query explicit mins-env bundles without executing manifest text."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any


FEATURE_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
ENVIRONMENT_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
HANDLERS = ("converge", "verify", "doctor", "remove")
RESERVED_ENVIRONMENT = {
    "HOME",
    "MINS_ENV_DIR",
    "MINS_ENV_DRY_RUN",
    "MINS_ENV_MANIFEST",
    "MINS_ENV_PURGE",
    "MINS_ENV_ROOT",
    "MINS_ENV_STATE_FILE",
    "MINS_ENV_LAUNCHER_ENTRYPOINT",
    "MINS_FEATURE",
    "MINS_FEATURE_ROOT",
    "MINS_PLATFORM",
    "MINS_PLATFORM_HIERARCHY",
    "MINS_SHELL",
    "OLDPWD",
    "PATH",
    "PWD",
    "SHELL",
    "TMPDIR",
    "USER",
    "WORKSPACE_ROOT",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_STATE_HOME",
}


class ManifestError(RuntimeError):
    pass


def load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ManifestError(f"manifest not found: {path}") from error
    except json.JSONDecodeError as error:
        raise ManifestError(f"invalid JSON in {path}: {error}") from error
    if not isinstance(value, dict):
        raise ManifestError(f"manifest must contain an object: {path}")
    return value


def bundle_manifest(raw: str) -> Path:
    path = Path(raw).expanduser().resolve()
    return path / "mins-env.bundle.json" if path.is_dir() else path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def expand(value: str, root: Path, core_root: Path, workspace_root: Path) -> str:
    expanded = (
        value.replace("${BUNDLE_ROOT}", str(root))
        .replace("${MINS_ENV_ROOT}", str(core_root))
        .replace("${WORKSPACE_ROOT}", str(workspace_root))
    )
    if expanded.startswith("/") and any(
        token in value
        for token in ("${BUNDLE_ROOT}", "${MINS_ENV_ROOT}", "${WORKSPACE_ROOT}")
    ):
        return os.path.normpath(expanded)
    return expanded


def expand_tree(value: Any, root: Path, core_root: Path, workspace_root: Path) -> Any:
    if isinstance(value, str):
        return expand(value, root, core_root, workspace_root)
    if isinstance(value, list):
        return [expand_tree(item, root, core_root, workspace_root) for item in value]
    if isinstance(value, dict):
        return {
            key: expand_tree(item, root, core_root, workspace_root)
            for key, item in value.items()
        }
    return deepcopy(value)


def merge_settings(
    target: dict[str, Any], incoming: dict[str, Any], label: str
) -> None:
    for key, value in incoming.items():
        if key not in target:
            target[key] = deepcopy(value)
        elif isinstance(target[key], dict) and isinstance(value, dict):
            merge_settings(target[key], value, f"{label}.{key}")
        elif isinstance(target[key], list) and isinstance(value, list):
            target[key].extend(deepcopy(value))
        elif target[key] != value:
            raise ManifestError(f"conflicting setting {label}.{key}")


def validate_handler(feature: str, action: str, value: Any) -> None:
    if not isinstance(value, dict):
        raise ManifestError(f"{feature}.{action} must be a structured handler object")
    selectors = [key for key in ("run", "command") if key in value]
    if len(selectors) != 1 or not isinstance(value[selectors[0]], str):
        raise ManifestError(
            f"{feature}.{action} requires exactly one string 'run' or 'command'"
        )
    args = value.get("args", [])
    if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
        raise ManifestError(f"{feature}.{action}.args must be an array of strings")
    unknown = set(value) - {"run", "command", "args"}
    if unknown:
        raise ManifestError(
            f"{feature}.{action} contains unsupported fields: {', '.join(sorted(unknown))}"
        )


def resolved_run(value: dict[str, Any], root: Path) -> Path | None:
    raw = value.get("run")
    if not isinstance(raw, str):
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def require_contained(path: Path, root: Path, label: str) -> None:
    try:
        path.relative_to(root)
    except ValueError as error:
        raise ManifestError(
            f"portable {label} escapes its package root: {path}"
        ) from error


def require_existing(path: Path, label: str) -> None:
    if not path.exists():
        raise ManifestError(f"portable {label} is missing: {path}")


def validate_portable_feature(name: str, value: dict[str, Any], root: Path) -> None:
    for action in HANDLERS:
        handler = value.get(action)
        if not isinstance(handler, dict):
            continue
        path = resolved_run(handler, root)
        if path is not None:
            require_contained(path, root, f"handler {name}.{action}")
            require_existing(path, f"handler {name}.{action}")
    environment = value.get("env", {})
    if not isinstance(environment, dict):
        return
    raw_paths = environment.get("PATH+", [])
    values = raw_paths if isinstance(raw_paths, list) else [raw_paths]
    for raw in values:
        if not isinstance(raw, str):
            continue
        path = Path(raw).expanduser()
        if path.is_absolute():
            require_contained(path.resolve(), root, f"PATH entry for {name}")
            if not path.is_dir():
                raise ManifestError(
                    f"portable PATH entry for {name} is missing: {path}"
                )


def validate_feature(name: str, value: dict[str, Any]) -> None:
    if not isinstance(value.get("description"), str) or not value["description"]:
        raise ManifestError(f"feature '{name}' requires a description")
    if value.get("tier") not in {"core", "recommended", "optional"}:
        raise ManifestError(f"feature '{name}' has an invalid tier")
    scope = value.get("scope")
    if scope is not None and (
        not isinstance(scope, dict)
        or not all(
            isinstance(key, str) and isinstance(enabled, bool)
            for key, enabled in scope.items()
        )
    ):
        raise ManifestError(f"feature '{name}' has an invalid scope")
    dependencies = value.get("depends_on", [])
    if not isinstance(dependencies, list) or not all(
        isinstance(item, str) and FEATURE_ID.fullmatch(item) for item in dependencies
    ):
        raise ManifestError(f"feature '{name}' has invalid dependencies")
    if len(dependencies) != len(set(dependencies)):
        raise ManifestError(f"feature '{name}' repeats a dependency")
    environment = value.get("env", {})
    if not isinstance(environment, dict):
        raise ManifestError(f"feature '{name}' has an invalid environment")
    for key, raw in environment.items():
        values = raw if isinstance(raw, list) else [raw]
        if (
            not isinstance(key, str)
            or (key != "PATH+" and not ENVIRONMENT_KEY.fullmatch(key))
            or not values
            or not all(isinstance(item, str) for item in values)
        ):
            raise ManifestError(f"feature '{name}' has an invalid environment value")
        if key != "PATH+" and isinstance(raw, list):
            raise ManifestError(
                f"feature '{name}' environment key '{key}' must be scalar"
            )
    for key in ("default_enabled", "reconcile_after_selection_change"):
        if key in value and not isinstance(value[key], bool):
            raise ManifestError(f"feature '{name}' field '{key}' must be boolean")


def order_features(features: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    for name, definition in features.items():
        validate_feature(name, definition)
        unknown = set(definition.get("depends_on", [])) - set(features)
        if unknown:
            raise ManifestError(
                f"feature '{name}' depends on unknown features: "
                + ", ".join(sorted(unknown))
            )

    ordered: dict[str, dict[str, Any]] = {}
    visiting: list[str] = []
    complete: set[str] = set()

    def visit(name: str) -> None:
        if name in complete:
            return
        if name in visiting:
            cycle = visiting[visiting.index(name) :] + [name]
            raise ManifestError("feature dependency cycle: " + " -> ".join(cycle))
        visiting.append(name)
        for dependency in features[name].get("depends_on", []):
            visit(dependency)
        visiting.pop()
        complete.add(name)
        ordered[name] = features[name]

    for name in features:
        visit(name)
    return ordered


def compose(core_path: Path, bundle_args: list[str]) -> dict[str, Any]:
    core_path = core_path.resolve()
    core_root = core_path.parent
    sources: list[tuple[Path, dict[str, Any], bool]] = [
        (core_path, load_object(core_path), True)
    ]
    sources.extend(
        (path, load_object(path), False)
        for path in (bundle_manifest(raw) for raw in bundle_args)
    )

    core = sources[0][1]
    if core.get("version") != 3:
        raise ManifestError(f"unsupported core manifest version in {core_path}")

    workspace_root = core_root
    workspace_owner = "core default"
    for path, value, is_core in sources:
        raw_workspace = value.get("workspace_root")
        if raw_workspace is None:
            continue
        if not isinstance(raw_workspace, str):
            raise ManifestError(f"workspace_root must be a string in {path}")
        candidate = Path(
            expand(raw_workspace, path.parent, core_root, workspace_root)
        ).expanduser()
        if not candidate.is_absolute():
            candidate = path.parent / candidate
        candidate = candidate.resolve()
        if workspace_owner != "core default" and candidate != workspace_root:
            raise ManifestError(
                f"workspace_root conflicts between {workspace_owner} and {path}"
            )
        workspace_root = candidate
        workspace_owner = str(path)

    result: dict[str, Any] = {
        "version": 3,
        "id": str(core.get("id", "mins-env")),
        "presets": deepcopy(core.get("presets", {})),
        "recommended_features": [],
        "features": {},
        "aliases": {},
        "resources": {},
        "settings": {},
        "environment": {},
        "doctor_aliases": {},
        "fact_providers": [],
        "workspace_root": str(workspace_root),
        "bundles": [],
    }

    for path, value, is_core in sources:
        root = path.parent.resolve()
        bundle_id = value.get("id")
        bundle_version = value.get("bundle_version", value.get("version"))
        if not isinstance(bundle_id, str) or not bundle_id:
            raise ManifestError(f"manifest id is required: {path}")
        if not is_core and value.get("version") != 1:
            raise ManifestError(f"unsupported bundle manifest version in {path}")
        portable = value.get("portable", is_core)
        if not isinstance(portable, bool):
            raise ManifestError(f"portable must be boolean in {path}")
        if any(item["id"] == bundle_id for item in result["bundles"]):
            raise ManifestError(f"duplicate bundle id: {bundle_id}")
        result["bundles"].append(
            {
                "id": bundle_id,
                "version": str(bundle_version),
                "path": str(root),
                "manifest": str(path),
                "sha256": digest(path),
                "core": is_core,
                "portable": portable,
            }
        )

        facts = value.get("facts")
        if facts is not None:
            normalized_facts = expand_tree(facts, root, core_root, workspace_root)
            validate_handler(bundle_id, "facts", normalized_facts)
            facts_path = resolved_run(normalized_facts, root)
            if portable:
                if facts_path is None:
                    raise ManifestError(
                        f"portable facts provider for {bundle_id} must use run"
                    )
                require_contained(facts_path, root, f"facts provider for {bundle_id}")
                require_existing(facts_path, f"facts provider for {bundle_id}")
            result["fact_providers"].append(
                {
                    "origin": bundle_id,
                    "root": str(root),
                    "handler": normalized_facts,
                }
            )

        raw_features = value.get("features", {})
        if not isinstance(raw_features, dict):
            raise ManifestError(f"features must be an object in {path}")
        for name, definition in raw_features.items():
            if not FEATURE_ID.fullmatch(name) or not isinstance(definition, dict):
                raise ManifestError(f"invalid feature definition '{name}' in {path}")
            if name in result["features"]:
                owner = result["features"][name]["_origin"]
                raise ManifestError(
                    f"feature '{name}' is owned by both {owner} and {bundle_id}"
                )
            normalized = expand_tree(definition, root, core_root, workspace_root)
            normalized["_origin"] = bundle_id
            normalized["_root"] = str(root)
            for action in HANDLERS:
                if action in normalized:
                    validate_handler(name, action, normalized[action])
            if portable:
                validate_portable_feature(name, normalized, root)
            result["features"][name] = normalized

        recommended = value.get("recommended_features", [])
        if not isinstance(recommended, list) or not all(
            isinstance(item, str) for item in recommended
        ):
            raise ManifestError(f"recommended_features must be an array in {path}")
        result["recommended_features"].extend(recommended)

        aliases = value.get("aliases", {})
        if not isinstance(aliases, dict):
            raise ManifestError(f"aliases must be an object in {path}")
        for old, new in aliases.items():
            if not isinstance(old, str) or not isinstance(new, str):
                raise ManifestError(f"aliases must map strings in {path}")
            if old in result["aliases"] and result["aliases"][old] != new:
                raise ManifestError(f"conflicting alias for '{old}'")
            result["aliases"][old] = new

        resources = value.get("resources", {})
        if not isinstance(resources, dict):
            raise ManifestError(f"resources must be an object in {path}")
        for kind, entries in resources.items():
            if not isinstance(entries, list) or not all(
                isinstance(item, str) for item in entries
            ):
                raise ManifestError(f"resource list '{kind}' is invalid in {path}")
            destination = result["resources"].setdefault(kind, [])
            for entry in entries:
                expanded = Path(
                    expand(entry, root, core_root, workspace_root)
                ).expanduser()
                if not expanded.is_absolute():
                    expanded = root / expanded
                resolved = expanded.resolve()
                if portable:
                    require_contained(resolved, root, f"{kind} resource")
                    require_existing(resolved, f"{kind} resource")
                if str(resolved) in destination:
                    raise ManifestError(f"duplicate {kind} resource: {resolved}")
                destination.append(str(resolved))

        settings = value.get("settings", {})
        if not isinstance(settings, dict):
            raise ManifestError(f"settings must be an object in {path}")
        merge_settings(
            result["settings"],
            expand_tree(settings, root, core_root, workspace_root),
            "settings",
        )

        environment = value.get("environment", {})
        if not isinstance(environment, dict):
            raise ManifestError(f"environment must be an object in {path}")
        for key, raw in environment.items():
            if not isinstance(key, str) or not ENVIRONMENT_KEY.fullmatch(key):
                raise ManifestError(f"invalid environment key in {path}")
            if key in RESERVED_ENVIRONMENT:
                raise ManifestError(
                    f"bundle cannot replace reserved environment key: {key}"
                )
            if not isinstance(raw, str) or "\0" in raw:
                raise ManifestError(f"environment must map strings in {path}")
            expanded = expand(raw, root, core_root, workspace_root)
            if key in result["environment"]:
                raise ManifestError(f"duplicate environment value for {key}")
            result["environment"][key] = expanded

        doctor_aliases = value.get("doctor_aliases", {})
        if not isinstance(doctor_aliases, dict) or not all(
            isinstance(key, str) and isinstance(target, str)
            for key, target in doctor_aliases.items()
        ):
            raise ManifestError(f"doctor_aliases must map strings in {path}")
        for key, target in doctor_aliases.items():
            if (
                key in result["doctor_aliases"]
                and result["doctor_aliases"][key] != target
            ):
                raise ManifestError(f"conflicting doctor alias: {key}")
            result["doctor_aliases"][key] = target

    for old, new in result["aliases"].items():
        if new not in result["features"]:
            raise ManifestError(f"alias '{old}' targets unknown feature '{new}'")
    unknown_recommended = set(result["recommended_features"]) - set(result["features"])
    if unknown_recommended:
        raise ManifestError(
            "recommended_features contains unknown IDs: "
            + ", ".join(sorted(unknown_recommended))
        )
    if len(result["recommended_features"]) != len(set(result["recommended_features"])):
        raise ManifestError("recommended_features contains duplicates")
    unknown_doctors = set(result["doctor_aliases"].values()) - set(result["features"])
    if unknown_doctors:
        raise ManifestError(
            "doctor_aliases target unknown features: "
            + ", ".join(sorted(unknown_doctors))
        )
    result["features"] = order_features(result["features"])
    return result


def handler_argv(manifest: dict[str, Any], feature: str, action: str) -> list[str]:
    definition = manifest.get("features", {}).get(feature)
    if not isinstance(definition, dict):
        raise ManifestError(f"unknown feature: {feature}")
    value = definition.get(action)
    if value is None:
        return []
    validate_handler(feature, action, value)
    return structured_argv(value, Path(definition["_root"]))


def structured_argv(value: dict[str, Any], root: Path) -> list[str]:
    if "run" in value:
        executable = Path(value["run"]).expanduser()
        if not executable.is_absolute():
            executable = root / executable
        first = str(executable.resolve())
    else:
        first = value["command"]
    return [first, *value.get("args", [])]


def emit_nul(values: list[str]) -> None:
    for value in values:
        sys.stdout.buffer.write(value.encode() + b"\0")


def main() -> int:
    if len(sys.argv) < 2:
        return 2
    command = sys.argv[1]
    try:
        if command == "compose":
            if len(sys.argv) < 3:
                return 2
            value = compose(Path(sys.argv[2]), sys.argv[3:])
            print(json.dumps(value, indent=2))
            return 0
        if command == "handler-argv":
            if len(sys.argv) != 5:
                return 2
            value = load_object(Path(sys.argv[2]))
            emit_nul(handler_argv(value, sys.argv[3], sys.argv[4]))
            return 0
        if command == "environment":
            if len(sys.argv) != 3:
                return 2
            value = load_object(Path(sys.argv[2])).get("environment", {})
            emit_nul([item for pair in value.items() for item in pair])
            return 0
        if command == "resource":
            if len(sys.argv) != 4:
                return 2
            value = (
                load_object(Path(sys.argv[2])).get("resources", {}).get(sys.argv[3], [])
            )
            if not isinstance(value, list):
                raise ManifestError(f"invalid resource list: {sys.argv[3]}")
            print("\n".join(str(item) for item in value))
            return 0
        if command == "fingerprint":
            if len(sys.argv) != 3:
                return 2
            raw = Path(sys.argv[2]).read_bytes()
            print(hashlib.sha256(raw).hexdigest())
            return 0
        return 2
    except (ManifestError, OSError) as error:
        print(f"mins-env manifest: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
