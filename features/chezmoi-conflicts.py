#!/usr/bin/env python3
"""Apply chezmoi with explicit conflict choices and remembered local merges."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO


HOME = Path.home().resolve()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import paths  # noqa: E402

STATE_ROOT = paths.state_dir() / "chezmoi-merges"
INDEX_PATH = STATE_ROOT / "index.json"
BASE_ROOT = STATE_ROOT / "bases"
ENVY_ROOT = Path(
    os.environ.get("ENVY_ROOT", Path(__file__).resolve().parent.parent)
).resolve()
ENGINE = ENVY_ROOT / "lib" / "smart_merge.py"
CHEZMOI = shutil.which("chezmoi")


class BrokerError(RuntimeError):
    pass


@dataclass(frozen=True)
class StatusEntry:
    local_change: str
    apply_change: str
    target: Path


@dataclass(frozen=True)
class ManagedTarget:
    kind: str
    mode: int | None


def digest(contents: bytes) -> str:
    return hashlib.sha256(contents).hexdigest()


def run_chezmoi(
    source: Path,
    *arguments: str,
    capture: bool = False,
) -> subprocess.CompletedProcess[bytes]:
    if not CHEZMOI:
        raise BrokerError("chezmoi is not installed")
    return subprocess.run(
        [CHEZMOI, *arguments, "--source", str(source)],
        check=False,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )


def status(source: Path) -> list[StatusEntry]:
    process = run_chezmoi(source, "status", "--path-style", "absolute", capture=True)
    if process.returncode != 0:
        raise BrokerError(process.stderr.decode(errors="replace").strip())
    entries: list[StatusEntry] = []
    for raw_line in process.stdout.decode(errors="surrogateescape").splitlines():
        if len(raw_line) < 4:
            continue
        target = Path(raw_line[3:])
        resolved = target.parent.resolve(strict=False) / target.name
        if not target.is_absolute() or os.path.commonpath((HOME, resolved)) != str(HOME):
            raise BrokerError(f"refusing unexpected chezmoi target: {target}")
        entries.append(StatusEntry(raw_line[0], raw_line[1], target))
    return entries


def written_targets(source: Path) -> set[Path]:
    process = run_chezmoi(
        source, "state", "get-bucket", "--bucket", "entryState", capture=True
    )
    if process.returncode != 0:
        return set()
    try:
        value = json.loads(process.stdout)
    except json.JSONDecodeError:
        return set()
    if not isinstance(value, dict):
        return set()
    return {Path(path) for path in value if isinstance(path, str)}


def is_conflict(item: StatusEntry, written: set[Path]) -> bool:
    if item.apply_change in {" ", "R"}:
        return False
    locally_changed = item.local_change != " "
    preexisting = item.target not in written and (
        item.target.exists() or item.target.is_symlink()
    )
    return locally_changed or preexisting


def managed_contents(source: Path, target: Path) -> bytes | None:
    process = run_chezmoi(source, "cat", str(target), capture=True)
    return process.stdout if process.returncode == 0 else None


def managed_target(source: Path, target: Path) -> ManagedTarget | None:
    process = run_chezmoi(
        source,
        "dump",
        "--format",
        "json",
        "--recursive=false",
        str(target),
        capture=True,
    )
    if process.returncode != 0:
        return None
    try:
        value = json.loads(process.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or len(value) != 1:
        return None
    target_state = next(iter(value.values()))
    if not isinstance(target_state, dict):
        return None
    kind = target_state.get("type")
    if not isinstance(kind, str):
        return None
    mode = target_state.get("perm")
    parsed_mode = mode if isinstance(mode, int) and not isinstance(mode, bool) else None
    return ManagedTarget(kind, parsed_mode)


def source_path(source: Path, target: Path) -> str:
    process = run_chezmoi(source, "source-path", str(target), capture=True)
    if process.returncode != 0:
        return "unknown"
    return process.stdout.decode(errors="replace").strip()


def load_index() -> dict[str, Any]:
    if not INDEX_PATH.is_file():
        return {"version": 1, "entries": {}}
    if INDEX_PATH.is_symlink():
        raise BrokerError(f"refusing symlink merge ledger: {INDEX_PATH}")
    try:
        value = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BrokerError(f"invalid merge ledger {INDEX_PATH}: {error}") from error
    if value.get("version") != 1 or not isinstance(value.get("entries"), dict):
        raise BrokerError(f"unsupported merge ledger: {INDEX_PATH}")
    return value


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_bytes(path: Path, contents: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as handle:
            handle.write(contents)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def base_path(target: Path) -> Path:
    return BASE_ROOT / f"{hashlib.sha256(str(target).encode()).hexdigest()}.base"


def stored_base_path(value: Any) -> Path | None:
    if not isinstance(value, str):
        return None
    candidate = (STATE_ROOT / value).resolve(strict=False)
    try:
        candidate.relative_to(BASE_ROOT.resolve(strict=False))
    except ValueError:
        return None
    return candidate


def remove_overlay(index: dict[str, Any], target: Path) -> None:
    entry = index["entries"].pop(str(target), None)
    if isinstance(entry, dict):
        path = stored_base_path(entry.get("base"))
        if path is not None and path.is_file() and not path.is_symlink():
            path.unlink()


def overlay_current(
    entry: dict[str, Any] | None,
    target: Path,
    incoming: bytes | None,
    desired_mode: int | None,
) -> bool:
    if (
        not isinstance(entry, dict)
        or incoming is None
        or target.is_symlink()
        or not target.is_file()
    ):
        return False
    try:
        local = target.read_bytes()
        actual_mode = stat.S_IMODE(target.stat().st_mode)
    except OSError:
        return False
    recorded_mode = entry.get("mode")
    return (
        entry.get("managed_sha256") == digest(incoming)
        and entry.get("local_sha256") == digest(local)
        and isinstance(recorded_mode, int)
        and not isinstance(recorded_mode, bool)
        and recorded_mode == actual_mode
        and desired_mode is not None
        and recorded_mode == desired_mode
    )


def agent_policy() -> str:
    if os.environ.get("ENVY_SMART_MERGE_AGENT_COMMAND"):
        return "auto"
    configured = os.environ.get("ENVY_SMART_MERGE_AGENT", "none")
    return configured if configured in {"none", "auto", "claude"} else "none"


def planned_merge(
    source: Path,
    target: Path,
    incoming: bytes,
    entry: dict[str, Any] | None,
) -> bytes | None:
    if target.is_symlink() or not target.is_file():
        print("  Merge is available only for regular-file targets.")
        return None
    if not ENGINE.is_file():
        print(f"  Merge engine is unavailable: {ENGINE}")
        return None
    local = target.read_bytes()
    previous: bytes | None = None
    if isinstance(entry, dict):
        stored_base = stored_base_path(entry.get("base"))
        if (
            stored_base is not None
            and stored_base.is_file()
            and not stored_base.is_symlink()
        ):
            candidate = stored_base.read_bytes()
            if digest(candidate) == entry.get("managed_sha256"):
                previous = candidate

    suffix = target.suffix
    with tempfile.TemporaryDirectory(prefix="envy-chezmoi-merge.") as temporary:
        root = Path(temporary)
        local_path = root / f"LOCAL{suffix}"
        incoming_path = root / f"INCOMING{suffix}"
        output_path = root / f"RESULT{suffix}"
        context_path = root / "CONTEXT.txt"
        local_path.write_bytes(local)
        incoming_path.write_bytes(incoming)
        context_path.write_text(
            "=== CHEZMOI MERGE CONTEXT ===\n"
            f"Destination: {target}\n"
            f"Source state: {source_path(source, target)}\n"
            "The result is a remembered machine-local overlay. Do not edit the source repository.\n",
            encoding="utf-8",
        )
        arguments = [
            sys.executable,
            str(ENGINE),
            "--local",
            str(local_path),
            "--incoming",
            str(incoming_path),
            "--output",
            str(output_path),
            "--context",
            str(context_path),
            "--agent",
            agent_policy(),
        ]
        if previous is not None:
            base_file = root / f"BASE{suffix}"
            base_file.write_bytes(previous)
            arguments[2:2] = ["--base", str(base_file)]
        process = subprocess.run(
            arguments,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if process.stdout.strip():
            print(f"  {process.stdout.strip()}")
        if process.returncode != 0 or not output_path.is_file():
            detail = process.stderr.strip() or "merge engine could not resolve safely"
            print(f"  Merge failed: {detail}", file=sys.stderr)
            return None
        return output_path.read_bytes()


def show_diff(source: Path, target: Path) -> None:
    process = run_chezmoi(source, "diff", "--no-pager", str(target))
    if process.returncode != 0:
        print(f"Unable to show diff for {target}", file=sys.stderr)


def open_tty() -> TextIO | None:
    try:
        tty = open("/dev/tty", "r", encoding="utf-8", buffering=1)
    except OSError:
        return None
    if not os.isatty(tty.fileno()):
        tty.close()
        return None
    return tty


def prompt_action(
    tty: TextIO,
    source: Path,
    conflict: StatusEntry,
    incoming: bytes | None,
    ledger_entry: dict[str, Any] | None,
    managed: ManagedTarget | None,
) -> tuple[str, bytes | None]:
    directory_metadata = (
        managed is not None
        and managed.kind == "dir"
        and conflict.target.is_dir()
        and not conflict.target.is_symlink()
    )
    deletion = conflict.apply_change == "D" and managed is None
    print(f"\nChezmoi conflict: {conflict.target}")
    if directory_metadata:
        print(
            "  [O]verwrite applies managed directory metadata without removing contents."
        )
        print(
            "  [M]erge keeps directory contents and applies that metadata (same result)."
        )
    elif deletion:
        print("  [O]verwrite removes the target to match managed state.")
        print("  [M]erge is unavailable because managed state deletes the target.")
    else:
        print("  [O]verwrite replaces the local target with managed content.")
        print(
            "  [M]erge preserves compatible changes locally "
            "(algorithmic, then configured agent fallback)."
        )
        print(
            "      If enabled and needed, the agent receives isolated copies of this file."
        )
    print("  [S]kip leaves the target unchanged and envy visibly drifted.")
    while True:
        print(
            "Action [D]iff/[O]verwrite/[A]ll overwrite/[M]erge/[S]kip/[Q]uit: ",
            end="",
            flush=True,
        )
        reply = tty.readline()
        if reply == "":
            return "quit", None
        choice = reply.strip().lower()
        if choice in {"d", "diff"}:
            show_diff(source, conflict.target)
            continue
        if choice in {"o", "overwrite"}:
            return "overwrite", None
        if choice in {"a", "all", "all-overwrite"}:
            return "all-overwrite", None
        if choice in {"s", "skip"}:
            return "skip", None
        if choice in {"q", "quit"}:
            return "quit", None
        if choice in {"m", "merge"}:
            if directory_metadata:
                print("  Keeping directory contents and applying managed metadata.")
                return "overwrite", None
            if deletion:
                print("  Merge is unavailable when managed state deletes the target.")
                continue
            if incoming is None:
                kind = managed.kind if managed is not None else "unknown"
                print(
                    f"  Merge is available only for regular files (managed type: {kind})."
                )
                continue
            result = planned_merge(source, conflict.target, incoming, ledger_entry)
            if result is not None:
                return "merge", result
            continue
        print("  Enter D, O, A, M, S, or Q.")


def apply_target(source: Path, target: Path) -> None:
    process = run_chezmoi(
        source,
        "apply",
        "--force",
        "--no-tty",
        "--recursive=false",
        str(target),
    )
    if process.returncode != 0:
        raise BrokerError(f"chezmoi failed to apply {target}")


def record_overlay(
    index: dict[str, Any],
    target: Path,
    incoming: bytes,
    merged: bytes,
    mode: int,
) -> None:
    stored_base = base_path(target)
    BASE_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(BASE_ROOT, 0o700)
    atomic_bytes(stored_base, incoming)
    index["entries"][str(target)] = {
        "base": str(stored_base.relative_to(STATE_ROOT)),
        "managed_sha256": digest(incoming),
        "local_sha256": digest(merged),
        "mode": mode,
        "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }


def converge(source: Path) -> int:
    index = load_index()
    entries = status(source)
    written = written_targets(source)
    tty: TextIO | None = None
    actions: dict[Path, str] = {}
    merges: dict[Path, tuple[bytes, bytes, int]] = {}
    accepted: set[Path] = set()
    overwrite_all = False

    for item in entries:
        if item.apply_change in {" ", "R"}:
            continue
        incoming = managed_contents(source, item.target)
        managed = managed_target(source, item.target)
        ledger_entry = index["entries"].get(str(item.target))
        desired_mode = (
            managed.mode
            if isinstance(ledger_entry, dict)
            and incoming is not None
            and managed is not None
            else None
        )
        if overlay_current(ledger_entry, item.target, incoming, desired_mode):
            accepted.add(item.target)
            continue
        if not is_conflict(item, written):
            actions[item.target] = "overwrite"
            continue
        if overwrite_all:
            actions[item.target] = "overwrite"
            continue
        if tty is None:
            tty = open_tty()
            if tty is None:
                print(
                    f"Chezmoi conflict requires an interactive decision: {item.target}",
                    file=sys.stderr,
                )
                return 1
        action, merged = prompt_action(
            tty, source, item, incoming, ledger_entry, managed
        )
        if action == "quit":
            return 1
        if action == "all-overwrite":
            overwrite_all = True
            action = "overwrite"
        actions[item.target] = action
        if action == "merge" and merged is not None and incoming is not None:
            if desired_mode is None and managed is not None:
                desired_mode = managed.mode
            mode = (
                desired_mode
                if desired_mode is not None
                else stat.S_IMODE(item.target.stat().st_mode)
            )
            merges[item.target] = (incoming, merged, mode)

    if tty is not None:
        tty.close()

    for item in entries:
        if item.apply_change == "R":
            apply_target(source, item.target)
            continue
        action = actions.get(item.target)
        if action == "overwrite":
            apply_target(source, item.target)
            remove_overlay(index, item.target)

    for target, (incoming, merged, mode) in merges.items():
        atomic_bytes(target, merged, mode)
        record_overlay(index, target, incoming, merged, mode)

    skipped = [target for target, action in actions.items() if action == "skip"]
    if not skipped:
        # --refresh-externals is required for recovery: chezmoi's persistent
        # state records externals as fetched, so without it a checkout that was
        # deleted behind chezmoi's back is never re-cloned. chezmoi also exits 0
        # when an external clone fails, so a missing checkout is reported by
        # lib/dependencies.py rather than detected here.
        process = run_chezmoi(
            source,
            "apply",
            "--force",
            "--no-tty",
            "--refresh-externals=always",
            "--include",
            "externals",
        )
        if process.returncode != 0:
            raise BrokerError("chezmoi failed to refresh externals")

    atomic_json(INDEX_PATH, index)
    if skipped:
        print("Skipped chezmoi targets remain drifted:", file=sys.stderr)
        for target in skipped:
            print(f"  {target}", file=sys.stderr)
        return 1
    if accepted:
        print(f"Retained {len(accepted)} remembered machine-local merge(s).")
    return 0


def verify(source: Path) -> int:
    index = load_index()
    problems: list[Path] = []
    for item in status(source):
        if item.apply_change in {" ", "R"}:
            continue
        incoming = managed_contents(source, item.target)
        managed = managed_target(source, item.target)
        ledger_entry = index["entries"].get(str(item.target))
        desired_mode = (
            managed.mode
            if isinstance(ledger_entry, dict)
            and incoming is not None
            and managed is not None
            else None
        )
        if overlay_current(ledger_entry, item.target, incoming, desired_mode):
            continue
        problems.append(item.target)
    if problems:
        print(
            "Chezmoi targets differ from managed or remembered merged state:",
            file=sys.stderr,
        )
        for target in problems:
            print(f"  {target}", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] not in {"apply", "verify"}:
        print("usage: chezmoi-conflicts.py {apply|verify} SOURCE", file=sys.stderr)
        return 2
    source = Path(sys.argv[2]).resolve()
    try:
        return converge(source) if sys.argv[1] == "apply" else verify(source)
    except (BrokerError, OSError, subprocess.SubprocessError) as error:
        print(f"chezmoi-conflicts: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
