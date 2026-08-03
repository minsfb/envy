#!/usr/bin/env bash
# Structured state wrapper for envy. Source this file.

[[ -n "${_ENVY_STATE_SH_LOADED:-}" ]] && return 0
_ENVY_STATE_SH_LOADED=1

# shellcheck source=paths.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/paths.sh"
_ENVY_STATE_DIR="$ENVY_CONFIG_DIR"
_ENVY_STATE_FILE="$_ENVY_STATE_DIR/state.json"
_ENVY_STATE_PY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/state.py"
export ENVY_STATE_DIR="$_ENVY_STATE_DIR" ENVY_STATE_FILE="$_ENVY_STATE_FILE"

_nv_state() { python3 "$_ENVY_STATE_PY" "$_ENVY_STATE_FILE" "$@"; }

nv_state_dir() { mkdir -p "$_ENVY_STATE_DIR"; echo "$_ENVY_STATE_DIR"; }
nv_state_file() { echo "$_ENVY_STATE_FILE"; }
nv_state_exists() { [[ -f "$_ENVY_STATE_FILE" ]]; }
nv_state_validate() { _nv_state validate; }

nv_state_init() {
  local manifest="$1" platform="$2" shell="$3" root="$4" preset="$5"
  mkdir -p "$_ENVY_STATE_DIR"
  _nv_state init "$manifest" "$platform" "$shell" "$root" "$preset"
}

nv_state_migrate() {
  local manifest="$1" platform="$2" shell="$3" root="$4"
  _nv_state migrate "$manifest" "$platform" "$shell" "$root"
}

nv_state_get() { _nv_state get "$@"; }
nv_state_set() { _nv_state set "$1" "$2"; }

nv_state_feature_init() { _nv_state feature-init "$1" "$2" "$3" "$4"; }
nv_state_feature_get() { _nv_state get "features.$1.$2" "${3:-null}"; }
nv_state_feature_set() { _nv_state set "features.$1.$2" "$3"; }
nv_state_feature_result() { _nv_state feature-result "$1" "$2" "${3:-}"; }
nv_state_feature_verify() { _nv_state feature-verify "$1" "$2" "${3:-}"; }
nv_state_list_enabled() { _nv_state list-enabled; }
nv_state_summary() { _nv_state summary; }
nv_state_export() { _nv_state export; }

nv_state_get_commit() { nv_state_get core_revision '""'; }
nv_state_set_commit() { nv_state_set core_revision "$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$1")"; }
