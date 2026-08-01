#!/usr/bin/env bash
# Composed manifest access and structured feature execution. Source this file.

[[ -n "${_MINS_FEATURES_SH_LOADED:-}" ]] && return 0
_MINS_FEATURES_SH_LOADED=1

MINS_ENV_ROOT="${MINS_ENV_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
MINS_ENV_DIR="$MINS_ENV_ROOT"
_MINS_CORE_JSON="$MINS_ENV_ROOT/mins-env.json"
_MINS_MANIFEST_PY="$MINS_ENV_ROOT/lib/manifest.py"
_MINS_FACTS_PY="$MINS_ENV_ROOT/lib/facts.py"
_MINS_ENV_JSON=""
_MINS_MANIFEST_TEMP=""
readonly MINS_HANDLER_UNAVAILABLE=125
export MINS_ENV_ROOT MINS_ENV_DIR

# shellcheck source=lib/platform.sh
source "$MINS_ENV_ROOT/lib/platform.sh"

_mins_py_manifest() {
  local script="$1"
  shift
  python3 -c "$script" "${_MINS_ENV_JSON:?manifest is not initialized}" "$@"
}

mins_manifest_init() {
  local key value facts_file facts_valid=true
  local bundles=("$@")
  _MINS_MANIFEST_TEMP="$(mktemp "${TMPDIR:-/tmp}/mins-env-manifest.XXXXXX.json")"
  python3 "$_MINS_MANIFEST_PY" compose "$_MINS_CORE_JSON" "${bundles[@]}" > "$_MINS_MANIFEST_TEMP" || {
    rm -f "$_MINS_MANIFEST_TEMP"
    _MINS_MANIFEST_TEMP=""
    return 1
  }
  _MINS_ENV_JSON="$_MINS_MANIFEST_TEMP"
  MINS_ENV_MANIFEST="$_MINS_ENV_JSON"
  WORKSPACE_ROOT="$(_mins_py_manifest 'import json,sys; print(json.load(open(sys.argv[1]))["workspace_root"])')"
  export WORKSPACE_ROOT MINS_ENV_MANIFEST
  while IFS= read -r -d '' key && IFS= read -r -d '' value; do
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || {
      echo "Invalid bundle environment key: $key" >&2
      return 1
    }
    printf -v "$key" '%s' "$value"
    export "${key?}"
  done < <(python3 "$_MINS_MANIFEST_PY" environment "$_MINS_ENV_JSON")
  facts_file="$(mktemp "${TMPDIR:-/tmp}/mins-env-facts.XXXXXX")"
  if ! python3 "$_MINS_FACTS_PY" "$_MINS_ENV_JSON" > "$facts_file"; then
    rm -f "$facts_file"
    return 1
  fi
  while IFS= read -r -d '' key && IFS= read -r -d '' value; do
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || {
      echo "Invalid host fact environment key: $key" >&2
      facts_valid=false
      break
    }
    printf -v "$key" '%s' "$value"
    export "${key?}"
  done < "$facts_file"
  rm -f "$facts_file"
  [[ "$facts_valid" == true ]] || return 1
  mins_detect_platform
}

mins_manifest_cleanup() {
  [[ -n "$_MINS_MANIFEST_TEMP" ]] && rm -f "$_MINS_MANIFEST_TEMP"
  _MINS_MANIFEST_TEMP=""
}

mins_manifest_file() { echo "$_MINS_ENV_JSON"; }
mins_manifest_validate() { python3 -m json.tool "$_MINS_ENV_JSON" >/dev/null; }
mins_manifest_fingerprint() { python3 "$_MINS_MANIFEST_PY" fingerprint "$_MINS_ENV_JSON"; }

mins_manifest_get() {
  local dotted="${1:?manifest field required}"
  _mins_py_manifest '
import json,sys
value=json.load(open(sys.argv[1]))
for part in sys.argv[2].split("."):
    if not isinstance(value,dict) or part not in value: raise SystemExit(1)
    value=value[part]
if isinstance(value,(dict,list)): print(json.dumps(value,separators=(",",":")))
elif isinstance(value,bool): print("true" if value else "false")
elif value is not None: print(value)
' "$dotted"
}

