#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE="$ROOT/chezmoi"
TEMP="$(mktemp -d)"
SOCKET_NAME="envy-smoke-$$"
trap 'tmux -L "$SOCKET_NAME" kill-server >/dev/null 2>&1 || true; rm -rf "$TEMP"' EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }

command -v chezmoi >/dev/null 2>&1 || { echo 'SKIP: chezmoi is not installed'; exit 0; }
command -v zsh >/dev/null 2>&1 || { echo 'SKIP: zsh is not installed'; exit 0; }

OVERRIDE="$(printf '{"xdgConfigHome":"%s/.config","xdgDataHome":"%s/.local/share"}' "$TEMP/home" "$TEMP/home")"
chezmoi execute-template --source "$SOURCE" --override-data "$OVERRIDE" \
  <"$SOURCE/dot_zshrc.tmpl" >"$TEMP/zshrc"
chezmoi execute-template --source "$SOURCE" --override-data "$OVERRIDE" \
  <"$SOURCE/dot_bashrc.tmpl" >"$TEMP/bashrc"
ENVY_BIN_DIR="$TEMP/home/.local/bin" chezmoi execute-template \
  --source "$SOURCE" --destination "$TEMP/home" \
  <"$SOURCE/dot_tmux.conf.tmpl" >"$TEMP/tmux.conf"
zsh -n "$TEMP/zshrc"
bash -n "$TEMP/bashrc"
grep -q 'shell.d' "$TEMP/zshrc" || fail 'zsh bundle fragment hook is missing'
grep -q 'ENVY_SECRETS_FILE' "$TEMP/zshrc" || fail 'configured secrets hook is missing'

mkdir -p "$TEMP/home"
cp "$TEMP/bashrc" "$TEMP/home/.bashrc"
mkdir -p "$TEMP/bin"
cat >"$TEMP/bin/zsh" <<'EOF'
#!/usr/bin/env bash
echo handoff >"$HANDOFF_MARKER"
EOF
chmod +x "$TEMP/bin/zsh"
ENVY_BASH_EXECD='' ENVY_NO_ZSH='' HANDOFF_MARKER="$TEMP/handoff" HOME="$TEMP/home" \
  PATH="$TEMP/bin:/usr/bin:/bin" bash --noprofile -ic 'exit 99' \
  </dev/null >/dev/null 2>&1 || true
[[ -f "$TEMP/handoff" ]] || fail 'interactive Bash did not hand off to zsh'

if command -v tmux >/dev/null 2>&1; then
  tmux -L "$SOCKET_NAME" -f "$TEMP/tmux.conf" new-session -d -s smoke
  [[ "$(tmux -L "$SOCKET_NAME" show-options -gv set-titles)" == on ]] \
    || fail 'tmux terminal titles are not enabled'
  [[ "$(tmux -L "$SOCKET_NAME" show-options -gv bell-action)" == any ]] \
    || fail 'tmux bells are not forwarded from every window'
  [[ "$(tmux -L "$SOCKET_NAME" show-options -gv visual-bell)" == off ]] \
    || fail 'tmux is replacing terminal bells with status messages'
  [[ "$(tmux -L "$SOCKET_NAME" show-options -gwv monitor-bell)" == on ]] \
    || fail 'tmux bell monitoring is not enabled'
  tmux -L "$SOCKET_NAME" set-option -t smoke @tmux_resume_base real-session
  status_left="$(tmux -L "$SOCKET_NAME" show-option -Av -t smoke status-left)"
  rendered_status="$(tmux -L "$SOCKET_NAME" display-message -p -t smoke "$status_left")"
  [[ "$rendered_status" == *' real-session '* ]] \
    || fail 'resume view footer does not show the base session'
fi

echo 'envy shell smoke tests passed'
