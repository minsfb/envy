#!/usr/bin/env bash
# Composed manifest access and structured feature execution. Source this file.

[[ -n "${_ENVY_FEATURES_SH_LOADED:-}" ]] && return 0
_ENVY_FEATURES_SH_LOADED=1

ENVY_ROOT="${ENVY_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
ENVY_DIR="$ENVY_ROOT"
_ENVY_CORE_JSON="$ENVY_ROOT/envy.json"
_ENVY_MANIFEST_PY="$ENVY_ROOT/lib/manifest.py"
_ENVY_FACTS_PY="$ENVY_ROOT/lib/facts.py"
_ENVY_JSON=""
_ENVY_MANIFEST_TEMP=""
_ENVY_MANIFEST_EXPORT_KEYS=()
_ENVY_FACT_EXPORT_KEYS=()
readonly ENVY_HANDLER_UNAVAILABLE=125
export ENVY_ROOT ENVY_DIR

# shellcheck source=lib/platform.sh
source "$ENVY_ROOT/lib/platform.sh"

_nv_py_manifest() {
  local script="$1"
  shift
  python3 -c "$script" "${_ENVY_JSON:?manifest is not initialized}" "$@"
}

nv_manifest_init() {
  local key value facts_file facts_valid=true
  local bundles=("$@")
  _ENVY_MANIFEST_EXPORT_KEYS=()
  _ENVY_FACT_EXPORT_KEYS=()
  _ENVY_MANIFEST_TEMP="$(mktemp "${TMPDIR:-/tmp}/envy-manifest.json.XXXXXX")"
  python3 "$_ENVY_MANIFEST_PY" compose "$_ENVY_CORE_JSON" ${bundles[0]+"${bundles[@]}"} > "$_ENVY_MANIFEST_TEMP" || {
    rm -f "$_ENVY_MANIFEST_TEMP"
    _ENVY_MANIFEST_TEMP=""
    return 1
  }
  _ENVY_JSON="$_ENVY_MANIFEST_TEMP"
  ENVY_MANIFEST="$_ENVY_JSON"
  WORKSPACE_ROOT="$(_nv_py_manifest 'import json,sys; print(json.load(open(sys.argv[1]))["workspace_root"])')"
  export WORKSPACE_ROOT ENVY_MANIFEST
  while IFS= read -r -d '' key && IFS= read -r -d '' value; do
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || {
      echo "Invalid bundle environment key: $key" >&2
      return 1
    }
    printf -v "$key" '%s' "$value"
    export "${key?}"
    _ENVY_MANIFEST_EXPORT_KEYS+=("$key")
  done < <(python3 "$_ENVY_MANIFEST_PY" environment "$_ENVY_JSON")
  facts_file="$(mktemp "${TMPDIR:-/tmp}/envy-facts.XXXXXX")"
  if ! python3 "$_ENVY_FACTS_PY" "$_ENVY_JSON" > "$facts_file"; then
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
    _ENVY_FACT_EXPORT_KEYS+=("$key")
  done < "$facts_file"
  rm -f "$facts_file"
  [[ "$facts_valid" == true ]] || return 1
  nv_detect_platform
}

nv_manifest_cleanup() {
  [[ -n "$_ENVY_MANIFEST_TEMP" ]] && rm -f "$_ENVY_MANIFEST_TEMP"
  _ENVY_MANIFEST_TEMP=""
}

nv_manifest_file() { echo "$_ENVY_JSON"; }
nv_manifest_validate() { python3 -m json.tool "$_ENVY_JSON" >/dev/null; }
nv_manifest_fingerprint() { python3 "$_ENVY_MANIFEST_PY" fingerprint "$_ENVY_JSON"; }

