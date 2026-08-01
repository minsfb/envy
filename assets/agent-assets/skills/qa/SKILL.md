---
name: qa
description: >-
  Discover and run project-appropriate tests, linters, type checks, and syntax
  validation. Use when asked to validate changes, run QA, check a project before
  committing, or determine the repository's required verification commands.
---
# Project QA

Run the repository's own validation rather than inventing a generic test suite.

## Discover

1. Find the project root from its VCS metadata or build manifest.
2. Read repository instruction files and contributor documentation.
3. Look for a project-specific QA skill under `.agents/skills`,
   `.claude/skills`, or `.llms/skills`. Do not recursively invoke this same
   generic skill.
4. Prefer explicit commands in CI configuration, `Makefile`, task runners, and
   package scripts. Match the checked-in package manager and lockfile.
5. Do not install dependencies or change lockfiles merely to run QA without
   user approval.

Common signals include:

- JavaScript/TypeScript: test, lint, typecheck, and build scripts in
  `package.json`.
- Python: `pyproject.toml`, `tox.ini`, `pytest.ini`, or `unittest` suites.
- Rust: `cargo test`, `cargo check`, and configured formatting/lint checks.
- Shell: repository test scripts, `bash -n`, and ShellCheck when configured.
- Other stacks: use their checked-in task runner or CI commands.

## Validate

Run the narrowest required checks first, then broaden to the repository's full
required suite. Preserve the first useful failure output.

For changed-file validation:

- Use `node --check` only for JavaScript files it can parse (`.js`, `.mjs`,
  `.cjs`). Validate TypeScript with the project's compiler or linter.
- Compile Python files with `py_compile` when no stronger project check covers
  them.
- Parse JSON with a JSON parser and shell with the appropriate shell's syntax
  mode.
- Use a configured HTML parser or linter. Do not infer validity by counting
  opening and closing tags.
- If no reliable validator is available, report that the check was skipped;
  do not label it passed.

Include staged, unstaged, and untracked files when determining the changed
surface, without modifying them.

## Report

Output a concise summary:

| Check | Status | Details |
|-------|--------|---------|
| Tests | pass/fail/skipped | command and result |
| Lint/types | pass/fail/skipped | command and result |
| Changed files | pass/fail/skipped | files and validators |

List failures first with file and line references where the tool provides them.
State any check that could not be run and why.
