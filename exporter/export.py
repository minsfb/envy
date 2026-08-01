#!/usr/bin/env python3
"""Create and optionally publish a clean, allowlisted mins-env export."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Optional, Sequence, Set, Tuple
from urllib.parse import urlparse


ALLOWED_URL_HOSTS = {
    "github.com",
    "www.github.com",
    "claude.com",
    "git-lfs.github.com",
    "mise.jdx.dev",
    "nodejs.org",
}
GENERATED = {"LICENSE", "PROVENANCE.json", "SBOM.cdx.json", "SHA256SUMS"}
SECRET_PATTERNS = (
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(rb"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(rb"\bAGE-SECRET-KEY-1[0-9A-Z]{20,}\b"),
)
URL = re.compile(r"https?://[^\s)'\"<>}\]]+")
URL_WITH_USERINFO = re.compile(r"(https?://)[^/@\s]+@", re.IGNORECASE)
ABSOLUTE_USER_PATH = re.compile(
    rb"(?<![A-Za-z0-9_$.-])/(?:(?:home|Users))/[A-Za-z0-9._-]+/"
)
DEFAULT_AUTHOR_NAME = "mins-env exporter"
DEFAULT_AUTHOR_EMAIL = "mins-env-exporter@users.noreply.github.com"
FALLBACK_COMMIT_DATE = "2000-01-01T00:00:00+00:00"


class ExportError(RuntimeError):
    pass


@dataclass(frozen=True)
class SourceState:
    root: Path
    entries: Tuple[str, ...]
    revision: str
    dirty: bool
    commit_date: str
    with_release_metadata: bool

    @property
    def output_paths(self) -> Set[str]:
        paths = set(self.entries)
        if self.with_release_metadata:
            paths.update(GENERATED)
        return paths


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_detail(value: str) -> str:
    encoded = value.encode("utf-8", errors="replace")
    for pattern in SECRET_PATTERNS:
        encoded = pattern.sub(b"<redacted>", encoded)
    redacted = URL_WITH_USERINFO.sub(
        r"\1<redacted>@", encoded.decode("utf-8", errors="replace").strip()
    )
    lines = [line.strip() for line in redacted.splitlines() if line.strip()]
    return " | ".join(lines[-4:])[:2000] or "no diagnostic output"


def run(
    command: Sequence[str],
    *,
    cwd: Optional[Path] = None,
    env: Optional[dict[str, str]] = None,
    stdin: Optional[str] = None,
    accepted: Tuple[int, ...] = (0,),
    label: str,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(
        list(command),
        cwd=str(cwd) if cwd is not None else None,
        env=env,
        check=False,
        text=True,
        input=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if process.returncode not in accepted:
        raise ExportError(f"{label} failed: {safe_detail(process.stderr)}")
    return process


def git(
    root: Path,
    arguments: Sequence[str],
    *,
    env: Optional[dict[str, str]] = None,
    stdin: Optional[str] = None,
    accepted: Tuple[int, ...] = (0,),
    label: str,
) -> subprocess.CompletedProcess[str]:
    return run(
        ("git", "-C", str(root), *arguments),
        env=env,
        stdin=stdin,
        accepted=accepted,
        label=label,
    )


def load_allowlist(root: Path) -> Tuple[str, ...]:
    path = root / "exporter" / "allowlist.txt"
    try:
        entries = tuple(
            line.strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
    except FileNotFoundError as error:
        raise ExportError(f"allowlist is missing: {path}") from error
    if not entries:
        raise ExportError("export allowlist is empty")
    if len(entries) != len(set(entries)):
        raise ExportError("export allowlist contains duplicate paths")
    if entries != tuple(sorted(entries)):
        raise ExportError("export allowlist must be sorted")
    for entry in entries:
        candidate = Path(entry)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ExportError(f"unsafe allowlist path: {entry}")
        if (
            entry in GENERATED
            or ".git" in candidate.parts
            or ".gitmodules" in candidate.parts
        ):
            raise ExportError(f"reserved allowlist path: {entry}")
    return entries


def tree_files(root: Path) -> Set[str]:
    result: Set[str] = set()
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if relative.parts[:1] == (".git",) or "__pycache__" in relative.parts:
            continue
        if path.is_file() or path.is_symlink():
            result.add(relative.as_posix())
    return result


def validate_file(path: Path, relative: str) -> None:
    value = path.lstat()
    if stat.S_ISLNK(value.st_mode) or not stat.S_ISREG(value.st_mode):
        raise ExportError(f"export path is not a regular file: {relative}")
    contents = path.read_bytes()
    if b"\0" in contents:
        raise ExportError(f"binary file is not allowed: {relative}")
    if contents.startswith(b"version https://git-lfs.github.com/spec/v1"):
        raise ExportError(f"Git LFS pointer is not allowed: {relative}")
    if ABSOLUTE_USER_PATH.search(contents):
        raise ExportError(f"absolute user path found: {relative}")
    for pattern in SECRET_PATTERNS:
        if pattern.search(contents):
            raise ExportError(f"probable credential material found: {relative}")
    try:
        text = contents.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ExportError(f"non-UTF-8 file is not allowed: {relative}") from error
    for raw_url in URL.findall(text):
        host = (urlparse(raw_url.rstrip(".,;:")).hostname or "").lower()
        if host not in ALLOWED_URL_HOSTS:
            raise ExportError(f"unapproved URL host '{host}' in {relative}")


def verify_tree(root: Path, expected: Set[str]) -> None:
    actual = tree_files(root)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ExportError(f"export tree mismatch; missing={missing}, extra={extra}")
    for relative in sorted(actual):
        validate_file(root / relative, relative)


def verify_source_tree(root: Path, expected: Set[str]) -> None:
    actual = tree_files(root)
    if actual == expected:
        verify_tree(root, expected)
        return
    exported = expected | GENERATED
    if actual == exported:
        verify_tree(root, exported)
        return
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    raise ExportError(f"export source mismatch; missing={missing}, extra={extra}")


def source_git_state(root: Path) -> Tuple[str, bool, str]:
    top_level = git(
        root,
        ("rev-parse", "--show-toplevel"),
        accepted=(0, 128),
        label="source repository lookup",
    )
    if top_level.returncode != 0:
        return "uncommitted-export", True, FALLBACK_COMMIT_DATE
    if Path(top_level.stdout.strip()).resolve() != root:
        return "uncommitted-export", True, FALLBACK_COMMIT_DATE
    revision_result = git(
        root,
        ("rev-parse", "--verify", "HEAD"),
        accepted=(0, 128),
        label="source revision lookup",
    )
    if revision_result.returncode != 0:
        return "uncommitted-export", True, FALLBACK_COMMIT_DATE
    revision = revision_result.stdout.strip()
    status = git(
        root,
        ("status", "--porcelain=v1", "--untracked-files=all"),
        label="source status lookup",
    )
    date = git(
        root,
        ("show", "-s", "--format=%cI", "HEAD"),
        label="source commit date lookup",
    ).stdout.strip()
    if not date:
        raise ExportError("source commit has no committer date")
    return revision, bool(status.stdout.strip()), date


def release_metadata_requested(args: argparse.Namespace) -> bool:
    values = (
        args.license_file is not None,
        bool(args.approval_id and args.approval_id.strip()),
        bool(args.copyright_holder and args.copyright_holder.strip()),
    )
    if any(values) and not all(values):
        raise ExportError(
            "license file, approval ID, and copyright holder must be supplied together"
        )
    if not all(values):
        return False
    license_file = args.license_file.expanduser()
    if not license_file.is_file() or license_file.is_symlink():
        raise ExportError("an approved regular license file is required")
    args.license_file = license_file.resolve(strict=True)
    return True


def inspect_source(args: argparse.Namespace) -> SourceState:
    source = args.source.expanduser().resolve()
    if not source.is_dir():
        raise ExportError(f"source directory is missing: {source}")
    entries = load_allowlist(source)
    verify_source_tree(source, set(entries))
    revision, dirty, commit_date = source_git_state(source)
    if dirty and not args.allow_dirty_for_test:
        raise ExportError("source checkout is dirty; export requires a reviewed commit")
    return SourceState(
        root=source,
        entries=entries,
        revision=revision,
        dirty=dirty,
        commit_date=commit_date,
        with_release_metadata=release_metadata_requested(args),
    )


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def generate_metadata(
    destination: Path,
    source: SourceState,
    approval_id: str,
    holder: str,
) -> None:
    provenance = {
        "schema": 1,
        "source_revision": source.revision,
        "approval_id": approval_id,
        "copyright_holder": holder,
        "source_dirty": source.dirty,
        "history_policy": "new-root-no-inherited-objects",
        "files": [
            {
                "path": relative,
                "source_path": relative,
                "source_revision": source.revision,
                "sha256": sha256(destination / relative),
            }
            for relative in source.entries
        ]
        + [
            {
                "path": "LICENSE",
                "source_path": "approved-license-input",
                "source_revision": approval_id,
                "sha256": sha256(destination / "LICENSE"),
            }
        ],
    }
    write_json(destination / "PROVENANCE.json", provenance)

    dependencies = json.loads(
        (destination / "dependencies.json").read_text(encoding="utf-8")
    )
    components = []
    for item in dependencies.get("dependencies", []):
        component = {
            "type": "application",
            "name": item["name"],
            "version": item.get("version", item.get("commit", "external")),
            "licenses": [{"license": {"id": item["license"]}}],
            "properties": [
                {
                    "name": "mins-env:distribution",
                    "value": item.get("distribution", "external"),
                }
            ],
        }
        if item.get("url"):
            component["externalReferences"] = [{"type": "vcs", "url": item["url"]}]
        if item.get("commit"):
            component["properties"].append(
                {"name": "mins-env:commit", "value": item["commit"]}
            )
        components.append(component)
    write_json(
        destination / "SBOM.cdx.json",
        {
            "bomFormat": "CycloneDX",
            "specVersion": "1.5",
            "version": 1,
            "metadata": {"component": {"type": "application", "name": "mins-env"}},
            "components": components,
        },
    )

    checksum_paths = sorted(
        path
        for path in destination.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS"
    )
    (destination / "SHA256SUMS").write_text(
        "".join(
            f"{sha256(path)}  {path.relative_to(destination).as_posix()}\n"
            for path in checksum_paths
        ),
        encoding="utf-8",
    )


def copy_snapshot_files(source: Path, destination: Path, paths: Set[str]) -> None:
    for relative in sorted(paths):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, target)


@contextmanager
def snapshot(
    source: SourceState,
    args: argparse.Namespace,
    *,
    parent: Optional[Path] = None,
) -> Iterator[Path]:
    staging = Path(tempfile.mkdtemp(prefix="mins-env-export-", dir=parent))
    try:
        copy_snapshot_files(source.root, staging, set(source.entries))
        if source.with_release_metadata:
            shutil.copy2(args.license_file, staging / "LICENSE")
            generate_metadata(
                staging,
                source,
                args.approval_id.strip(),
                args.copyright_holder.strip(),
            )
        verify_tree(staging, source.output_paths)
        yield staging
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def export_directory(
    source: SourceState, args: argparse.Namespace, destination: Path
) -> None:
    destination = destination.expanduser().resolve()
    if destination.exists():
        raise ExportError(f"destination already exists: {destination}")
    try:
        destination.relative_to(source.root)
    except ValueError:
        pass
    else:
        raise ExportError("destination must be outside the source tree")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with snapshot(source, args, parent=destination.parent) as staging:
        staging.rename(destination)
    print(f"mins-env exporter: wrote {destination}")


def validate_remote(remote: str) -> str:
    value = remote.strip()
    if not value or value != remote or value.startswith("-"):
        raise ExportError("remote must be a non-empty Git URL or path")
    if any(character in value for character in ("\0", "\r", "\n")):
        raise ExportError("remote contains a control character")
    if value.lower().startswith("ext::"):
        raise ExportError("Git external-command remotes are forbidden")
    parsed = urlparse(value)
    if parsed.scheme.lower() in {"http", "https"} and (
        parsed.username is not None or parsed.password is not None
    ):
        raise ExportError(
            "credential-bearing HTTP remotes are forbidden; use a credential helper"
        )
    return value


def validate_identity(label: str, value: str) -> str:
    result = value.strip()
    if not result or any(character in result for character in ("\0", "\r", "\n")):
        raise ExportError(f"{label} must be a non-empty single line")
    return result


def validate_branch(branch: str) -> str:
    value = branch.strip()
    if not value or value != branch:
        raise ExportError("branch must be non-empty and have no surrounding whitespace")
    run(
        ("git", "check-ref-format", "--branch", value),
        label="branch validation",
    )
    return value


def remote_branch_tip(checkout: Path, branch: str) -> Optional[str]:
    result = git(
        checkout,
        ("ls-remote", "--exit-code", "--heads", "origin", f"refs/heads/{branch}"),
        accepted=(0, 2),
        label="remote branch lookup",
    )
    if result.returncode == 2:
        return None
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        raise ExportError("remote branch lookup returned an unexpected result")
    fields = lines[0].split()
    if len(fields) != 2 or fields[1] != f"refs/heads/{branch}":
        raise ExportError("remote branch lookup returned an unexpected ref")
    return fields[0]


def initialize_checkout(checkout: Path, remote: str, branch: str) -> Optional[str]:
    hooks = checkout.parent / "disabled-hooks"
    hooks.mkdir()
    attributes = checkout.parent / "empty-attributes"
    attributes.touch()
    run(
        (
            "git",
            "-c",
            "init.templateDir=",
            "init",
            "--quiet",
            "--initial-branch",
            branch,
            str(checkout),
        ),
        label="staging checkout initialization",
    )
    git(checkout, ("config", "core.hooksPath", str(hooks)), label="hook isolation")
    git(
        checkout,
        ("config", "core.attributesFile", str(attributes)),
        label="attribute isolation",
    )
    git(checkout, ("config", "core.autocrlf", "false"), label="line-ending isolation")
    git(checkout, ("config", "core.fileMode", "true"), label="file-mode isolation")
    git(checkout, ("config", "commit.gpgSign", "false"), label="signing isolation")
    git(checkout, ("remote", "add", "origin", remote), label="remote setup")

    observed_tip = remote_branch_tip(checkout, branch)
    git(
        checkout,
        ("symbolic-ref", "HEAD", f"refs/heads/{branch}"),
        label="branch setup",
    )
    if observed_tip is None:
        return None
    git(
        checkout,
        (
            "fetch",
            "--quiet",
            "--no-tags",
            "origin",
            f"+refs/heads/{branch}:refs/remotes/origin/{branch}",
        ),
        label="staging branch fetch",
    )
    fetched_tip = git(
        checkout,
        ("rev-parse", "--verify", f"refs/remotes/origin/{branch}"),
        label="fetched branch lookup",
    ).stdout.strip()
    return fetched_tip


def verify_index(checkout: Path, expected: Set[str]) -> None:
    result = git(
        checkout,
        ("ls-files", "--stage", "-z"),
        label="staging index verification",
    )
    records = [entry for entry in result.stdout.split("\0") if entry]
    staged = {}
    for record in records:
        prefix, relative = record.split("\t", 1)
        mode, object_id, stage = prefix.split()
        if stage != "0":
            raise ExportError(f"non-zero staging entry found: {relative}")
        staged[relative] = (mode, object_id)
    actual = set(staged)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ExportError(f"staging index mismatch; missing={missing}, extra={extra}")
    ordered = sorted(expected)
    raw_object_ids = git(
        checkout,
        ("hash-object", "--no-filters", "--stdin-paths"),
        stdin="".join(f"{relative}\n" for relative in ordered),
        label="staged byte verification",
    ).stdout.splitlines()
    if len(raw_object_ids) != len(ordered):
        raise ExportError("staged byte verification returned an unexpected result")
    for relative, raw_object_id in zip(ordered, raw_object_ids):
        mode, object_id = staged[relative]
        expected_mode = (
            "100755" if checkout.joinpath(relative).stat().st_mode & 0o111 else "100644"
        )
        if mode != expected_mode:
            raise ExportError(f"staged executable mode differs: {relative}")
        if object_id != raw_object_id:
            raise ExportError(f"Git clean filter changed staged bytes: {relative}")


def commit_environment(source: SourceState, args: argparse.Namespace) -> dict[str, str]:
    name = validate_identity("author name", args.author_name)
    email = validate_identity("author email", args.author_email)
    result = os.environ.copy()
    result.update(
        {
            "GIT_AUTHOR_NAME": name,
            "GIT_AUTHOR_EMAIL": email,
            "GIT_AUTHOR_DATE": source.commit_date,
            "GIT_COMMITTER_NAME": name,
            "GIT_COMMITTER_EMAIL": email,
            "GIT_COMMITTER_DATE": source.commit_date,
        }
    )
    return result


def publish_remote(source: SourceState, args: argparse.Namespace, remote: str) -> None:
    remote = validate_remote(remote)
    branch = validate_branch(args.branch)
    with snapshot(source, args) as prepared:
        with tempfile.TemporaryDirectory(prefix="mins-env-publish-") as raw_temp:
            temp = Path(raw_temp)
            checkout = temp / "checkout"
            remote_tip = initialize_checkout(checkout, remote, branch)
            copy_snapshot_files(prepared, checkout, source.output_paths)
            verify_tree(checkout, source.output_paths)
            git(
                checkout,
                ("add", "--all", "--force"),
                label="staging index update",
            )
            verify_index(checkout, source.output_paths)
            message = f"Export mins-env {source.revision}"
            git(
                checkout,
                (
                    "-c",
                    "commit.cleanup=verbatim",
                    "-c",
                    "i18n.commitEncoding=UTF-8",
                    "commit",
                    "--quiet",
                    "--no-verify",
                    "--no-gpg-sign",
                    "-m",
                    message,
                ),
                env=commit_environment(source, args),
                label="staging commit creation",
            )
            commit = git(
                checkout,
                ("rev-parse", "--verify", "HEAD"),
                label="staging commit lookup",
            ).stdout.strip()
            clean = git(
                checkout,
                ("status", "--porcelain=v1", "--untracked-files=all"),
                label="committed staging status lookup",
            )
            if clean.stdout.strip():
                raise ExportError("staging checkout is dirty after commit")

            if commit == remote_tip:
                current_tip = remote_branch_tip(checkout, branch)
                if current_tip != commit:
                    raise ExportError("remote branch moved during export; retry")
                print(f"mins-env exporter: {branch} already matches {source.revision}")
                return
            if not args.push:
                print(f"mins-env exporter: prepared {commit} for {branch}; not pushed")
                return
            expected_tip = remote_tip or ""
            git(
                checkout,
                (
                    "push",
                    "--quiet",
                    f"--force-with-lease=refs/heads/{branch}:{expected_tip}",
                    "origin",
                    f"HEAD:refs/heads/{branch}",
                ),
                label="staging push",
            )
            published_tip = remote_branch_tip(checkout, branch)
            if published_tip != commit:
                raise ExportError(
                    "remote branch moved immediately after push; inspect before retrying"
                )
            print(f"mins-env exporter: pushed {commit} to {branch}")


def parser() -> argparse.ArgumentParser:
    root = Path(__file__).resolve().parents[1]
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--source", type=Path, default=root)
    result.add_argument("--destination", type=Path)
    result.add_argument(
        "--remote",
        default=os.environ.get("MINS_ENV_EXPORT_REMOTE"),
        help="staging Git URL/path (or set MINS_ENV_EXPORT_REMOTE)",
    )
    result.add_argument("--branch", default="main")
    result.add_argument(
        "--push",
        action="store_true",
        help="push the prepared commit; without this flag the remote is read-only",
    )
    result.add_argument(
        "--author-name",
        default=os.environ.get("MINS_ENV_EXPORT_AUTHOR_NAME", DEFAULT_AUTHOR_NAME),
    )
    result.add_argument(
        "--author-email",
        default=os.environ.get("MINS_ENV_EXPORT_AUTHOR_EMAIL", DEFAULT_AUTHOR_EMAIL),
    )
    result.add_argument("--license-file", type=Path)
    result.add_argument("--approval-id")
    result.add_argument("--copyright-holder")
    result.add_argument(
        "--allow-dirty-for-test", action="store_true", help=argparse.SUPPRESS
    )
    return result


def export(args: argparse.Namespace) -> None:
    if (args.destination is None) == (args.remote is None):
        raise ExportError("select exactly one of --destination or --remote")
    if args.push and args.remote is None:
        raise ExportError("--push requires --remote")
    source = inspect_source(args)
    if args.destination is not None:
        export_directory(source, args, args.destination)
    else:
        publish_remote(source, args, args.remote)


def main() -> int:
    try:
        export(parser().parse_args())
        return 0
    except (ExportError, OSError, json.JSONDecodeError) as error:
        print(f"mins-env exporter: {error}", file=os.sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
