#!/usr/bin/env bash
# Chezmoi provider for envy: converge, conservatively remove, or diagnose.
set -euo pipefail

ACTION="${1:-converge}"
ENVY_ROOT="${ENVY_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
SRC="${XDG_DATA_HOME:-$HOME/.local/share}/envy/chezmoi-source"
CONFLICT_BROKER="$ENVY_ROOT/features/chezmoi-conflicts.py"
COMPOSER="$ENVY_ROOT/lib/compose_chezmoi.py"
STATE_ROOT="${XDG_STATE_HOME:-$HOME/.local/state}/envy"
BACKUP_ROOT="$STATE_ROOT/backups/chezmoi"
# shellcheck source=lib/pkg.sh
source "$ENVY_ROOT/lib/pkg.sh"

export PATH="$HOME/.local/bin:$PATH"
pkg_use_external_proxy

_compose_source() {
  local source
  local sources=()
  while IFS= read -r source; do [[ -n "$source" ]] && sources+=("$source"); done < <(
    python3 "$ENVY_ROOT/lib/manifest.py" resource "${ENVY_MANIFEST:?}" chezmoi_sources
  )
  [[ -n "${sources[0]+set}" ]] || { echo "No chezmoi sources are configured" >&2; return 1; }
  python3 "$COMPOSER" "$SRC" "${sources[@]}"
}

_capture_backups() {
  mkdir -p "$BACKUP_ROOT/files"
  touch "$BACKUP_ROOT/existing.list" "$BACKUP_ROOT/absent.list" "$BACKUP_ROOT/pending-absent.list"
  local rel target destination
  while IFS= read -r rel; do
    [[ -n "$rel" && "$rel" != README.md ]] || continue
    if grep -Fqx -- "$rel" "$BACKUP_ROOT/existing.list" || grep -Fqx -- "$rel" "$BACKUP_ROOT/absent.list"; then
      continue
    fi
    target="$HOME/$rel"
    if [[ -f "$target" || -L "$target" ]]; then
      echo "$rel" >> "$BACKUP_ROOT/existing.list"
      destination="$BACKUP_ROOT/files/$rel"
      mkdir -p "$(dirname "$destination")"
      cp -Pp "$target" "$destination"
    elif [[ ! -e "$target" ]]; then
      echo "$rel" >> "$BACKUP_ROOT/absent.list"
      echo "$rel" >> "$BACKUP_ROOT/pending-absent.list"
    fi
  done < <(chezmoi managed --source "$SRC")
  touch "$BACKUP_ROOT/captured"
}

_record_created_files() {
  touch "$BACKUP_ROOT/created.list"
  local rel target
  while IFS= read -r rel; do
    target="$HOME/$rel"
    if { [[ -f "$target" || -L "$target" ]]; } && ! grep -Fqx -- "$rel" "$BACKUP_ROOT/created.list"; then
      echo "$rel" >> "$BACKUP_ROOT/created.list"
    fi
  done < "$BACKUP_ROOT/pending-absent.list"
  : > "$BACKUP_ROOT/pending-absent.list"
}

_link_owned_xdg_path() {
  local source="$1" target="$2" ledger="$BACKUP_ROOT/xdg-links.list"
  mkdir -p "$(dirname "$target")"
  if [[ -L "$target" && "$(readlink "$target")" == "$source" ]]; then
    return 0
  fi
  if [[ -e "$target" || -L "$target" ]]; then
    echo "Refusing to replace existing XDG path: $target" >&2
    return 1
  fi
  ln -s "$source" "$target"
  touch "$ledger"
  if ! awk -F '\t' -v target="$target" '$2 == target { found=1 } END { exit !found }' "$ledger"; then
    printf '%s\t%s\n' "$source" "$target" >> "$ledger"
  fi
}

_link_xdg_overrides() {
  local config_home="${XDG_CONFIG_HOME:-$HOME/.config}" data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
  if [[ "$config_home" != "$HOME/.config" ]]; then
    _link_owned_xdg_path "$HOME/.config/mise/config.toml" "$config_home/mise/config.toml"
    _link_owned_xdg_path "$HOME/.config/envy/tmux-autoattach.sh" "$config_home/envy/tmux-autoattach.sh"
  fi
  if [[ "$data_home" != "$HOME/.local/share" ]]; then
    _link_owned_xdg_path "$HOME/.local/share/envy/zsh" "$data_home/envy/zsh"
  fi
}

