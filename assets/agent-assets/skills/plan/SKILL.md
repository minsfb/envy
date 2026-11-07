---
name: plan
description: >-
  Turn established design intent into scoped deliverables, dependency order,
  and a proportionate execution and validation plan. Use before substantial
  implementation or when scope and coordination need revision.
---
# Plan The Work At The Right Scale

Reuse accepted design and existing plans. If outcome or a consequential
architectural choice is still unclear, resolve that part with `design` first.
Do not repeat design discovery for a well-specified change.

## Choose The Process

Assess affected surfaces, dependency coupling, uncertainty, failure impact,
reversibility, duration, external ownership, and validation cost. Line count or
agent availability alone is a poor measure. Choose the smallest process that
can keep the work coherent:

| Work | Useful execution shape |
|---|---|
| Bounded, understood change | Short checklist, one implementer, focused checks and final inspection |
| Several related surfaces or meaningful uncertainty | Explicit increments, integration checkpoints, targeted independent review where it adds evidence |
| Sustained work with multiple owners or dependencies | Workstream map, contract owners, durable checkpoints, optional agent fanout, coordinated integration and final gates |

Risk can warrant deeper review even for a small change. Tight coupling can
warrant serial work even on a large project. Independent QA and architecture
reviews are options, not a mandatory team for every feature.

For substantial new work, present the scope, execution shape, expected resource
use, review depth/cadence, and completion criteria for user alignment. Obtain
approval for material commitments not already authorized. Do not add a new
approval ceremony to ordinary requested implementation or an approved roadmap.

## Break Down Deliverable Work

Define increments around observable behavior and integration boundaries. Record
only the detail needed to execute: inputs, output, acceptance criteria, owner,
dependencies, and checks. A dependency should identify the actual artifact or
contract needed, not merely say another agent must finish.

Choose an integration owner and the order for combining changes. Stabilize
interfaces early where that permits independent progress. Make temporary seams,
compatibility work, migrations, and follow-up cleanup explicit, with owners and
exit conditions. Avoid unowned debt hidden between increments.

Use a chat checklist for small work. Durable design/roadmap documents, issues,
tasks, PRs, or diffs should follow the project's conventions and the user's
requested scope. Tool availability is not authorization to create external
tracking artifacts. Link existing records rather than creating parallel plans.

## Plan Evidence And Cadence

Use `qa` to select checks for affected behavior and dependencies. Run focused
checks during implementation. Schedule full relevant suites and costly device
or interactive regression at stable workstream review points when warranted,
while retaining mandatory repository and release gates. Do not rerun an
unchanged SDK's entire suite for an isolated consumer documentation edit.

Separate agent concurrency from resource concurrency. Record constrained build,
service, or device ownership; avoid several agents starting expensive builds on
the same host. Establish checkpoints where contracts, design drift, validation
coverage, and integration readiness will be checked.

For sustained multi-owner work, hand execution to `coordinate-workstreams`;
otherwise proceed through `implement`. Replan when evidence changes scope,
binding design, external dependencies, material resources, or completion
criteria. Routine sequencing and implementation choices remain autonomous
within the agreed plan. Report consequential changes without halting unrelated
work that remains authorized.
