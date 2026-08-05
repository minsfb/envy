#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_ROOT="$ROOT"
TEMP="$(mktemp -d)"
SOCKET_NAME="nv-persistence-$$"

cleanup() {
  tmux -L "$SOCKET_NAME" kill-server >/dev/null 2>&1 || true
  rm -rf "$TEMP"
}
trap cleanup EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

HOME_DIR="$TEMP/home"
BIN_DIR="$HOME_DIR/.local/bin"
CONFIG_DIR="$HOME_DIR/.config"
STATE_DIR="$HOME_DIR/.local/state"
mkdir -p "$BIN_DIR" "$CONFIG_DIR" "$STATE_DIR" \
  "$HOME_DIR/.claude" "$HOME_DIR/.codex" \
  "$HOME_DIR/.pi/agent/extensions" \
  "$HOME_DIR/.local/share/envy/tmux/tmux-resurrect" \
  "$HOME_DIR/.local/share/envy/tmux/tmux-continuum"

for source in "$ENV_ROOT"/chezmoi/dot_local/bin/executable_*; do
  target="$BIN_DIR/${source##*/executable_}"
  cp "$source" "$target"
  chmod +x "$target"
done
ENVY_BIN_DIR="$BIN_DIR" HOME="$HOME_DIR" chezmoi execute-template \
  --source "$ENV_ROOT/chezmoi" --destination "$HOME_DIR" \
  <"$ENV_ROOT/chezmoi/dot_pi/agent/extensions/nv-tmux-session.ts.tmpl" \
  >"$HOME_DIR/.pi/agent/extensions/nv-tmux-session.ts"

cat >"$HOME_DIR/.local/share/envy/tmux/tmux-resurrect/resurrect.tmux" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
cat >"$HOME_DIR/.local/share/envy/tmux/tmux-continuum/continuum.tmux" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
chmod +x \
  "$HOME_DIR/.local/share/envy/tmux/tmux-resurrect/resurrect.tmux" \
  "$HOME_DIR/.local/share/envy/tmux/tmux-continuum/continuum.tmux"

cat >"$HOME_DIR/.claude/settings.json" <<'EOF'
{
  "hooks": {
    "SessionStart": [{"hooks": [{"type": "command", "command": "keep-claude-hook"}]}]
  }
}
EOF
cat >"$HOME_DIR/.codex/hooks.json" <<'EOF'
{
  "description": "keep this metadata",
  "hooks": {
    "Stop": [{"hooks": [{"type": "command", "command": "keep-codex-hook"}]}]
  }
}
EOF

TMUX_DIR="$(cd "$(dirname "$(command -v tmux)")" && pwd)"
FEATURE_ENV=(
  HOME="$HOME_DIR"
  XDG_CONFIG_HOME="$CONFIG_DIR"
  XDG_STATE_HOME="$STATE_DIR"
  PATH="$BIN_DIR:$TMUX_DIR:/usr/bin:/bin"
  ENVY_MANIFEST="$ENV_ROOT/envy.json"
)
env "${FEATURE_ENV[@]}" "$ENV_ROOT/features/tmux-persistence.py" converge >/dev/null
env "${FEATURE_ENV[@]}" "$ENV_ROOT/features/tmux-persistence.py" verify
[[ -f "$CONFIG_DIR/envy/tmux-persistence.enabled" ]] || fail 'feature marker was not created'

python3 - "$HOME_DIR/.claude/settings.json" "$HOME_DIR/.codex/hooks.json" <<'PY'
import json
import sys

claude = json.load(open(sys.argv[1], encoding="utf-8"))
codex = json.load(open(sys.argv[2], encoding="utf-8"))
blob = json.dumps(claude)
assert "keep-claude-hook" in blob
assert "nv-agent-register" in blob
blob = json.dumps(codex)
assert "keep-codex-hook" in blob
assert "keep this metadata" in blob
assert "nv-agent-register" in blob
PY

cp "$HOME_DIR/.codex/hooks.json" "$TEMP/codex-hooks.valid.json"
printf '{"hooks":"invalid"}\n' >"$HOME_DIR/.codex/hooks.json"
if env "${FEATURE_ENV[@]}" "$ENV_ROOT/features/tmux-persistence.py" doctor \
  >"$TEMP/malformed-verify.out" 2>&1; then
  fail 'malformed Codex hooks unexpectedly verified'
