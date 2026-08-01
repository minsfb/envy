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

A new `MEMORY.md` can start with:

```markdown
# Project Memory
## What This Is
## Architecture
## Key Files
## Test Suite
## Git State
```

## 2. Record Only Durable Context

Capture the goal, current state, important architecture, files changed, exact
validation results, VCS state, unresolved issues, learned constraints, and the
next concrete actions. Exclude secrets, transient narration, and facts already
easy to recover from the repository.

Review the resulting diff. Do not commit solely because this skill ran. If the
user asked to commit the handoff, include it in an appropriate logical commit;
otherwise leave it for review.

## 3. Finish

Report the handoff path and any uncommitted state. Invoke a harness-native
compaction or clear operation only when that capability exists and the user
requested it.
