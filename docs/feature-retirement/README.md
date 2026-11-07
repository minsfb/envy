# Deprecated features

Deprecation retires an installed feature's owned integration without deleting
user data or enabling a replacement. A retained definition is a migration record,
not an alias to another feature. Keep it available for hosts that skip releases.

Bundle declarations use schema 2 and `requires: ["feature-retirement-v1"]` so older
cores refuse before changing state. Core declarations use schema 4. Ordinary
bundle schema 1 and core schema 3 remain supported; composed manifests and state
remain schema 3.

```json
{
  "description": "Retained cleanup for an old integration",
  "tier": "optional",
  "lifecycle": "deprecated",
  "retirement_revision": "1",
  "scope": {"global": true},
  "retire": {"run": "features/retire-old.py", "args": ["retire"]},
  "verify_retired": {"run": "features/retire-old.py", "args": ["verify"]},
  "retirement_inputs": ["lib/retirement-support.py"]
}
```

Retained handlers must be idempotent, bounded and noninteractive. They own only
exact resources whose ownership they can establish; keep modified or ambiguous
resources and fail with actionable documentation rather than deleting them.
Credentials, recovery material and shared tools are never implied cleanup targets.
Declare every supporting implementation file in `retirement_inputs`.
Optional `replacement` is informational; it never selects or depends on another
feature. Deprecated declarations cannot provide active handlers, dependencies,
environment exports, defaults, recommendations or aliases.

`envy upgrade` includes eligible deprecated definitions even when disabled if
installation history/ownership remains. `--only`, `--except`, platform scope and
`--dry-run` apply. A fresh host does not run cleanup or permit enablement. Status
shows lifecycle and retirement progress. Verify checks cleanup without running it.
An uninstall also uses non-purging retirement, including with `--purge`.

Each retirement is verified after cleanup. Failed/interrupted operations remain
retryable; retry must use the same pinned implementation. Restore the recorded
revision before retrying if its files changed. Completed retirement is rechecked
on later upgrades and can adopt a new cleanup revision. Handled interruption
terminates owned handler processes; SIGKILL/power loss cannot guarantee cleanup.
Handlers have a 60-second deadline and raw output is suppressed to protect data.

Complete outstanding retirement before detaching its bundle. Detach/attach must
not reinstall a retired feature or use an irreversible cleanup as rollback.
An independently enabled successor converges normally, with no required ordering
or cross-feature handoff. Its credential availability is a separate concern.
