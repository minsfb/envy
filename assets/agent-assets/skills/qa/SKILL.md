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

Before running a command, classify its effective behavior, not its name:

| Mode | Boundary |
|------|----------|
| Isolated | Owned disposable fixtures, synthetic inputs/credentials, and no dependency on existing services or user state. |
| Integration | Multiple real components or an explicitly provisioned test service; record ownership, endpoints, and effects. |
| Live | Existing/shared services, real accounts or credentials, production-like data, or externally visible actions. This includes localhost daemons. |

For each distinct execution boundary, resolve the command/cwd, targets, credential
source categories (not values), expected writes, and applicable user authority.
Inspect the relevant command, configuration precedence, and subprocess paths;
check only relevant environment keys without dumping their values. A localhost
URL, mocked top-level client, tool-disabled model, or claimed agent sandbox does
not establish isolation. A mixed suite inherits its live portion's requirements.

Use the project's supported isolated mode when available. If endpoint resolution,
credential inheritance, or isolation is unknown, investigate without executing
the affected command. If that leaves a live target or operation outside existing
authority, stop that check and ask for the missing scope; continue independent
safe checks. Existing explicit authority need not be requested again. Ordinary
owned-fixture tests do not require a separate approval ceremony. Do not silently
strip credentials, rewrite endpoints, or weaken tests to make them appear safe.
These instructions are a decision workflow, not a sandbox for arbitrary code;
the project must implement and test its own isolation boundary.

Choose checks by affected behavior and dependency boundaries. During editing,
run focused checks that can catch regressions in the current increment. A
consumer change warrants relevant consumer/integration tests; it does not
inherently require every test of an unchanged underlying SDK.

At a stable workstream review point, run the full relevant suite when warranted
and every mandatory repository/CI/release gate. Reserve costly on-device or
interactive regression for that final stage, selecting a smaller safe scope
where supported. Broaden earlier for a concrete diagnostic reason or changed
risk, not automatically after each edit or subagent handoff. Reuse prior evidence
only when its source, inputs, environment, and scope still support the claim.
Serialize expensive builds and exclusive devices according to the work plan.
Preserve useful redacted failure evidence. Unexpected live traffic, secret output,
or writes outside the agreed targets stop the affected check; report what happened
and do not retry until the cause and authority are resolved.

For changed-file validation:

- Use `node --check` only for JavaScript files it can parse (`.js`, `.mjs`,
  `.cjs`). Validate TypeScript with the project's compiler or linter.
- For Python syntax only, resolve `scripts/check-python-syntax.py` against this
  skill's directory and run `python3 -I -B /resolved/helper/path FILE.py ...`
  from the project cwd, so input paths retain their meaning. It compiles source
  in memory without importing/executing it or writing bytecode. It reports JSON
  file/line/column/error-category records without source lines or exception text;
  exit 0 means all checked files compiled, 1 means a file failed, 2 means usage
  error, and 130 means interrupted. It uses the selected interpreter's grammar,
  suppresses source-bearing compiler warnings, and is not a lint/runtime/type
  check. `py_compile` writes bytecode even with
  `-B`; do not use it for a non-writing syntax check.
- Parse JSON with a JSON parser and shell with the appropriate shell's syntax
  mode.
- Use a configured HTML parser or linter. Do not infer validity by counting
  opening and closing tags.
- If no reliable validator is available, report that the check was skipped;
  do not label it passed.

Prefer non-writing parser/check modes. If a required compiler/framework generates
outputs or caches, identify their paths and keep them in an authorized owned test
root where supported. Do not claim mutation-free validation or delete user caches
when a tool cannot avoid writing. Syntax checks do not prove behavioral correctness.

Include all relevant tracked and untracked changes when determining the
changed surface, including staged content in VCSs that have a staging area.
Inspect these through the repository's native workflow without modifying them.

## Report

Report the tested revision and any uncommitted surface, then concise evidence:

| Command / cwd | Mode / targets | Evidence | Exit / result | Limits / writes |
|---------------|----------------|----------|---------------|-----------------|
| Exact command, with secret arguments never included | isolated, integration, live, or unresolved | executed here, supplied log, or recommended only | pass, fail, skipped, blocked, or interrupted; exit when observed | actual assertions, skips, generated paths, unknowns |

List failures and blockers first with safe file/line diagnostics. Report supplied
logs as supplied, not as a check you ran; recommendations are not results. A
reviewer's account of running a check is reviewer-reported evidence; adapter-observed
execution requires captured execution evidence, not that self-report or label.
Keep this provenance distinction when handing checks to an independent review.
For this handoff, use the review schema's
[blocked/unresolved/recommended-only mapping](../review/references/review-bundle.md#results-and-import);
do not guess an execution mode or claim unperformed work was executed.
A tool exit 0 that reports skipped checks is not evidence those checks passed. An
interrupted run is incomplete, not a pass. State why unavailable checks were not
run, and keep synthetic secrets, source excerpts containing credentials, full
environments/configurations, and raw auth errors out of the report.