fi
grep -q 'codex start hook missing' "$TEMP/malformed-verify.out" || \
  fail 'malformed Codex hooks did not produce a clean verification failure'
cp "$TEMP/codex-hooks.valid.json" "$HOME_DIR/.codex/hooks.json"

tmux -L "$SOCKET_NAME" -f /dev/null new-session -d -s work
socket_path=$(tmux -L "$SOCKET_NAME" display-message -p '#{socket_path}')
server_pid=$(tmux -L "$SOCKET_NAME" display-message -p '#{pid}')
pane_id=$(tmux -L "$SOCKET_NAME" display-message -p -t work:0.0 '#{pane_id}')
tmux_env="$socket_path,$server_pid,0"

env "${FEATURE_ENV[@]}" TMUX="$tmux_env" "$BIN_DIR/nv-tmux-bootstrap"
[[ "$(tmux -L "$SOCKET_NAME" show-option -gqv @nv_tmux_persistence)" == 1 ]] || \
  fail 'tmux bootstrap did not activate persistence'
tmux -L "$SOCKET_NAME" show-hooks -g client-detached | grep -q 'nv-tmux-save' || \
  fail 'tmux bootstrap did not register the detach save hook'

payload='{"session_id":"session-123","transcript_path":"/tmp/session-123.jsonl","cwd":"/tmp/project"}'
printf '%s' "$payload" | env "${FEATURE_ENV[@]}" TMUX="$tmux_env" TMUX_PANE="$pane_id" \
  "$BIN_DIR/nv-agent-register" codex start

[[ "$(tmux -L "$SOCKET_NAME" show-option -pqv -t "$pane_id" @nv_agent_kind)" == codex ]] || \
  fail 'agent kind was not registered on the pane'
[[ "$(tmux -L "$SOCKET_NAME" show-option -pqv -t "$pane_id" @nv_agent_session_id)" == session-123 ]] || \
  fail 'agent session ID was not registered on the pane'

tmux -L "$SOCKET_NAME" new-session -d -s __tmux_resume_test -t work
tmux -L "$SOCKET_NAME" set-option -t __tmux_resume_test @tmux_resume_transient 1

STATE_FILE="$TEMP/tmux_resurrect_test.txt"
printf 'grouped_session\t__tmux_resume_test\twork\t:\t:0\n' >"$STATE_FILE"
printf 'pane\twork\t0\t1\t:*\t0\tagent\t:/tmp/project\t1\tcodex\t:codex\n' >>"$STATE_FILE"
env "${FEATURE_ENV[@]}" TMUX="$tmux_env" "$BIN_DIR/nv-tmux-snapshot" "$STATE_FILE"
grep -q '^grouped_session' "$STATE_FILE" && fail 'temporary resume view remained in snapshot'
grep -Fq ":$BIN_DIR/nv-agent-resume v1 codex session-123" "$STATE_FILE" || \
  fail 'snapshot did not contain the normalized agent resume command'

for agent_command in claude codex pi; do
  printf '#!/usr/bin/env bash\nexit 0\n' >"$TEMP/$agent_command"
  chmod +x "$TEMP/$agent_command"
done
resume_command=$(PATH="$TEMP:$PATH" "$BIN_DIR/nv-agent-resume" --print-command v1 codex session-123)
[[ "$resume_command" == *'"resume", "session-123"'* ]] || fail 'Codex resume command was not resolved'
resume_command=$(PATH="$TEMP:$PATH" "$BIN_DIR/nv-agent-resume" --print-command v1 claude claude-123)
[[ "$resume_command" == *'"--resume", "claude-123"'* ]] || fail 'Claude resume command was not resolved'
resume_command=$(PATH="$TEMP:$PATH" "$BIN_DIR/nv-agent-resume" --print-command v1 pi pi-123)
[[ "$resume_command" == *'"--session", "pi-123"'* ]] || fail 'Pi resume command was not resolved'

