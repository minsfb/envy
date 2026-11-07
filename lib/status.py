#!/usr/bin/env python3
"""Render envy status from one composed manifest and state read."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def in_scope(definition: dict[str, Any], hierarchy: tuple[str, ...]) -> bool:
    scope = definition.get("scope")
    if not isinstance(scope, dict):
        return True
    for ancestor in hierarchy:
        if ancestor in scope:
            return bool(scope[ancestor])
    return False


def main() -> int:
    if len(sys.argv) != 6:
        return 2
    manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    state = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    platform, checkout, raw_hierarchy = sys.argv[3:]
    hierarchy = tuple(raw_hierarchy.split())
    bundles = [
        item.get("id", "unknown")
        for item in manifest.get("bundles", [])
        if not item.get("core")
    ]

    print("envy status")
    print(
        f"  Platform: {platform} | Preset: {state.get('preset', 'custom')} "
        f"| Checkout: {checkout}"
    )
    if bundles:
        print(f"  Bundles:  {', '.join(bundles)}")
    transaction = state.get("bundle_transaction")
    if isinstance(transaction, dict):
        print(
            "  Pending:  bundle "
            f"{transaction.get('action', 'operation')} "
            f"{transaction.get('id', 'unknown')} "
            f"({transaction.get('phase', 'unknown')})"
        )
    print()
    print(
        f"  {'FEATURE':<18} {'TIER':<11} {'ORIGIN':<18} "
        f"{'ENABLED':<10} {'LIFECYCLE':<12} {'STATUS':<20} VERIFY"
    )

    enabled_count = failed_count = drifted_count = unknown_count = 0
    state_features = state.get("features", {})
    for name, definition in manifest.get("features", {}).items():
        if not in_scope(definition, hierarchy):
            continue
        feature = state_features.get(name, {})
        enabled = bool(feature.get("enabled", False))
        status = feature.get("status", "disabled")
        verification = feature.get("verification", "unknown")
        tier = definition.get("tier", "optional")
        origin = definition.get("_origin", "core")
        print(
            f"  {name:<18} {tier:<11} {origin:<18} "
            f"{str(enabled).lower():<10} {definition.get('lifecycle', 'active'):<12} {status:<20} {verification}"
        )
        if enabled:
            enabled_count += 1
            failed_count += status == "failed"
            drifted_count += verification == "drifted"
            unknown_count += verification == "unknown"
        elif feature.get("retirement"):
            failed_count += feature["retirement"].get("status") == "failed"

    print()
    print(
        f"  {enabled_count} enabled, {failed_count} failed convergence, "
        f"{drifted_count} drifted, {unknown_count} unchecked"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
