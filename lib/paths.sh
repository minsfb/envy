#!/usr/bin/env bash
# Resolve the directories envy owns. Source this file.
#
# Every root is a single configured value rather than a literal repeated at
# each call site, so relocating one is a configuration change and the migration
# of a legacy layout has exactly one place to look. Resolution order per root:
# an explicit ENVY_* override, then the matching XDG base directory, then the
# XDG default. Keep this in agreement with lib/paths.py.

[[ -n "${_ENVY_PATHS_SH_LOADED:-}" ]] && return 0
_ENVY_PATHS_SH_LOADED=1

_nv_resolve_root() {
  local explicit="$1" base="$2" base_default="$3" leaf="$4"
  if [[ -n "$explicit" ]]; then
    echo "$explicit"
  elif [[ -n "$base" ]]; then
    echo "$base/$leaf"
  else
    echo "$HOME/$base_default/$leaf"
  fi
}

ENVY_CONFIG_DIR="$(_nv_resolve_root \
  "${ENVY_CONFIG_DIR:-}" "${XDG_CONFIG_HOME:-}" ".config" "envy")"
ENVY_STATE_DIR="$(_nv_resolve_root \
  "${ENVY_STATE_DIR:-}" "${XDG_STATE_HOME:-}" ".local/state" "envy")"
ENVY_DATA_DIR="$(_nv_resolve_root \
  "${ENVY_DATA_DIR:-}" "${XDG_DATA_HOME:-}" ".local/share" "envy")"
ENVY_BIN_DIR="${ENVY_BIN_DIR:-$HOME/.local/bin}"
export ENVY_CONFIG_DIR ENVY_STATE_DIR ENVY_DATA_DIR ENVY_BIN_DIR

nv_config_dir() { echo "$ENVY_CONFIG_DIR"; }
nv_runtime_state_dir() { echo "$ENVY_STATE_DIR"; }
nv_data_dir() { echo "$ENVY_DATA_DIR"; }
nv_bin_dir() { echo "$ENVY_BIN_DIR"; }
