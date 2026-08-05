#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }
assert_file() { [[ -f "$1" ]] || fail "missing file: $1"; }
assert_not_file() { [[ ! -e "$1" && ! -L "$1" ]] || fail "unexpected file: $1"; }
assert_contains() { grep -qF -- "$2" "$1" || fail "$1 does not contain: $2"; }
assert_eq() { [[ "$1" == "$2" ]] || fail "expected '$2', got '$1'"; }

HOME_DIR="$SANDBOX/home"
BUNDLE_DIR="$SANDBOX/test-bundle"
OTHER_BUNDLE_DIR="$SANDBOX/other-bundle"
STATE="$HOME_DIR/.config/envy/state.json"
LOG="$HOME_DIR/.bundle-handler.log"
LOCK_DIR="$HOME_DIR/.config/envy/state.lock.d"
mkdir -p "$HOME_DIR" "$BUNDLE_DIR" "$OTHER_BUNDLE_DIR"

cat >"$BUNDLE_DIR/feature.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

action="${1:?action required}"
marker="$HOME/.bundle-${ENVY_FEATURE:?}"
failure="$HOME/.fail-${action}-${ENVY_FEATURE}"
printf '%s %s\n' "$action" "$ENVY_FEATURE" >>"$HOME/.bundle-handler.log"

if [[ -f "$failure" ]]; then
  rm -f "$failure"
  exit 17
fi

case "$action" in
  converge) touch "$marker" ;;
  verify) [[ -f "$marker" ]] ;;
  remove) rm -f "$marker" ;;
  *) exit 2 ;;
esac
EOF

cat >"$OTHER_BUNDLE_DIR/envy.bundle.json" <<'EOF'
{
  "version": 1,
  "id": "other-fixture-bundle",
  "bundle_version": "1",
  "portable": true,
  "features": {}
}
EOF
chmod +x "$BUNDLE_DIR/feature.sh"

cat >"$BUNDLE_DIR/envy.bundle.json" <<'EOF'
{
  "version": 1,
  "id": "fixture-bundle",
  "bundle_version": "1",
  "portable": true,
  "features": {
    "fixture-base": {
      "description": "Bundle lifecycle test foundation",
      "tier": "recommended",
      "default_enabled": true,
      "scope": {"global": true},
      "converge": {"run": "feature.sh", "args": ["converge"]},
      "verify": {"run": "feature.sh", "args": ["verify"]},
      "remove": {"run": "feature.sh", "args": ["remove"]}
    },
    "fixture-leaf": {
      "description": "Bundle lifecycle test dependent",
      "tier": "recommended",
      "default_enabled": true,
      "scope": {"global": true},
      "depends_on": ["fixture-base"],
      "converge": {"run": "feature.sh", "args": ["converge"]},
      "verify": {"run": "feature.sh", "args": ["verify"]},
      "remove": {"run": "feature.sh", "args": ["remove"]}
    }
  },
  "recommended_features": ["fixture-base", "fixture-leaf"]
}
EOF

run_cli() {
  HOME="$HOME_DIR" \
  PATH="/usr/bin:/bin" \
  SHELL=/bin/bash \
  ENVY_PLATFORM_OVERRIDE=linux \
  ENVY_SKIP_DEPENDENCY_PINS=1 \
  "$ROOT/bin/envy" "$@"
}

state_get() {
  python3 "$ROOT/lib/state.py" "$STATE" get "$1" "${2:-null}"
}

bundle_status() {
  python3 - "$STATE" <<'PY'
import json
import sys

state = json.load(open(sys.argv[1], encoding="utf-8"))
active = any(
    item.get("id") == "fixture-bundle"
    for item in state.get("bundles", [])
    if isinstance(item, dict)
)
detached = any(
    item.get("id") == "fixture-bundle"
    for item in state.get("detached_bundles", [])
    if isinstance(item, dict)
)
print("active" if active else "detached" if detached else "forgotten")
PY
}

