#!/usr/bin/env python3
"""Prepare offline operator inputs; this does not run or grade an agent."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[3]
SKILL = "assets/agent-assets/skills/reflect/SKILL.md"
REPOSITORIES = ("portable-tools", "product", "site-bundle", "deployment")
REPOSITORY_ROUTING_VARIABLES = {
    "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE",
}


def fixture_git_executable() -> str:
    # Reject repository redirection before creating fixture content. Do not
    # print inherited values or discard unrelated security/transport settings.
    if REPOSITORY_ROUTING_VARIABLES.intersection(os.environ):
        raise RuntimeError("refusing fixture with repository-routing Git variables set")
    executable = shutil.which("git")
    if executable is None:
        raise RuntimeError("fixture requires Git on the caller's PATH")
    # Anchor relative PATH entries before changing cwd, but retain a selected
    # symlink's name for dispatchers that inspect argv[0].
    return str(Path(executable).absolute())


def git_output(directory: Path, *args: str) -> str:
    environment = {
        **os.environ,
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Fixture",
        "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
        "GIT_COMMITTER_NAME": "Fixture",
        "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    }
    try:
        result = subprocess.run(
            [fixture_git_executable(), *args], cwd=directory, env=environment,
            text=True, capture_output=True, check=False, timeout=30,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("fixture Git command timed out") from None
    if result.returncode:
        # Arbitrary Git/wrapper stderr can contain credentials or config values.
        raise RuntimeError(f"fixture Git command failed (exit {result.returncode})")
    return result.stdout


def git(directory: Path, *args: str) -> str:
    # Bind the owned repository and worktree even when a config channel supplies
    # core.worktree. Empty owned attribute/exclude files keep content filters
    # and ignores out of the fixture; retain unrelated inherited configuration.
    return git_output(
        directory,
        "--git-dir", str(directory / ".git"),
        "--work-tree", str(directory),
        "-c", "user.name=Fixture",
        "-c", "user.email=fixture@example.invalid",
        "-c", "commit.gpgSign=false",
        "-c", f"core.hooksPath={directory / '.git/fixture-hooks'}",
        "-c", "core.fsmonitor=false",
        "-c", "core.ignoreStat=false",
        "-c", f"core.excludesFile={directory / '.git/fixture-excludes'}",
        "-c", f"core.attributesFile={directory / '.git/fixture-attributes'}",
        *args,
    )


def prepare(root: Path, skill: str) -> None:
    """Populate a newly created empty root only; callers own its lifetime."""
    fixture_git_executable()
    if not root.is_dir() or any(root.iterdir()):
        raise ValueError("fixture root must be an existing empty directory")
    fixture = json.loads(Path(__file__).with_name("workspace.json").read_text())
    files = dict(fixture["files"])
    files["portable-tools/assets/skills/reflect/SKILL.md"] = skill
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    for repository in REPOSITORIES:
        directory = root / repository
        git(directory, "init", "-q", "--template=")
        for name in ("fixture-excludes", "fixture-attributes"):
            (directory / ".git" / name).write_text("", encoding="utf-8")
        git(directory, "add", ".")
        git(directory, "commit", "-q", "-m", "Synthetic fixture baseline")
    for relative, content in fixture["dirty_files"].items():
        (root / relative).write_text(content, encoding="utf-8")
    for harness in (".claude", ".agents"):
        directory = root / "home" / harness / "skills"
        directory.mkdir(parents=True)
        for name in ("reflect", "verify-work"):
            (directory / name).symlink_to(
                root / "portable-tools" / "assets" / "skills" / name,
                target_is_directory=True,
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", help="full baseline commit; default: working tree")
    args = parser.parse_args()
    fixture_git_executable()  # Fail before allocating even an empty temp root.
    if args.revision:
        if not re.fullmatch(r"[0-9a-f]{40}", args.revision):
            parser.error("--revision must be a full lowercase commit ID")
        # Read the source checkout (which may itself be a linked worktree).
        # This command does not initialize or write repository state.
        skill = git_output(ROOT, "--no-optional-locks", "show", f"{args.revision}:{SKILL}")
    else:
        skill = (ROOT / SKILL).read_text(encoding="utf-8")
    root = Path(tempfile.mkdtemp(prefix="reflect-eval-")).resolve()
    prepare(root, skill)
    print(json.dumps({
        "root": str(root),
        "skill_revision": args.revision or "working-tree (record its commit and diff)",
        "claude_skill": str(root / "home/.claude/skills/reflect/SKILL.md"),
        "codex_skill": str(root / "home/.agents/skills/reflect/SKILL.md"),
    }, indent=2))


if __name__ == "__main__":
    main()
