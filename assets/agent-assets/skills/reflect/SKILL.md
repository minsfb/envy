---
name: reflect
description: >-
  Review a completed work session, identify repeated friction or reusable
  knowledge, and improve existing instructions, skills, scripts, or agents.
  Use for end-of-session reflection, automation review, or deciding what should
  become durable agent guidance.
---
# Reflect On A Work Session

## Phase 1: Session Review

Summarize what was accomplished this session:
- What was the goal?
- What files were created/modified?
- What commands were run repeatedly?
- What research was done? (file reads, searches, API calls)
- What went wrong? (bugs found, wrong approaches, rework needed)
- What went right? (patterns that worked well)

## Phase 2: Pattern Detection

Identify repeated patterns from this session:

1. **Repeated commands** — same shell commands typed more than once (test suites, builds, deploys, validation checks)
2. **Repeated research** — same files read, same searches performed, same context gathered at session start
3. **Repeated decision-making** — same judgment calls made (e.g. "should I parallelize?" "which commit convention?")
4. **Friction points** — where did you get stuck, have to redo work, or waste time?
5. **Knowledge that was hard to find** — gotchas, field name mismatches, config quirks, undocumented behavior

## Phase 3: Coverage Check

Check what automation already exists before creating anything:

- Read repository instruction files and project-local skill roots.
- Inspect the active harness roots, including `.agents/skills`,
  `.claude/skills`, and `.llms/skills` when present.
- Inspect user-level roots such as `~/.agents/skills`, `~/.claude/skills`, and
  their canonical source repositories. Resolve symlinks before editing.
- Check scripts, docs, agents, and existing skills for each repeated pattern.

## Phase 4: Create or Update Automation

For UNCOVERED patterns, propose and create the appropriate artifact:

| Pattern Type | Create This | Location |
|-------------|-------------|----------|
| Stable repository-wide constraints | Instruction file | Repository-native `AGENTS.md` or adapter |
| A reusable multi-step workflow | Skill | Canonical project or personal skill source |
| Deterministic repeated mechanics | Script or command | Owning repository or skill resource |
| Complex delegated role | Agent | Harness-specific agent source |
| One-time session state | Handoff | Repository-approved memory or handoff file |

For EXISTING artifacts that need updating, read the file and apply targeted edits.

Do not edit an installed symlink as though it were the source of truth. Put
cross-project assets in their configured canonical repository, project assets
in the project, and harness-only behavior in a harness-targeted group. Follow
the skill authoring guidance available in the active harness.

## Phase 5: Update MEMORY.md

If durable continuation context is useful, load and follow the `handoff`
workflow. Do not duplicate its implementation or commit memory automatically.

## Phase 6: Report

Present a summary:

**Session:** what was accomplished
**Patterns found:** X repeated patterns identified
**Automation created/updated:** list of new/modified skills, agents
**Coverage:** what % of repeated patterns are now automated
**Remaining gaps:** patterns that couldn't be automated (and why)

Offer a harness-native compaction or handoff only when it is useful and
available.