mins_manifest_resource() {
  python3 "$_MINS_MANIFEST_PY" resource "$_MINS_ENV_JSON" "${1:?resource name required}"
}

mins_manifest_setting() {
  mins_manifest_get "settings.${1:?setting path required}"
}

mins_manifest_bundles() {
  _mins_py_manifest '
import json,sys
for item in json.load(open(sys.argv[1])).get("bundles",[]):
    if not item.get("core"): print(item["path"])
'
}

mins_list_features() {
  _mins_py_manifest 'import json,sys; print("\n".join(json.load(open(sys.argv[1]))["features"]))'
}

mins_feature_exists() {
  _mins_py_manifest 'import json,sys; raise SystemExit(0 if sys.argv[2] in json.load(open(sys.argv[1]))["features"] else 1)' "$1"
}

mins_feature_get() {
  local feature="${1:?feature required}" field="${2:?field required}"
  _mins_py_manifest '
import json,sys
feature=json.load(open(sys.argv[1]))["features"].get(sys.argv[2])
if feature is None or sys.argv[3] not in feature: raise SystemExit(1)
value=feature[sys.argv[3]]
if isinstance(value,(dict,list)): print(json.dumps(value,separators=(",",":")))
elif isinstance(value,bool): print("true" if value else "false")
else: print(value)
' "$feature" "$field"
}

mins_feature_tier() { mins_feature_get "$1" tier 2>/dev/null || echo optional; }
mins_feature_default_enabled() {
  local feature="$1"
  [[ "$(mins_feature_get "$feature" default_enabled 2>/dev/null || echo false)" == true ]] && return 0
  _mins_py_manifest '
import json,sys
raise SystemExit(0 if sys.argv[2] in json.load(open(sys.argv[1])).get("recommended_features",[]) else 1)
' "$feature"
}
mins_feature_origin() { mins_feature_get "$1" _origin; }
mins_feature_root() { mins_feature_get "$1" _root; }

mins_feature_in_scope() {
  local scope
  scope="$(mins_feature_get "$1" scope 2>/dev/null)" || return 0
  mins_resolve_scope "$MINS_PLATFORM" "$scope"
}

mins_feature_dependencies() {
  local value
  value="$(mins_feature_get "$1" depends_on 2>/dev/null)" || return 0
  python3 -c 'import json,sys; print("\n".join(json.loads(sys.argv[1])))' "$value"
}

mins_feature_get_env() {
  local value
  value="$(mins_feature_get "$1" env 2>/dev/null)" || return 0
  python3 -c '
import json,sys
for key,value in json.loads(sys.argv[1]).items(): print(f"{key}={value}")
' "$value"
}

mins_feature_resolve_handler() {
  mins_feature_get "${1:?feature required}" "${2:?action required}"
}

mins_run_feature_handler() {
  local feature="${1:?feature required}" action="${2:?action required}"
  local item first root argv_file rc=0
  local argv=()
  mins_feature_resolve_handler "$feature" "$action" >/dev/null 2>&1 \
    || return "$MINS_HANDLER_UNAVAILABLE"
  argv_file="$(mktemp "${TMPDIR:-/tmp}/mins-env-handler.XXXXXX")"
  python3 "$_MINS_MANIFEST_PY" handler-argv "$_MINS_ENV_JSON" "$feature" "$action" > "$argv_file" || rc=$?
  if [[ $rc -ne 0 ]]; then rm -f "$argv_file"; return "$rc"; fi
  while IFS= read -r -d '' item; do argv+=("$item"); done < "$argv_file"
  rm -f "$argv_file"
  [[ -n "${argv[0]+set}" ]] || return "$MINS_HANDLER_UNAVAILABLE"
  first="${argv[0]}"
  root="$(mins_feature_root "$feature")"
  if [[ -f "$first" && ! -x "$first" ]]; then
    case "$first" in
      *.py) argv=(python3 "${argv[@]}") ;;
      *) argv=(bash "${argv[@]}") ;;
    esac
  fi
  (
    cd "$root" || exit
    export MINS_FEATURE="$feature" MINS_FEATURE_ROOT="$root"
    "${argv[@]}"
  )
}

mins_verify_feature() {
  mins_run_feature_handler "$1" verify
}
