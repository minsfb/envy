#!/usr/bin/env python3
"""Resolve the directories envy owns.

Every root is a single configured value rather than a literal repeated at each
call site, so relocating one is a configuration change and the migration of a
legacy layout has exactly one place to look.

Resolution order per root: an explicit ENVY_* override, then the matching XDG
base directory, then the XDG default.
"""

from __future__ import annotations

import os
from pathlib import Path

# root name -> (explicit override, XDG base variable, XDG default, leaf)
ROOTS = {
    "config": ("ENVY_CONFIG_DIR", "XDG_CONFIG_HOME", ".config", "envy"),
    "state": ("ENVY_STATE_DIR", "XDG_STATE_HOME", ".local/state", "envy"),
    "data": ("ENVY_DATA_DIR", "XDG_DATA_HOME", ".local/share", "envy"),
}
BIN_OVERRIDE = "ENVY_BIN_DIR"
BIN_DEFAULT = ".local/bin"


def home() -> Path:
    return Path(os.environ.get("HOME") or Path.home())


def root(name: str) -> Path:
    try:
        override, base_variable, base_default, leaf = ROOTS[name]
    except KeyError:
        raise ValueError(f"unknown envy root: {name}") from None
    explicit = os.environ.get(override)
    if explicit:
        return Path(explicit).expanduser()
    base = os.environ.get(base_variable)
    return (Path(base) if base else home() / base_default).expanduser() / leaf


def xdg_home(base_variable: str, base_default: str) -> Path:
    """An XDG base directory itself, for paths that other tools own.

    envy's own roots come from the helpers below; this is only for reaching
    third-party locations such as an editor or terminal configuration.
    """
    base = os.environ.get(base_variable)
    return (Path(base) if base else home() / base_default).expanduser()


def xdg_config_home() -> Path:
    return xdg_home("XDG_CONFIG_HOME", ".config")


def xdg_state_home() -> Path:
    return xdg_home("XDG_STATE_HOME", ".local/state")


def xdg_data_home() -> Path:
    return xdg_home("XDG_DATA_HOME", ".local/share")


def config_dir() -> Path:
    """state.json, init-sh.env, shell.d, and other configuration envy writes."""
    return root("config")


def state_dir() -> Path:
    """Logs, provider markers, backups, and other reproducible runtime state."""
    return root("state")


def data_dir() -> Path:
    """The composed Chezmoi source and managed plugin checkouts."""
    return root("data")


def bin_dir() -> Path:
    """Where the launcher and managed helper commands are installed."""
    explicit = os.environ.get(BIN_OVERRIDE)
    if explicit:
        return Path(explicit).expanduser()
    return home() / BIN_DEFAULT


def state_file() -> Path:
    explicit = os.environ.get("ENVY_STATE_FILE")
    if explicit:
        return Path(explicit).expanduser()
    return config_dir() / "state.json"


def describe() -> dict[str, str]:
    return {
        "config": str(config_dir()),
        "state": str(state_dir()),
        "data": str(data_dir()),
        "bin": str(bin_dir()),
        "state_file": str(state_file()),
    }


if __name__ == "__main__":
    for key, value in describe().items():
        print(f"{key}\t{value}")
