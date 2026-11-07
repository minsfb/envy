# Feature retirement — contracts

## Manifest

Bundles using this contract set version 2 and requires containing
feature-retirement-v1. Unknown capabilities refuse. Existing schema-1 bundles
remain supported but cannot declare lifecycle deprecated. Core sources with
deprecated definitions use version 4; composed output remains version 3.

Feature fields:

- lifecycle: active (default) or deprecated.
- retirement_revision: nonempty stable printable identifier.
- retire: structured run/args handler, package-contained existing file.
- verify_retired: structured run/args read-only handler, likewise contained.
- retirement_inputs: optional list of supporting package-relative regular files
  hashed for incomplete-operation identity. No directories or escaping symlinks.
- replacement: optional informational feature name; no activation semantics.

Deprecated definitions cannot declare converge, remove, verify, doctor, env,
nonempty depends_on, default_enabled true, reconcile_after_selection_change true,
or tier core. Recommendations and aliases cannot select them; active features
cannot depend on them. Use the explicit retirement entry points only.

## State and commands

Deprecated records are disabled and expose lifecycle=deprecated. Installed
history adds retirement containing status (pending/running/failed/retired),
revision, identity and completion/error metadata. Only actual handlers establish
completion. Data/ownership are retained for safe cleanup. Unknown origin refuses.

upgrade applies selected eligible retirement, including disabled historical
features. --only/--except do not widen. --force permits rechecking but does not
bypass identity or safety checks. status and verify show pending or verified
retirement without executing cleanup. install --enable refuses deprecated IDs.
Uninstall retirement is always non-purging. Bundle detach requires outstanding
retirement to be completed first; attach never installs deprecated definitions.

Retirement handlers inherit public package/feature context, ENVY_PURGE=false,
ENVY_DRY_RUN=0, and no broad cleanup authority. Exit 0 means retire succeeded,
but verify_retired must also exit 0. Any other status is incomplete and causes
upgrade failure. No missing-handler-is-success behavior applies to retirement.
Interrupted effects are not rolled back; idempotent retry is the contract.
