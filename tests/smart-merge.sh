#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENGINE="$ROOT/bin/smart-merge"
GIT_ADAPTER="$ROOT/bin/smart-merge-git"
GIT_SETUP="$ROOT/bin/smart-merge-git-setup"
TEMP="$(mktemp -d)"
trap 'rm -rf "$TEMP"' EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }

mkdir -p "$TEMP/json" "$TEMP/text" "$TEMP/agent"
printf '{"local":true,"nested":{"choice":"local"}}\n' >"$TEMP/json/local.json"
printf '{"managed":true,"nested":{"choice":"managed","added":1}}\n' >"$TEMP/json/incoming.json"
"$ENGINE" --local "$TEMP/json/local.json" --incoming "$TEMP/json/incoming.json" \
  --output "$TEMP/json/result.json" >/dev/null
python3 - "$TEMP/json/result.json" <<'PY'
import json,sys
value=json.load(open(sys.argv[1], encoding="utf-8"))
assert value == {"local": True, "managed": True, "nested": {"choice": "local", "added": 1}}
PY

printf 'a\nb\nc\n' >"$TEMP/text/base"
printf 'local-a\nb\nc\n' >"$TEMP/text/local"
printf 'a\nb\nincoming-c\n' >"$TEMP/text/incoming"
"$ENGINE" --base "$TEMP/text/base" --local "$TEMP/text/local" \
  --incoming "$TEMP/text/incoming" --output "$TEMP/text/result" >/dev/null
grep -q '^local-a$' "$TEMP/text/result" || fail 'local text change was lost'
grep -q '^incoming-c$' "$TEMP/text/result" || fail 'incoming text change was lost'

printf 'local\n' >"$TEMP/agent/local"
printf 'incoming\n' >"$TEMP/agent/incoming"
if "$ENGINE" --local "$TEMP/agent/local" --incoming "$TEMP/agent/incoming" \
  --output "$TEMP/agent/disabled" >/dev/null 2>&1; then
  fail 'agent fallback was enabled by default'
fi
cat >"$TEMP/fake-agent" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf 'resolved\n' >"$4"
EOF
chmod +x "$TEMP/fake-agent"
MINS_SMART_MERGE_AGENT_COMMAND="$TEMP/fake-agent" \
  "$ENGINE" --local "$TEMP/agent/local" --incoming "$TEMP/agent/incoming" \
  --output "$TEMP/agent/result" --agent auto >/dev/null
grep -q '^resolved$' "$TEMP/agent/result" || fail 'explicit agent command did not run'

mkdir -p "$TEMP/git-home"
HOME="$TEMP/git-home" "$GIT_SETUP"
[[ "$(HOME="$TEMP/git-home" git config --global merge.tool)" == smart-merge ]] \
  || fail 'Git smart-merge tool was not configured'
git_command="$(HOME="$TEMP/git-home" git config --global mergetool.smart-merge.cmd)"
[[ "$git_command" == *smart-merge-git* ]] || fail 'Git adapter command is incorrect'

printf 'a\nb\nc\n' > "$TEMP/text/git-base"
printf 'local-a\nb\nc\n' > "$TEMP/text/git-local"
printf 'a\nb\nremote-c\n' > "$TEMP/text/git-remote"
cp "$TEMP/text/git-local" "$TEMP/text/git-result"
"$GIT_ADAPTER" "$TEMP/text/git-base" "$TEMP/text/git-local" \
  "$TEMP/text/git-remote" "$TEMP/text/git-result" >/dev/null
grep -q '^local-a$' "$TEMP/text/git-result" || fail 'Git adapter lost local change'
grep -q '^remote-c$' "$TEMP/text/git-result" || fail 'Git adapter lost remote change'

echo "smart merge standalone tests passed"
