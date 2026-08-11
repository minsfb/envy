#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMP="$(mktemp -d)"
trap 'rm -rf "$TEMP"' EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

HOME_DIR="$TEMP/home"
BIN_DIR="$HOME_DIR/.local/bin"
CONFIG_DIR="$HOME_DIR/.config"
STATE_DIR="$HOME_DIR/.local/state"
mkdir -p \
  "$BIN_DIR" \
  "$STATE_DIR" \
  "$HOME_DIR/.claude" \
  "$HOME_DIR/.codex" \
  "$HOME_DIR/.pi/agent/extensions"

for helper in nv-agent-notify nv-terminal-notify; do
  cp "$ROOT/chezmoi/dot_local/bin/executable_$helper" "$BIN_DIR/$helper"
  chmod +x "$BIN_DIR/$helper"
done
ENVY_BIN_DIR="$BIN_DIR" HOME="$HOME_DIR" chezmoi execute-template \
  --source "$ROOT/chezmoi" --destination "$HOME_DIR" \
  <"$ROOT/chezmoi/dot_pi/agent/extensions/nv-tmux-session.ts.tmpl" \
  >"$HOME_DIR/.pi/agent/extensions/nv-tmux-session.ts"
mkdir -p "$CONFIG_DIR/alacritty"
cp "$ROOT/chezmoi/dot_config/alacritty/alacritty.toml" \
  "$CONFIG_DIR/alacritty/alacritty.toml"

cat >"$HOME_DIR/.claude/settings.json" <<'EOF'
{
  "hooks": {
    "SessionStart": [{"hooks": [
      {"type": "command", "command": "keep-claude-hook"},
      {"type": "command", "command": "\"$HOME/.local/bin/nv-agent-register\" claude start"}
    ]}]
  }
}
EOF
cat >"$HOME_DIR/.codex/hooks.json" <<'EOF'
{
  "description": "keep this metadata",
  "hooks": {
    "SessionStart": [{"hooks": [
      {"type": "command", "command": "keep-codex-hook"},
      {"type": "command", "command": "\"$HOME/.local/bin/nv-agent-register\" codex start"}
    ]}]
  }
}
EOF

cat >"$TEMP/tmux" <<'EOF'
#!/usr/bin/env bash
case "${1:-}" in
  display-message)
    case "${*: -1}" in
      '#{window_id}') printf '%s\n' '@1' ;;
      '#{pane_tty}') printf '%s\n' "$FAKE_TTY" ;;
      *) exit 1 ;;
    esac
    ;;
  list-clients) printf '%s\n' "@1|${FAKE_CLIENT_FLAGS:-attached}" ;;
  list-sessions) exit 1 ;;
  *) exit 1 ;;
esac
EOF
chmod +x "$TEMP/tmux"

FEATURE_ENV=(
  HOME="$HOME_DIR"
  XDG_CONFIG_HOME="$CONFIG_DIR"
  XDG_STATE_HOME="$STATE_DIR"
  PATH="$TEMP:$BIN_DIR:/usr/bin:/bin"
  ENVY_MANIFEST="$ROOT/envy.json"
)

env "${FEATURE_ENV[@]}" "$ROOT/features/agent-notifications.py" converge >/dev/null
env "${FEATURE_ENV[@]}" "$ROOT/features/agent-notifications.py" verify

MARKER="$CONFIG_DIR/envy/agent-notifications.enabled"
[[ -f "$MARKER" ]] || fail 'feature marker was not created'
grep -q '^adapters=claude,codex,pi$' "$MARKER" || \
  fail 'feature marker does not contain selected adapters'
grep -q '^condition=unfocused$' "$MARKER" || \
  fail 'feature marker does not contain the notification condition'

python3 - "$ROOT/envy.json" "$TEMP/external-manifest.json" <<'PY'
import json
import sys

source, destination = sys.argv[1:]
manifest = json.load(open(source, encoding="utf-8"))
manifest["settings"]["agent_notifications"]["adapters"].append("example-adapter")
with open(destination, "w", encoding="utf-8") as handle:
    json.dump(manifest, handle)
PY
env "${FEATURE_ENV[@]}" ENVY_MANIFEST="$TEMP/external-manifest.json" \
  "$ROOT/features/agent-notifications.py" converge >/dev/null
grep -q '^adapters=claude,codex,example-adapter,pi$' "$MARKER" || \
  fail 'bundle-provided notification adapter was rejected'
env "${FEATURE_ENV[@]}" "$ROOT/features/agent-notifications.py" converge >/dev/null

python3 - "$HOME_DIR/.claude/settings.json" "$HOME_DIR/.codex/hooks.json" <<'PY'
import json
import sys

