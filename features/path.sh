#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-verify}"
ENV_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/envy/init-sh.env"

_verify() {
  [[ -f "$ENV_FILE" ]]
  [[ -d "${ENVY_ROOT:?}/bin" ]]
  bash -c '
    set -u
    source "$1"
    [[ "$ENVY_ROOT" == "$2" && "$WORKSPACE_ROOT" == "$3" ]]
    case ":$PATH:" in *":$2/bin:"*) ;; *) exit 1 ;; esac
  ' _ "$ENV_FILE" "$ENVY_ROOT" "${WORKSPACE_ROOT:?}"
}

case "$ACTION" in
  converge) : ;; # The engine atomically regenerates init-sh.env after handlers.
  verify|doctor) _verify ;;
  remove) echo "Generated environment will be regenerated without disabled feature paths" ;;
  *) echo "usage: path.sh {converge|verify|doctor|remove}" >&2; exit 2 ;;
esac