_converge() {
  _compose_source
  local was_installed=true
  command -v chezmoi >/dev/null 2>&1 || was_installed=false
  pkg_ensure chezmoi chezmoi
  [[ "$was_installed" == true ]] || { mkdir -p "$STATE_ROOT/providers"; touch "$STATE_ROOT/providers/chezmoi.installed"; }
  chezmoi init --source "$SRC"
  _capture_backups
  "$CONFLICT_BROKER" apply "$SRC"
  if [[ "${ENVY_SKIP_DEPENDENCY_PINS:-0}" != 1 ]]; then
    python3 "$ENVY_ROOT/lib/dependencies.py" converge
  fi
  _link_xdg_overrides
  _record_created_files
  if [[ -n "${ENVY_STATE_FILE:-}" && -f "$ENVY_STATE_FILE" ]]; then
    local encoded_backup_root
    encoded_backup_root="$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$BACKUP_ROOT")"
    python3 "$ENVY_DIR/lib/state.py" "$ENVY_STATE_FILE" set ownership.chezmoi.backup_root "$encoded_backup_root"
  fi
}

_remove() {
  local rel source target
  if [[ -f "$BACKUP_ROOT/existing.list" ]]; then
    while IFS= read -r rel; do
      source="$BACKUP_ROOT/files/$rel"
      target="$HOME/$rel"
      [[ -e "$source" || -L "$source" ]] || continue
      mkdir -p "$(dirname "$target")"
      rm -f "$target"
      cp -Pp "$source" "$target"
    done < "$BACKUP_ROOT/existing.list"
  fi
  if [[ -f "$BACKUP_ROOT/created.list" ]]; then
    while IFS= read -r rel; do rm -f "$HOME/$rel"; done < "$BACKUP_ROOT/created.list"
  fi
  if [[ -f "$BACKUP_ROOT/xdg-links.list" ]]; then
    while IFS=$'\t' read -r source target; do
      if [[ -L "$target" && "$(readlink "$target")" == "$source" ]]; then rm -f "$target"; fi
    done < "$BACKUP_ROOT/xdg-links.list"
  fi
  if [[ "${ENVY_PURGE:-false}" == true ]]; then
    rm -rf "$HOME/.local/share/envy/zsh"
    if [[ -f "$STATE_ROOT/providers/chezmoi.installed" && "$(command -v chezmoi 2>/dev/null || true)" == "$HOME/.local/bin/chezmoi" ]]; then
      rm -f "$HOME/.local/bin/chezmoi"
    fi
    rm -rf "$SRC"
    rm -rf "$BACKUP_ROOT"
  fi
}

# Recompose into a scratch directory and compare with the active composed
# source so edits inside core or bundle chezmoi trees register as drift.
_composition_current() {
  local temp source rc=0
  local sources=()
  while IFS= read -r source; do [[ -n "$source" ]] && sources+=("$source"); done < <(
    python3 "$ENVY_ROOT/lib/manifest.py" resource "${ENVY_MANIFEST:?}" chezmoi_sources
  )
  [[ -n "${sources[0]+set}" ]] || return 1
  temp="$(mktemp -d "${TMPDIR:-/tmp}/envy-compose-verify.XXXXXX")"
  if python3 "$COMPOSER" "$temp/src" "${sources[@]}" >/dev/null 2>&1; then
    diff -r -q "$temp/src" "$SRC" >/dev/null 2>&1 || rc=1
  else
    rc=1
  fi
  rm -rf "$temp"
  return "$rc"
}

_verify() {
  command -v chezmoi >/dev/null
  [[ -d "$SRC" ]]
  _composition_current
  "$CONFLICT_BROKER" verify "$SRC"
  if [[ "${ENVY_SKIP_DEPENDENCY_PINS:-0}" != 1 ]]; then
    python3 "$ENVY_ROOT/lib/dependencies.py" verify
  fi
}

_doctor() {
  _verify
  if ! chezmoi doctor; then
    echo "Warning: chezmoi doctor reported host capability or network limitations; checking managed state." >&2
  fi
  "$CONFLICT_BROKER" verify "$SRC"
}

case "$ACTION" in
  converge) _converge ;;
  verify) _verify ;;
  remove) _remove ;;
  doctor) _doctor ;;
  *) echo "usage: chezmoi.sh {converge|verify|remove|doctor}" >&2; exit 2 ;;
esac