feature_location() {
  python3 - "$STATE" "$1" <<'PY'
import json
import sys

state = json.load(open(sys.argv[1], encoding="utf-8"))
name = sys.argv[2]
if name in state.get("features", {}):
    print("active")
elif name in state.get("detached_features", {}):
    print("detached")
else:
    print("missing")
PY
}

log_count() {
  local entry="$1"
  [[ -f "$LOG" ]] || { echo 0; return; }
  grep -cFx "$entry" "$LOG" || true
}

# Locking a first-install dry run must not leave an otherwise unused config root.
run_cli install --preset core --dry-run >"$SANDBOX/first-install-dry-run.out"
[[ ! -d "$HOME_DIR/.config/envy" ]] \
  || fail "first-install dry run left an empty envy config directory"

resolved="$SANDBOX/manifest.json"
python3 "$ROOT/lib/manifest.py" compose "$ROOT/envy.json" "$BUNDLE_DIR" >"$resolved"
mkdir -p "$(dirname "$STATE")"
python3 "$ROOT/lib/state.py" "$STATE" init \
  "$resolved" linux bash "$ROOT" lifecycle-test
python3 "$ROOT/lib/state.py" "$STATE" set features.fixture-base.enabled true
python3 "$ROOT/lib/state.py" "$STATE" set features.fixture-base.selection_explicit true
python3 "$ROOT/lib/state.py" "$STATE" set features.fixture-leaf.enabled true
python3 "$ROOT/lib/state.py" "$STATE" set features.fixture-leaf.selection_explicit true

# Establish a current, enabled bundle without converging unrelated core features.
run_cli upgrade --only fixture-base,fixture-leaf --force >"$SANDBOX/setup.out"
assert_file "$HOME_DIR/.bundle-fixture-base"
assert_file "$HOME_DIR/.bundle-fixture-leaf"
assert_eq "$(bundle_status)" active

# Explicit --bundle remains a bootstrap input, not a way to replace installed state.
cp "$STATE" "$SANDBOX/state-before-set-change.json"
if run_cli --bundle "$OTHER_BUNDLE_DIR" status >"$SANDBOX/set-change.out" 2>&1; then
  fail "explicit --bundle replaced the installed bundle set"
fi
assert_contains "$SANDBOX/set-change.out" \
  "--bundle cannot change an installed bundle set; use bundle attach/detach"
cmp -s "$STATE" "$SANDBOX/state-before-set-change.json" \
  || fail "bundle-set rejection mutated installed state"

# A live lock fails closed; a dead owner's lock is recovered and released.
mkdir "$LOCK_DIR"
printf 'live-test-token\n%s\n' "$$" >"$LOCK_DIR/owner"
if run_cli status >"$SANDBOX/live-lock.out" 2>&1; then
  fail "status acquired a lock held by a live process"
fi
assert_contains "$SANDBOX/live-lock.out" "another envy command is updating state"
assert_file "$LOCK_DIR/owner"
rm -f "$LOCK_DIR/owner"
rmdir "$LOCK_DIR"

mkdir "$LOCK_DIR"
printf 'stale-test-token\n999999999\n' >"$LOCK_DIR/owner"
run_cli status >"$SANDBOX/stale-lock.out"
[[ ! -d "$LOCK_DIR" ]] || fail "stale state lock was not recovered and released"

# A detach dry run must not run handlers or create a transaction.
: >"$LOG"
run_cli bundle detach fixture-bundle --dry-run >"$SANDBOX/detach-dry-run.out"
assert_contains "$SANDBOX/detach-dry-run.out" "Dry run: no changes made."
assert_eq "$(log_count 'remove fixture-base')" 0
assert_eq "$(log_count 'remove fixture-leaf')" 0
assert_eq "$(state_get bundle_transaction.action '""')" ""
assert_eq "$(bundle_status)" active

