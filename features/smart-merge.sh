#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-converge}"
ENGINE="${MINS_ENV_ROOT:?}/lib/smart_merge.py"
CLI="$MINS_ENV_ROOT/bin/smart-merge"
GIT_ADAPTER="$MINS_ENV_ROOT/bin/smart-merge-git"
GIT_SETUP="$MINS_ENV_ROOT/bin/smart-merge-git-setup"

_verify() {
  [[ -f "$ENGINE" && -x "$CLI" && -x "$GIT_ADAPTER" && -x "$GIT_SETUP" ]]
  python3 -m py_compile "$ENGINE"
  "$CLI" --help >/dev/null
  "$GIT_SETUP" --help >/dev/null
}

case "$ACTION" in
  converge|verify|doctor) _verify ;;
  remove) echo "Retaining the smart-merge engine in the mins-env checkout" ;;
  *) echo "usage: smart-merge.sh {converge|verify|doctor|remove}" >&2; exit 2 ;;
esac
