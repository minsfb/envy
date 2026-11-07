# Feature retirement — requirements

## Overview

Permit trusted packages to declare a feature Deprecated and retire recorded
owned integration on ordinary upgrade without requiring a successor feature.

## Problem statement

Features absent from the composed manifest are retained only as detached state;
their handlers cannot participate in normal upgrades. Enabled-feature upgrade
also skips disabled features with retained integration. Retirement needs an
explicit, durable operation distinct from installation and user-data purging.

## Goals and user stories

- A package maintainer can retain a non-installable cleanup definition.
- An owner can preview, run and retry retirement through normal upgrade.
- An owner can distinguish retired configuration from unresolved cleanup.
- Independent features can be installed without predecessor state dependencies.

## Requirements

| ID | Requirement |
|---|---|
| R1 | Manifest lifecycle distinguishes active and deprecated; missing means active. Deprecation requires a stable revision, structured retire handler, and read-only retirement verification. Reject malformed definitions and dependency/alias/recommendation routes that activate deprecated features. |
| R2 | Fresh installs never select deprecated features. Explicit enable refuses before mutation. Do not run their ordinary converge handlers or contribute their feature environment. |
| R3 | Upgrade considers deprecated features with recorded installation/resource history even when disabled, and detached history when the owning package is attached. Never-installed entries are not cleaned by speculative scans. Scope and explicit selectors are respected. |
| R4 | Persist pending/running/failed/retired progress and exact revision/handler identity without secrets. A handled failure or interruption is retryable; never certify retirement before verification. Repeated completion is a no-op after verification. Source changes during incomplete work refuse unsafe replay. |
| R5 | Invoke only non-purging retirement; handlers remove exact owned integration and preserve user data, shared resources and ambiguous ownership. Core provides the lifecycle, not provider-specific cleanup rules. |
| R6 | Status/verify/dry-run show retirement accurately without invoking mutations. Ordinary authentication or network availability is not a retirement prerequisite. |
| R7 | A retirement has no automatic successor selection or required successor state. An optional replacement note is informational. Partial upgrade selectors do not widen to unrelated retirements. |
| R8 | Bundle detach/attach and uninstall must not accidentally reactivate deprecated definitions or invoke installation as rollback. Concurrent mutations use the existing state lock. |
| R9 | A migrating bundle declares a required manifest capability in a format unsupported engines reject. Unsupported lifecycle semantics must not be silently ignored. |
| R10 | Package records and handlers survive skipped releases; retiring a feature does not delete package source files or credential data. |

## Non-goals

Cross-feature transactions, automatic successor installation, credential
provisioning, user-data purge, remote execution, and automatic rollback of
arbitrary external changes. Do not alter unrelated convergence failures.

## Edge cases and success criteria

Cover fresh/disabled/installed/detached records, explicit selection, failed
cleanup, verification failure, interrupted retries, changed implementations,
platform changes, user uninstall, bundle attach/detach, unknown feature IDs and
repeat upgrades. Passing tests must exercise actual CLI/state transitions using
owned temporary homes and synthetic handler effects; no shared service access.

## Constraints and open questions

Use existing structured handlers, dependency validation and atomic state writes.
No successor dependency graph. Final state schema/handler contract belongs in
DESIGN/CONTRACTS after acceptance cases. Implementation is authorized as part of
the owner's autonomous plan; this document records the approved requirements.
