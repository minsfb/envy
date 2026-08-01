#!/usr/bin/env python3
"""Select, link, verify, and diagnose user-level agent assets."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
PLATFORM_HIERARCHY = {
    "darwin": ("darwin", "unix", "global"),
    "linux": ("linux", "unix", "global"),
    "wsl": ("wsl", "linux", "unix", "global"),
    "git-bash": ("git-bash", "windows", "global"),
    "windows": ("windows", "global"),
    "unix": ("unix", "global"),
    "global": ("global",),
}


class AssetError(RuntimeError):
    pass


@dataclass(frozen=True)
class Asset:
    kind: str
    name: str
    source: Path
    group: str
    harness: str
    target: Path

    def as_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "name": self.name,
            "source": str(self.source),
            "group": self.group,
            "harness": self.harness,
            "target": str(self.target),
        }


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as handle:
            value = json.load(handle)
    except FileNotFoundError as error:
        raise AssetError(f"{label} not found: {path}") from error
    except json.JSONDecodeError as error:
        raise AssetError(f"invalid {label} JSON at {path}: {error}") from error
    if not isinstance(value, dict):
        raise AssetError(f"{label} must contain a JSON object: {path}")
    return value


def configured_manifests(args: argparse.Namespace) -> list[Path]:
    explicit = args.manifest or os.environ.get("ENVY_AGENT_ASSETS_MANIFEST")
    if explicit:
        return [Path(explicit).expanduser().resolve()]
    manifest_path = os.environ.get("ENVY_MANIFEST")
    if not manifest_path:
        raise AssetError("no agent asset manifest is configured")
    resolved = load_json(Path(manifest_path), "resolved envy manifest")
    resources = resolved.get("resources", {}).get("agent_asset_manifests", [])
    if not isinstance(resources, list) or not resources:
        raise AssetError("no agent_asset_manifests resources are configured")
    return [Path(value).expanduser().resolve() for value in resources]


def compose_manifests(paths: list[Path]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "version": 1,
        "distribution_feature": "agent-assets",
        "destinations": {},
        "audit_roots": [],
        "skill_groups": [],
        "agent_groups": [],
        "plugin_audit": False,
    }
    group_names: set[tuple[str, str]] = set()
    for path in paths:
        value = load_json(path, "agent asset manifest")
        if value.get("version") != 1:
            raise AssetError(f"unsupported agent asset manifest version: {path}")
        destinations = value.get("destinations", {})
        if not isinstance(destinations, dict):
            raise AssetError(f"destinations must be an object: {path}")
        for harness, definitions in destinations.items():
            existing = result["destinations"].setdefault(harness, {})
            if not isinstance(definitions, dict):
                raise AssetError(f"destination '{harness}' must be an object")
            for kind, destination in definitions.items():
                if kind in existing and existing[kind] != destination:
                    raise AssetError(f"conflicting destination {harness}.{kind}")
                existing[kind] = destination
        for key in ("skill_groups", "agent_groups"):
            groups = value.get(key, [])
            if not isinstance(groups, list):
                raise AssetError(f"{key} must be an array: {path}")
            for raw_group in groups:
                if not isinstance(raw_group, dict):
                    raise AssetError(f"invalid group in {path}")
                group = dict(raw_group)
                name = group.get("name")
                identity = (key, str(name))
                if identity in group_names:
                    raise AssetError(f"duplicate agent asset group: {name}")
                group_names.add(identity)
                raw_source = group.get("path")
                if isinstance(raw_source, str):
                    source = Path(raw_source).expanduser()
                    if not source.is_absolute():
                        source = path.parent / source
                    group["path"] = str(source.resolve())
                result[key].append(group)
        audit_roots = value.get("audit_roots", [])
        if not isinstance(audit_roots, list):
            raise AssetError(f"audit_roots must be an array: {path}")
        result["audit_roots"].extend(audit_roots)
        result["plugin_audit"] = bool(
            result["plugin_audit"] or value.get("plugin_audit", False)
        )
    return result


def lexical_target(link: Path) -> Path:
    raw = Path(os.readlink(link))
    if not raw.is_absolute():
        raw = link.parent / raw
    return Path(os.path.abspath(raw))


def is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 128), b""):
            digest.update(chunk)
    return digest.hexdigest()


def unquote_yaml_scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        if value[0] == '"':
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                pass
        return value[1:-1].replace("''", "'")
    return value


def frontmatter(path: Path) -> tuple[dict[str, str], list[str]]:
    """Parse the small YAML subset used by skill metadata without PyYAML."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        return {}, [f"cannot read {path}: {error}"]
    if not lines or lines[0].strip() != "---":
        return {}, [f"missing YAML frontmatter in {path}"]
    try:
        end = next(
            index for index, line in enumerate(lines[1:], 1) if line.strip() == "---"
        )
    except StopIteration:
        return {}, [f"unterminated YAML frontmatter in {path}"]

    metadata: dict[str, str] = {}
    issues: list[str] = []
    index = 1
    while index < end:
        line = lines[index]
        if not line.strip() or line.lstrip().startswith("#"):
            index += 1
            continue
        if line[:1].isspace():
            # Nested metadata belongs to keys the doctor does not interpret.
            index += 1
            continue
        if ":" not in line:
            issues.append(f"unsupported frontmatter line {index + 1} in {path}")
            index += 1
            continue
        key, raw = line.split(":", 1)
        key = key.strip()
        raw = raw.strip()
        if raw in {">", ">-", ">+", "|", "|-", "|+"}:
            block: list[str] = []
            index += 1
            while index < end and (
                not lines[index].strip() or lines[index][:1].isspace()
            ):
                block.append(lines[index].strip())
                index += 1
            metadata[key] = " ".join(part for part in block if part)
            continue
        metadata[key] = unquote_yaml_scalar(raw)
        index += 1
    return metadata, issues


