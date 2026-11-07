# Export, migration and prior-reuse fixtures

Tests generate real recorder evidence through the G2 owned fictional fixture.
No provider is called, no user state is read, and all exports/imports/migrations
remain within an owned temporary directory. The legacy fixture is a copy of
those recorder originals with its graph removed, plus an explicitly supplied
historical ledger. Original requests, receipts and return hashes remain real.

`selection.json` is a small explicit reuse policy example. Exact source and
record IDs are obtained from the tested snapshot. Earlier inspection is retained
as `prior_run`; an import never asserts fresh reading or independent support.

The compatibility ledger retains original source/read/return fields. Pending
graph leads carry `graph_lead_id` and `graph_obligation_id` without invented
targets or plan items. `graph_disposition` and `graph_obligation_state` preserve
their graph state independently of the conservative legacy `pending` label.
`validate_ledger(root, ledger)` verifies the registered manifest, exact generated
ledger and graph snapshot hashes, then returns every lead's question scopes and
work outcomes. Generated flags alone provide no authority. Extra graph
dimensions remain in the full inventories; semantic review is a separate
explicit attestation, never inferred by export.

Exports retain one graph sequence plus an observed external budget head. A
later controller registration event does not change that represented sequence.
Original files not yet normalized remain retained with a limitation instead of
appearing as accepted graph facts. Copies of captures must match both the file
manifest and the graph's original capture hash.

Migration uses a new separate destination and never rewrites the old bundle.
Explicit old inspections stay `legacy_attested`; old read flags alone do not
become inspections. A lead without a known contract question remains an
unscoped historical observation with the exact original text and pending
diagnostics. Prior imports retain selected originals and all declared lineage,
without recursively fetching or importing ancestors. Selection is at most
5,000 IDs and must fit the core expanded packet bounds; oversize requests fail
before acceptance rather than being silently truncated.
