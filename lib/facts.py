#!/usr/bin/env python3
"""Resolve structured bundle host facts without shell evaluation."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from manifest import (
    ENVIRONMENT_KEY,
    RESERVED_ENVIRONMENT,
    ManifestError,
    structured_argv,
)


PLATFORM = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class FactError(RuntimeError):
    pass


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise FactError(f"resolved manifest must contain an object: {path}")
    return value


def provider_value(provider: dict[str, Any]) -> dict[str, Any]:
    origin = provider.get("origin", "unknown")
    root = Path(str(provider.get("root", "")))
    handler = provider.get("handler")
    if not isinstance(handler, dict) or not root.is_dir():
        raise FactError(f"invalid facts provider metadata for {origin}")
    try:
        command = structured_argv(handler, root)
    except ManifestError as error:
        raise FactError(str(error)) from error
    try:
        process = subprocess.run(
            command,
            cwd=root,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise FactError(f"facts provider failed for {origin}: {error}") from error
    if process.returncode != 0:
        detail = process.stderr.decode(errors="replace").strip()
        raise FactError(
            f"facts provider failed for {origin}: {detail or process.returncode}"
        )
    if len(process.stdout) > 1024 * 1024:
        raise FactError(f"facts provider output is too large: {origin}")
    try:
        value = json.loads(process.stdout or b"{}")
    except json.JSONDecodeError as error:
        raise FactError(
            f"facts provider returned invalid JSON: {origin}: {error}"
        ) from error
    if not isinstance(value, dict):
        raise FactError(f"facts provider must return an object: {origin}")
    unknown = set(value) - {"platform", "hierarchy", "environment"}
    if unknown:
        raise FactError(f"facts provider returned unsupported fields: {origin}")
    return value


def merge(manifest: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {"environment": {}}
    for provider in manifest.get("fact_providers", []):
        if not isinstance(provider, dict):
            raise FactError("invalid facts provider entry")
        value = provider_value(provider)
        platform = value.get("platform")
        hierarchy = value.get("hierarchy")
        if (platform is None) != (hierarchy is None):
            raise FactError(
                "facts provider must return platform and hierarchy together"
            )
        if platform is not None:
            if not isinstance(platform, str) or not PLATFORM.fullmatch(platform):
                raise FactError(f"invalid platform fact: {platform!r}")
            if (
                not isinstance(hierarchy, list)
                or not hierarchy
                or not all(
                    isinstance(item, str) and PLATFORM.fullmatch(item)
                    for item in hierarchy
                )
                or len(hierarchy) != len(set(hierarchy))
                or hierarchy[0] != platform
                or hierarchy[-1] != "global"
            ):
                raise FactError(f"invalid platform hierarchy for {platform}")
            existing = result.get("platform")
            if existing is not None and existing != platform:
                raise FactError(
                    f"conflicting platform facts: {existing} and {platform}"
                )
            result["platform"] = platform
            result["hierarchy"] = hierarchy

        environment = value.get("environment", {})
        if not isinstance(environment, dict):
            raise FactError("facts environment must be an object")
        for key, raw in environment.items():
            if (
                not isinstance(key, str)
                or not ENVIRONMENT_KEY.fullmatch(key)
                or key in RESERVED_ENVIRONMENT
                or not isinstance(raw, str)
                or "\0" in raw
            ):
                raise FactError(f"invalid facts environment key: {key!r}")
            existing = result["environment"].get(key)
            if existing is not None and existing != raw:
                raise FactError(f"conflicting facts environment value for {key}")
            result["environment"][key] = raw
    return result


def emit_nul(values: list[str]) -> None:
    for value in values:
        sys.stdout.buffer.write(value.encode() + b"\0")


def main() -> int:
    if len(sys.argv) != 2:
        return 2
    try:
        value = merge(load(Path(sys.argv[1])))
        pairs: list[str] = []
        if "MINS_PLATFORM_OVERRIDE" not in os.environ and value.get("platform"):
            pairs.extend(("MINS_PLATFORM_OVERRIDE", value["platform"]))
        if "MINS_PLATFORM_HIERARCHY_OVERRIDE" not in os.environ and value.get(
            "hierarchy"
        ):
            pairs.extend(
                (
                    "MINS_PLATFORM_HIERARCHY_OVERRIDE",
                    " ".join(value["hierarchy"]),
                )
            )
        for key, raw in value["environment"].items():
            if key not in os.environ:
                pairs.extend((key, raw))
        emit_nul(pairs)
        return 0
    except (FactError, OSError, json.JSONDecodeError) as error:
        print(f"mins-env facts: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
