#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }
assert_file() { [[ -f "$1" ]] || fail "missing file: $1"; }
assert_not_file() { [[ ! -e "$1" && ! -L "$1" ]] || fail "unexpected file: $1"; }
assert_contains() { grep -qF "$2" "$1" || fail "$1 does not contain: $2"; }
assert_eq() { [[ "$1" == "$2" ]] || fail "expected '$2', got '$1'"; }

HOME_DIR="$SANDBOX/home"
BIN_DIR="$SANDBOX/bin"
mkdir -p "$HOME_DIR" "$BIN_DIR"

cat >"$BIN_DIR/chezmoi" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
command="${1:-}"
case "$command" in
  init) mkdir -p "$HOME/.config/chezmoi" ;;
  managed)
    printf '%s\n' .bashrc .zshrc .config/mise/config.toml .config/mise/mise.lock .config/envy/tmux-autoattach.sh
    ;;
  status)
    [[ -f "$HOME/.fake-chezmoi-applied" ]] || printf ' A %s/.bashrc\n' "$HOME"
    ;;
  apply)
    [[ "${HTTPS_PROXY:-}" == proxy-value ]]
    [[ "${https_proxy:-}" == proxy-value ]]
    mkdir -p "$HOME/.config/mise" "$HOME/.config/envy"
    printf '# fake bashrc\n' >"$HOME/.bashrc"
    printf '# fake zshrc\n' >"$HOME/.zshrc"
    cp "$ENVY_ROOT/chezmoi/dot_config/mise/config.toml.tmpl" "$HOME/.config/mise/config.toml"
    cp "$ENVY_ROOT/chezmoi/dot_config/mise/mise.lock" "$HOME/.config/mise/mise.lock"
    cp "$ENVY_ROOT/chezmoi/dot_config/envy/tmux-autoattach.sh" "$HOME/.config/envy/tmux-autoattach.sh"
    touch "$HOME/.fake-chezmoi-applied"
    ;;
  doctor|verify) exit 0 ;;
  --version) echo 'chezmoi test' ;;
  *) echo "unexpected fake chezmoi command: $*" >&2; exit 1 ;;
esac
EOF

cat >"$BIN_DIR/mise" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
case "${1:-}" in
  install)
    [[ "${HTTPS_PROXY:-}" == proxy-value ]]
    [[ "${https_proxy:-}" == proxy-value ]]
    [[ -z "${DOTNET_ROOT:-}" ]]
    if [[ " $* " == *' --dry-run-code '* ]]; then
      [[ -f "$HOME/.fake-mise-installed" ]]
    else
      touch "$HOME/.fake-mise-installed"
    fi
    ;;
  doctor|activate) exit 0 ;;
  --version) echo 'mise test' ;;
  *) exit 0 ;;
esac
EOF
chmod +x "$BIN_DIR/chezmoi" "$BIN_DIR/mise"

run_cli() {
  HOME="$HOME_DIR" \
  PATH="$BIN_DIR:/usr/bin:/bin" \
  ENVY_PLATFORM_OVERRIDE=linux \
  ENVY_NETWORK_PROXY=proxy-value \
  HTTP_PROXY='' HTTPS_PROXY='' http_proxy='' https_proxy='' \
  DOTNET_ROOT=/system/dotnet \
  ENVY_SKIP_DEPENDENCY_PINS=1 \
  "$ROOT/bin/envy" "$@"
}

run_cli install --preset core --dry-run >"$SANDBOX/dry-run.out"
assert_not_file "$HOME_DIR/.config/envy/state.json"

run_cli install --preset core >"$SANDBOX/install.out"
STATE="$HOME_DIR/.config/envy/state.json"
assert_file "$STATE"
assert_file "$HOME_DIR/.local/bin/envy"
assert_file "$HOME_DIR/.config/envy/init-sh.env"
assert_file "$HOME_DIR/.fake-mise-installed"
assert_eq "$(python3 "$ROOT/lib/state.py" "$STATE" get schema_version)" 3
assert_eq "$(python3 "$ROOT/lib/state.py" "$STATE" get envy_root)" "$ROOT"
assert_eq "$(python3 "$ROOT/lib/state.py" "$STATE" get platform_hierarchy)" "linux unix global"
assert_eq "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["bundles"][0]["portable"])' "$STATE")" True
assert_eq "$(python3 "$ROOT/lib/state.py" "$STATE" get features.smart-merge.enabled)" true
assert_eq "$(python3 "$ROOT/lib/state.py" "$STATE" get features.chezmoi.enabled)" true
assert_eq "$(python3 "$ROOT/lib/state.py" "$STATE" get features.secrets.enabled)" false
assert_contains "$HOME_DIR/.config/envy/init-sh.env" "export ENVY_ROOT=$ROOT"
assert_contains "$HOME_DIR/.config/envy/init-sh.env" "export WORKSPACE_ROOT=$ROOT"
assert_contains "$HOME_DIR/.config/envy/init-sh.env" "$ROOT/bin"
assert_contains "$HOME_DIR/.local/bin/envy" "exec $ROOT/bin/envy"

if run_cli secrets pull-key --yes test-host >"$SANDBOX/secrets-disabled.out" 2>&1; then
  fail "pull-key unexpectedly ran without the secrets feature installed"
fi
assert_contains "$SANDBOX/secrets-disabled.out" "The secrets feature is not installed"

python3 "$ROOT/lib/state.py" "$STATE" set features.secrets.enabled true
rm "$HOME_DIR/.fake-mise-installed"
if run_cli secrets pull-key --yes test-host >"$SANDBOX/secrets-uninitialized.out" 2>&1; then
  fail "pull-key unexpectedly ran with an uninitialized secrets dependency"
fi
assert_contains "$SANDBOX/secrets-uninitialized.out" "The secrets feature is not initialized"
touch "$HOME_DIR/.fake-mise-installed"

if run_cli secrets pull-key --yes test-host >"$SANDBOX/secrets-tools-missing.out" 2>&1; then
  fail "pull-key unexpectedly reached SSH without the managed secrets tools"
fi
assert_contains "$SANDBOX/secrets-tools-missing.out" "missing managed tools: sops, age, age-keygen"
python3 "$ROOT/lib/state.py" "$STATE" set features.secrets.enabled false

run_cli upgrade >"$SANDBOX/repeat.out"
assert_contains "$SANDBOX/repeat.out" '4 current'
run_cli verify --only smart-merge,chezmoi,mise,path >"$SANDBOX/verify.out"
assert_contains "$SANDBOX/verify.out" '0 drifted'
run_cli doctor state --json >"$SANDBOX/doctor.json"
assert_contains "$SANDBOX/doctor.json" '"state": "ok"'

run_cli uninstall --dry-run >"$SANDBOX/uninstall-dry.out"
assert_file "$HOME_DIR/.local/bin/envy"
run_cli uninstall --yes >"$SANDBOX/uninstall.out"
assert_not_file "$HOME_DIR/.local/bin/envy"
assert_eq "$(python3 "$ROOT/lib/state.py" "$STATE" get installed)" false

echo "envy standalone integration tests passed"
