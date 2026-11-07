#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMP="$(mktemp -d)"
# Ownership checks intentionally reject symlink ancestors (such as macOS /var).
TEMP="$(cd "$TEMP" && pwd -P)"
trap 'rm -rf "$TEMP"' EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }

HOME_DIR="$TEMP/home"
ASSETS="$TEMP/assets"
mkdir -p "$HOME_DIR/.config/envy" "$HOME_DIR/.local/bin" "$ASSETS/extra/example"
cat >"$ASSETS/extra/example/SKILL.md" <<'EOF'
---
name: example
description: Example additional skill.
---
# Example
EOF
cat >"$ASSETS/manifest.json" <<EOF
{
  "version": 1,
  "distribution_feature": "agent-assets",
  "destinations": {
    "claude": {"skills": "~/.claude/skills"},
    "codex": {"skills": "~/.agents/skills"}
  },
  "launcher_predecessors": [
    {"manager": "example-bundle", "script": "$TEMP/legacy-agent-assets.py"}
  ],
  "skill_groups": [
    {"name": "extra", "path": "extra", "targets": ["claude", "codex"]}
  ]
}
EOF
cat >"$HOME_DIR/.local/bin/agent-assets" <<EOF
#!/usr/bin/env bash
# managed by example-bundle agent-assets
exec $TEMP/legacy-agent-assets.py "\$@"
EOF
cat >"$HOME_DIR/.config/envy/state.json" <<'EOF'
{
  "schema_version": 3,
  "installed": true,
  "platform": "linux",
  "features": {"agent-assets": {"enabled": true}}
}
EOF
cat >"$TEMP/resolved.json" <<EOF
{
  "resources": {
    "agent_asset_manifests": [
      "$ROOT/assets/agent-assets.json",
      "$ASSETS/manifest.json"
    ]
  }
}
EOF

ENVIRONMENT=(
  HOME="$HOME_DIR"
  ENVY_PLATFORM=linux
  ENVY_MANIFEST="$TEMP/resolved.json"
  ENVY_STATE_FILE="$HOME_DIR/.config/envy/state.json"
)
env "${ENVIRONMENT[@]}" "$ROOT/features/agent-assets.py" converge >/dev/null
grep -q '^# managed by envy agent-assets$' "$HOME_DIR/.local/bin/agent-assets" \
  || fail 'declared predecessor launcher was not adopted'
[[ -L "$HOME_DIR/.claude/skills/handoff" ]] || fail 'core Claude skill was not linked'
[[ -L "$HOME_DIR/.agents/skills/handoff" ]] || fail 'core Codex skill was not linked'
[[ -L "$HOME_DIR/.claude/skills/example" ]] || fail 'additional Claude skill was not linked'
env "${ENVIRONMENT[@]}" "$ROOT/features/agent-assets.py" verify >/dev/null

mkdir -p "$HOME_DIR/.claude/skills/foreign"
printf 'keep\n' >"$HOME_DIR/.claude/skills/foreign/SKILL.md"
env "${ENVIRONMENT[@]}" "$ROOT/features/agent-assets.py" remove >/dev/null
[[ ! -e "$HOME_DIR/.claude/skills/handoff" ]] || fail 'managed core link was retained'
[[ ! -e "$HOME_DIR/.claude/skills/example" ]] || fail 'managed additional link was retained'
[[ -f "$HOME_DIR/.claude/skills/foreign/SKILL.md" ]] || fail 'foreign skill was removed'

echo "agent asset standalone tests passed"