mkdir -p "$TEMP/resurrect"
touch "$TEMP/resurrect/last"
cat >"$TEMP/fake-restore" <<EOF
#!/usr/bin/env bash
touch "$TEMP/restore-ran"
EOF
chmod +x "$TEMP/fake-restore"
tmux -L "$SOCKET_NAME" set-option -g @resurrect-dir "$TEMP/resurrect"
tmux -L "$SOCKET_NAME" set-option -g @resurrect-restore-script-path "$TEMP/fake-restore"
tmux -L "$SOCKET_NAME" set-option -gu @nv_restore_attempted 2>/dev/null || true
tmux -L "$SOCKET_NAME" set-option -gu @nv_restore_complete 2>/dev/null || true
env "${FEATURE_ENV[@]}" TMUX="$tmux_env" "$BIN_DIR/nv-tmux-restore" --startup
[[ -f "$TEMP/restore-ran" ]] || fail 'startup restore script was not invoked'
[[ "$(tmux -L "$SOCKET_NAME" show-option -gqv @nv_restore_complete)" == 1 ]] || \
  fail 'startup restore did not mark completion'

printf 'pane\twork\t0\t1\t:*\t0\tzsh\t:/tmp\t1\tzsh\t:zsh\n' \
  >"$TEMP/resurrect/tmux_resurrect_20250101T000000.txt"
printf 'pane\twork\t0\t1\t:*\t0\tagent\t:/tmp\t1\tcodex\t:nv-agent-resume v1 codex session-123\n' \
  >"$TEMP/resurrect/tmux_resurrect_20250102T000000.txt"
ln -sf tmux_resurrect_20250101T000000.txt "$TEMP/resurrect/last"
rm -f "$TEMP/restore-ran"
env "${FEATURE_ENV[@]}" TMUX="$tmux_env" \
  "$BIN_DIR/nv-tmux-snapshot" restore tmux_resurrect_20250102T000000.txt
[[ "$(readlink "$TEMP/resurrect/last")" == tmux_resurrect_20250102T000000.txt ]] || \
  fail 'snapshot restore did not repoint the last checkpoint'
[[ -f "$TEMP/restore-ran" ]] || fail 'snapshot restore did not invoke the restore script'
grep -Fq ":$BIN_DIR/nv-agent-resume v1 codex session-123" \
  "$TEMP/resurrect/tmux_resurrect_20250102T000000.txt" || \
  fail 'snapshot restore did not upgrade a legacy relative resume command'
if env "${FEATURE_ENV[@]}" TMUX="$tmux_env" \
  "$BIN_DIR/nv-tmux-snapshot" restore '../outside.txt' 2>/dev/null; then
  fail 'snapshot restore accepted an invalid checkpoint name'
fi

end_payload='{"session_id":"session-123"}'
printf '%s' "$end_payload" | env "${FEATURE_ENV[@]}" TMUX="$tmux_env" TMUX_PANE="$pane_id" \
  "$BIN_DIR/nv-agent-register" codex end
[[ -z "$(tmux -L "$SOCKET_NAME" show-option -pqv -t "$pane_id" @nv_agent_session_id)" ]] || \
  fail 'agent session ID was not cleared on session end'
python3 - "$STATE_DIR/envy/agent-sessions/codex/session-123.json" <<'PY'
import json
import sys

record = json.load(open(sys.argv[1], encoding="utf-8"))
assert record["schema"] == 1
assert record["active"] is False
assert record["cwd_hint"] == "/tmp/project"
assert record["transcript_path"] == "/tmp/session-123.jsonl"
PY

env "${FEATURE_ENV[@]}" "$ENV_ROOT/features/tmux-persistence.py" remove >/dev/null
[[ ! -e "$CONFIG_DIR/envy/tmux-persistence.enabled" ]] || fail 'feature marker was not removed'
python3 - "$HOME_DIR/.claude/settings.json" "$HOME_DIR/.codex/hooks.json" <<'PY'
import json
import sys

claude = json.load(open(sys.argv[1], encoding="utf-8"))
codex = json.load(open(sys.argv[2], encoding="utf-8"))
assert "keep-claude-hook" in json.dumps(claude)
assert "keep-codex-hook" in json.dumps(codex)
assert "nv-agent-register" not in json.dumps(claude)
assert "nv-agent-register" not in json.dumps(codex)
PY

echo 'tmux persistence tests passed'
