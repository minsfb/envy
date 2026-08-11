#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def load_module(name: str, path: Path):
    specification = importlib.util.spec_from_file_location(name, path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


class PluginMarketplaceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.feature = load_module(
            "agent_assets", ROOT / "features" / "agent-assets.py"
        )

    def test_available_versions_follow_registered_marketplaces(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            claude_root = Path(temporary) / ".claude"
            marketplace = claude_root / "example-market"
            manifest = marketplace / ".claude-plugin" / "marketplace.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(
                json.dumps(
                    {"plugins": [{"name": "example", "version": "1.2.3"}]}
                ),
                encoding="utf-8",
            )
            registry = claude_root / "plugins" / "known_marketplaces.json"
            registry.parent.mkdir(parents=True)
            registry.write_text(
                json.dumps(
                    {
                        "example-market": {
                            "installLocation": str(marketplace),
                        }
                    }
                ),
                encoding="utf-8",
            )

            versions = self.feature.available_plugin_versions(claude_root)

        self.assertEqual(versions, {"example@example-market": "1.2.3"})

    def test_missing_or_invalid_marketplaces_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            claude_root = Path(temporary) / ".claude"
            registry = claude_root / "plugins" / "known_marketplaces.json"
            registry.parent.mkdir(parents=True)
            registry.write_text(
                json.dumps(
                    {
                        "missing": {"installLocation": "missing"},
                        "invalid": {"installLocation": 42},
                        "malformed": {
                            "installLocation": str(claude_root / "malformed")
                        },
                    }
                ),
                encoding="utf-8",
            )
            malformed = (
                claude_root
                / "malformed"
                / ".claude-plugin"
                / "marketplace.json"
            )
            malformed.parent.mkdir(parents=True)
            malformed.write_text('{"plugins": {}}', encoding="utf-8")

            versions = self.feature.available_plugin_versions(claude_root)

        self.assertEqual(versions, {})


if __name__ == "__main__":
    unittest.main()
