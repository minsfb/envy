#!/usr/bin/env bash
# Structured state wrapper for mins-env. Source this file.

[[ -n "${_MINS_STATE_SH_LOADED:-}" ]] && return 0
_MINS_STATE_SH_LOADED=1

_MINS_STATE_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/mins-env"
_MINS_STATE_FILE="$_MINS_STATE_DIR/state.json"
_MINS_STATE_PY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/state.py"
export MINS_ENV_STATE_DIR="$_MINS_STATE_DIR" MINS_ENV_STATE_FILE="$_MINS_STATE_FILE"

_mins_state() { python3 "$_MINS_STATE_PY" "$_MINS_STATE_FILE" "$@"; }

mins_state_dir() { mkdir -p "$_MINS_STATE_DIR"; echo "$_MINS_STATE_DIR"; }
mins_state_file() { echo "$_MINS_STATE_FILE"; }
mins_state_exists() { [[ -f "$_MINS_STATE_FILE" ]]; }
mins_state_validate() { _mins_state validate; }

mins_state_init() {
  local manifest="$1" platform="$2" shell="$3" root="$4" preset="$5"
  mkdir -p "$_MINS_STATE_DIR"
  _mins_state init "$manifest" "$platform" "$shell" "$root" "$preset"
}

mins_state_migrate() {
  local manifest="$1" platform="$2" shell="$3" root="$4"
  _mins_state migrate "$manifest" "$platform" "$shell" "$root"
}

mins_state_get() { _mins_state get "$@"; }
mins_state_set() { _mins_state set "$1" "$2"; }

mins_state_feature_init() { _mins_state feature-init "$1" "$2" "$3" "$4"; }
mins_state_feature_get() { _mins_state get "features.$1.$2" "${3:-null}"; }
mins_state_feature_set() { _mins_state set "features.$1.$2" "$3"; }
mins_state_feature_result() { _mins_state feature-result "$1" "$2" "${3:-}"; }
mins_state_feature_verify() { _mins_state feature-verify "$1" "$2" "${3:-}"; }
mins_state_list_enabled() { _mins_state list-enabled; }
mins_state_summary() { _mins_state summary; }
mins_state_export() { _mins_state export; }

mins_state_get_commit() { mins_state_get core_revision '""'; }
mins_state_set_commit() { mins_state_set core_revision "$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$1")"; }
