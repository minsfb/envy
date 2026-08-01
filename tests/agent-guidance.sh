#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE="$ROOT/chezmoi"
TEMP="$(mktemp -d)"
trap 'rm -rf "$TEMP"' EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }

command -v chezmoi >/dev/null 2>&1 || { echo 'SKIP: chezmoi is not installed'; exit 0; }
DEST="$TEMP/home"
mkdir -p "$DEST"
override="$(printf '{"xdgConfigHome":"%s/.config","xdgDataHome":"%s/.local/share"}' "$DEST" "$DEST")"
chezmoi apply --source "$SOURCE" --destination "$DEST" \
  --override-data "$override" --exclude=externals
canonical="$DEST/.config/agent-guidance/AGENTS.md"
[[ "$(readlink "$DEST/.claude/CLAUDE.md")" == "$canonical" ]] \
  || fail 'Claude guidance adapter is incorrect'
[[ "$(readlink "$DEST/.codex/AGENTS.md")" == "$canonical" ]] \
  || fail 'Codex guidance adapter is incorrect'
# shellcheck disable=SC2088 # Assert the literal path documented for users.
grep -qF '~/.config/agent-guidance/rules/' "$canonical" \
  || fail 'bundle guidance extension point is missing'
echo 'envy agent guidance tests passed'
