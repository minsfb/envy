#!/usr/bin/env bash
# Run with the latest generated environment, even from a long-lived transport.
set -euo pipefail

[[ "${1:-}" != -- ]] || shift
if (( $# == 0 )); then
  echo 'usage: envy exec [--] COMMAND [ARG...]' >&2
  exit 2
fi

# shellcheck source=paths.sh
source "${ENVY_ROOT:?}/lib/paths.sh"
_envy_exec_file="$ENVY_CONFIG_DIR/init-sh.env"
if [[ ! -r "$_envy_exec_file" ]]; then
  echo 'envy exec: generated environment is missing; run envy install or upgrade' >&2
  exit 1
fi

# Feature PATH^ entries may override user-bin and system tools intentionally.
export PATH="$ENVY_BIN_DIR${PATH:+:$PATH}"
# shellcheck disable=SC1090
source "$_envy_exec_file"

# Repeated invocations must not accumulate duplicate PATH entries. Use exact
# string comparisons (not patterns), preserving order and empty components.
_envy_exec_paths=()
_envy_exec_rest="$PATH"
_envy_exec_unique=""
while :; do
  _envy_exec_entry="${_envy_exec_rest%%:*}"
  _envy_exec_seen=false
  for _envy_exec_old in ${_envy_exec_paths[0]+"${_envy_exec_paths[@]}"}; do
    if [[ "$_envy_exec_old" == "$_envy_exec_entry" ]]; then
      _envy_exec_seen=true
      break
    fi
  done
  if [[ "$_envy_exec_seen" == false ]]; then
    if (( ${#_envy_exec_paths[@]} > 0 )); then
      _envy_exec_unique+=:
    fi
    _envy_exec_unique+="$_envy_exec_entry"
    _envy_exec_paths+=("$_envy_exec_entry")
  fi
  [[ "$_envy_exec_rest" == *:* ]] || break
  _envy_exec_rest="${_envy_exec_rest#*:}"
done
export PATH="$_envy_exec_unique"
unset _envy_exec_file _envy_exec_paths _envy_exec_rest _envy_exec_unique
unset _envy_exec_entry _envy_exec_seen _envy_exec_old
exec -- "$@"
