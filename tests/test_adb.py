#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "mins_env_adb", ROOT / "features" / "adb.py"
)
assert SPEC and SPEC.loader
adb = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(adb)


class AdbTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.sdk = self.root / "sdk"
        executable = self.sdk / "platform-tools" / "adb"
        executable.parent.mkdir(parents=True)
        executable.write_text(
            "#!/bin/sh\necho 'Android Debug Bridge test'\n", encoding="utf-8"
        )
        executable.chmod(0o755)
        self.state = self.root / "state.json"
        self.state.write_text(
            json.dumps({"schema_version": 3, "features": {"adb": {"data": {}}}}),
            encoding="utf-8",
        )
        os.environ.update(
            {
                "MINS_ADB_SDK_ROOT": str(self.sdk),
                "MINS_ENV_STATE_FILE": str(self.state),
                "MINS_ENV_ROOT": str(ROOT),
            }
        )

    def tearDown(self) -> None:
        for key in ("MINS_ADB_SDK_ROOT", "MINS_ENV_STATE_FILE", "MINS_ENV_ROOT"):
            os.environ.pop(key, None)
        self.temporary.cleanup()

    def test_explicit_sdk_is_recorded_and_verified(self) -> None:
        self.assertEqual(adb.converge(), 0)
        value = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(value["features"]["adb"]["data"]["sdk_root"], str(self.sdk))
        self.assertEqual(adb.verify(False), 0)

    def test_invalid_explicit_sdk_is_rejected(self) -> None:
        os.environ["MINS_ADB_SDK_ROOT"] = str(self.root / "missing")
        with self.assertRaises(adb.AdbError):
            adb.choose(adb.candidates())


if __name__ == "__main__":
    unittest.main()
