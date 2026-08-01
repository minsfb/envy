#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-converge}"

_verify() {
  command -v zsh >/dev/null 2>&1 || return 1
  command -v tmux >/dev/null 2>&1 || return 1
  if [[ "$(uname -s)" == Darwin ]]; then
    command -v alacritty >/dev/null 2>&1 || [[ -d /Applications/Alacritty.app ]]
  fi
}

_converge() {
  command -v zsh >/dev/null 2>&1 || {
    echo "zsh is not installed; install it through the host-supported package mechanism" >&2
    return 1
  }
  if [[ "$(uname -s)" == Darwin ]]; then
    command -v brew >/dev/null 2>&1 || { echo "Homebrew is required to install terminal dependencies" >&2; return 1; }
    command -v tmux >/dev/null 2>&1 || brew install tmux
    if ! command -v alacritty >/dev/null 2>&1 && [[ ! -d /Applications/Alacritty.app ]]; then
      brew install --cask alacritty
    fi
  else
    command -v tmux >/dev/null 2>&1 || {
      echo "tmux is not installed; install it through the host-supported package mechanism" >&2
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
