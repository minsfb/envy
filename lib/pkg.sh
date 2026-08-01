#!/usr/bin/env bash
# Minimal package-provider boundary for mins-env. Source this file.

pkg_use_external_proxy() {
  local proxy="${MINS_ENV_NETWORK_PROXY:-}"
  [[ -n "$proxy" ]] || return 0
  [[ -n "${HTTP_PROXY:-}" ]] || HTTP_PROXY="$proxy"
  [[ -n "${HTTPS_PROXY:-}" ]] || HTTPS_PROXY="$proxy"
  [[ -n "${http_proxy:-}" ]] || http_proxy="$proxy"
  [[ -n "${https_proxy:-}" ]] || https_proxy="$proxy"
  export HTTP_PROXY HTTPS_PROXY http_proxy https_proxy
}

pkg_ensure() {
  local command_name="${1:?command required}"
  local brew_formula="${2:-}"
  if command -v "$command_name" >/dev/null 2>&1; then
    echo "  OK $command_name found at $(command -v "$command_name")"
    return 0
  fi
  if [[ "$(uname -s)" == Darwin ]] && command -v brew >/dev/null 2>&1 \
    && [[ -n "$brew_formula" ]]; then
    brew install "$brew_formula"
    hash -r 2>/dev/null || true
  fi
  if command -v "$command_name" >/dev/null 2>&1; then
    echo "  OK $command_name installed at $(command -v "$command_name")"
    return 0
  fi
  echo "  $command_name is required but was not found." >&2
  echo "  Install it with a trusted package manager, then rerun mins-env upgrade." >&2
  return 1
}
