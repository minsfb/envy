#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BROKER="$ROOT/features/chezmoi-conflicts.py"
TEMP="$(mktemp -d)"
trap 'rm -rf "$TEMP"' EXIT

command -v chezmoi >/dev/null 2>&1 || { echo 'SKIP: chezmoi is not installed'; exit 0; }
fail() { echo "FAIL: $*" >&2; exit 1; }

HOME_DIR="$TEMP/home"
SOURCE="$TEMP/source"
CONFIG_HOME="$TEMP/config"
STATE_HOME="$TEMP/state"
mkdir -p "$HOME_DIR" "$SOURCE/dot_agentdir" "$CONFIG_HOME" "$STATE_HOME"

BROKER_ENV=(
  HOME="$HOME_DIR"
  XDG_CONFIG_HOME="$CONFIG_HOME"
  XDG_STATE_HOME="$STATE_HOME"
  ENVY_ROOT="$ROOT"
)

run_prompt() {
  local expected_rc=$1 output=$2
  shift 2
  env "${BROKER_ENV[@]}" python3 - "$BROKER" "$SOURCE" "$output" "$expected_rc" "$@" <<'PY'
import errno
import fcntl
import os
import pty
import select
import signal
import struct
import sys
import termios
import time

broker, source, output_path, expected_rc, *answers = sys.argv[1:]
pid, master = pty.fork()
if pid == 0:
    os.execve(broker, [broker, "apply", source], os.environ.copy())

fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 240, 0, 0))
transcript = bytearray()
scan_from = 0
prompt = b"[Q]uit: "
deadline = time.monotonic() + 30

try:
    while True:
        if time.monotonic() > deadline:
            os.kill(pid, signal.SIGKILL)
            raise RuntimeError("timed out waiting for chezmoi conflict prompt")
        readable, _, _ = select.select([master], [], [], 0.25)
        if not readable:
            finished, _ = os.waitpid(pid, os.WNOHANG)
            if finished:
                break
            continue
        try:
            chunk = os.read(master, 4096)
        except OSError as error:
            if error.errno == errno.EIO:
                break
            raise
        if not chunk:
            break
        transcript.extend(chunk)
        while True:
            position = transcript.find(prompt, scan_from)
            if position < 0:
                break
            if not answers:
                raise RuntimeError("broker prompted more often than expected")
            os.write(master, (answers.pop(0) + "\n").encode())
            scan_from = position + len(prompt)
finally:
    os.close(master)

_, status = os.waitpid(pid, 0)
with open(output_path, "wb") as output:
    output.write(transcript)

actual_rc = os.WEXITSTATUS(status) if os.WIFEXITED(status) else 128
if answers:
    raise RuntimeError(
        f"unused prompt answers: {answers}\n{transcript.decode(errors='replace')}"
    )
if actual_rc != int(expected_rc):
    raise RuntimeError(f"expected exit {expected_rc}, got {actual_rc}")
PY
}

printf 'base\nmanaged-one\n' >"$SOURCE/dot_test"
printf 'managed-child\n' >"$SOURCE/dot_agentdir/managed"
env "${BROKER_ENV[@]}" "$BROKER" apply "$SOURCE"
grep -q '^managed-one$' "$HOME_DIR/.test" || fail 'initial apply failed'

printf 'local-child\n' >"$HOME_DIR/.agentdir/local"
chmod 700 "$HOME_DIR/.agentdir"
env "${BROKER_ENV[@]}" "$BROKER" apply "$SOURCE" </dev/null
grep -q '^local-child$' "$HOME_DIR/.agentdir/local" \
  || fail 'noninteractive directory metadata convergence removed local contents'
python3 - "$HOME_DIR/.agentdir" <<'PY'
import os
import stat
import sys

assert stat.S_IMODE(os.stat(sys.argv[1]).st_mode) == 0o755
PY

printf 'managed-preexisting\n' >"$SOURCE/dot_preexisting"
printf 'local-preexisting\n' >"$HOME_DIR/.preexisting"
run_prompt 0 "$TEMP/preexisting.out" o
grep -q '^managed-preexisting$' "$HOME_DIR/.preexisting" \
  || fail 'pre-existing unmanaged target was not presented as a conflict'

