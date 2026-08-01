#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMP="$(mktemp -d)"
trap 'rm -rf "$TEMP"' EXIT

DEST="$TEMP/export"
REEXPORTED="$TEMP/reexport"
REMOTE="$TEMP/staging.git"
FIRST_CHECKOUT="$TEMP/first-checkout"
FINAL_CHECKOUT="$TEMP/final-checkout"

python3 "$ROOT/exporter/export.py" \
  --source "$ROOT" \
  --destination "$DEST" \
  --license-file "$ROOT/THIRD_PARTY.md" \
  --approval-id test-only \
  --copyright-holder test-only \
  --allow-dirty-for-test

[[ ! -e "$DEST/.git" ]] || {
  echo 'export inherited Git metadata' >&2
  exit 1
}
[[ -f "$DEST/PROVENANCE.json" && -f "$DEST/SBOM.cdx.json" ]] || {
  echo 'export metadata is missing' >&2
  exit 1
}
python3 - "$DEST" <<'PY'
import hashlib
import sys
from pathlib import Path

root = Path(sys.argv[1])
for line in (root / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
    expected, relative = line.split("  ", 1)
    actual = hashlib.sha256((root / relative).read_bytes()).hexdigest()
    if actual != expected:
        raise SystemExit(f"checksum mismatch: {relative}")
PY
(cd "$DEST" && python3 tests/test_manifest_state.py >/dev/null)
(cd "$DEST" && tests/agent-notifications.sh >/dev/null)
(cd "$DEST" && tests/smart-merge.sh >/dev/null)

python3 "$DEST/exporter/export.py" \
  --source "$DEST" \
  --destination "$REEXPORTED" \
  --license-file "$DEST/LICENSE" \
  --approval-id reexport-test-only \
  --copyright-holder test-only \
  --allow-dirty-for-test
[[ -f "$REEXPORTED/SHA256SUMS" ]] || {
  echo 're-export metadata is missing' >&2
  exit 1
}

git init --quiet --bare --initial-branch=main "$REMOTE"
python3 "$ROOT/exporter/export.py" \
  --source "$ROOT" \
  --remote "$REMOTE" \
  --push \
  --allow-dirty-for-test

git clone --quiet --branch main "$REMOTE" "$FIRST_CHECKOUT"
python3 - "$ROOT" "$FIRST_CHECKOUT" <<'PY'
import stat
import subprocess
import sys
from pathlib import Path

source = Path(sys.argv[1])
checkout = Path(sys.argv[2])
entries = [
    line.strip()
    for line in (source / "exporter" / "allowlist.txt").read_text(encoding="utf-8").splitlines()
    if line.strip() and not line.lstrip().startswith("#")
]
tracked = {
    line
    for line in subprocess.check_output(
        ["git", "-C", str(checkout), "ls-tree", "-r", "--name-only", "HEAD"],
        text=True,
    ).splitlines()
    if line
}
if tracked != set(entries):
    raise SystemExit(
        f"staging tree mismatch; missing={sorted(set(entries) - tracked)}, "
        f"extra={sorted(tracked - set(entries))}"
    )
for relative in entries:
    left = source / relative
    right = checkout / relative
    if left.read_bytes() != right.read_bytes():
        raise SystemExit(f"staged bytes differ: {relative}")
    executable_bits = stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    left_executable = bool(left.stat().st_mode & executable_bits)
    right_executable = bool(right.stat().st_mode & executable_bits)
    if left_executable != right_executable:
        raise SystemExit(f"staged executable mode differs: {relative}")
PY

initial_tip="$(git --git-dir="$REMOTE" rev-parse refs/heads/main)"
python3 "$ROOT/exporter/export.py" \
  --source "$ROOT" \
  --remote "$REMOTE" \
  --push \
  --allow-dirty-for-test >/dev/null
[[ "$(git --git-dir="$REMOTE" rev-parse refs/heads/main)" == "$initial_tip" ]] || {
  echo 'unchanged export created a commit' >&2
  exit 1
}

git -C "$FIRST_CHECKOUT" config user.name test-only
git -C "$FIRST_CHECKOUT" config user.email test-only@localhost
printf 'remote-only\n' >"$FIRST_CHECKOUT/remote-only.txt"
git -C "$FIRST_CHECKOUT" add remote-only.txt
git -C "$FIRST_CHECKOUT" commit --quiet -m 'test remote drift'
git -C "$FIRST_CHECKOUT" push --quiet origin main
drift_tip="$(git --git-dir="$REMOTE" rev-parse refs/heads/main)"

preview_output="$(
  python3 "$ROOT/exporter/export.py" \
    --source "$ROOT" \
    --remote "$REMOTE" \
    --allow-dirty-for-test
)"
prepared_tip="$(printf '%s\n' "$preview_output" | sed -n 's/.*prepared \([0-9a-f][0-9a-f]*\) for .*/\1/p')"
[[ -n "$prepared_tip" ]] || {
  echo 'read-only staging run did not report a prepared commit' >&2
  exit 1
}
[[ "$(git --git-dir="$REMOTE" rev-parse refs/heads/main)" == "$drift_tip" ]] || {
  echo 'read-only staging run changed the remote' >&2
  exit 1
}

python3 "$ROOT/exporter/export.py" \
  --source "$ROOT" \
  --remote "$REMOTE" \
  --push \
  --allow-dirty-for-test >/dev/null
[[ "$(git --git-dir="$REMOTE" rev-parse refs/heads/main)" == "$prepared_tip" ]] || {
  echo 'repeated export did not create the deterministic previewed commit' >&2
  exit 1
}
[[ "$prepared_tip" == "$initial_tip" ]] || {
  echo 'staging drift changed the deterministic export commit' >&2
  exit 1
}
[[ "$(git --git-dir="$REMOTE" rev-list --count --all)" == 1 ]] || {
  echo 'staging export retained reachable staging history' >&2
  exit 1
}
[[ "$(git --git-dir="$REMOTE" rev-list --parents -n 1 refs/heads/main | awk '{print NF}')" == 1 ]] || {
  echo 'staging export commit is not a root commit' >&2
  exit 1
}

git clone --quiet --branch main "$REMOTE" "$FINAL_CHECKOUT"
[[ ! -e "$FINAL_CHECKOUT/remote-only.txt" ]] || {
  echo 'staging-only drift survived exact export' >&2
  exit 1
}

if python3 "$ROOT/exporter/export.py" \
  --source "$ROOT" \
  --remote 'https://user:password@github.com/mins/envy.git' \
  --allow-dirty-for-test >/dev/null 2>&1; then
  echo 'credential-bearing remote was accepted' >&2
  exit 1
fi

echo 'mins-env exporter tests passed'