# A partial reverse-order removal is journaled. Abort restores dependency-first.
: >"$LOG"
touch "$HOME_DIR/.fail-remove-fixture-base"
if run_cli bundle detach fixture-bundle >"$SANDBOX/detach-for-abort.out" 2>&1; then
  fail "detach unexpectedly succeeded despite the one-shot remove failure"
fi
assert_eq "$(state_get bundle_transaction.action)" detach
assert_eq "$(state_get bundle_transaction.phase)" removing
assert_eq "$(state_get bundle_transaction.removed_features)" '["fixture-leaf"]'
assert_not_file "$HOME_DIR/.bundle-fixture-leaf"
assert_file "$HOME_DIR/.bundle-fixture-base"

# Read-only status exposes recovery instructions, but a changed source is refused
# before state migration can overwrite the transaction's recorded identity.
run_cli status >"$SANDBOX/pending-status.out"
assert_contains "$SANDBOX/pending-status.out" \
  "Pending:  bundle detach fixture-bundle (removing)"
cp "$STATE" "$SANDBOX/state-before-source-change.json"
cp "$BUNDLE_DIR/envy.bundle.json" "$SANDBOX/bundle-manifest.saved"
sed 's/"bundle_version": "1"/"bundle_version": "changed"/' \
  "$SANDBOX/bundle-manifest.saved" >"$BUNDLE_DIR/envy.bundle.json"
if run_cli status >"$SANDBOX/source-change.out" 2>&1; then
  fail "pending detach accepted a changed bundle manifest"
fi
assert_contains "$SANDBOX/source-change.out" \
  "old bundle manifest changed during the pending transaction"
cmp -s "$STATE" "$SANDBOX/state-before-source-change.json" \
  || fail "pending manifest rejection allowed state migration"
cp "$SANDBOX/bundle-manifest.saved" "$BUNDLE_DIR/envy.bundle.json"

run_cli bundle detach fixture-bundle --abort >"$SANDBOX/abort.out"
assert_contains "$SANDBOX/abort.out" "Detach of 'fixture-bundle' aborted."
assert_eq "$(state_get bundle_transaction.action '""')" ""
assert_file "$HOME_DIR/.bundle-fixture-base"
assert_file "$HOME_DIR/.bundle-fixture-leaf"
assert_eq "$(log_count 'converge fixture-base')" 1
assert_eq "$(log_count 'converge fixture-leaf')" 1

# Retry resumes after the already removed leaf, and other mutations are blocked.
: >"$LOG"
touch "$HOME_DIR/.fail-remove-fixture-base"
if run_cli bundle detach fixture-bundle >"$SANDBOX/detach-first.out" 2>&1; then
  fail "detach unexpectedly succeeded despite the retry fixture failure"
fi
if run_cli upgrade --only fixture-base --force >"$SANDBOX/blocked-upgrade.out" 2>&1; then
  fail "upgrade ran while a bundle transaction was pending"
fi
assert_contains "$SANDBOX/blocked-upgrade.out" "pending"
assert_eq "$(log_count 'converge fixture-base')" 0
if run_cli upgrade --from-install --only fixture-base --force \
  >"$SANDBOX/blocked-from-install.out" 2>&1; then
  fail "upgrade --from-install bypassed the pending transaction guard"
fi
assert_contains "$SANDBOX/blocked-from-install.out" "pending"
assert_eq "$(log_count 'converge fixture-base')" 0
run_cli bundle detach fixture-bundle >"$SANDBOX/detach-resume.out"
assert_eq "$(log_count 'remove fixture-leaf')" 1
assert_eq "$(log_count 'remove fixture-base')" 2
assert_eq "$(state_get bundle_transaction.action '""')" ""
assert_eq "$(bundle_status)" detached
assert_eq "$(feature_location fixture-base)" detached
assert_eq "$(feature_location fixture-leaf)" detached
assert_not_file "$HOME_DIR/.bundle-fixture-base"
assert_not_file "$HOME_DIR/.bundle-fixture-leaf"

