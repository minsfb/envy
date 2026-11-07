#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-converge}"
ENGINE="${ENVY_ROOT:?}/lib/smart_merge.py"
CLI="$ENVY_ROOT/bin/nv-smart-merge"
GIT_ADAPTER="$ENVY_ROOT/bin/nv-smart-merge-git"
GIT_SETUP="$ENVY_ROOT/bin/nv-smart-merge-git-setup"

_verify() {
  [[ -f "$ENGINE" && -x "$CLI" && -x "$GIT_ADAPTER" && -x "$GIT_SETUP" ]]
  # Verification must work in a read-only checkout without creating bytecode.
  python3 -B -c 'import sys; from pathlib import Path; compile(Path(sys.argv[1]).read_bytes(), sys.argv[1], "exec")' "$ENGINE"
  PYTHONDONTWRITEBYTECODE=1 "$CLI" --help >/dev/null
  "$GIT_SETUP" --help >/dev/null
}

case "$ACTION" in
  converge|verify|doctor) _verify ;;
  remove) echo "Retaining the smart-merge engine in the envy checkout" ;;
  *) echo "usage: smart-merge.sh {converge|verify|doctor|remove}" >&2; exit 2 ;;
esac
