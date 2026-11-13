#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMP="$(mktemp -d)"
trap 'rm -rf "$TEMP"' EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }

command -v chezmoi >/dev/null 2>&1 || { echo 'SKIP: chezmoi is not installed'; exit 0; }
# Compose the real guidance sources without unrelated provisioned assets.
# Some chezmoi versions fetch external archives while building target state,
# before --exclude=externals takes effect; a fresh fixture must remain offline.
SOURCE="$TEMP/source"
mkdir -p "$SOURCE/dot_config" "$SOURCE/dot_claude" "$SOURCE/private_dot_codex" "$SOURCE/dot_pi/agent"
cp "$ROOT/chezmoi/.chezmoi.toml.tmpl" "$SOURCE/"
cp -R "$ROOT/chezmoi/dot_config/agent-guidance" "$SOURCE/dot_config/"
cp "$ROOT/chezmoi/dot_claude/symlink_CLAUDE.md.tmpl" "$SOURCE/dot_claude/"
cp "$ROOT/chezmoi/private_dot_codex/symlink_AGENTS.md.tmpl" "$SOURCE/private_dot_codex/"
cp "$ROOT/chezmoi/dot_pi/agent/symlink_AGENTS.md.tmpl" "$SOURCE/dot_pi/agent/"
cp "$ROOT/chezmoi/dot_pi/agent/symlink_skills.tmpl" "$SOURCE/dot_pi/agent/"
DEST="$TEMP/home"
mkdir -p "$DEST/.agents/skills/example" "$DEST/.claude/skills/foreign" "$DEST/.codex/skills/.system"
printf 'shared skill\n' >"$DEST/.agents/skills/example/SKILL.md"
printf 'keep Claude\n' >"$DEST/.claude/skills/foreign/SKILL.md"
printf 'keep Codex\n' >"$DEST/.codex/skills/.system/owned-by-codex"
override="$(printf '{"xdgConfigHome":"%s/.config","xdgDataHome":"%s/.local/share"}' "$DEST" "$DEST")"
chezmoi apply --source "$SOURCE" --destination "$DEST" \
  --override-data "$override" --exclude=externals
canonical="$DEST/.config/agent-guidance/AGENTS.md"
[[ "$(readlink "$DEST/.claude/CLAUDE.md")" == "$canonical" ]] \
  || fail 'Claude guidance adapter is incorrect'
[[ "$(readlink "$DEST/.codex/AGENTS.md")" == "$canonical" ]] \
  || fail 'Codex guidance adapter is incorrect'
[[ "$(readlink "$DEST/.pi/agent/AGENTS.md")" == "$canonical" ]] \
  || fail 'Pi guidance adapter is incorrect'
[[ "$(readlink "$DEST/.pi/agent/skills")" == "$DEST/.agents/skills" ]] \
  || fail 'Pi skills do not point at the shared native discovery root'
[[ "$(cat "$DEST/.pi/agent/skills/example/SKILL.md")" == 'shared skill' ]] \
  || fail 'Pi skill alias does not expose the shared skill'
[[ "$(cat "$DEST/.claude/skills/foreign/SKILL.md")" == 'keep Claude' ]] \
  || fail 'unrelated Claude skill was changed'
[[ -f "$DEST/.codex/skills/.system/owned-by-codex" ]] \
  || fail 'Codex-owned skill resources were changed'
# Reapply through the native manager and observe subsequent canonical edits.
chezmoi apply --source "$SOURCE" --destination "$DEST" \
  --override-data "$override" --exclude=externals
printf 'new shared skill\n' >"$DEST/.agents/skills/example/SKILL.md"
[[ "$(cat "$DEST/.pi/agent/skills/example/SKILL.md")" == 'new shared skill' ]] \
  || fail 'Pi skills were copied rather than linked'
# shellcheck disable=SC2088 # Assert the literal path documented for users.
grep -qF '~/.config/agent-guidance/rules/' "$canonical" \
  || fail 'bundle guidance extension point is missing'
grep -qF 'Never write to a public repository or upstream project that the user does not' "$canonical" \
  || fail 'upstream write approval rule is missing'
echo 'envy agent guidance tests passed'