printf 'base\nmanaged-two\n' >"$SOURCE/dot_test"
printf 'base\nlocal-two\n' >"$HOME_DIR/.test"
run_prompt 0 "$TEMP/overwrite.out" '' d o
tr -d '\r' <"$TEMP/overwrite.out" >"$TEMP/overwrite.normalized"
grep -qF 'Action [D]iff/[O]verwrite/[A]ll overwrite/[M]erge/[S]kip/[Q]uit:' \
  "$TEMP/overwrite.normalized" || fail 'clear action menu was not displayed'
grep -qF 'Enter D, O, A, M, S, or Q.' "$TEMP/overwrite.normalized" \
  || fail 'empty input did not require Enter-confirmed action'
grep -q '^managed-two$' "$HOME_DIR/.test" || fail 'overwrite did not apply managed target'

printf 'base\nmanaged-three\n' >"$SOURCE/dot_test"
printf 'base\nlocal-three\n' >"$HOME_DIR/.test"
run_prompt 1 "$TEMP/skip.out" s
grep -q '^local-three$' "$HOME_DIR/.test" || fail 'skip changed the local target'
run_prompt 0 "$TEMP/resolve-skip.out" o

printf 'managed-directory-collision\n' >"$SOURCE/dot_directory_collision"
mkdir "$HOME_DIR/.directory_collision"
printf 'local-child\n' >"$HOME_DIR/.directory_collision/local"
run_prompt 1 "$TEMP/directory-skip.out" s
grep -q '^local-child$' "$HOME_DIR/.directory_collision/local" \
  || fail 'pre-existing directory collision was changed without confirmation'
rm "$HOME_DIR/.directory_collision/local"
rmdir "$HOME_DIR/.directory_collision"
env "${BROKER_ENV[@]}" "$BROKER" apply "$SOURCE"

printf '{"managed":0}\n' >"$SOURCE/dot_settings.json"
env "${BROKER_ENV[@]}" "$BROKER" apply "$SOURCE"
printf '{"managed":1,"nested":{"added":2}}\n' >"$SOURCE/dot_settings.json"
printf '{"local":true,"nested":{"choice":"local"}}\n' >"$HOME_DIR/.settings.json"
run_prompt 0 "$TEMP/merge.out" m
python3 - "$HOME_DIR/.settings.json" <<'PY'
import json
import sys

value = json.load(open(sys.argv[1], encoding="utf-8"))
assert value == {
    "local": True,
    "managed": 1,
    "nested": {"choice": "local", "added": 2},
}
PY
env "${BROKER_ENV[@]}" "$BROKER" verify "$SOURCE"
env "${BROKER_ENV[@]}" "$BROKER" apply "$SOURCE" >"$TEMP/repeat.out"
grep -qF 'Retained 1 remembered machine-local merge(s).' "$TEMP/repeat.out" \
  || fail 'remembered merge was not reused'

mv "$SOURCE/dot_settings.json" "$SOURCE/executable_dot_settings.json"
printf '{"managed":1,"new":3,"nested":{"added":2}}\n' \
  >"$SOURCE/executable_dot_settings.json"
run_prompt 0 "$TEMP/remerge.out" m
python3 - "$HOME_DIR/.settings.json" <<'PY'
import json
import sys

value = json.load(open(sys.argv[1], encoding="utf-8"))
assert value["local"] is True
assert value["managed"] == 1
assert value["new"] == 3
assert value["nested"] == {"choice": "local", "added": 2}
PY
[[ -x "$HOME_DIR/.settings.json" ]] || fail 'remembered merge did not adopt managed mode change'
env "${BROKER_ENV[@]}" "$BROKER" verify "$SOURCE"

printf 'managed-link\n' >"$SOURCE/dot_linkmerge"
printf 'outside\n' >"$TEMP/outside"
ln -s "$TEMP/outside" "$HOME_DIR/.linkmerge"
run_prompt 0 "$TEMP/symlink.out" m o
grep -qF 'Merge is available only for regular-file targets.' "$TEMP/symlink.out" \
  || fail 'symlink merge refusal was not explained'
[[ ! -L "$HOME_DIR/.linkmerge" ]] || fail 'overwrite did not replace target symlink'
grep -q '^outside$' "$TEMP/outside" || fail 'merge followed and changed a target symlink'

echo 'envy chezmoi conflict tests passed'