# Repeating a completed detach is an idempotent no-op.
: >"$LOG"
run_cli bundle detach fixture-bundle >"$SANDBOX/detach-repeat.out"
assert_contains "$SANDBOX/detach-repeat.out" "already detached"
assert_eq "$(log_count 'remove fixture-base')" 0
assert_eq "$(log_count 'remove fixture-leaf')" 0

# Simulate interruption after attach journaling but before commit. A pathless attach
# resumes from the stored source, then retains its post-commit reconciliation journal.
: >"$LOG"
attach_old_manifest="$SANDBOX/attach-old-manifest.json"
attach_new_manifest="$SANDBOX/attach-new-manifest.json"
attach_plan="$SANDBOX/attach-plan.json"
python3 "$ROOT/lib/manifest.py" compose "$ROOT/envy.json" >"$attach_old_manifest"
python3 "$ROOT/lib/manifest.py" compose \
  "$ROOT/envy.json" "$BUNDLE_DIR" >"$attach_new_manifest"
python3 "$ROOT/lib/bundles.py" plan-attach \
  "$attach_old_manifest" "$STATE" "$attach_new_manifest" \
  fixture-bundle "$attach_plan"
python3 "$ROOT/lib/bundles.py" start "$STATE" "$attach_plan"
assert_eq "$(state_get bundle_transaction.action)" attach
assert_eq "$(state_get bundle_transaction.phase)" attaching
touch "$HOME_DIR/.fail-converge-fixture-leaf"
if run_cli bundle attach >"$SANDBOX/attach-first.out" 2>&1; then
  fail "attach unexpectedly succeeded despite the one-shot converge failure"
fi
assert_eq "$(state_get bundle_transaction.action)" attach
assert_eq "$(state_get bundle_transaction.phase)" reconciling
assert_eq "$(state_get bundle_transaction.reconciled_features)" '["fixture-base"]'
assert_file "$HOME_DIR/.bundle-fixture-base"
assert_not_file "$HOME_DIR/.bundle-fixture-leaf"
run_cli bundle attach >"$SANDBOX/attach-resume.out"
assert_eq "$(log_count 'converge fixture-base')" 1
assert_eq "$(log_count 'converge fixture-leaf')" 2
assert_eq "$(state_get bundle_transaction.action '""')" ""
assert_eq "$(bundle_status)" active
assert_eq "$(feature_location fixture-base)" active
assert_eq "$(feature_location fixture-leaf)" active
assert_eq "$(state_get features.fixture-base.enabled)" true
assert_eq "$(state_get features.fixture-leaf.enabled)" true
assert_file "$HOME_DIR/.bundle-fixture-base"
assert_file "$HOME_DIR/.bundle-fixture-leaf"

# Forget is explicitly non-removing, including when the recorded source vanished.
: >"$LOG"
run_cli bundle forget fixture-bundle --dry-run >"$SANDBOX/forget-dry-run.out" 2>&1
assert_contains "$SANDBOX/forget-dry-run.out" "skips its removal handlers"
assert_contains "$SANDBOX/forget-dry-run.out" "Dry run: no changes made."
assert_eq "$(bundle_status)" active
assert_eq "$(log_count 'remove fixture-base')" 0
assert_eq "$(log_count 'remove fixture-leaf')" 0

mv "$BUNDLE_DIR" "$SANDBOX/missing-test-bundle"
run_cli bundle forget fixture-bundle >"$SANDBOX/forget.out" 2>&1
assert_contains "$SANDBOX/forget.out" "Bundle 'fixture-bundle' forgotten"
assert_eq "$(bundle_status)" forgotten
assert_eq "$(feature_location fixture-base)" missing
assert_eq "$(feature_location fixture-leaf)" missing
assert_eq "$(log_count 'remove fixture-base')" 0
assert_eq "$(log_count 'remove fixture-leaf')" 0
assert_file "$HOME_DIR/.bundle-fixture-base"
assert_file "$HOME_DIR/.bundle-fixture-leaf"

echo "envy bundle lifecycle integration tests passed"
