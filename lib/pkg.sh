#!/usr/bin/env bash
# Minimal package-provider boundary for envy. Source this file.

pkg_use_external_proxy() {
  local proxy="${ENVY_NETWORK_PROXY:-}"
  [[ -n "$proxy" ]] || return 0
  [[ -n "${HTTP_PROXY:-}" ]] || HTTP_PROXY="$proxy"
  [[ -n "${HTTPS_PROXY:-}" ]] || HTTPS_PROXY="$proxy"
  [[ -n "${http_proxy:-}" ]] || http_proxy="$proxy"
  [[ -n "${https_proxy:-}" ]] || https_proxy="$proxy"
  export HTTP_PROXY HTTPS_PROXY http_proxy https_proxy
}

pkg_root() {
  echo "${ENVY_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
}

# True when dependencies.json can provision this tool on this host: an artifact
# for the current platform with a recorded digest, or brew on macOS.
pkg_can_install() {
  local name="${1:?dependency required}"
  python3 "$(pkg_root)/lib/provision.py" can-install "$name" >/dev/null 2>&1
}

pkg_ensure() {
  local command_name="${1:?command required}"
  local brew_formula="${2:-}"
  local dependency="${3:-$command_name}"
  local root provisioned=false
  root="$(pkg_root)"
  if [[ -f "$root/lib/provision.py" ]] \
    && python3 "$root/lib/provision.py" declared "$dependency" 2>/dev/null; then
    provisioned=true
  fi

  # Installs the pinned release, or reuses an already-pinned binary.
  if [[ "$provisioned" == true ]] \
    && python3 "$root/lib/provision.py" install "$dependency"; then
    hash -r 2>/dev/null || true
    return 0
  fi

  if [[ "$(uname -s)" == Darwin ]] && command -v brew >/dev/null 2>&1 \
    && [[ -n "$brew_formula" ]]; then
    brew install "$brew_formula"
    hash -r 2>/dev/null || true
  fi

  # An unpinned but working tool still converges; the pin is reported as drift
  # by lib/dependencies.py rather than blocking every feature behind it.
  if command -v "$command_name" >/dev/null 2>&1; then
    echo "  OK $command_name found at $(command -v "$command_name")"
    if [[ "$provisioned" == true ]] \
      && ! python3 "$root/lib/provision.py" verify "$dependency" >/dev/null 2>&1; then
      echo "  Warning: $command_name does not match the pinned version." >&2
    fi
    return 0
  fi

  echo "  $command_name is required but was not found." >&2
  echo "  Install it with a trusted package manager, then rerun envy upgrade." >&2
  if [[ "$provisioned" == true ]]; then
    echo "  envy can install the pinned release once its digest is recorded:" >&2
    echo "    python3 $root/lib/provision.py record $dependency" >&2
  fi
  return 1
}