nv_manifest_get() {
  local dotted="${1:?manifest field required}"
  _nv_py_manifest '
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

nv_manifest_resource() {
  python3 "$_ENVY_MANIFEST_PY" resource "$_ENVY_JSON" "${1:?resource name required}"
}

nv_manifest_setting() {
  nv_manifest_get "settings.${1:?setting path required}"
}

nv_manifest_bundles() {
  _nv_py_manifest '
import json,sys
for item in json.load(open(sys.argv[1])).get("bundles",[]):
    if not item.get("core"): print(item["path"])
'
}

nv_list_features() {
  _nv_py_manifest 'import json,sys; print("\n".join(json.load(open(sys.argv[1]))["features"]))'
}

nv_feature_exists() {
  _nv_py_manifest 'import json,sys; raise SystemExit(0 if sys.argv[2] in json.load(open(sys.argv[1]))["features"] else 1)' "$1"
}

nv_feature_get() {
  local feature="${1:?feature required}" field="${2:?field required}"
  _nv_py_manifest '
import json,sys
feature=json.load(open(sys.argv[1]))["features"].get(sys.argv[2])
if feature is None or sys.argv[3] not in feature: raise SystemExit(1)
value=feature[sys.argv[3]]
if isinstance(value,(dict,list)): print(json.dumps(value,separators=(",",":")))
elif isinstance(value,bool): print("true" if value else "false")
else: print(value)
' "$feature" "$field"
}

nv_feature_tier() { nv_feature_get "$1" tier 2>/dev/null || echo optional; }
nv_feature_default_enabled() {
  local feature="$1"
  [[ "$(nv_feature_get "$feature" default_enabled 2>/dev/null || echo false)" == true ]] && return 0
  _nv_py_manifest '
import json,sys
raise SystemExit(0 if sys.argv[2] in json.load(open(sys.argv[1])).get("recommended_features",[]) else 1)
' "$feature"
}
nv_feature_origin() { nv_feature_get "$1" _origin; }
nv_feature_root() { nv_feature_get "$1" _root; }

nv_feature_in_scope() {
  local scope
  scope="$(nv_feature_get "$1" scope 2>/dev/null)" || return 0
  nv_resolve_scope "$ENVY_PLATFORM" "$scope"
}

nv_feature_dependencies() {
  local value
  value="$(nv_feature_get "$1" depends_on 2>/dev/null)" || return 0
  python3 -c 'import json,sys; print("\n".join(json.loads(sys.argv[1])))' "$value"
}

nv_feature_get_env() {
  local value
  value="$(nv_feature_get "$1" env 2>/dev/null)" || return 0
  python3 -c '
import json,sys
for key,value in json.loads(sys.argv[1]).items(): print(f"{key}={value}")
' "$value"
}

nv_feature_resolve_handler() {
  nv_feature_get "${1:?feature required}" "${2:?action required}"
}

nv_run_feature_handler() {
  local feature="${1:?feature required}" action="${2:?action required}"
  local item first root argv_file rc=0
  local argv=()
  nv_feature_resolve_handler "$feature" "$action" >/dev/null 2>&1 \
    || return "$ENVY_HANDLER_UNAVAILABLE"
  argv_file="$(mktemp "${TMPDIR:-/tmp}/envy-handler.XXXXXX")"
  python3 "$_ENVY_MANIFEST_PY" handler-argv "$_ENVY_JSON" "$feature" "$action" > "$argv_file" || rc=$?
  if [[ $rc -ne 0 ]]; then rm -f "$argv_file"; return "$rc"; fi
  while IFS= read -r -d '' item; do argv+=("$item"); done < "$argv_file"
  rm -f "$argv_file"
  [[ -n "${argv[0]+set}" ]] || return "$ENVY_HANDLER_UNAVAILABLE"
  first="${argv[0]}"
  root="$(nv_feature_root "$feature")"
  if [[ -f "$first" && ! -x "$first" ]]; then
    case "$first" in
      *.py) argv=(python3 "${argv[@]}") ;;
      *) argv=(bash "${argv[@]}") ;;
    esac
  fi
  (
    cd "$root" || exit
    export ENVY_FEATURE="$feature" ENVY_FEATURE_ROOT="$root"
    "${argv[@]}"
  )
}

nv_verify_feature() {
  nv_run_feature_handler "$1" verify
}
