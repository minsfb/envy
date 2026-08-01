#!/usr/bin/env bash
# Converge the exact tool set declared by the active managed Mise configuration.
set -euo pipefail

ACTION="${1:-converge}"
ENVY_ROOT="${ENVY_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
STATE_ROOT="${XDG_STATE_HOME:-$HOME/.local/state}/envy"
MISE_CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/mise/config.toml"
MISE_CONFD_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/mise/conf.d"
# The path is resolved from ENVY_ROOT.
# shellcheck disable=SC1091
source "$ENVY_ROOT/lib/pkg.sh"
export PATH="$HOME/.local/bin:$PATH"
pkg_use_external_proxy
# The core dotnet backend honors an inherited system install root. A managed
# user install must remain under Mise's own data directory instead.
unset DOTNET_ROOT

# Bundles may layer additional tools through chezmoi-managed conf.d files.
# Those tools have no entry in the core lockfile, so they converge with a
# second unlocked pass that leaves the locked core tool set authoritative.
_confd_present() {
  local entry
  for entry in "$MISE_CONFD_DIR"/*.toml; do
    [[ -e "$entry" ]] && return 0
  done
  return 1
}

_converge() {
  [[ -f "$MISE_CONFIG" ]] || {
    echo "Mise config is missing: $MISE_CONFIG (upgrade chezmoi first)" >&2
    return 1
  }
  local was_installed=true
  command -v mise >/dev/null 2>&1 || was_installed=false
  pkg_ensure mise mise
  if [[ "$was_installed" != true ]]; then
    mkdir -p "$STATE_ROOT/providers"
    touch "$STATE_ROOT/providers/mise.installed"
  fi
  mise install --locked
  if _confd_present; then
    mise install
  fi
}

_verify() {
  command -v mise >/dev/null
  [[ -f "$MISE_CONFIG" ]]
  mise install --locked --dry-run-code >/dev/null
  if _confd_present; then
    mise install --dry-run-code >/dev/null
  fi
}

_remove() {
  # Tool downloads and shared caches may predate envy and are retained.
  if [[ "${ENVY_PURGE:-false}" == true ]] \
    && [[ -f "$STATE_ROOT/providers/mise.installed" ]] \
    && [[ "$(command -v mise 2>/dev/null || true)" == "$HOME/.local/bin/mise" ]]; then
    rm -f "$HOME/.local/bin/mise"
  fi
}

_doctor() {
  _verify
  mise doctor >/dev/null
}

case "$ACTION" in
  converge) _converge ;;
  verify) _verify ;;
  doctor) _doctor ;;
  remove) _remove ;;
  *) echo "usage: mise.sh {converge|verify|doctor|remove}" >&2; exit 2 ;;
esac
