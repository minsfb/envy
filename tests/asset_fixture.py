"""Shared owned-installation fixtures; importing this module runs no tests."""

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def snapshot(root):
    result = {}
    for path in root.rglob("*"):
        name = str(path.relative_to(root))
        if path.is_symlink():
            result[name] = ("symlink", os.readlink(path))
        elif path.is_dir():
            result[name] = ("directory",)
        elif path.is_file():
            result[name] = ("file", path.read_bytes())
    return result


def asset_environment(home, manifest, state, ownership, platform):
    # Preserve the caller's PATH, security, auth, proxy and CA context. Bind only
    # installer-owned paths and selectors; CLI paths additionally fix precedence.
    return {
        **os.environ,
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_STATE_HOME": str(home / ".local/state"),
        "XDG_DATA_HOME": str(home / ".local/share"),
        "ENVY_CONFIG_DIR": str(home / ".config/envy"),
        "ENVY_STATE_DIR": str(home / ".local/state/envy"),
        "ENVY_DATA_DIR": str(home / ".local/share/envy"),
        "ENVY_BIN_DIR": str(home / ".local/bin"),
        "ENVY_DIR": str(ROOT),
        "ENVY_MANIFEST": str(ROOT / "envy.json"),
        "ENVY_AGENT_ASSETS_MANIFEST": str(manifest),
        "ENVY_STATE_FILE": str(state),
        "ENVY_AGENT_ASSETS_OWNERSHIP": str(ownership),
        "ENVY_PLATFORM": platform,
        "ENVY_PLATFORM_HIERARCHY_OVERRIDE": "",
        "ENVY_DRY_RUN": "false",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
