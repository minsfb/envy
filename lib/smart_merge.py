#!/usr/bin/env python3
"""Repository-agnostic, staged merge engine with an optional agent fallback."""

from __future__ import annotations

import argparse
import copy
import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MISSING = object()


class MergeConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class MergeResult:
    contents: bytes
    stage: str


def read_bytes(path: Path | None) -> bytes | None:
    if path is None:
        return None
    return path.read_bytes()


def has_conflict_markers(contents: bytes) -> bool:
    for line in contents.splitlines():
        if line == b"=======" or line in {b"<<<<<<<", b"|||||||", b">>>>>>>"}:
            return True
        if line.startswith((b"<<<<<<< ", b"||||||| ", b">>>>>>> ")):
            return True
    return False


def merge_json_defaults(managed: Any, local: Any) -> Any:
    """Add managed defaults while retaining local scalar choices and extensions."""
    if isinstance(managed, dict) and isinstance(local, dict):
        result = copy.deepcopy(local)
        for key, value in managed.items():
            result[key] = (
                merge_json_defaults(value, local[key])
                if key in local
                else copy.deepcopy(value)
            )
        return result
    if isinstance(managed, list) and isinstance(local, list):
        result = copy.deepcopy(local)
        for value in managed:
            if value not in result:
                result.append(copy.deepcopy(value))
        return result
    return copy.deepcopy(local)


def merge_json_three_way(base: Any, local: Any, incoming: Any) -> Any:
    if local == incoming:
        return copy.deepcopy(local)
    if local == base:
        return copy.deepcopy(incoming)
    if incoming == base:
        return copy.deepcopy(local)

    if all(isinstance(value, dict) for value in (base, local, incoming)):
        result: dict[str, Any] = {}
        for key in dict.fromkeys((*base, *local, *incoming)):
            base_value = base.get(key, MISSING)
            local_value = local.get(key, MISSING)
            incoming_value = incoming.get(key, MISSING)
            if local_value == incoming_value:
                selected = local_value
            elif local_value == base_value:
                selected = incoming_value
            elif incoming_value == base_value:
                selected = local_value
            elif MISSING not in (base_value, local_value, incoming_value):
                selected = merge_json_three_way(base_value, local_value, incoming_value)
            else:
                raise MergeConflict(f"conflicting JSON key: {key}")
            if selected is not MISSING:
                result[key] = copy.deepcopy(selected)
        return result

    raise MergeConflict("both sides changed the same JSON value")


def encode_json(value: Any) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode()


def try_json_merge(
    base: bytes | None, local: bytes, incoming: bytes, suffix: str
) -> MergeResult | None:
    if suffix.lower() != ".json":
        return None
    try:
        local_value = json.loads(local)
        incoming_value = json.loads(incoming)
        if base is None:
            return MergeResult(
                encode_json(merge_json_defaults(incoming_value, local_value)),
                "json-additive",
            )
        base_value = json.loads(base)
        return MergeResult(
            encode_json(merge_json_three_way(base_value, local_value, incoming_value)),
            "json-three-way",
        )
    except (json.JSONDecodeError, MergeConflict):
        return None


def conflict_document(local: bytes, incoming: bytes) -> bytes:
    local_body = local + (b"" if local.endswith(b"\n") else b"\n")
    incoming_body = incoming + (b"" if incoming.endswith(b"\n") else b"\n")
    return (
        b"<<<<<<< LOCAL\n"
        + local_body
        + b"=======\n"
        + incoming_body
        + b">>>>>>> INCOMING\n"
    )


