#!/usr/bin/env bash
# Portable platform detection and scope hierarchy. Source this file.

[[ -n "${_MINS_PLATFORM_SH_LOADED:-}" ]] && return 0
_MINS_PLATFORM_SH_LOADED=1

mins_platform_hierarchy() {
  local platform="${1:?usage: mins_platform_hierarchy <platform>}"
  if [[ -n "${MINS_PLATFORM_HIERARCHY_OVERRIDE:-}" ]] \
    && [[ "$platform" == "${MINS_PLATFORM_OVERRIDE:-$platform}" ]]; then
    echo "$MINS_PLATFORM_HIERARCHY_OVERRIDE"
    return
  fi
  if [[ -n "${MINS_PLATFORM_HIERARCHY:-}" ]] \
    && [[ "$platform" == "${MINS_PLATFORM:-$platform}" ]]; then
    echo "$MINS_PLATFORM_HIERARCHY"
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

mins_platform_matches() {
  local platform="${1:?usage: mins_platform_matches <platform> <target>}"
  local target="${2:?usage: mins_platform_matches <platform> <target>}"
  local ancestor
  for ancestor in $(mins_platform_hierarchy "$platform"); do
    [[ "$ancestor" == "$target" ]] && return 0
  done
  return 1
}

mins_detect_platform() {
  local uname_s
  if [[ -n "${MINS_PLATFORM_OVERRIDE:-}" ]]; then
    MINS_PLATFORM="$MINS_PLATFORM_OVERRIDE"
  else
    uname_s="$(uname -s 2>/dev/null || echo unknown)"
    case "$uname_s" in
      Darwin) MINS_PLATFORM=darwin ;;
      Linux)
        if [[ -n "${WSL_DISTRO_NAME:-}" ]] \
          || grep -qi microsoft /proc/version 2>/dev/null; then
          MINS_PLATFORM=wsl
        else
          MINS_PLATFORM=linux
        fi
        ;;
      MINGW*|MSYS*|CYGWIN*) MINS_PLATFORM=git-bash ;;
      *) MINS_PLATFORM=unsupported ;;
    esac
  fi
  MINS_SHELL="$(basename "${SHELL:-bash}")"
  export MINS_PLATFORM MINS_SHELL
}

mins_resolve_scope() {
  local platform="${1:?usage: mins_resolve_scope <platform> <scope-json>}"
  local scope_json="${2:?usage: mins_resolve_scope <platform> <scope-json>}"
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
' "$scope_json" "$(mins_platform_hierarchy "$platform")")"
  [[ "$result" == 1 ]]
}

mins_platform_self_test() {
  [[ "$(mins_platform_hierarchy darwin)" == "darwin unix global" ]]
  [[ "$(mins_platform_hierarchy linux)" == "linux unix global" ]]
  [[ "$(mins_platform_hierarchy wsl)" == "wsl linux unix global" ]]
  [[ "$(mins_platform_hierarchy git-bash)" == "git-bash windows global" ]]
  mins_platform_matches linux unix
  ! mins_platform_matches linux windows
  mins_resolve_scope linux '{"unix":true}'
  ! mins_resolve_scope linux '{"linux":false,"unix":true}'
  echo "mins_platform_self_test: all tests passed"
}
