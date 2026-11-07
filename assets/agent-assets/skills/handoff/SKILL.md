---
name: handoff
description: >-
  Preserve concise project and session state for a later continuation. Use when
  the user asks for a handoff, wants to resume work in another session, or needs
  durable context before clearing or transferring a conversation.
---
# Prepare A Session Handoff

## 1. Resolve The Durable Location

Follow the repository's existing handoff or memory convention. If none exists,
use `MEMORY.md` in the project root only when it is appropriate for that
repository. Otherwise create a clearly named handoff document approved by the
user.

Use [the handoff template](assets/handoff-template.md) when there is no suitable
existing structure. Adapt it; omit fields that do not apply rather than creating
a new memory system or migrating unrelated documentation.

Choose storage for the intended audience. Keep sensitive context in
repository-approved restricted storage, ignored by VCS where appropriate.
Ignoring a file prevents accidental tracking; it is not access control. Exclude
credentials and raw private conversations even from an ignored handoff.

## 2. Put Current State Before History

Start with the objective, what is true now, completed versus pending work,
blockers or uncertainty, and the next concrete authorized action. Record the
user-granted scope and its limits; a past plan or approval is not new authority.
On continuation, recheck relevant live state and newer user instructions before
acting on the handoff.

For repository work, identify the repository, worktree, branch and exact current
revision. Distinguish committed-but-local work and uncommitted changes from
merged or deployed work; record a deployed revision only when relevant and
verified, otherwise mark it unknown. Attribute dirty changes to their owner
when known, without stashing, rewriting or cleaning them to prepare the handoff.

For a coordinated workstream, include the accepted design/plan references,
current phase, binding decisions versus assumptions, integration owner,
dependency checkpoints, active worker/resource ownership, and the next bounded
work packets. Reconcile in-flight work before launching replacement workers.
Use the existing current-state record; do not introduce a second roadmap.

Keep decision rationale, superseded plans and archived execution notes separate
from the current view. Refresh the current view without deleting historical
evidence. Link rather than repeat history or readily recoverable architecture.
For shelved work, record its repository, branch and exact commit (or other
recoverable local reference), design reference, status and resume criteria.
Say when a reference is local-only or unavailable; a branch name alone can move.

## 3. Preserve Evidence Needed To Continue

For required verification, record the command, working directory, tested
revision and relevant dirty state, actual result and limitations. Reference a
durable result or retain a concise sanitized summary in the approved handoff.
Include capture time, sensitivity/audience and retention constraints when they
affect reuse; follow project policy rather than inventing an expiry or cleanup
schedule.

A temporary path may be a convenience pointer, not the sole required record.
If it has expired and no durable record exists, mark the evidence unavailable
and name the authorized recheck or question needed. Do not reconstruct a pass
from memory, copy raw history indiscriminately, or publish sensitive artifacts
to make a reference durable.

## 4. Check Completeness And Finish

Read just the current view and its essential references as a new session would:

- Can it distinguish verified current state from history, local work and
  unknowns, using exact versions where applicable?
- Is the next action within recorded authority, with blockers and stop points
  explicit?
- Will the required evidence and shelved-work references remain usable without
  temporary files or the old conversation? Are access or retention limits clear?

Review the diff for unintended changes and sensitive content. Do not commit,
publish, delete history or clean worktrees solely because this skill ran. If the
user separately asked to commit the handoff, include it in an appropriate
logical commit; otherwise leave it for review.

Report the handoff path and any uncommitted state. Invoke a harness-native
compaction or clear operation only when that capability exists and the user
requested it.