def issue(severity: str, code: str, message: str, **context: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"severity": severity, "code": code, "message": message}
    result.update(context)
    return result


class AgentAssets:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        script = Path(__file__).resolve()
        self.env_dir = Path(
            os.environ.get("ENVY_DIR", script.parent.parent)
        ).resolve()
        self.manifest_paths = configured_manifests(args)
        self.manifest_path = self.manifest_paths[0]
        config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        state_home = Path(
            os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")
        )
        self.state_path = Path(
            args.state
            or os.environ.get(
                "ENVY_STATE_FILE", config_home / "envy" / "state.json"
            )
        ).expanduser()
        self.ownership_path = Path(
            args.ownership
            or os.environ.get(
                "ENVY_AGENT_ASSETS_OWNERSHIP",
                state_home / "envy" / "agent-assets.json",
            )
        ).expanduser()
        self.launcher = Path.home() / ".local" / "bin" / "agent-assets"
        self.manifest = compose_manifests(self.manifest_paths)
        self.issues: list[dict[str, Any]] = []
        self.state: dict[str, Any] | None = None
        if self.state_path.is_file():
            self.state = load_json(self.state_path, "envy state")
        self.ownership = self._load_ownership()
        self.groups: list[dict[str, Any]] = []
        self.all_assets: list[Asset] = []
        self.expected: list[Asset] = []
        self.source_roots: set[Path] = {path.parent for path in self.manifest_paths}
        self.destinations = self._destinations()
        self.platform = str(
            os.environ.get("ENVY_PLATFORM")
            or (self.state or {}).get("platform")
            or self._detect_platform()
        )
        self._load_assets()

    def _load_ownership(self) -> dict[str, Any]:
        if not self.ownership_path.is_file():
            return {"version": 1, "links": {}}
        try:
            value = load_json(self.ownership_path, "agent asset ownership")
        except AssetError as error:
            self.issues.append(issue("error", "ownership-invalid", str(error)))
            return {"version": 1, "links": {}}
        if not isinstance(value.get("links", {}), dict):
            self.issues.append(
                issue("error", "ownership-invalid", "ownership links must be an object")
            )
            return {"version": 1, "links": {}}
        return value

    @staticmethod
    def _detect_platform() -> str:
        if sys.platform == "darwin":
            return "darwin"
        if sys.platform.startswith("linux"):
            return "linux"
        if os.name == "nt":
            return "windows"
        return sys.platform

    def _destinations(self) -> dict[str, dict[str, Path]]:
        raw = self.manifest.get("destinations")
        if not isinstance(raw, dict) or not raw:
            raise AssetError("agent asset manifest requires destinations")
        result: dict[str, dict[str, Path]] = {}
        for harness, definitions in raw.items():
            if not isinstance(definitions, dict):
                raise AssetError(f"destination '{harness}' must be an object")
            result[harness] = {}
            for kind, path in definitions.items():
                if kind not in {"skills", "agents"} or not isinstance(path, str):
                    raise AssetError(f"invalid destination {harness}.{kind}")
                result[harness][kind] = Path(path).expanduser()
        return result

    def feature_enabled(self, name: str) -> bool:
        if self.state is None:
            return False
        feature = self.state.get("features", {}).get(name, {})
        return isinstance(feature, dict) and feature.get("enabled") is True

    def distribution_enabled(self) -> bool:
        name = self.manifest.get("distribution_feature")
        return isinstance(name, str) and self.feature_enabled(name)

    def platform_matches(self, targets: Iterable[str]) -> bool:
        override = os.environ.get("ENVY_PLATFORM_HIERARCHY_OVERRIDE", "").split()
        hierarchy = tuple(override) or PLATFORM_HIERARCHY.get(
            self.platform, (self.platform, "global")
        )
        return any(target in hierarchy for target in targets)

    def group_active(self, group: dict[str, Any]) -> tuple[bool, str]:
        if not self.distribution_enabled():
            return False, "agent asset distribution feature is disabled"
        platforms = group.get("platforms", ["global"])
        if not isinstance(platforms, list) or not all(
            isinstance(item, str) for item in platforms
        ):
            return False, "invalid platforms"
        if not self.platform_matches(platforms):
            return False, f"platform {self.platform} not selected"
        required = group.get("features", [])
        if not isinstance(required, list) or not all(
            isinstance(item, str) for item in required
        ):
            return False, "invalid features"
        disabled = [name for name in required if not self.feature_enabled(name)]
        if disabled:
            return False, f"disabled features: {', '.join(disabled)}"
        any_features = group.get("any_features", [])
        if not isinstance(any_features, list) or not all(
            isinstance(item, str) for item in any_features
        ):
            return False, "invalid any_features"
        if any_features and not any(
            self.feature_enabled(name) for name in any_features
        ):
            return False, f"none enabled: {', '.join(any_features)}"
        return True, "selected"

    def _load_assets(self) -> None:
        if self.manifest.get("version") != 1:
            raise AssetError("unsupported agent asset manifest version")
        for kind, key in (("skill", "skill_groups"), ("agent", "agent_groups")):
            definitions = self.manifest.get(key, [])
            if not isinstance(definitions, list):
                raise AssetError(f"{key} must be an array")
            for definition in definitions:
                self._load_group(kind, definition)
        self._check_source_duplicates()

    def _load_group(self, kind: str, definition: Any) -> None:
        if not isinstance(definition, dict):
            raise AssetError(f"{kind} group must be an object")
        name = definition.get("name")
        rel_path = definition.get("path")
        targets = definition.get("targets")
        if not isinstance(name, str) or not NAME_RE.fullmatch(name):
            raise AssetError(f"invalid {kind} group name: {name!r}")
        if (
            not isinstance(rel_path, str)
            or not isinstance(targets, list)
            or not targets
        ):
            raise AssetError(f"group '{name}' requires path and targets")
        active, reason = self.group_active(definition)
        group_info = {
            "kind": kind,
            "name": name,
            "path": str(Path(rel_path).expanduser().resolve()),
            "targets": targets,
            "selected": active,
            "reason": reason,
        }
        self.groups.append(group_info)
        source_root = Path(rel_path).expanduser().resolve()
        self.source_roots.add(source_root)
        if not source_root.is_dir():
            self.issues.append(
                issue(
                    "error" if active else "warning",
                    "source-group-missing",
                    f"source group does not exist: {source_root}",
                    group=name,
                )
            )
            return
        unknown_targets = [
            target for target in targets if target not in self.destinations
        ]
        if unknown_targets:
            self.issues.append(
                issue(
                    "error",
                    "target-unknown",
                    f"group '{name}' has unknown targets: {', '.join(unknown_targets)}",
                    group=name,
                )
            )
            return

        if kind == "skill":
            candidates = sorted(path for path in source_root.iterdir() if path.is_dir())
        else:
            candidates = sorted(
                path
                for path in source_root.glob("*.md")
                if path.name.lower() != "readme.md"
            )
        for source in candidates:
            metadata_path = source / "SKILL.md" if kind == "skill" else source
            asset_name = source.name if kind == "skill" else source.stem
            if kind == "skill" and not metadata_path.is_file():
                self.issues.append(
                    issue(
                        "error" if active else "warning",
                        "skill-definition-missing",
                        f"skill directory has no SKILL.md: {source}",
                        group=name,
                        name=asset_name,
                    )
                )
                continue
            metadata, parse_issues = frontmatter(metadata_path)
            for message in parse_issues:
                self.issues.append(
                    issue(
                        "error" if active else "warning",
                        "frontmatter-invalid",
                        message,
                        group=name,
                        name=asset_name,
                    )
                )
            canonical_name = metadata.get("name")
            description = metadata.get("description")
            if canonical_name != asset_name:
                self.issues.append(
                    issue(
                        "error" if active else "warning",
                        "name-mismatch",
                        f"metadata name {canonical_name!r} does not match {asset_name!r}",
                        group=name,
                        name=asset_name,
                    )
                )
            if not description:
                self.issues.append(
                    issue(
                        "error" if active else "warning",
                        "description-missing",
                        f"description is missing: {metadata_path}",
                        group=name,
                        name=asset_name,
                    )
                )
            if kind == "skill":
                extras = sorted(set(metadata) - {"name", "description"})
                if extras:
                    self.issues.append(
                        issue(
                            "warning",
                            "frontmatter-extra",
                            f"skill has noncanonical metadata fields: {', '.join(extras)}",
                            group=name,
                            name=asset_name,
                        )
                    )
            for harness in targets:
                destination_kind = "skills" if kind == "skill" else "agents"
                destination = self.destinations[harness].get(destination_kind)
                if destination is None:
                    self.issues.append(
                        issue(
                            "error",
                            "target-kind-unsupported",
                            f"target '{harness}' has no {destination_kind} destination",
                            group=name,
                            name=asset_name,
                        )
                    )
                    continue
                target_name = asset_name if kind == "skill" else source.name
                asset = Asset(
                    kind,
                    asset_name,
                    source.resolve(),
                    name,
                    harness,
                    destination / target_name,
                )
                self.all_assets.append(asset)
                if active:
                    self.expected.append(asset)

    def _check_source_duplicates(self) -> None:
        by_identity: dict[tuple[str, str, str], list[Asset]] = {}
        for asset in self.all_assets:
            by_identity.setdefault((asset.kind, asset.harness, asset.name), []).append(
                asset
            )
        for (kind, harness, name), assets in sorted(by_identity.items()):
            sources = sorted({str(asset.source) for asset in assets})
            if len(sources) < 2:
                continue
            selected = [asset for asset in self.expected if asset in assets]
            self.issues.append(
                issue(
                    "error" if len(selected) > 1 else "warning",
                    "source-duplicate",
                    f"{kind} '{name}' is defined more than once for {harness}",
                    name=name,
                    harness=harness,
                    sources=sources,
                )
            )

    def selected_source_errors(self) -> list[dict[str, Any]]:
        selected_groups = {group["name"] for group in self.groups if group["selected"]}
        return [
            item
            for item in self.issues
            if item["severity"] == "error"
            and (not item.get("group") or item.get("group") in selected_groups)
        ]

    def previous_links(self) -> dict[Path, Path]:
        raw = self.ownership.get("links", {})
        result: dict[Path, Path] = {}
        if isinstance(raw, dict):
            for target, source in raw.items():
                if isinstance(target, str) and isinstance(source, str):
                    result[Path(target)] = Path(source)
        return result

    def link_is_managed(self, link: Path, previous: dict[Path, Path]) -> bool:
        if not link.is_symlink():
            return False
        actual = lexical_target(link)
        recorded = previous.get(link)
        if recorded is not None and actual == recorded:
            return True
        return any(is_within(actual, root) for root in self.source_roots)

    def link_report(self, include_unmanaged: bool = False) -> dict[str, Any]:
        expected_by_target = {asset.target: asset for asset in self.expected}
        previous = self.previous_links()
        current: list[dict[str, Any]] = []
        missing: list[dict[str, Any]] = []
        conflicts: list[dict[str, Any]] = []
        stale: list[dict[str, Any]] = []
        unmanaged: list[dict[str, Any]] = []
        broken: list[dict[str, Any]] = []

        for target, asset in sorted(
            expected_by_target.items(), key=lambda item: str(item[0])
        ):
            if target.is_symlink():
                actual = lexical_target(target)
                if actual == asset.source:
                    current.append(asset.as_dict())
                elif self.link_is_managed(target, previous):
                    stale.append(
                        {
                            **asset.as_dict(),
                            "actual": str(actual),
                            "reason": "wrong managed target",
                        }
                    )
                else:
                    conflicts.append(
                        {
                            **asset.as_dict(),
                            "actual": str(actual),
                            "reason": "unmanaged symlink",
                        }
                    )
            elif target.exists():
                conflicts.append(
                    {**asset.as_dict(), "reason": "unmanaged filesystem entry"}
                )
            else:
                missing.append(asset.as_dict())

        roots: set[Path] = set()
        for definitions in self.destinations.values():
            roots.update(definitions.values())
        for root in sorted(roots, key=str):
            if not root.is_dir():
                continue
            for entry in sorted(root.iterdir()):
                if entry in expected_by_target:
                    continue
                if entry.is_symlink():
                    actual = lexical_target(entry)
                    if not entry.exists():
                        if self.link_is_managed(entry, previous):
                            stale.append(
                                {
                                    "path": str(entry),
                                    "actual": str(actual),
                                    "reason": "not selected",
                                }
                            )
                        elif include_unmanaged:
                            broken.append({"path": str(entry), "target": str(actual)})
                    elif self.link_is_managed(entry, previous):
                        stale.append(
                            {
                                "path": str(entry),
                                "actual": str(actual),
                                "reason": "not selected",
                            }
                        )
                    elif include_unmanaged:
                        unmanaged.append(
                            {
                                "path": str(entry),
                                "target": str(actual),
                                "type": "symlink",
                            }
                        )
                elif include_unmanaged:
                    unmanaged.append(
                        {
                            "path": str(entry),
                            "type": "directory" if entry.is_dir() else "file",
                        }
                    )
        return {
            "current": current,
            "missing": missing,
            "conflicts": conflicts,
            "stale": stale,
            "broken": broken,
            "unmanaged": unmanaged,
        }

    def launcher_content(self) -> str:
        return (
            "#!/usr/bin/env bash\n"
            "# managed by envy agent-assets\n"
            f'exec {shlex.quote(str(Path(__file__).resolve()))} "$@"\n'
        )

    def launcher_status(self) -> str:
        if not self.launcher.exists() and not self.launcher.is_symlink():
            return "missing"
        if not self.launcher.is_file() or self.launcher.is_symlink():
            return "conflict"
        try:
            return (
                "current"
                if self.launcher.read_text() == self.launcher_content()
                else "conflict"
            )
        except OSError:
            return "conflict"

    def preflight(self) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        links = self.link_report()
        errors = list(self.selected_source_errors())
        for conflict in links["conflicts"]:
            errors.append(
                issue(
                    "error",
                    "target-conflict",
                    f"refusing to replace unmanaged target: {conflict['target']}",
                    target=conflict["target"],
                )
            )
        if self.launcher_status() == "conflict":
            errors.append(
                issue(
                    "error",
                    "launcher-conflict",
                    f"refusing to replace unmanaged launcher: {self.launcher}",
                )
            )
        return links, errors

    @staticmethod
    def atomic_symlink(source: Path, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.parent / f".{target.name}.agent-assets-{os.getpid()}"
        try:
            temp.unlink(missing_ok=True)
            os.symlink(source, temp)
            os.replace(temp, target)
        finally:
            temp.unlink(missing_ok=True)

    def write_launcher(self) -> None:
        self.launcher.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=".agent-assets.", dir=self.launcher.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(self.launcher_content())
            os.chmod(temp_name, 0o755)
            os.replace(temp_name, self.launcher)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def converge(self) -> int:
        if self.state is None:
            print(
                f"agent-assets: envy state not found: {self.state_path}",
                file=sys.stderr,
            )
            return 2
        if not self.distribution_enabled():
            print(
                "agent-assets: distribution feature is disabled; run envy install --reconfigure"
            )
            return 2
        links, errors = self.preflight()
        if errors:
            self.print_issues(errors)
            return 1
        expected_by_target = {asset.target: asset for asset in self.expected}
        if self.args.dry_run:
            if not self.args.quiet:
                to_link = len(self.expected) - len(links["current"])
                print(
                    "Dry run: "
                    f"would link {to_link} assets, "
                    f"remove {len(links['stale'])} stale links, and manage {self.launcher}"
                )
            return 0

        previous = self.previous_links()
        for item in links["stale"]:
            raw_target = item.get("target") or item.get("path")
            if not raw_target:
                continue
            target = Path(raw_target)
            if target.is_symlink() and self.link_is_managed(target, previous):
                target.unlink()
        for target, asset in expected_by_target.items():
            if target.is_symlink() and lexical_target(target) == asset.source:
                continue
            self.atomic_symlink(asset.source, target)
        self.write_launcher()
        atomic_json(
            self.ownership_path,
            {
                "version": 1,
                "manifests": [str(path) for path in self.manifest_paths],
                "links": {
                    str(asset.target): str(asset.source)
                    for asset in sorted(
                        self.expected, key=lambda item: str(item.target)
                    )
                },
                "launcher": str(self.launcher),
            },
        )
        if not self.args.quiet:
            counts: dict[str, int] = {}
            for asset in self.expected:
                key = f"{asset.harness} {asset.kind}s"
                counts[key] = counts.get(key, 0) + 1
            print("Agent assets converged")
            for key, count in sorted(counts.items()):
                print(f"  {key:<20} {count}")
        return 0

    def remove(self) -> int:
        previous = self.previous_links()
        links = self.link_report()
        candidates: set[Path] = set(previous)
        for section in ("current", "stale"):
            for item in links[section]:
                raw = item.get("target") or item.get("path")
                if raw:
                    candidates.add(Path(raw))
        removed = 0
        for target in sorted(candidates, key=str):
            if target.is_symlink() and self.link_is_managed(target, previous):
                if not self.args.dry_run:
                    target.unlink()
                removed += 1
        if self.launcher_status() == "current":
            if not self.args.dry_run:
                self.launcher.unlink()
            removed += 1
        if not self.args.dry_run:
            self.ownership_path.unlink(missing_ok=True)
        if not self.args.quiet:
            prefix = "Would remove" if self.args.dry_run else "Removed"
            print(f"{prefix} {removed} managed agent asset entries")
        return 0

    def base_report(self, include_unmanaged: bool) -> dict[str, Any]:
        links = self.link_report(include_unmanaged=include_unmanaged)
        issues = list(self.issues)
        for item in links["missing"]:
            issues.append(
                issue(
                    "error",
                    "link-missing",
                    f"managed link is missing: {item['target']}",
                    **item,
                )
            )
        for item in links["conflicts"]:
            issues.append(
                issue(
                    "error",
                    "link-conflict",
                    f"managed target conflicts: {item['target']}",
                    **item,
                )
            )
        for item in links["stale"]:
            path = item.get("target") or item.get("path")
            issues.append(
                issue("error", "link-stale", f"managed link is stale: {path}", **item)
            )
        for item in links["broken"]:
            issues.append(
                issue(
                    "warning",
                    "link-broken",
                    f"unmanaged destination has a broken link: {item['path']}",
                    **item,
                )
            )
        launcher = self.launcher_status() if self.distribution_enabled() else "disabled"
        if self.distribution_enabled() and launcher != "current":
            issues.append(
                issue(
                    "error",
                    f"launcher-{launcher}",
                    f"agent-assets launcher is {launcher}: {self.launcher}",
                )
            )
        selected_groups = [group["name"] for group in self.groups if group["selected"]]
        report: dict[str, Any] = {
            "manifests": [str(path) for path in self.manifest_paths],
            "state": str(self.state_path),
            "ownership": str(self.ownership_path),
            "platform": self.platform,
            "selected_groups": selected_groups,
            "groups": self.groups,
            "expected": [asset.as_dict() for asset in self.expected],
            "links": links,
            "launcher": {"path": str(self.launcher), "status": launcher},
            "issues": issues,
        }
        return report

    def plugin_report(self) -> dict[str, Any]:
        settings_path = Path.home() / ".claude" / "settings.json"
        registry_path = Path.home() / ".claude" / "plugins" / "installed_plugins.json"
        available_path = (
            Path.home()
            / ".claude"
            / "agent-market"
            / ".claude-plugin"
            / "marketplace.json"
        )
        result: dict[str, Any] = {
            "settings": str(settings_path),
            "registry": str(registry_path),
            "enabled": [],
            "skill_collisions": [],
            "surface_collisions": [],
        }
        if not settings_path.is_file():
            return result
        try:
            settings = load_json(settings_path, "Claude settings")
        except AssetError as error:
            result["error"] = str(error)
            return result
        enabled = settings.get("enabledPlugins", {})
        if not isinstance(enabled, dict):
            result["error"] = "enabledPlugins is not an object"
            return result
        registry: dict[str, Any] = {}
        if registry_path.is_file():
            try:
                registry = load_json(registry_path, "Claude plugin registry").get(
                    "plugins", {}
                )
            except AssetError as error:
                result["registry_error"] = str(error)
        available: dict[str, str] = {}
        if available_path.is_file():
            try:
                market = load_json(available_path, "agent-market manifest")
                available = {
                    item.get("name"): item.get("version")
                    for item in market.get("plugins", [])
                    if isinstance(item, dict)
                    and isinstance(item.get("name"), str)
                    and isinstance(item.get("version"), str)
                }
            except AssetError:
                pass

        skills: dict[str, list[dict[str, str]]] = {}
        for plugin_name in sorted(
            name for name, value in enabled.items() if value is True
        ):
            records = (
                registry.get(plugin_name, []) if isinstance(registry, dict) else []
            )
            record: dict[str, Any] = {}
            if isinstance(records, list) and records:
                record = next(
                    (
                        item
                        for item in records
                        if isinstance(item, dict)
                        and isinstance(item.get("installPath"), str)
                        and Path(item["installPath"]).is_dir()
                    ),
                    records[0] if isinstance(records[0], dict) else {},
                )
            raw_install_path = (
                record.get("installPath") if isinstance(record, dict) else None
            )
            install_path = (
                Path(raw_install_path) if isinstance(raw_install_path, str) else None
            )
            cache_exists = install_path is not None and install_path.is_dir()
            short_name = plugin_name.split("@", 1)[0]
            plugin: dict[str, Any] = {
                "name": plugin_name,
                "version": record.get("version") if isinstance(record, dict) else None,
                "available_version": available.get(short_name),
                "install_path": str(install_path) if install_path is not None else None,
                "cache_exists": cache_exists,
                "skills": [],
            }
            if cache_exists:
                for definition in sorted(install_path.rglob("SKILL.md")):
                    metadata, _ = frontmatter(definition)
                    skill_name = metadata.get("name") or definition.parent.name
                    entry = {
                        "name": skill_name,
                        "plugin": plugin_name,
                        "path": str(definition),
                        "sha256": sha256(definition),
                    }
                    plugin["skills"].append(entry)
                    skills.setdefault(skill_name, []).append(entry)
            result["enabled"].append(plugin)
        for name, entries in sorted(skills.items()):
            if len(entries) < 2:
                continue
            result["skill_collisions"].append(
                {
                    "name": name,
                    "identical": len({entry["sha256"] for entry in entries}) == 1,
                    "sources": [
                        {"plugin": entry["plugin"], "path": entry["path"]}
                        for entry in entries
                    ],
                }
            )
        claude_root = self.destinations.get("claude", {}).get("skills")
        if claude_root is not None and claude_root.is_dir():
            direct: dict[str, str] = {}
            for entry in sorted(claude_root.iterdir()):
                if entry.is_symlink() and not entry.exists():
                    continue
                definition = entry / "SKILL.md"
                if not definition.is_file():
                    continue
                metadata, _ = frontmatter(definition)
                direct[metadata.get("name") or entry.name] = str(entry)
            for name in sorted(set(direct) & set(skills)):
                result["surface_collisions"].append(
                    {
                        "name": name,
                        "direct_path": direct[name],
                        "plugins": sorted({entry["plugin"] for entry in skills[name]}),
                    }
                )
        return result

    def audit_roots_report(self) -> list[dict[str, Any]]:
        definitions = self.manifest.get("audit_roots", [])
        if not isinstance(definitions, list):
            return [{"error": "audit_roots must be an array"}]
        roots: list[dict[str, Any]] = []
        for definition in definitions:
            if not isinstance(definition, dict) or not isinstance(
                definition.get("path"), str
            ):
                roots.append({"error": "invalid audit root definition"})
                continue
            root = Path(definition["path"]).expanduser()
            record: dict[str, Any] = {
                "name": definition.get("name", root.name),
                "path": str(root),
                "owner": definition.get("owner"),
                "read_only": definition.get("read_only", False),
                "alias_of": definition.get("alias_of"),
                "exists": root.is_dir(),
                "assets": [],
            }
            if root.is_dir():
                if definition.get("flat_markdown"):
                    candidates = sorted(
                        path for path in root.glob("*.md") if path.is_file()
                    )
                    for path in candidates:
                        metadata, _ = frontmatter(path)
                        record["assets"].append(
                            {
                                "name": metadata.get("name") or path.stem,
                                "path": str(path),
                                "type": "legacy-markdown",
                            }
                        )
                else:
                    for path in sorted(root.iterdir()):
                        if not path.is_dir():
                            continue
                        definition_path = path / "SKILL.md"
                        if not definition_path.is_file():
                            continue
                        metadata, _ = frontmatter(definition_path)
                        record["assets"].append(
                            {
                                "name": metadata.get("name") or path.name,
                                "path": str(path),
                                "target": (
                                    str(lexical_target(path))
                                    if path.is_symlink()
                                    else None
                                ),
                                "type": "skill",
                            }
                        )
            roots.append(record)
        return roots

    def destination_metadata_issues(
        self, report: dict[str, Any]
    ) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        seen: set[Path] = set()
        previous = self.previous_links()
        for definitions in self.destinations.values():
            root = definitions.get("skills")
            if root is None or root in seen or not root.is_dir():
                continue
            seen.add(root)
            for entry in sorted(root.iterdir()):
                if entry.is_symlink() and not entry.exists():
                    continue
                if entry.is_symlink() and self.link_is_managed(entry, previous):
                    continue
                if not entry.is_dir():
                    continue
                definition = entry / "SKILL.md"
                if not definition.is_file():
                    issues.append(
                        issue(
                            "warning",
                            "destination-skill-missing",
                            f"skill directory has no SKILL.md: {entry}",
                            path=str(entry),
                        )
                    )
                    continue
                metadata, parse_issues = frontmatter(definition)
                for message in parse_issues:
                    issues.append(
                        issue("warning", "destination-frontmatter-invalid", message)
                    )
                if metadata.get("name") != entry.name:
                    issues.append(
                        issue(
                            "warning",
                            "destination-name-mismatch",
                            f"skill name does not match destination directory: {entry}",
                            path=str(entry),
                            name=metadata.get("name"),
                        )
                    )
                if not metadata.get("description"):
                    issues.append(
                        issue(
                            "warning",
                            "destination-description-missing",
                            f"skill description is missing: {definition}",
                            path=str(definition),
                        )
                    )
        return issues

    def doctor(self) -> int:
        report = self.base_report(include_unmanaged=True)
        report["issues"].extend(self.destination_metadata_issues(report))
        report["audit_roots"] = self.audit_roots_report()
        for root in report["audit_roots"]:
            if root.get("error"):
                report["issues"].append(
                    issue("error", "audit-root-invalid", root["error"])
                )
            elif not root.get("exists") and not root.get("alias_of"):
                report["issues"].append(
                    issue(
                        "warning",
                        "audit-root-missing",
                        f"configured audit root is absent: {root['path']}",
                        path=root["path"],
                    )
                )
            for asset in root.get("assets", []):
                if asset.get("type") == "legacy-markdown":
                    report["issues"].append(
                        issue(
                            "warning",
                            "legacy-skill-file",
                            f"legacy standalone skill file remains: {asset['path']}",
                            path=asset["path"],
                        )
                    )
        if self.manifest.get("plugin_audit") is True:
            plugins = self.plugin_report()
            report["plugins"] = plugins
            if plugins.get("error"):
                report["issues"].append(
                    issue("error", "plugins-invalid", plugins["error"])
                )
            if plugins.get("registry_error"):
                report["issues"].append(
                    issue("error", "plugin-registry-invalid", plugins["registry_error"])
                )
            for plugin in plugins.get("enabled", []):
                if not plugin["cache_exists"]:
                    report["issues"].append(
                        issue(
                            "error",
                            "plugin-cache-missing",
                            f"enabled plugin cache is missing: {plugin['name']}",
                            plugin=plugin["name"],
                            path=plugin["install_path"],
                        )
                    )
                elif plugin.get("available_version") and plugin.get(
                    "version"
                ) != plugin.get("available_version"):
                    report["issues"].append(
                        issue(
                            "warning",
                            "plugin-outdated",
                            f"plugin {plugin['name']} is {plugin['version']}; available {plugin['available_version']}",
                            plugin=plugin["name"],
                        )
                    )
            for collision in plugins.get("skill_collisions", []):
                report["issues"].append(
                    issue(
                        "warning",
                        "plugin-skill-duplicate",
                        f"enabled plugins duplicate skill '{collision['name']}'",
                        **collision,
                    )
                )
            for collision in plugins.get("surface_collisions", []):
                report["issues"].append(
                    issue(
                        "warning",
                        "claude-skill-collision",
                        f"a direct Claude skill and enabled plugin share '{collision['name']}'",
                        **collision,
                    )
                )
        self.finish_report(report, title="Agent asset doctor")
        return 1 if any(item["severity"] == "error" for item in report["issues"]) else 0

    def status_or_verify(self, verify: bool) -> int:
        report = self.base_report(include_unmanaged=False)
        errors = [item for item in report["issues"] if item["severity"] == "error"]
        if verify:
            if errors:
                self.print_issues(errors)
            return 1 if errors else 0
        self.finish_report(report, title="Agent asset status")
        return 1 if errors else 0

    def finish_report(self, report: dict[str, Any], title: str) -> None:
        severities = {"error": 0, "warning": 0, "info": 0}
        for item in report["issues"]:
            severities[item["severity"]] = severities.get(item["severity"], 0) + 1
        report["summary"] = {
            "expected": len(report["expected"]),
            "current": len(report["links"]["current"]),
            "errors": severities.get("error", 0),
            "warnings": severities.get("warning", 0),
        }
        if self.args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
            return
        if self.args.quiet:
            return
        print(title)
        print(f"  manifests         {', '.join(report['manifests'])}")
        print(f"  platform          {report['platform']}")
        print(f"  selected groups   {', '.join(report['selected_groups']) or '(none)'}")
        print(
            f"  managed links     {report['summary']['current']}/{report['summary']['expected']} current"
        )
        if "plugins" in report:
            enabled = report["plugins"].get("enabled", [])
            missing = sum(not item["cache_exists"] for item in enabled)
            outdated = sum(
                bool(item.get("available_version"))
                and item.get("version") != item.get("available_version")
                for item in enabled
            )
            collisions = len(report["plugins"].get("skill_collisions", []))
            surface_collisions = len(report["plugins"].get("surface_collisions", []))
            print(
                f"  Claude plugins    {len(enabled)} enabled, {missing} missing, "
                f"{outdated} outdated, {collisions + surface_collisions} skill collisions"
            )
        print(
            f"  issues            {report['summary']['errors']} errors, "
            f"{report['summary']['warnings']} warnings"
        )
        self.print_issues(report["issues"])

    @staticmethod
    def print_issues(items: Iterable[dict[str, Any]]) -> None:
        items = list(items)
        if not items:
            return
        print("Issues:", file=sys.stderr)
        for item in items:
            print(
                f"  {item['severity'].upper():<7} {item['code']}: {item['message']}",
                file=sys.stderr,
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=("converge", "status", "verify", "doctor", "remove")
    )
    parser.add_argument("--manifest")
    parser.add_argument("--state")
    parser.add_argument("--ownership")
    parser.add_argument("--dry-run", "-n", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    if os.environ.get("ENVY_DRY_RUN", "false").lower() in {"1", "true", "yes"}:
        args.dry_run = True
    return args


def main() -> int:
    args = parse_args()
    try:
        manager = AgentAssets(args)
    except AssetError as error:
        if args.json:
            print(
                json.dumps({"summary": {"errors": 1}, "issues": [str(error)]}, indent=2)
            )
        else:
            print(f"agent-assets: {error}", file=sys.stderr)
        return 2
    if args.action == "converge":
        return manager.converge()
    if args.action == "remove":
        return manager.remove()
    if args.action == "doctor":
        return manager.doctor()
    return manager.status_or_verify(verify=args.action == "verify")


if __name__ == "__main__":
    raise SystemExit(main())
