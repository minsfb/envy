---
name: ship
description: >-
  Validate completed changes, create logically atomic commits, and push them
  with the repository's native version-control workflow. Use when the user asks
  to ship, commit and push, finalize changes, or publish completed work.
---
# Ship Changes

## 1. Validate

Load and follow the available QA workflow for this harness and repository. Do
not assume a literal `/qa` command exists. Stop before committing if required
checks fail, unless the user explicitly accepts the failure.

## 2. Review

Read repository instructions and detect its VCS rather than defaulting across
an unknown directory. Inspect status and diffs, including staged changes. Do
not discard, rewrite, or include unrelated user changes.

## 3. Commit

Group only the intended changes into logical commits. Follow the repository's
message convention; use conventional commit prefixes only when the repository
does. Prefer an imperative subject without a trailing period.

Use non-interactive VCS commands. Never use destructive reset or checkout to
make the worktree look clean.

## 4. Push And Report

Push through the configured remote. If access fails, report the error without
rewriting remotes or credentials unless the user explicitly requests recovery.
Report commit hashes, subjects, validation results, and push status.
