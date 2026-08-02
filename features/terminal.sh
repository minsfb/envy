#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-converge}"
ENVY_ROOT="${ENVY_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
BOOTSTRAP_CONFIG="$ENVY_ROOT/bootstrap-packages.toml"

_verify() {
  command -v zsh >/dev/null 2>&1 || return 1
  command -v tmux >/dev/null 2>&1 || return 1
  if [[ "$(uname -s)" == Darwin ]]; then
    command -v alacritty >/dev/null 2>&1 || [[ -d /Applications/Alacritty.app ]] || return 1
    # Provided by the chezmoi external; without it Alacritty silently falls
    # back to Menlo instead of the managed JetBrains Mono config.
    [[ -f "$HOME/Library/Fonts/JetBrainsMono/JetBrainsMono-Regular.ttf" ]] || {
      echo "JetBrains Mono is missing from ~/Library/Fonts/JetBrainsMono; run 'envy upgrade --only chezmoi' with github.com access" >&2
      return 1
    }
  fi
}

# The first native Linux manager wins; Linuxbrew is skipped deliberately so it
# never duplicates or shadows packages owned by the host distribution.
_system_package_manager() {
  if command -v apt-get >/dev/null 2>&1; then echo apt
  elif command -v dnf >/dev/null 2>&1; then echo dnf
  elif command -v pacman >/dev/null 2>&1; then echo pacman
  elif command -v apk >/dev/null 2>&1; then echo apk
  else return 1
  fi
}

_bootstrap_packages() {
  local manager trusted
  command -v mise >/dev/null 2>&1 || return 1
  manager="$(_system_package_manager)" || return 1
  trusted="$BOOTSTRAP_CONFIG:${XDG_CONFIG_HOME:-$HOME/.config}/mise/config.toml"
  if [[ -n "${MISE_TRUSTED_CONFIG_PATHS:-}" ]]; then
    trusted="$trusted:$MISE_TRUSTED_CONFIG_PATHS"
  fi
  MISE_GLOBAL_CONFIG_FILE="$BOOTSTRAP_CONFIG" \
  MISE_TRUSTED_CONFIG_PATHS="$trusted" \
    mise bootstrap packages apply --manager "$manager" --yes
}

_converge() {
  if [[ "$(uname -s)" == Darwin ]]; then
    command -v zsh >/dev/null 2>&1 || {
      echo "zsh is not installed; install it through the host-supported package mechanism" >&2
      return 1
    }
    command -v brew >/dev/null 2>&1 || { echo "Homebrew is required to install terminal dependencies" >&2; return 1; }
    command -v tmux >/dev/null 2>&1 || brew install tmux
    if ! command -v alacritty >/dev/null 2>&1 && [[ ! -d /Applications/Alacritty.app ]]; then
      brew install --cask alacritty
    fi
  else
    if ! command -v tmux >/dev/null 2>&1 || ! command -v zsh >/dev/null 2>&1; then
      _bootstrap_packages || {
        echo "tmux or zsh is missing and no supported bootstrap path is available; install them through the host-supported package mechanism" >&2
        return 1
      }
    fi
    command -v tmux >/dev/null 2>&1 && command -v zsh >/dev/null 2>&1 || {
      echo "tmux and zsh are still missing after the package bootstrap" >&2
      return 1
    }
  fi
}

case "$ACTION" in
  converge) _converge ;;
  verify|doctor) _verify ;;
  remove) echo "Retaining terminal applications and tmux" ;;
  *) echo "usage: terminal.sh {converge|verify|remove|doctor}" >&2; exit 2 ;;
esac
