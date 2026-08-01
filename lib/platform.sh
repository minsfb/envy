#!/usr/bin/env bash
# Portable platform detection and scope hierarchy. Source this file.

[[ -n "${_ENVY_PLATFORM_SH_LOADED:-}" ]] && return 0
_ENVY_PLATFORM_SH_LOADED=1

nv_platform_hierarchy() {
  local platform="${1:?usage: nv_platform_hierarchy <platform>}"
  if [[ -n "${ENVY_PLATFORM_HIERARCHY_OVERRIDE:-}" ]] \
    && [[ "$platform" == "${ENVY_PLATFORM_OVERRIDE:-$platform}" ]]; then
    echo "$ENVY_PLATFORM_HIERARCHY_OVERRIDE"
    return
  fi
  if [[ -n "${ENVY_PLATFORM_HIERARCHY:-}" ]] \
    && [[ "$platform" == "${ENVY_PLATFORM:-$platform}" ]]; then
    echo "$ENVY_PLATFORM_HIERARCHY"
    return
  fi
  case "$platform" in
    darwin) echo "darwin unix global" ;;
    linux) echo "linux unix global" ;;
    wsl) echo "wsl linux unix global" ;;
    git-bash) echo "git-bash windows global" ;;
    windows) echo "windows global" ;;
    unix) echo "unix global" ;;
    global) echo "global" ;;
    *) echo "$platform global" ;;
  esac
}

nv_platform_matches() {
  local platform="${1:?usage: nv_platform_matches <platform> <target>}"
  local target="${2:?usage: nv_platform_matches <platform> <target>}"
  local ancestor
  for ancestor in $(nv_platform_hierarchy "$platform"); do
    [[ "$ancestor" == "$target" ]] && return 0
  done
  return 1
}

nv_detect_platform() {
  local uname_s
  if [[ -n "${ENVY_PLATFORM_OVERRIDE:-}" ]]; then
    ENVY_PLATFORM="$ENVY_PLATFORM_OVERRIDE"
  else
    uname_s="$(uname -s 2>/dev/null || echo unknown)"
    case "$uname_s" in
      Darwin) ENVY_PLATFORM=darwin ;;
      Linux)
        if [[ -n "${WSL_DISTRO_NAME:-}" ]] \
          || grep -qi microsoft /proc/version 2>/dev/null; then
          ENVY_PLATFORM=wsl
        else
          ENVY_PLATFORM=linux
        fi
        ;;
      MINGW*|MSYS*|CYGWIN*) ENVY_PLATFORM=git-bash ;;
      *) ENVY_PLATFORM=unsupported ;;
    esac
  fi
  ENVY_SHELL="$(basename "${SHELL:-bash}")"
  export ENVY_PLATFORM ENVY_SHELL
}

nv_resolve_scope() {
  local platform="${1:?usage: nv_resolve_scope <platform> <scope-json>}"
  local scope_json="${2:?usage: nv_resolve_scope <platform> <scope-json>}"
  local result
  result="$(python3 -c '
import json,sys
scope=json.loads(sys.argv[1])
for ancestor in sys.argv[2].split():
    if ancestor in scope:
        print("1" if scope[ancestor] else "0")
        break
else:
    print("0")
' "$scope_json" "$(nv_platform_hierarchy "$platform")")"
  [[ "$result" == 1 ]]
}

nv_platform_self_test() {
  [[ "$(nv_platform_hierarchy darwin)" == "darwin unix global" ]]
  [[ "$(nv_platform_hierarchy linux)" == "linux unix global" ]]
  [[ "$(nv_platform_hierarchy wsl)" == "wsl linux unix global" ]]
  [[ "$(nv_platform_hierarchy git-bash)" == "git-bash windows global" ]]
  nv_platform_matches linux unix
  ! nv_platform_matches linux windows
  nv_resolve_scope linux '{"unix":true}'
  ! nv_resolve_scope linux '{"linux":false,"unix":true}'
  echo "nv_platform_self_test: all tests passed"
}