claude = json.load(open(sys.argv[1], encoding="utf-8"))
codex = json.load(open(sys.argv[2], encoding="utf-8"))
assert "keep-claude-hook" in json.dumps(claude)
assert "keep-codex-hook" in json.dumps(codex)
assert "keep this metadata" in json.dumps(codex)
assert "nv-agent-register" in json.dumps(claude)
assert "nv-agent-register" in json.dumps(codex)

def commands(value, event):
    return [
        item["command"]
        for group in value["hooks"][event]
        for item in group["hooks"]
        if "command" in item
    ]

assert any("nv-agent-notify" in item for item in commands(claude, "Stop"))
assert any("nv-agent-notify" in item for item in commands(claude, "PermissionRequest"))
assert any(
    group.get("matcher") == "elicitation_dialog"
    and any("nv-agent-notify" in item.get("command", "") for item in group["hooks"])
    for group in claude["hooks"]["Notification"]
)
assert any("nv-agent-notify" in item for item in commands(codex, "Stop"))
assert any("nv-agent-notify" in item for item in commands(codex, "PermissionRequest"))
PY

grep -q 'agent_settled' "$HOME_DIR/.pi/agent/extensions/nv-tmux-session.ts" || \
  fail 'Pi settled adapter is missing'

BELL="$TEMP/bell.out"
: >"$BELL"
env "${FEATURE_ENV[@]}" TMUX_PANE=%1 FAKE_TTY="$BELL" FAKE_CLIENT_FLAGS=attached \
  "$BIN_DIR/nv-agent-notify" codex
[[ "$(od -An -tx1 "$BELL" | tr -d ' \n')" == 07 ]] || \
  fail 'agent helper did not write BEL to the pane tty'

: >"$BELL"
env "${FEATURE_ENV[@]}" TMUX_PANE=%1 FAKE_TTY="$BELL" FAKE_CLIENT_FLAGS=attached,focused \
  "$BIN_DIR/nv-agent-notify" codex
[[ ! -s "$BELL" ]] || fail 'unfocused condition rang in a focused tmux window'

python3 - "$MARKER" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
path.write_text(
    path.read_text(encoding="utf-8").replace(
        "condition=unfocused\n", "condition=always\n"
    ),
    encoding="utf-8",
)
PY
: >"$BELL"
env "${FEATURE_ENV[@]}" TMUX_PANE=%1 FAKE_TTY="$BELL" FAKE_CLIENT_FLAGS=attached,focused \
  "$BIN_DIR/nv-agent-notify" codex
[[ "$(od -An -tx1 "$BELL" | tr -d ' \n')" == 07 ]] || \
  fail 'always condition did not ring in a focused tmux window'
env "${FEATURE_ENV[@]}" "$ROOT/features/agent-notifications.py" converge >/dev/null

cat >"$TEMP/uname" <<'EOF'
#!/usr/bin/env bash
echo Darwin
EOF
cat >"$TEMP/osascript" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$FAKE_OSASCRIPT_LOG"
EOF
chmod +x "$TEMP/uname" "$TEMP/osascript"
OSASCRIPT_LOG="$TEMP/osascript.log"
touch "$OSASCRIPT_LOG"
env "${FEATURE_ENV[@]}" TMPDIR="$TEMP" FAKE_OSASCRIPT_LOG="$OSASCRIPT_LOG" \
  "$BIN_DIR/nv-terminal-notify"
env "${FEATURE_ENV[@]}" TMPDIR="$TEMP" FAKE_OSASCRIPT_LOG="$OSASCRIPT_LOG" \
  "$BIN_DIR/nv-terminal-notify"
[[ "$(wc -l <"$OSASCRIPT_LOG" | tr -d ' ')" == 1 ]] || \
  fail 'local terminal notifications were not deduplicated'
grep -q 'display notification' "$OSASCRIPT_LOG" || \
  fail 'local terminal notification did not invoke osascript'

env "${FEATURE_ENV[@]}" "$ROOT/features/agent-notifications.py" remove >/dev/null
[[ ! -e "$MARKER" ]] || fail 'feature marker was not removed'
python3 - "$HOME_DIR/.claude/settings.json" "$HOME_DIR/.codex/hooks.json" <<'PY'
import json
import sys

claude = json.load(open(sys.argv[1], encoding="utf-8"))
codex = json.load(open(sys.argv[2], encoding="utf-8"))
assert "keep-claude-hook" in json.dumps(claude)
assert "keep-codex-hook" in json.dumps(codex)
assert "nv-agent-register" in json.dumps(claude)
assert "nv-agent-register" in json.dumps(codex)
assert "nv-agent-notify" not in json.dumps(claude)
assert "nv-agent-notify" not in json.dumps(codex)
PY

echo 'agent notification tests passed'
