# Feature retirement — design

## Architecture and file references

| Area | Location | Responsibility |
|---|---|---|
| Manifest | lib/manifest.py | Version/capability validation and structured retirement handlers |
| State | lib/state.py, lib/retirement.py | Eligibility, progress, implementation pin, retirement execution |
| Lifecycle | bin/envy, lib/features.sh | Selection, dispatch, verification and uninstall/detach boundaries |
| Consumers | lib/render_env.py, lib/status.py, lib/bundles.py | Exclude installation behavior; retain visible history |
| Tests | tests/test_retirement.py | Actual isolated CLI plus strict manifest acceptance |

Bundle schema 2 adds required capability negotiation. Schema 1 cannot declare
deprecation; engines without schema-2 support refuse before any handler executes.
Resolved manifests remain version 3. Core manifests declaring retirement require
source schema 4 (accepted alongside 3), preventing silent use by prior engines.

State keeps schema 3 with additive feature lifecycle/retirement fields. Before
disabling a deprecated feature, retain eligibility from enabled state, prior
convergence or recorded feature data/ownership. Fresh disabled records remain
not-installed and no handler scans them. Restored detached records use the same
normalization. Origin mismatches refuse cleanup. A deprecated feature never
contributes generated environment or participates in ordinary dependency setup.

Retirement execution is independent of ordinary convergence. The CLI selects
eligible deprecated records under the same explicit scope/selector rules, then
calls lib/retirement.py under the existing lifecycle state lock. The runner pins
the feature definition, retire/verify source files and declared supporting files.
Pending work gets its pin before executing. Incomplete work with a different pin
refuses replay; restore the recorded implementation before retry. Completed work
can accept a subsequent revision as another idempotent cleanup pass.

The runner persists running, invokes retire with ENVY_PURGE=false and dry-run
disabled, then checks verify_retired and implementation identity before certifying
retired. Children have a deadline and owned process group; handled interruption
stops the group before returning. Failure records a fixed diagnostic and retains
retry eligibility. A retired record is reverified; drift schedules another pass.
Never persist handler output, credentials or rollback payloads. Package handlers
are trusted and must not access credentials during retirement.

Uninstall uses the same retirement route; purge cannot broaden the retirement
handler's authority. Bundle detach refuses outstanding eligible retirement with
instructions to upgrade it first; it does not attempt converge-based rollback.
Attach restores history but does not force deprecated features to converge.
The subsequent upgrade handles their pending retirement independently.

Status and verify never execute retire; existing state normalization/verification
metadata behavior remains. Dry run operates on temporary state and executes
neither retire nor verification with installation effects. No successor is
enabled or configured. Shared-resource ownership remains each handler's concern.

## Extension and verification

Declare deprecated lifecycle, a revision and two package-contained handlers;
declare imported helper files as retirement_inputs. Remove active dependency,
recommendation and alias routes. Add exact-owned-resource fixture tests and retain
the definition for skipped-release upgrades. Current acceptance initially fails
all ten tests because source schema 2 is unsupported; implement then broaden
coverage to bundle transitions, signals, scope, drift and consumer rendering.