def try_text_three_way(
    base: bytes | None, local: bytes, incoming: bytes
) -> tuple[MergeResult | None, bytes]:
    if base is None:
        return None, conflict_document(local, incoming)
    if any(b"\0" in value for value in (base, local, incoming)):
        return None, b""
    with tempfile.TemporaryDirectory(prefix="smart-merge-diff3.") as temporary:
        root = Path(temporary)
        local_path = root / "LOCAL"
        base_path = root / "BASE"
        incoming_path = root / "INCOMING"
        local_path.write_bytes(local)
        base_path.write_bytes(base)
        incoming_path.write_bytes(incoming)
        diff3 = shutil.which("diff3")
        if diff3:
            process = subprocess.run(
                [
                    diff3,
                    "-m",
                    str(local_path),
                    str(base_path),
                    str(incoming_path),
                ],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            if process.returncode == 0:
                return MergeResult(process.stdout, "text-three-way"), process.stdout
            if process.returncode == 1:
                return None, process.stdout

        # git merge-file is a portable fallback and does not require a repository.
        git = shutil.which("git")
        if git:
            process = subprocess.run(
                [
                    git,
                    "merge-file",
                    "--diff3",
                    "-p",
                    str(local_path),
                    str(base_path),
                    str(incoming_path),
                ],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            if process.returncode == 0:
                return MergeResult(process.stdout, "text-three-way"), process.stdout
            if process.stdout:
                return None, process.stdout
    return None, conflict_document(local, incoming)


def validate(contents: bytes, suffix: str) -> None:
    if has_conflict_markers(contents):
        raise MergeConflict("conflict markers remain")
    if suffix.lower() == ".json":
        try:
            json.loads(contents)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise MergeConflict(f"merged JSON is invalid: {error}") from error


def build_agent_prompt(has_base: bool, has_context: bool) -> str:
    base_instruction = (
        "BASE is the prior common version. " if has_base else "No BASE is available. "
    )
    context_instruction = (
        "Read CONTEXT for domain-specific intent. " if has_context else ""
    )
    return (
        "Resolve a file merge in this isolated directory. "
        + base_instruction
        + "LOCAL is the current version and INCOMING is the other/new version. "
        + context_instruction
        + "RESULT contains the best algorithmic attempt. Preserve compatible unique changes "
        + "from both sides, remove every conflict marker, and edit only RESULT. Do not merely "
        + "describe the merge."
    )


def run_agent(
    base: bytes | None,
    local: bytes,
    incoming: bytes,
    candidate: bytes,
    context: bytes | None,
    agent: str,
) -> MergeResult | None:
    if any(b"\0" in value for value in (local, incoming)):
        return None
    if agent == "none":
        return None
    with tempfile.TemporaryDirectory(prefix="smart-merge-agent.") as temporary:
        root = Path(temporary)
        base_path = root / "BASE"
        local_path = root / "LOCAL"
        incoming_path = root / "INCOMING"
        result_path = root / "RESULT"
        context_path = root / "CONTEXT"
        if base is not None:
            base_path.write_bytes(base)
        local_path.write_bytes(local)
        incoming_path.write_bytes(incoming)
        result_path.write_bytes(candidate)
        if context is not None:
            context_path.write_bytes(context)

        override = os.environ.get("MINS_SMART_MERGE_AGENT_COMMAND", "").strip()
        if override:
            command = [
                *shlex.split(override),
                str(base_path) if base is not None else "",
                str(local_path),
                str(incoming_path),
                str(result_path),
                str(context_path) if context is not None else "",
            ]
        elif agent == "claude":
            claude = shutil.which("claude")
            if not claude:
                return None
            command = [
                claude,
                "-p",
                "--safe-mode",
                "--tools",
                "Read,Edit",
                "--permission-mode",
                "acceptEdits",
                "--allowedTools",
                "Read,Edit",
                "--no-session-persistence",
                build_agent_prompt(base is not None, context is not None),
            ]
        else:
            return None
        try:
            process = subprocess.run(
                command,
                cwd=root,
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=300,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if process.returncode != 0 or not result_path.is_file():
            return None
        result = result_path.read_bytes()
        return MergeResult(result, "agent")


def merge_contents(
    *,
    base: bytes | None,
    local: bytes,
    incoming: bytes,
    suffix: str,
    context: bytes | None = None,
    agent: str = "none",
) -> MergeResult | None:
    if local == incoming:
        return MergeResult(local, "identical")
    if base is not None:
        if local == base:
            return MergeResult(incoming, "incoming-only")
        if incoming == base:
            return MergeResult(local, "local-only")

    structured = try_json_merge(base, local, incoming, suffix)
    if structured is not None:
        validate(structured.contents, suffix)
        return structured

    textual, candidate = try_text_three_way(base, local, incoming)
    if textual is not None:
        validate(textual.contents, suffix)
        return textual

    agent_result = run_agent(base, local, incoming, candidate, context, agent)
    if agent_result is None:
        return None
    try:
        validate(agent_result.contents, suffix)
    except MergeConflict:
        return None
    return agent_result


def atomic_write(path: Path, contents: bytes, reference: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        mode = stat.S_IMODE(path.stat().st_mode)
    elif reference.exists():
        mode = stat.S_IMODE(reference.stat().st_mode)
    else:
        mode = 0o600
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as handle:
            handle.write(contents)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--base", type=Path)
    result.add_argument("--local", type=Path, required=True)
    result.add_argument("--incoming", type=Path, required=True)
    result.add_argument("--output", type=Path, required=True)
    result.add_argument("--context", type=Path)
    result.add_argument(
        "--agent",
        choices=("auto", "claude", "none"),
        default=os.environ.get("MINS_SMART_MERGE_AGENT", "none"),
        help=(
            "agent fallback policy; 'auto' uses only an explicit "
            "MINS_SMART_MERGE_AGENT_COMMAND"
        ),
    )
    result.add_argument("--quiet", action="store_true")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        output = args.output.resolve(strict=False)
        if args.output.is_symlink():
            raise MergeConflict("refusing symlink output")
        for label, path in (
            ("base", args.base),
            ("local", args.local),
            ("incoming", args.incoming),
            ("context", args.context),
        ):
            if path is not None and path.resolve(strict=False) == output:
                raise MergeConflict(f"--output must differ from --{label}")
        base = read_bytes(args.base)
        local = args.local.read_bytes()
        incoming = args.incoming.read_bytes()
        context = read_bytes(args.context)
        suffix = args.output.suffix or args.incoming.suffix or args.local.suffix
        result = merge_contents(
            base=base,
            local=local,
            incoming=incoming,
            suffix=suffix,
            context=context,
            agent=args.agent,
        )
        if result is None:
            if not args.quiet:
                print("smart-merge: unresolved", file=sys.stderr)
            return 1
        atomic_write(args.output, result.contents, args.local)
        if not args.quiet:
            print(f"smart-merge: resolved with {result.stage}")
        return 0
    except (OSError, MergeConflict) as error:
        print(f"smart-merge: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
