-- Retrospective research graph v1. Requires SQLite 3.38+ (built-in JSON).
-- The caller enables foreign_keys, WAL, synchronous=FULL and busy_timeout.
-- Execute the migration only into an empty database, then bootstrap in one
-- transaction as described in references/graph-contract.md. No provider calls.
PRAGMA user_version = 1;

CREATE TABLE schema_migrations (
    schema_version INTEGER PRIMARY KEY CHECK (schema_version > 0),
    migration_id TEXT NOT NULL UNIQUE,
    migration_sha256 TEXT NOT NULL CHECK (length(migration_sha256) = 64 AND migration_sha256 NOT GLOB '*[^0-9a-f]*'),
    applied_at TEXT NOT NULL
);

CREATE TABLE graph_metadata (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    run_id TEXT NOT NULL UNIQUE,
    schema_version INTEGER NOT NULL REFERENCES schema_migrations(schema_version),
    initial_contract_sha256 TEXT NOT NULL REFERENCES contract_snapshots(contract_sha256) DEFERRABLE INITIALLY DEFERRED,
    created_at TEXT NOT NULL
);

CREATE TABLE actors (
    actor_id TEXT PRIMARY KEY NOT NULL,
    parent_actor_id TEXT REFERENCES actors(actor_id),
    role TEXT NOT NULL CHECK (role IN ('coordinator', 'searcher', 'reader', 'coverage_reviewer', 'evidence_reviewer', 'research_lead')),
    declared_runtime_identity TEXT,
    branch_scope TEXT,
    stage_scope TEXT CHECK (stage_scope IN ('baseline', 'priority', 'deep')),
    registration_batch_id TEXT NOT NULL REFERENCES ingest_batches(batch_id) DEFERRABLE INITIALLY DEFERRED,
    CHECK (parent_actor_id IS NULL OR parent_actor_id <> actor_id),
    CHECK ((role = 'coordinator') = (parent_actor_id IS NULL))
);
CREATE UNIQUE INDEX one_root_coordinator ON actors(role) WHERE role = 'coordinator' AND parent_actor_id IS NULL;

CREATE TABLE ingest_batches (
    batch_id TEXT PRIMARY KEY NOT NULL,
    actor_id TEXT NOT NULL REFERENCES actors(actor_id) DEFERRABLE INITIALLY DEFERRED,
    assignment_id TEXT REFERENCES assignments(assignment_id) DEFERRABLE INITIALLY DEFERRED,
    packet_kind TEXT NOT NULL CHECK (packet_kind IN ('bootstrap', 'observe', 'reconcile', 'import', 'migrate')),
    payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256) = 64 AND payload_sha256 NOT GLOB '*[^0-9a-f]*'),
    packet_path TEXT NOT NULL UNIQUE,
    accepted_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL CHECK (schema_version = 1),
    resulting_sequence INTEGER NOT NULL UNIQUE CHECK (resulting_sequence > 0),
    FOREIGN KEY (resulting_sequence, batch_id) REFERENCES graph_events(sequence, batch_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE graph_events (
    sequence INTEGER PRIMARY KEY CHECK (sequence > 0),
    batch_id TEXT NOT NULL REFERENCES ingest_batches(batch_id) DEFERRABLE INITIALLY DEFERRED,
    event_kind TEXT NOT NULL CHECK (event_kind IN ('bootstrap', 'create', 'revise', 'reconcile', 'claim', 'release', 'complete', 'bound', 'import', 'export')),
    entity_kind TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    prior_revision INTEGER CHECK (prior_revision >= 1),
    next_revision INTEGER NOT NULL CHECK (next_revision >= 1),
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json) AND json_type(payload_json) = 'object'),
    UNIQUE (sequence, batch_id),
    CHECK ((prior_revision IS NULL AND next_revision = 1) OR (prior_revision IS NOT NULL AND next_revision = prior_revision + 1))
);
CREATE INDEX events_by_batch ON graph_events(batch_id, sequence);
CREATE INDEX events_by_entity ON graph_events(entity_kind, entity_id, sequence);

CREATE TABLE contract_snapshots (
    contract_sha256 TEXT PRIMARY KEY NOT NULL CHECK (length(contract_sha256) = 64 AND contract_sha256 NOT GLOB '*[^0-9a-f]*'),
    contract_revision INTEGER NOT NULL UNIQUE CHECK (contract_revision >= 1),
    relative_path TEXT NOT NULL UNIQUE,
    created_batch_id TEXT NOT NULL REFERENCES ingest_batches(batch_id) DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE plan_snapshots (
    plan_sha256 TEXT PRIMARY KEY NOT NULL CHECK (length(plan_sha256) = 64 AND plan_sha256 NOT GLOB '*[^0-9a-f]*'),
    contract_sha256 TEXT NOT NULL REFERENCES contract_snapshots(contract_sha256),
    relative_path TEXT NOT NULL UNIQUE,
    created_batch_id TEXT NOT NULL REFERENCES ingest_batches(batch_id) DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE plan_items (
    plan_sha256 TEXT NOT NULL REFERENCES plan_snapshots(plan_sha256),
    plan_item_id TEXT NOT NULL,
    PRIMARY KEY (plan_sha256, plan_item_id)
);

CREATE TABLE artifacts (
    artifact_id TEXT PRIMARY KEY NOT NULL,
    title TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    published_at TEXT,
    canonical_artifact_id TEXT REFERENCES artifacts(artifact_id),
    canonical_basis TEXT,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_batch_id TEXT NOT NULL REFERENCES ingest_batches(batch_id),
    updated_batch_id TEXT NOT NULL REFERENCES ingest_batches(batch_id),
    CHECK (canonical_artifact_id IS NULL OR (canonical_artifact_id <> artifact_id AND COALESCE(length(trim(canonical_basis)), 0) > 0))
);
CREATE TABLE artifact_keys (
    namespace TEXT NOT NULL CHECK (length(trim(namespace)) > 0),
    external_id TEXT NOT NULL CHECK (length(trim(external_id)) > 0),
    artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
    basis TEXT NOT NULL CHECK (length(trim(basis)) > 0),
    created_batch_id TEXT NOT NULL REFERENCES ingest_batches(batch_id),
    PRIMARY KEY (namespace, external_id)
);
CREATE INDEX keys_by_artifact ON artifact_keys(artifact_id);
CREATE TABLE artifact_versions (
    version_id TEXT PRIMARY KEY NOT NULL,
    artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
    provider_revision TEXT CHECK (provider_revision IS NULL OR length(provider_revision) > 0),
    modified_at TEXT,
    first_observed_at TEXT NOT NULL,
    version_basis TEXT NOT NULL CHECK (length(trim(version_basis)) > 0),
    created_batch_id TEXT NOT NULL REFERENCES ingest_batches(batch_id),
    UNIQUE (version_id, artifact_id)
);
CREATE UNIQUE INDEX known_provider_revision ON artifact_versions(artifact_id, provider_revision) WHERE provider_revision IS NOT NULL;

CREATE TABLE questions (
    question_id TEXT PRIMARY KEY NOT NULL,
    text TEXT NOT NULL CHECK (length(trim(text)) > 0),
    central INTEGER NOT NULL CHECK (central IN (0, 1)),
    contract_revision INTEGER NOT NULL REFERENCES contract_snapshots(contract_revision),
    current_status TEXT NOT NULL DEFAULT 'open' CHECK (current_status IN ('open', 'answered', 'bounded', 'reopened')),
    reason TEXT,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_batch_id TEXT NOT NULL REFERENCES ingest_batches(batch_id),
    updated_batch_id TEXT NOT NULL REFERENCES ingest_batches(batch_id),
    CHECK (current_status NOT IN ('answered', 'bounded') OR COALESCE(length(trim(reason)), 0) > 0)
);

CREATE TABLE work_items (
    work_id TEXT PRIMARY KEY NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('search', 'read', 'inspect_visuals', 'review', 'reconcile')),
    artifact_id TEXT REFERENCES artifacts(artifact_id),
    question_id TEXT NOT NULL REFERENCES questions(question_id),
    request_json TEXT NOT NULL CHECK (json_valid(request_json) AND json_type(request_json) = 'object'),
    logical_key TEXT NOT NULL UNIQUE CHECK (length(trim(logical_key)) > 0),
    required_scope_key TEXT,
    format_policy_sha256 TEXT CHECK (format_policy_sha256 IS NULL OR (length(format_policy_sha256) = 64 AND format_policy_sha256 NOT GLOB '*[^0-9a-f]*')),
    purpose TEXT NOT NULL CHECK (length(trim(purpose)) > 0),
    stage TEXT NOT NULL CHECK (stage IN ('baseline', 'priority', 'deep')),
    branch TEXT NOT NULL CHECK (length(trim(branch)) > 0),
    generation INTEGER NOT NULL CHECK (generation >= 0),
    priority INTEGER NOT NULL DEFAULT 50 CHECK (priority BETWEEN 0 AND 100),
    state TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending', 'leased', 'running', 'completed', 'blocked', 'deferred', 'cancelled')),
    terminal_reason TEXT,
    transition_reason TEXT NOT NULL CHECK (length(trim(transition_reason)) > 0),
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_batch_id TEXT NOT NULL REFERENCES ingest_batches(batch_id),
    updated_batch_id TEXT NOT NULL REFERENCES ingest_batches(batch_id),
    CHECK (kind NOT IN ('read', 'inspect_visuals') OR (artifact_id IS NOT NULL AND COALESCE(length(required_scope_key), 0) > 0 AND format_policy_sha256 IS NOT NULL)),
    CHECK ((state IN ('completed', 'blocked', 'deferred', 'cancelled') AND COALESCE(length(trim(terminal_reason)), 0) > 0) OR (state IN ('pending', 'leased', 'running') AND terminal_reason IS NULL))
);
CREATE INDEX pending_work_by_wave ON work_items(state, stage, generation, branch, priority DESC, work_id);
CREATE INDEX work_by_artifact_question ON work_items(artifact_id, question_id, state);

CREATE TABLE assignments (
    assignment_id TEXT PRIMARY KEY NOT NULL,
    work_id TEXT NOT NULL REFERENCES work_items(work_id),
    actor_id TEXT NOT NULL REFERENCES actors(actor_id),
    parent_assignment_id TEXT REFERENCES assignments(assignment_id),
    claimed_at TEXT NOT NULL,
    lease_expires_at TEXT NOT NULL,
    finished_at TEXT,
    state TEXT NOT NULL DEFAULT 'leased' CHECK (state IN ('leased', 'running', 'reconciling', 'completed', 'released', 'cancelled')),
    generation_at_claim INTEGER NOT NULL CHECK (generation_at_claim >= 0),
    plan_item_id TEXT NOT NULL,
    plan_sha256 TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_batch_id TEXT NOT NULL REFERENCES ingest_batches(batch_id),
    updated_batch_id TEXT NOT NULL REFERENCES ingest_batches(batch_id),
    FOREIGN KEY (plan_sha256, plan_item_id) REFERENCES plan_items(plan_sha256, plan_item_id),
    CHECK (COALESCE(julianday(lease_expires_at) > julianday(claimed_at), 0)),
    CHECK ((state IN ('leased', 'running', 'reconciling') AND finished_at IS NULL) OR (state IN ('completed', 'released', 'cancelled') AND finished_at IS NOT NULL)),
    CHECK (finished_at IS NULL OR COALESCE(julianday(finished_at) >= julianday(claimed_at), 0)),
    CHECK (parent_assignment_id IS NULL OR parent_assignment_id <> assignment_id)
);
CREATE UNIQUE INDEX one_active_assignment ON assignments(work_id) WHERE state IN ('leased', 'running', 'reconciling');
CREATE INDEX assignments_by_actor ON assignments(actor_id, state);

CREATE TABLE operation_links (
    operation_id TEXT PRIMARY KEY NOT NULL,
    assignment_id TEXT REFERENCES assignments(assignment_id),
    budget_event_id TEXT,
    request_sha256 TEXT NOT NULL CHECK (length(request_sha256) = 64 AND request_sha256 NOT GLOB '*[^0-9a-f]*'),
    receipt_sha256 TEXT CHECK (receipt_sha256 IS NULL OR (length(receipt_sha256) = 64 AND receipt_sha256 NOT GLOB '*[^0-9a-f]*')),
    execution_state TEXT NOT NULL CHECK (execution_state IN ('reserved', 'admitted', 'dispatched', 'succeeded', 'failed', 'interrupted', 'cancelled', 'unknown')),
    verification_state TEXT NOT NULL CHECK (verification_state IN ('pending', 'verified', 'legacy_attested')),
    return_id TEXT REFERENCES reader_returns(return_id) DEFERRABLE INITIALLY DEFERRED,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_batch_id TEXT NOT NULL REFERENCES ingest_batches(batch_id),
    updated_batch_id TEXT NOT NULL REFERENCES ingest_batches(batch_id),
    CHECK (verification_state <> 'verified' OR (assignment_id IS NOT NULL AND budget_event_id IS NOT NULL)),
    CHECK (verification_state <> 'verified' OR execution_state NOT IN ('succeeded', 'failed') OR receipt_sha256 IS NOT NULL)
);
CREATE INDEX operations_by_assignment ON operation_links(assignment_id, execution_state);

CREATE TABLE captures (
    capture_id TEXT PRIMARY KEY NOT NULL,
    artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
    operation_id TEXT NOT NULL REFERENCES operation_links(operation_id),
    version_id TEXT,
    format TEXT NOT NULL CHECK (length(trim(format)) > 0),
    relative_path TEXT NOT NULL,
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'),
    capture_extent_json TEXT NOT NULL CHECK (json_valid(capture_extent_json) AND json_type(capture_extent_json) = 'object'),
    extraction_status TEXT NOT NULL CHECK (extraction_status IN ('complete', 'partial', 'failed', 'unavailable', 'legacy_attested')),
    receipt_sha256 TEXT NOT NULL CHECK (length(receipt_sha256) = 64 AND receipt_sha256 NOT GLOB '*[^0-9a-f]*'),
    captured_at TEXT NOT NULL,
    limitations TEXT NOT NULL DEFAULT '',
    created_batch_id TEXT NOT NULL REFERENCES ingest_batches(batch_id),
    FOREIGN KEY (version_id, artifact_id) REFERENCES artifact_versions(version_id, artifact_id)
);
CREATE INDEX captures_by_artifact ON captures(artifact_id, version_id);
CREATE TABLE locators (
    locator_id TEXT PRIMARY KEY NOT NULL,
    capture_id TEXT NOT NULL REFERENCES captures(capture_id),
    locator_kind TEXT NOT NULL CHECK (locator_kind IN ('document', 'section', 'slide', 'node', 'comment', 'paragraph', 'timestamp', 'line', 'metadata')),
    locator_json TEXT NOT NULL CHECK (json_valid(locator_json) AND json_type(locator_json) = 'object'),
    excerpt TEXT,
    excerpt_hash TEXT CHECK (excerpt_hash IS NULL OR (length(excerpt_hash) = 64 AND excerpt_hash NOT GLOB '*[^0-9a-f]*')),
    created_batch_id TEXT NOT NULL REFERENCES ingest_batches(batch_id),
    CHECK ((excerpt IS NULL) = (excerpt_hash IS NULL))
);
CREATE INDEX locators_by_capture ON locators(capture_id);

CREATE TABLE reader_returns (
    return_id TEXT PRIMARY KEY NOT NULL,
    actor_id TEXT NOT NULL REFERENCES actors(actor_id),
    assignment_id TEXT REFERENCES assignments(assignment_id),
    file_path TEXT NOT NULL UNIQUE,
    payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256) = 64 AND payload_sha256 NOT GLOB '*[^0-9a-f]*'),
    receipt_sha256 TEXT CHECK (receipt_sha256 IS NULL OR (length(receipt_sha256) = 64 AND receipt_sha256 NOT GLOB '*[^0-9a-f]*')),
    recorded_budget_event TEXT,
    verification_state TEXT NOT NULL CHECK (verification_state IN ('verified', 'legacy_attested')),
    limitations TEXT NOT NULL DEFAULT '',
    created_batch_id TEXT NOT NULL REFERENCES ingest_batches(batch_id),
    UNIQUE (return_id, actor_id),
    CHECK (verification_state <> 'verified' OR (assignment_id IS NOT NULL AND receipt_sha256 IS NOT NULL AND recorded_budget_event IS NOT NULL)),
    CHECK (verification_state = 'verified' OR length(trim(limitations)) > 0)
);
CREATE TABLE inspections (
    inspection_id TEXT PRIMARY KEY NOT NULL,
    artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
    return_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    inspection_extent TEXT NOT NULL CHECK (length(trim(inspection_extent)) > 0),
    method TEXT NOT NULL CHECK (length(trim(method)) > 0),
    inspected_at TEXT NOT NULL,
    attested_started_at TEXT,
    attested_finished_at TEXT,
    provenance_basis TEXT NOT NULL CHECK (provenance_basis IN ('native', 'legacy_attested', 'prior_run')),
    limitations TEXT NOT NULL DEFAULT '',
    created_batch_id TEXT NOT NULL REFERENCES ingest_batches(batch_id),
    FOREIGN KEY (return_id, actor_id) REFERENCES reader_returns(return_id, actor_id),
    CHECK ((attested_started_at IS NULL AND attested_finished_at IS NULL) OR (attested_started_at IS NOT NULL AND attested_finished_at IS NOT NULL AND COALESCE(julianday(attested_started_at) <= julianday(attested_finished_at) AND julianday(attested_finished_at) <= julianday(inspected_at), 0)))
);
CREATE INDEX inspections_by_artifact ON inspections(artifact_id, return_id);
CREATE TABLE inspection_captures (
    inspection_id TEXT NOT NULL REFERENCES inspections(inspection_id),
    capture_id TEXT NOT NULL REFERENCES captures(capture_id),
    inspected_extent_json TEXT NOT NULL CHECK (json_valid(inspected_extent_json) AND json_type(inspected_extent_json) = 'object'),
    PRIMARY KEY (inspection_id, capture_id)
);
CREATE TABLE inspection_scopes (
    inspection_id TEXT NOT NULL REFERENCES inspections(inspection_id),
    question_id TEXT NOT NULL REFERENCES questions(question_id),
    required_scope_key TEXT NOT NULL CHECK (length(required_scope_key) > 0),
    format_policy_sha256 TEXT NOT NULL CHECK (length(format_policy_sha256) = 64 AND format_policy_sha256 NOT GLOB '*[^0-9a-f]*'),
    coverage TEXT NOT NULL CHECK (coverage IN ('partial', 'sufficient')),
    basis TEXT NOT NULL CHECK (length(trim(basis)) > 0),
    PRIMARY KEY (inspection_id, question_id, required_scope_key, format_policy_sha256)
);

CREATE TABLE reference_scans (
    scan_id TEXT PRIMARY KEY NOT NULL,
    capture_id TEXT NOT NULL REFERENCES captures(capture_id),
    return_id TEXT REFERENCES reader_returns(return_id),
    method TEXT NOT NULL CHECK (length(trim(method)) > 0),
    scanned_extent_json TEXT NOT NULL CHECK (json_valid(scanned_extent_json) AND json_type(scanned_extent_json) = 'object'),
    extracted_count INTEGER NOT NULL CHECK (extracted_count >= 0),
    limitations TEXT NOT NULL DEFAULT '',
    created_batch_id TEXT NOT NULL REFERENCES ingest_batches(batch_id)
);
CREATE TABLE reference_occurrences (
    reference_id TEXT PRIMARY KEY NOT NULL,
    from_artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
    locator_id TEXT NOT NULL REFERENCES locators(locator_id),
    scan_id TEXT NOT NULL REFERENCES reference_scans(scan_id),
    local_reference_key TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('link', 'mention')),
    original_value TEXT NOT NULL CHECK (length(trim(original_value)) > 0),
    label TEXT NOT NULL DEFAULT '',
    target_artifact_id TEXT REFERENCES artifacts(artifact_id),
    extraction_method TEXT NOT NULL CHECK (length(trim(extraction_method)) > 0),
    created_batch_id TEXT NOT NULL REFERENCES ingest_batches(batch_id),
    UNIQUE (scan_id, local_reference_key),
    CHECK (kind <> 'mention' OR target_artifact_id IS NULL)
);
CREATE INDEX reference_children ON reference_occurrences(from_artifact_id, target_artifact_id);
CREATE TABLE reference_resolutions (
    resolution_id TEXT PRIMARY KEY NOT NULL,
    reference_id TEXT NOT NULL REFERENCES reference_occurrences(reference_id),
    target_artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
    basis TEXT NOT NULL CHECK (length(trim(basis)) > 0),
    actor_id TEXT NOT NULL REFERENCES actors(actor_id),
    replaces_resolution_id TEXT REFERENCES reference_resolutions(resolution_id),
    created_batch_id TEXT NOT NULL REFERENCES ingest_batches(batch_id),
    CHECK (replaces_resolution_id IS NULL OR replaces_resolution_id <> resolution_id)
);
CREATE UNIQUE INDEX one_initial_resolution ON reference_resolutions(reference_id) WHERE replaces_resolution_id IS NULL;
CREATE UNIQUE INDEX one_resolution_successor ON reference_resolutions(replaces_resolution_id) WHERE replaces_resolution_id IS NOT NULL;

CREATE TABLE discoveries (
    discovery_id TEXT PRIMARY KEY NOT NULL,
    artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
    operation_id TEXT REFERENCES operation_links(operation_id),
    origin_reference_id TEXT REFERENCES reference_occurrences(reference_id),
    supplied_seed_id TEXT,
    prior_import_id TEXT REFERENCES prior_run_imports(import_id) DEFERRABLE INITIALLY DEFERRED,
    rank INTEGER CHECK (rank >= 0),
    observed_title TEXT NOT NULL,
    observed_url TEXT,
    created_batch_id TEXT NOT NULL REFERENCES ingest_batches(batch_id),
    CHECK ((operation_id IS NOT NULL) + (origin_reference_id IS NOT NULL) + (supplied_seed_id IS NOT NULL) + (prior_import_id IS NOT NULL) = 1)
);
CREATE INDEX discoveries_by_artifact ON discoveries(artifact_id);

CREATE TABLE role_vocabulary (
    role TEXT PRIMARY KEY NOT NULL,
    description TEXT NOT NULL
);
INSERT INTO role_vocabulary VALUES ('content', 'What was proposed, built, specified, demonstrated or measured'), ('critical', 'Evaluation, objections, alternatives and gating decisions'), ('trunk', 'Organization, ownership and paths to related evidence');
CREATE TABLE relationship_vocabulary (
    predicate TEXT PRIMARY KEY NOT NULL,
    description TEXT NOT NULL
);
INSERT INTO relationship_vocabulary VALUES ('organizes', 'Organizes related work'), ('reviews', 'Evaluates another record'), ('challenges', 'Challenges a position'), ('approves', 'Approves the stated scope'), ('implements', 'Implements the stated scope'), ('tests', 'Tests the stated scope'), ('revises', 'Revises earlier work'), ('supersedes', 'Replaces earlier work'), ('responds_to', 'Responds to earlier work');

CREATE TABLE assertions (
    assertion_id TEXT PRIMARY KEY NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('role', 'relevance', 'finding', 'contribution', 'relationship')),
    subject_artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
    predicate TEXT NOT NULL,
    object_artifact_id TEXT REFERENCES artifacts(artifact_id),
    value_json TEXT CHECK (value_json IS NULL OR json_valid(value_json)),
    subject_locator_id TEXT REFERENCES locators(locator_id),
    object_locator_id TEXT REFERENCES locators(locator_id),
    question_id TEXT REFERENCES questions(question_id),
    temporal_scope_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(temporal_scope_json) AND json_type(temporal_scope_json) = 'object'),
    basis TEXT NOT NULL CHECK (basis IN ('metadata', 'inspected', 'participant_account', 'inference', 'legacy_attested', 'prior_run')),
    return_id TEXT REFERENCES reader_returns(return_id),
    assessed_at TEXT NOT NULL,
    created_batch_id TEXT NOT NULL REFERENCES ingest_batches(batch_id),
    CHECK (object_locator_id IS NULL OR object_artifact_id IS NOT NULL),
    CHECK (COALESCE(
        (kind = 'role' AND predicate = 'has_role' AND object_artifact_id IS NULL AND json_type(value_json) = 'text') OR
        (kind = 'relevance' AND predicate = 'is_relevant' AND object_artifact_id IS NULL AND question_id IS NOT NULL AND json_type(value_json) = 'text' AND json_extract(value_json, '$') IN ('unassessed', 'relevant', 'irrelevant', 'uncertain')) OR
        (kind = 'relationship' AND object_artifact_id IS NOT NULL AND value_json IS NULL) OR
        (kind = 'finding' AND predicate = 'states' AND object_artifact_id IS NULL AND json_type(value_json) = 'object' AND json_type(value_json, '$.text') = 'text' AND length(trim(json_extract(value_json, '$.text'))) > 0) OR
        (kind = 'contribution' AND predicate = 'contributed' AND object_artifact_id IS NULL AND json_type(value_json) = 'object' AND json_type(value_json, '$.contributor') = 'text' AND json_type(value_json, '$.text') = 'text' AND json_extract(value_json, '$.attribution_basis') IN ('named_author', 'revision_author', 'commenter', 'credited_collaborator', 'participant_account', 'uncertain')), 0))
);
CREATE INDEX assertions_by_subject ON assertions(subject_artifact_id, kind, question_id);
CREATE INDEX assertions_by_object ON assertions(object_artifact_id, predicate);
CREATE TABLE assertion_evidence (
    evidence_id TEXT PRIMARY KEY NOT NULL,
    assertion_id TEXT NOT NULL REFERENCES assertions(assertion_id),
    locator_id TEXT NOT NULL REFERENCES locators(locator_id),
    inspection_id TEXT REFERENCES inspections(inspection_id),
    stance TEXT NOT NULL CHECK (stance IN ('support', 'qualify', 'conflict')),
    basis TEXT NOT NULL CHECK (basis IN ('metadata', 'inspected', 'participant_account', 'inference')),
    rationale TEXT NOT NULL CHECK (length(trim(rationale)) > 0),
    created_batch_id TEXT NOT NULL REFERENCES ingest_batches(batch_id),
    CHECK (basis <> 'inspected' OR inspection_id IS NOT NULL)
);
CREATE UNIQUE INDEX distinct_assertion_evidence ON assertion_evidence(assertion_id, locator_id, COALESCE(inspection_id, ''), stance, basis);
CREATE INDEX evidence_by_assertion ON assertion_evidence(assertion_id, locator_id);
CREATE TABLE assertion_decisions (
    decision_id TEXT PRIMARY KEY NOT NULL,
    assertion_id TEXT NOT NULL REFERENCES assertions(assertion_id),
    disposition TEXT NOT NULL CHECK (disposition IN ('accepted', 'challenged', 'withdrawn', 'superseded')),
    reason TEXT NOT NULL CHECK (length(trim(reason)) > 0),
    actor_id TEXT NOT NULL REFERENCES actors(actor_id),
    replacement_assertion_id TEXT REFERENCES assertions(assertion_id),
    expected_revision INTEGER NOT NULL CHECK (expected_revision >= 0),
    created_batch_id TEXT NOT NULL REFERENCES ingest_batches(batch_id),
    UNIQUE (assertion_id, expected_revision),
    CHECK ((disposition = 'superseded' AND replacement_assertion_id IS NOT NULL AND replacement_assertion_id <> assertion_id) OR (disposition <> 'superseded' AND replacement_assertion_id IS NULL))
);

CREATE TABLE claims (
    claim_id TEXT PRIMARY KEY NOT NULL,
    text TEXT NOT NULL CHECK (length(trim(text)) > 0),
    basis TEXT NOT NULL CHECK (basis IN ('documented', 'participant_account', 'inference', 'recommendation', 'legacy_attested', 'prior_run')),
    temporal_scope_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(temporal_scope_json) AND json_type(temporal_scope_json) = 'object'),
    comparison_scope_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(comparison_scope_json) AND json_type(comparison_scope_json) = 'object'),
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'accepted', 'challenged', 'withdrawn', 'superseded')),
    reason TEXT NOT NULL CHECK (length(trim(reason)) > 0),
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_batch_id TEXT NOT NULL REFERENCES ingest_batches(batch_id),
    updated_batch_id TEXT NOT NULL REFERENCES ingest_batches(batch_id)
);
CREATE TABLE claim_revisions (
    claim_id TEXT NOT NULL REFERENCES claims(claim_id),
    revision INTEGER NOT NULL CHECK (revision >= 1),
    text TEXT NOT NULL,
    basis TEXT NOT NULL,
    temporal_scope_json TEXT NOT NULL,
    comparison_scope_json TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_batch_id TEXT NOT NULL REFERENCES ingest_batches(batch_id),
    PRIMARY KEY (claim_id, revision)
);
CREATE TABLE claim_evidence (
    evidence_id TEXT PRIMARY KEY NOT NULL,
    claim_id TEXT NOT NULL,
    claim_revision INTEGER NOT NULL,
    locator_id TEXT NOT NULL REFERENCES locators(locator_id),
    assertion_id TEXT REFERENCES assertions(assertion_id),
    stance TEXT NOT NULL CHECK (stance IN ('support', 'qualify', 'conflict')),
    rationale TEXT NOT NULL CHECK (length(trim(rationale)) > 0),
    created_batch_id TEXT NOT NULL REFERENCES ingest_batches(batch_id),
    FOREIGN KEY (claim_id, claim_revision) REFERENCES claim_revisions(claim_id, revision)
);
CREATE UNIQUE INDEX distinct_claim_evidence ON claim_evidence(claim_id, claim_revision, locator_id, COALESCE(assertion_id, ''), stance);
CREATE TABLE claim_questions (
    claim_id TEXT NOT NULL,
    claim_revision INTEGER NOT NULL,
    question_id TEXT NOT NULL REFERENCES questions(question_id),
    relationship TEXT NOT NULL CHECK (relationship IN ('answers', 'partly_answers', 'complicates')),
    PRIMARY KEY (claim_id, claim_revision, question_id, relationship),
    FOREIGN KEY (claim_id, claim_revision) REFERENCES claim_revisions(claim_id, revision)
);

CREATE TABLE leads (
    lead_id TEXT PRIMARY KEY NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('vocabulary', 'linked_record', 'neighborhood', 'observed_gap', 'inferred_gap')),
    original_value TEXT NOT NULL CHECK (length(trim(original_value)) > 0),
    normalized_key TEXT NOT NULL,
    significance TEXT NOT NULL CHECK (length(trim(significance)) > 0),
    disposition TEXT NOT NULL DEFAULT 'pending' CHECK (disposition IN ('pending', 'queued', 'investigating', 'resolved', 'bounded', 'excluded', 'deferred')),
    reason TEXT,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_batch_id TEXT NOT NULL REFERENCES ingest_batches(batch_id),
    updated_batch_id TEXT NOT NULL REFERENCES ingest_batches(batch_id),
    CHECK (disposition NOT IN ('resolved', 'bounded', 'excluded', 'deferred') OR COALESCE(length(trim(reason)), 0) > 0)
);
CREATE INDEX leads_by_disposition ON leads(disposition, kind, normalized_key, lead_id);
CREATE TABLE lead_origins (
    origin_id TEXT PRIMARY KEY NOT NULL,
    lead_id TEXT NOT NULL REFERENCES leads(lead_id),
    return_id TEXT REFERENCES reader_returns(return_id),
    returned_lead_id TEXT,
    reference_id TEXT REFERENCES reference_occurrences(reference_id),
    locator_id TEXT REFERENCES locators(locator_id),
    question_id TEXT NOT NULL REFERENCES questions(question_id),
    original_value TEXT NOT NULL CHECK (length(trim(original_value)) > 0),
    origin_basis TEXT NOT NULL CHECK (length(trim(origin_basis)) > 0),
    created_batch_id TEXT NOT NULL REFERENCES ingest_batches(batch_id),
    UNIQUE (return_id, returned_lead_id, question_id),
    CHECK ((return_id IS NULL) = (returned_lead_id IS NULL))
);
CREATE TABLE work_origins (
    origin_id TEXT PRIMARY KEY NOT NULL,
    work_id TEXT NOT NULL REFERENCES work_items(work_id),
    lead_id TEXT REFERENCES leads(lead_id),
    reference_id TEXT REFERENCES reference_occurrences(reference_id),
    parent_operation_id TEXT REFERENCES operation_links(operation_id),
    root_basis TEXT,
    originating_generation INTEGER NOT NULL CHECK (originating_generation >= 0),
    created_batch_id TEXT NOT NULL REFERENCES ingest_batches(batch_id),
    CHECK (lead_id IS NOT NULL OR reference_id IS NOT NULL OR parent_operation_id IS NOT NULL OR COALESCE(length(trim(root_basis)), 0) > 0),
    CHECK (root_basis IS NULL OR (lead_id IS NULL AND reference_id IS NULL AND parent_operation_id IS NULL AND originating_generation = 0))
);
CREATE INDEX work_by_lead_origin ON work_origins(lead_id, work_id);
CREATE INDEX work_by_reference_origin ON work_origins(reference_id, work_id);
CREATE TABLE followup_obligations (
    obligation_id TEXT PRIMARY KEY NOT NULL,
    reference_id TEXT UNIQUE REFERENCES reference_occurrences(reference_id),
    lead_id TEXT UNIQUE REFERENCES leads(lead_id),
    work_id TEXT REFERENCES work_items(work_id),
    state TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending', 'scheduled', 'terminal')),
    disposition TEXT CHECK (disposition IN ('resolved', 'bounded', 'excluded', 'deferred')),
    reason TEXT,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_batch_id TEXT NOT NULL REFERENCES ingest_batches(batch_id),
    updated_batch_id TEXT NOT NULL REFERENCES ingest_batches(batch_id),
    CHECK ((reference_id IS NOT NULL) + (lead_id IS NOT NULL) = 1),
    CHECK ((state = 'pending' AND work_id IS NULL AND disposition IS NULL) OR (state = 'scheduled' AND work_id IS NOT NULL AND disposition IS NULL) OR (state = 'terminal' AND disposition IS NOT NULL AND COALESCE(length(trim(reason)), 0) > 0))
);
CREATE INDEX obligations_by_state ON followup_obligations(state, obligation_id);

CREATE TABLE source_outcomes (
    outcome_id TEXT PRIMARY KEY NOT NULL,
    artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
    question_id TEXT NOT NULL REFERENCES questions(question_id),
    required_scope_key TEXT NOT NULL,
    readability TEXT NOT NULL CHECK (readability IN ('unknown', 'readable', 'access_blocked', 'unreadable', 'transient_retry_pending', 'retry_exhausted')),
    disposition TEXT CHECK (disposition IN ('used', 'contextual', 'excluded', 'deferred')),
    reason TEXT NOT NULL CHECK (length(trim(reason)) > 0),
    operation_id TEXT REFERENCES operation_links(operation_id),
    locator_id TEXT REFERENCES locators(locator_id),
    actor_id TEXT NOT NULL REFERENCES actors(actor_id),
    observed_at TEXT NOT NULL,
    replaces_outcome_id TEXT UNIQUE REFERENCES source_outcomes(outcome_id),
    changed_basis TEXT,
    created_batch_id TEXT NOT NULL REFERENCES ingest_batches(batch_id),
    CHECK (replaces_outcome_id IS NULL OR replaces_outcome_id <> outcome_id)
);
CREATE INDEX outcomes_by_scope ON source_outcomes(artifact_id, question_id, required_scope_key);

CREATE TABLE export_manifests (
    export_id TEXT PRIMARY KEY NOT NULL,
    graph_sequence INTEGER NOT NULL REFERENCES graph_events(sequence),
    schema_version INTEGER NOT NULL CHECK (schema_version = 1),
    contract_sha256 TEXT NOT NULL REFERENCES contract_snapshots(contract_sha256),
    plan_sha256 TEXT REFERENCES plan_snapshots(plan_sha256),
    budget_event_head TEXT,
    files_json TEXT NOT NULL CHECK (json_valid(files_json) AND json_type(files_json) = 'array'),
    created_at TEXT NOT NULL
);
CREATE TABLE prior_run_imports (
    import_id TEXT PRIMARY KEY NOT NULL,
    prior_run_id TEXT NOT NULL,
    prior_export_id TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL CHECK (length(manifest_sha256) = 64 AND manifest_sha256 NOT GLOB '*[^0-9a-f]*'),
    source_artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
    policy TEXT NOT NULL CHECK (policy IN ('leads_only', 'attested_evidence', 'legacy_attested')),
    scope_json TEXT NOT NULL CHECK (json_valid(scope_json) AND json_type(scope_json) = 'object'),
    limitations TEXT NOT NULL DEFAULT '',
    imported_batch_id TEXT NOT NULL REFERENCES ingest_batches(batch_id),
    UNIQUE (prior_run_id, prior_export_id, manifest_sha256, import_id)
);
CREATE TABLE prior_run_records (
    record_id TEXT PRIMARY KEY NOT NULL,
    import_id TEXT NOT NULL REFERENCES prior_run_imports(import_id),
    prior_record_kind TEXT NOT NULL CHECK (prior_record_kind IN ('artifact', 'assertion', 'inspection', 'lead', 'claim')),
    prior_record_id TEXT NOT NULL,
    artifact_id TEXT REFERENCES artifacts(artifact_id),
    assertion_id TEXT REFERENCES assertions(assertion_id),
    inspection_id TEXT REFERENCES inspections(inspection_id),
    lead_id TEXT REFERENCES leads(lead_id),
    claim_id TEXT REFERENCES claims(claim_id),
    original_namespace TEXT,
    original_external_id TEXT,
    lineage_json TEXT NOT NULL CHECK (json_valid(lineage_json) AND json_type(lineage_json) = 'array'),
    limitations TEXT NOT NULL DEFAULT '',
    UNIQUE (import_id, prior_record_kind, prior_record_id),
    CHECK ((artifact_id IS NOT NULL) + (assertion_id IS NOT NULL) + (inspection_id IS NOT NULL) + (lead_id IS NOT NULL) + (claim_id IS NOT NULL) = 1),
    CHECK ((prior_record_kind = 'artifact' AND artifact_id IS NOT NULL) OR (prior_record_kind = 'assertion' AND assertion_id IS NOT NULL) OR (prior_record_kind = 'inspection' AND inspection_id IS NOT NULL) OR (prior_record_kind = 'lead' AND lead_id IS NOT NULL) OR (prior_record_kind = 'claim' AND claim_id IS NOT NULL)),
    CHECK ((original_namespace IS NULL) = (original_external_id IS NULL))
);

-- Canonical identity affects query projection, never rewrites original records.
CREATE VIEW canonical_artifacts AS
WITH RECURSIVE redirects(artifact_id, canonical_id) AS (
    SELECT artifact_id, artifact_id FROM artifacts
    UNION ALL
    SELECT r.artifact_id, a.canonical_artifact_id FROM redirects r
    JOIN artifacts a ON a.artifact_id = r.canonical_id
    WHERE a.canonical_artifact_id IS NOT NULL
)
SELECT r.artifact_id, r.canonical_id FROM redirects r
JOIN artifacts a ON a.artifact_id = r.canonical_id
WHERE a.canonical_artifact_id IS NULL;

CREATE VIEW current_reference_resolutions AS
SELECT r.* FROM reference_resolutions r
WHERE NOT EXISTS (SELECT 1 FROM reference_resolutions n WHERE n.replaces_resolution_id = r.resolution_id);
CREATE VIEW effective_reference_targets AS
SELECT r.reference_id, src.canonical_id AS from_artifact_id, dst.canonical_id AS target_artifact_id,
       r.from_artifact_id AS observed_from_artifact_id, r.target_artifact_id AS observed_target_artifact_id,
       r.kind, r.original_value, r.label, r.locator_id, r.scan_id, x.resolution_id, x.basis AS resolution_basis
FROM reference_occurrences r
JOIN canonical_artifacts src ON src.artifact_id = r.from_artifact_id
LEFT JOIN current_reference_resolutions x ON x.reference_id = r.reference_id
LEFT JOIN canonical_artifacts dst ON dst.artifact_id = COALESCE(x.target_artifact_id, r.target_artifact_id);
CREATE VIEW unresolved_references AS SELECT * FROM effective_reference_targets WHERE target_artifact_id IS NULL;

CREATE VIEW current_assertion_decisions AS
SELECT d.*, d.expected_revision + 1 AS revision FROM assertion_decisions d
WHERE NOT EXISTS (SELECT 1 FROM assertion_decisions n WHERE n.assertion_id = d.assertion_id AND n.expected_revision > d.expected_revision);
CREATE VIEW reconciled_assertions AS
SELECT a.*, COALESCE(d.disposition, 'proposed') AS disposition, COALESCE(d.revision, 0) AS revision,
       d.decision_id, d.reason AS decision_reason, d.replacement_assertion_id
FROM assertions a LEFT JOIN current_assertion_decisions d ON d.assertion_id = a.assertion_id;
CREATE VIEW artifact_roles AS
SELECT c.canonical_id AS artifact_id, a.assertion_id, json_extract(a.value_json, '$') AS role,
       a.question_id, a.subject_locator_id, a.basis, a.disposition, a.revision
FROM reconciled_assertions a JOIN canonical_artifacts c ON c.artifact_id = a.subject_artifact_id
WHERE a.kind = 'role';
CREATE VIEW artifact_question_assessments AS
SELECT c.canonical_id AS artifact_id, a.assertion_id, json_extract(a.value_json, '$') AS relevance,
       a.question_id, a.subject_locator_id, a.basis, a.disposition, a.revision
FROM reconciled_assertions a JOIN canonical_artifacts c ON c.artifact_id = a.subject_artifact_id
WHERE a.kind = 'relevance';
CREATE VIEW sufficient_inspection AS
SELECT c.canonical_id AS artifact_id, s.question_id, s.required_scope_key, s.format_policy_sha256,
       i.inspection_id, i.return_id
FROM inspection_scopes s JOIN inspections i USING (inspection_id)
JOIN reader_returns r USING (return_id)
JOIN canonical_artifacts c ON c.artifact_id = i.artifact_id
WHERE s.coverage = 'sufficient' AND i.provenance_basis = 'native' AND r.verification_state = 'verified'
  AND EXISTS (SELECT 1 FROM inspection_captures ic WHERE ic.inspection_id = i.inspection_id);
CREATE VIEW current_source_outcomes AS
SELECT s.* FROM source_outcomes s
WHERE NOT EXISTS (SELECT 1 FROM source_outcomes n WHERE n.replaces_outcome_id = s.outcome_id);
CREATE VIEW pending_frontier AS
SELECT w.* FROM work_items w WHERE w.state = 'pending'
  AND NOT EXISTS (SELECT 1 FROM assignments a WHERE a.work_id = w.work_id AND a.state IN ('leased', 'running', 'reconciling'));

-- One registration obligation may require work for several questions.
-- work_id on that obligation is only its first routing pointer.
CREATE VIEW obligation_question_scopes AS
SELECT o.obligation_id, lo.question_id FROM followup_obligations o
JOIN lead_origins lo ON lo.lead_id = o.lead_id OR lo.reference_id = o.reference_id
UNION
SELECT o.obligation_id, w.question_id FROM followup_obligations o
JOIN work_origins wo ON wo.lead_id = o.lead_id OR wo.reference_id = o.reference_id
JOIN work_items w ON w.work_id = wo.work_id
UNION
SELECT o.obligation_id, w.question_id FROM followup_obligations o
JOIN reference_occurrences r USING (reference_id) JOIN reference_scans s USING (scan_id)
JOIN captures c ON c.capture_id = s.capture_id JOIN operation_links op USING (operation_id)
JOIN assignments a ON a.assignment_id = op.assignment_id JOIN work_items w ON w.work_id = a.work_id
UNION
SELECT o.obligation_id, a.question_id FROM followup_obligations o
JOIN reference_occurrences r USING (reference_id)
JOIN canonical_artifacts src ON src.artifact_id = r.from_artifact_id
JOIN canonical_artifacts assessed ON assessed.canonical_id = src.canonical_id
JOIN assertions a ON a.subject_artifact_id = assessed.artifact_id
WHERE a.question_id IS NOT NULL
UNION
SELECT o.obligation_id, s.question_id FROM followup_obligations o
JOIN reference_occurrences r USING (reference_id)
JOIN canonical_artifacts src ON src.artifact_id = r.from_artifact_id
JOIN canonical_artifacts inspected ON inspected.canonical_id = src.canonical_id
JOIN inspections i ON i.artifact_id = inspected.artifact_id
JOIN inspection_scopes s USING (inspection_id);

CREATE VIEW obligation_scope_status AS
SELECT s.obligation_id, s.question_id,
    CASE WHEN NOT EXISTS (SELECT 1 FROM work_origins wo JOIN work_items w USING (work_id)
        WHERE (wo.lead_id = o.lead_id OR wo.reference_id = o.reference_id) AND w.question_id = s.question_id)
        THEN 'unassigned'
    WHEN EXISTS (SELECT 1 FROM work_origins wo JOIN work_items w USING (work_id)
        WHERE (wo.lead_id = o.lead_id OR wo.reference_id = o.reference_id) AND w.question_id = s.question_id AND w.state IN ('pending', 'leased', 'running'))
        THEN 'pending'
    WHEN EXISTS (SELECT 1 FROM work_origins wo JOIN work_items w USING (work_id)
        WHERE (wo.lead_id = o.lead_id OR wo.reference_id = o.reference_id) AND w.question_id = s.question_id AND w.state IN ('blocked', 'deferred', 'cancelled'))
        THEN 'bounded'
    ELSE 'resolved' END AS status
FROM obligation_question_scopes s JOIN followup_obligations o USING (obligation_id);

-- Guards supplement the helper's actor, hash, packet and CAS validation.
CREATE TRIGGER canonical_cycle_insert BEFORE INSERT ON artifacts WHEN NEW.canonical_artifact_id IS NOT NULL BEGIN
    SELECT RAISE(ABORT, 'canonical redirect cycle') WHERE EXISTS (
        WITH RECURSIVE chain(id) AS (SELECT NEW.canonical_artifact_id UNION SELECT a.canonical_artifact_id FROM artifacts a JOIN chain c ON a.artifact_id = c.id WHERE a.canonical_artifact_id IS NOT NULL)
        SELECT 1 FROM chain WHERE id = NEW.artifact_id);
END;
CREATE TRIGGER canonical_cycle_update BEFORE UPDATE OF canonical_artifact_id ON artifacts WHEN NEW.canonical_artifact_id IS NOT NULL BEGIN
    SELECT RAISE(ABORT, 'canonical redirect cycle') WHERE EXISTS (
        WITH RECURSIVE chain(id) AS (SELECT NEW.canonical_artifact_id UNION SELECT a.canonical_artifact_id FROM artifacts a JOIN chain c ON a.artifact_id = c.id WHERE a.canonical_artifact_id IS NOT NULL)
        SELECT 1 FROM chain WHERE id = NEW.artifact_id);
END;
CREATE TRIGGER canonical_redirect_retained BEFORE UPDATE OF canonical_artifact_id ON artifacts
WHEN OLD.canonical_artifact_id IS NOT NULL AND NEW.canonical_artifact_id IS NOT OLD.canonical_artifact_id BEGIN
    SELECT RAISE(ABORT, 'canonical redirect is retained; reconcile its target');
END;
CREATE TRIGGER event_sequence BEFORE INSERT ON graph_events BEGIN
    SELECT RAISE(ABORT, 'event sequence must be contiguous') WHERE NEW.sequence <> COALESCE((SELECT MAX(sequence) FROM graph_events), 0) + 1;
END;
CREATE TRIGGER resolution_chain BEFORE INSERT ON reference_resolutions WHEN NEW.replaces_resolution_id IS NOT NULL BEGIN
    SELECT RAISE(ABORT, 'resolution predecessor belongs to another reference') WHERE NOT EXISTS (SELECT 1 FROM reference_resolutions WHERE resolution_id = NEW.replaces_resolution_id AND reference_id = NEW.reference_id);
END;
CREATE TRIGGER reference_location BEFORE INSERT ON reference_occurrences BEGIN
    SELECT RAISE(ABORT, 'reference locator must belong to its scan and source') WHERE NOT EXISTS (
        SELECT 1 FROM locators l JOIN captures c USING (capture_id) JOIN reference_scans s ON s.capture_id = c.capture_id
        JOIN canonical_artifacts ca ON ca.artifact_id = c.artifact_id
        JOIN canonical_artifacts src ON src.artifact_id = NEW.from_artifact_id
        WHERE l.locator_id = NEW.locator_id AND s.scan_id = NEW.scan_id AND ca.canonical_id = src.canonical_id);
END;
CREATE TRIGGER reference_obligation AFTER INSERT ON reference_occurrences BEGIN
    INSERT INTO followup_obligations(obligation_id, reference_id, created_batch_id, updated_batch_id)
    VALUES ('reference:' || NEW.reference_id, NEW.reference_id, NEW.created_batch_id, NEW.created_batch_id);
END;
CREATE TRIGGER lead_obligation AFTER INSERT ON leads BEGIN
    INSERT INTO followup_obligations(obligation_id, lead_id, created_batch_id, updated_batch_id)
    VALUES ('lead:' || NEW.lead_id, NEW.lead_id, NEW.created_batch_id, NEW.created_batch_id);
END;
CREATE TRIGGER inspection_return_verified BEFORE INSERT ON inspections BEGIN
    SELECT RAISE(ABORT, 'inspection requires a verified return or explicit historical attestation') WHERE NOT EXISTS (
        SELECT 1 FROM reader_returns r WHERE r.return_id = NEW.return_id AND
        ((NEW.provenance_basis = 'native' AND r.verification_state = 'verified') OR (NEW.provenance_basis IN ('legacy_attested', 'prior_run') AND r.verification_state = 'legacy_attested')));
END;
CREATE TRIGGER inspection_capture_source BEFORE INSERT ON inspection_captures BEGIN
    SELECT RAISE(ABORT, 'inspection capture belongs to another artifact') WHERE NOT EXISTS (
        SELECT 1 FROM inspections i JOIN canonical_artifacts a ON a.artifact_id = i.artifact_id
        JOIN captures c ON c.capture_id = NEW.capture_id JOIN canonical_artifacts b ON b.artifact_id = c.artifact_id
        WHERE i.inspection_id = NEW.inspection_id AND a.canonical_id = b.canonical_id);
    SELECT RAISE(ABORT, 'native inspection requires a verified successful capture') WHERE EXISTS (SELECT 1 FROM inspections WHERE inspection_id = NEW.inspection_id AND provenance_basis = 'native') AND NOT EXISTS (
        SELECT 1 FROM captures c JOIN operation_links o USING (operation_id) WHERE c.capture_id = NEW.capture_id AND o.execution_state = 'succeeded' AND o.verification_state = 'verified' AND o.receipt_sha256 = c.receipt_sha256 AND c.extraction_status IN ('complete', 'partial'));
END;
CREATE TRIGGER return_assignment_actor BEFORE INSERT ON reader_returns WHEN NEW.assignment_id IS NOT NULL BEGIN
    SELECT RAISE(ABORT, 'return actor differs from assignment') WHERE NOT EXISTS (SELECT 1 FROM assignments WHERE assignment_id = NEW.assignment_id AND actor_id = NEW.actor_id);
END;
CREATE TRIGGER inspected_evidence_capture BEFORE INSERT ON assertion_evidence WHEN NEW.inspection_id IS NOT NULL BEGIN
    SELECT RAISE(ABORT, 'evidence locator was not in the inspected capture') WHERE NOT EXISTS (
        SELECT 1 FROM locators l JOIN inspection_captures ic ON ic.capture_id = l.capture_id
        WHERE l.locator_id = NEW.locator_id AND ic.inspection_id = NEW.inspection_id);
END;
CREATE TRIGGER assertion_shape BEFORE INSERT ON assertions BEGIN
    SELECT RAISE(ABORT, 'unknown role') WHERE NEW.kind = 'role' AND NOT EXISTS (SELECT 1 FROM role_vocabulary WHERE role = json_extract(NEW.value_json, '$'));
    SELECT RAISE(ABORT, 'unknown relationship predicate') WHERE NEW.kind = 'relationship' AND NOT EXISTS (SELECT 1 FROM relationship_vocabulary WHERE predicate = NEW.predicate);
    SELECT RAISE(ABORT, 'subject locator belongs to another artifact') WHERE NEW.subject_locator_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM locators l JOIN captures c USING (capture_id) JOIN canonical_artifacts a ON a.artifact_id = c.artifact_id JOIN canonical_artifacts b ON b.artifact_id = NEW.subject_artifact_id
        WHERE l.locator_id = NEW.subject_locator_id AND a.canonical_id = b.canonical_id);
    SELECT RAISE(ABORT, 'object locator belongs to another artifact') WHERE NEW.object_locator_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM locators l JOIN captures c USING (capture_id) JOIN canonical_artifacts a ON a.artifact_id = c.artifact_id JOIN canonical_artifacts b ON b.artifact_id = NEW.object_artifact_id
        WHERE l.locator_id = NEW.object_locator_id AND a.canonical_id = b.canonical_id);
END;
CREATE TRIGGER assertion_decision_revision BEFORE INSERT ON assertion_decisions BEGIN
    SELECT RAISE(ABORT, 'stale assertion revision') WHERE NEW.expected_revision <> COALESCE((SELECT MAX(expected_revision) + 1 FROM assertion_decisions WHERE assertion_id = NEW.assertion_id), 0);
END;
CREATE TRIGGER claim_initial_revision AFTER INSERT ON claims BEGIN
    INSERT INTO claim_revisions VALUES (NEW.claim_id, NEW.revision, NEW.text, NEW.basis, NEW.temporal_scope_json, NEW.comparison_scope_json, NEW.status, NEW.reason, NEW.updated_batch_id);
END;
CREATE TRIGGER claim_next_revision AFTER UPDATE ON claims BEGIN
    INSERT INTO claim_revisions VALUES (NEW.claim_id, NEW.revision, NEW.text, NEW.basis, NEW.temporal_scope_json, NEW.comparison_scope_json, NEW.status, NEW.reason, NEW.updated_batch_id);
END;
CREATE TRIGGER outcome_correction BEFORE INSERT ON source_outcomes WHEN NEW.replaces_outcome_id IS NOT NULL BEGIN
    SELECT RAISE(ABORT, 'outcome correction must preserve artifact/question/scope') WHERE NOT EXISTS (SELECT 1 FROM source_outcomes s WHERE s.outcome_id = NEW.replaces_outcome_id AND s.artifact_id = NEW.artifact_id AND s.question_id = NEW.question_id AND s.required_scope_key = NEW.required_scope_key);
    SELECT RAISE(ABORT, 'reopening terminal readability requires changed basis') WHERE NEW.readability IN ('readable', 'transient_retry_pending', 'unknown') AND COALESCE(length(trim(NEW.changed_basis)), 0) = 0 AND EXISTS (SELECT 1 FROM source_outcomes s WHERE s.outcome_id = NEW.replaces_outcome_id AND s.readability IN ('access_blocked', 'unreadable', 'retry_exhausted'));
END;
CREATE TRIGGER assignment_generation BEFORE INSERT ON assignments BEGIN
    SELECT RAISE(ABORT, 'assignment generation differs from work') WHERE NOT EXISTS (SELECT 1 FROM work_items WHERE work_id = NEW.work_id AND generation = NEW.generation_at_claim AND state IN ('pending', 'leased'));
END;
CREATE TRIGGER assignment_binding BEFORE UPDATE ON assignments BEGIN
    SELECT RAISE(ABORT, 'assignment binding is immutable') WHERE NEW.work_id <> OLD.work_id OR NEW.actor_id <> OLD.actor_id OR NEW.parent_assignment_id IS NOT OLD.parent_assignment_id OR NEW.generation_at_claim <> OLD.generation_at_claim OR NEW.plan_sha256 <> OLD.plan_sha256 OR NEW.plan_item_id <> OLD.plan_item_id OR NEW.claimed_at <> OLD.claimed_at;
    SELECT RAISE(ABORT, 'unfinished operation requires reconciliation') WHERE NEW.state IN ('completed', 'released', 'cancelled') AND EXISTS (SELECT 1 FROM operation_links WHERE assignment_id = OLD.assignment_id AND execution_state IN ('reserved', 'admitted', 'dispatched', 'interrupted', 'unknown'));
END;
CREATE TRIGGER operation_binding BEFORE UPDATE ON operation_links BEGIN
    SELECT RAISE(ABORT, 'operation request and assignment are immutable') WHERE NEW.assignment_id IS NOT OLD.assignment_id OR NEW.request_sha256 <> OLD.request_sha256;
    SELECT RAISE(ABORT, 'recorded budget admission is immutable') WHERE OLD.budget_event_id IS NOT NULL AND NEW.budget_event_id IS NOT OLD.budget_event_id;
    SELECT RAISE(ABORT, 'recorded operation receipt is immutable') WHERE OLD.receipt_sha256 IS NOT NULL AND NEW.receipt_sha256 IS NOT OLD.receipt_sha256;
END;
CREATE TRIGGER operation_transition BEFORE UPDATE OF execution_state ON operation_links WHEN NEW.execution_state <> OLD.execution_state BEGIN
    SELECT RAISE(ABORT, 'invalid operation transition') WHERE NOT (
        (OLD.execution_state = 'reserved' AND NEW.execution_state IN ('admitted', 'cancelled', 'unknown')) OR
        (OLD.execution_state = 'admitted' AND NEW.execution_state IN ('dispatched', 'cancelled', 'unknown')) OR
        (OLD.execution_state = 'dispatched' AND NEW.execution_state IN ('succeeded', 'failed', 'interrupted', 'unknown')) OR
        (OLD.execution_state IN ('interrupted', 'unknown') AND NEW.execution_state IN ('succeeded', 'failed', 'cancelled', 'unknown')));
END;
CREATE TRIGGER assignment_transition BEFORE UPDATE OF state ON assignments WHEN NEW.state <> OLD.state BEGIN
    SELECT RAISE(ABORT, 'invalid assignment transition') WHERE NOT (
        (OLD.state = 'leased' AND NEW.state IN ('running', 'reconciling', 'released', 'cancelled')) OR
        (OLD.state = 'running' AND NEW.state IN ('completed', 'reconciling', 'released', 'cancelled')) OR
        (OLD.state = 'reconciling' AND NEW.state IN ('running', 'completed', 'released', 'cancelled')));
END;
CREATE TRIGGER obligation_work_origin BEFORE UPDATE OF work_id ON followup_obligations WHEN NEW.work_id IS NOT NULL BEGIN
    SELECT RAISE(ABORT, 'scheduled obligation needs matching work origin') WHERE NOT EXISTS (SELECT 1 FROM work_origins WHERE work_id = NEW.work_id AND ((NEW.lead_id IS NOT NULL AND lead_id = NEW.lead_id) OR (NEW.reference_id IS NOT NULL AND reference_id = NEW.reference_id)));
END;
CREATE TRIGGER work_generation BEFORE UPDATE ON work_items BEGIN
    SELECT RAISE(ABORT, 'work generation cannot decrease') WHERE NEW.generation < OLD.generation;
    SELECT RAISE(ABORT, 'active assignment fixes work identity and scope') WHERE EXISTS (SELECT 1 FROM assignments WHERE work_id = OLD.work_id AND state IN ('leased', 'running', 'reconciling')) AND (NEW.kind <> OLD.kind OR NEW.generation <> OLD.generation OR NEW.request_json <> OLD.request_json OR NEW.logical_key <> OLD.logical_key OR NEW.question_id <> OLD.question_id OR NEW.artifact_id IS NOT OLD.artifact_id OR NEW.required_scope_key IS NOT OLD.required_scope_key OR NEW.format_policy_sha256 IS NOT OLD.format_policy_sha256 OR NEW.branch <> OLD.branch OR NEW.stage <> OLD.stage OR NEW.purpose <> OLD.purpose);
END;
CREATE TRIGGER work_transition BEFORE UPDATE OF state ON work_items WHEN NEW.state <> OLD.state BEGIN
    SELECT RAISE(ABORT, 'invalid work transition') WHERE NOT (
        (OLD.state = 'pending' AND NEW.state IN ('leased', 'blocked', 'deferred', 'cancelled')) OR
        (OLD.state = 'leased' AND NEW.state IN ('running', 'pending', 'blocked', 'deferred', 'cancelled')) OR
        (OLD.state = 'running' AND NEW.state IN ('completed', 'pending', 'blocked', 'deferred', 'cancelled')) OR
        (OLD.state IN ('completed', 'blocked', 'deferred', 'cancelled') AND NEW.state = 'pending'));
    SELECT RAISE(ABORT, 'pending work retains an active assignment') WHERE NEW.state = 'pending' AND EXISTS (SELECT 1 FROM assignments WHERE work_id = OLD.work_id AND state IN ('leased', 'running', 'reconciling'));
    SELECT RAISE(ABORT, 'work state requires matching active assignment') WHERE NEW.state IN ('leased', 'running') AND NOT EXISTS (SELECT 1 FROM assignments WHERE work_id = OLD.work_id AND state = NEW.state);
    SELECT RAISE(ABORT, 'finish assignment before bounding or completing work') WHERE NEW.state IN ('completed', 'blocked', 'deferred', 'cancelled') AND EXISTS (SELECT 1 FROM assignments WHERE work_id = OLD.work_id AND state IN ('leased', 'running', 'reconciling'));
    SELECT RAISE(ABORT, 'read completion requires current scoped inspection and return') WHERE NEW.state = 'completed' AND NEW.kind IN ('read', 'inspect_visuals') AND NOT EXISTS (
        SELECT 1 FROM sufficient_inspection s JOIN reader_returns r ON r.return_id = s.return_id
        JOIN assignments a ON a.assignment_id = r.assignment_id JOIN canonical_artifacts ca ON ca.artifact_id = NEW.artifact_id
        WHERE a.work_id = NEW.work_id AND a.state = 'completed' AND s.artifact_id = ca.canonical_id AND s.question_id = NEW.question_id AND s.required_scope_key = NEW.required_scope_key AND s.format_policy_sha256 = NEW.format_policy_sha256
        AND a.assignment_id = (SELECT latest.assignment_id FROM assignments latest JOIN ingest_batches b ON b.batch_id = latest.created_batch_id WHERE latest.work_id = NEW.work_id ORDER BY b.resulting_sequence DESC, latest.assignment_id DESC LIMIT 1));
END;
CREATE TRIGGER obligation_origin_retained BEFORE UPDATE ON followup_obligations BEGIN
    SELECT RAISE(ABORT, 'obligation origin is immutable') WHERE NEW.reference_id IS NOT OLD.reference_id OR NEW.lead_id IS NOT OLD.lead_id;
END;
CREATE TRIGGER obligation_terminal_scope BEFORE UPDATE ON followup_obligations WHEN NEW.state = 'terminal' BEGIN
    SELECT RAISE(ABORT, 'terminal obligation requires all question scopes addressed') WHERE
        NOT EXISTS (SELECT 1 FROM obligation_scope_status WHERE obligation_id = NEW.obligation_id) OR
        EXISTS (SELECT 1 FROM obligation_scope_status WHERE obligation_id = NEW.obligation_id AND (status IN ('unassigned', 'pending') OR (NEW.disposition = 'resolved' AND status <> 'resolved')));
END;
CREATE TRIGGER lead_terminal_scope BEFORE UPDATE ON leads WHEN NEW.disposition IN ('resolved', 'bounded', 'excluded', 'deferred') BEGIN
    SELECT RAISE(ABORT, 'terminal lead requires all question scopes addressed') WHERE
        NOT EXISTS (SELECT 1 FROM obligation_scope_status WHERE obligation_id = 'lead:' || NEW.lead_id) OR
        EXISTS (SELECT 1 FROM obligation_scope_status WHERE obligation_id = 'lead:' || NEW.lead_id AND (status IN ('unassigned', 'pending') OR (NEW.disposition = 'resolved' AND status <> 'resolved')));
END;
CREATE TRIGGER lead_origin_reopens_scope AFTER INSERT ON lead_origins BEGIN
    UPDATE followup_obligations SET state = 'pending', work_id = NULL, disposition = NULL,
        reason = 'Additional question scope requires reconciliation', revision = revision + 1, updated_batch_id = NEW.created_batch_id
    WHERE state = 'terminal' AND (lead_id = NEW.lead_id OR reference_id = NEW.reference_id)
        AND EXISTS (SELECT 1 FROM obligation_scope_status s WHERE s.obligation_id = followup_obligations.obligation_id AND s.status IN ('unassigned', 'pending'));
    UPDATE leads SET disposition = 'pending', reason = 'Additional question scope requires reconciliation', revision = revision + 1, updated_batch_id = NEW.created_batch_id
    WHERE lead_id = NEW.lead_id AND disposition IN ('resolved', 'bounded', 'excluded', 'deferred')
        AND EXISTS (SELECT 1 FROM obligation_scope_status s WHERE s.obligation_id = 'lead:' || NEW.lead_id AND s.status IN ('unassigned', 'pending'));
END;
CREATE TRIGGER work_origin_reopens_scope AFTER INSERT ON work_origins BEGIN
    UPDATE followup_obligations SET state = 'pending', work_id = NULL, disposition = NULL,
        reason = 'Additional work requires reconciliation', revision = revision + 1, updated_batch_id = NEW.created_batch_id
    WHERE state = 'terminal' AND (lead_id = NEW.lead_id OR reference_id = NEW.reference_id)
        AND EXISTS (SELECT 1 FROM obligation_scope_status s WHERE s.obligation_id = followup_obligations.obligation_id AND s.status IN ('unassigned', 'pending'));
    UPDATE leads SET disposition = 'pending', reason = 'Additional work requires reconciliation', revision = revision + 1, updated_batch_id = NEW.created_batch_id
    WHERE lead_id = NEW.lead_id AND disposition IN ('resolved', 'bounded', 'excluded', 'deferred')
        AND EXISTS (SELECT 1 FROM obligation_scope_status s WHERE s.obligation_id = 'lead:' || NEW.lead_id AND s.status IN ('unassigned', 'pending'));
END;
CREATE TRIGGER assertion_reopens_question_scope AFTER INSERT ON assertions WHEN NEW.question_id IS NOT NULL BEGIN
    UPDATE followup_obligations SET state = 'pending', work_id = NULL, disposition = NULL,
        reason = 'Additional source question requires reconciliation', revision = revision + 1, updated_batch_id = NEW.created_batch_id
    WHERE state = 'terminal' AND reference_id IS NOT NULL
        AND EXISTS (SELECT 1 FROM obligation_scope_status s WHERE s.obligation_id = followup_obligations.obligation_id AND s.status IN ('unassigned', 'pending'));
END;
CREATE TRIGGER inspection_reopens_question_scope AFTER INSERT ON inspection_scopes BEGIN
    UPDATE followup_obligations SET state = 'pending', work_id = NULL, disposition = NULL,
        reason = 'Additional inspection question requires reconciliation', revision = revision + 1,
        updated_batch_id = (SELECT created_batch_id FROM inspections WHERE inspection_id = NEW.inspection_id)
    WHERE state = 'terminal' AND reference_id IS NOT NULL
        AND EXISTS (SELECT 1 FROM obligation_scope_status s WHERE s.obligation_id = followup_obligations.obligation_id AND s.status IN ('unassigned', 'pending'));
END;

-- Immutable observations are corrected with new records and reconciliation.
CREATE TRIGGER schema_migrations_no_update BEFORE UPDATE ON schema_migrations BEGIN
    SELECT RAISE(ABORT, 'schema_migrations is append-only');
END;
CREATE TRIGGER schema_migrations_no_delete BEFORE DELETE ON schema_migrations BEGIN
    SELECT RAISE(ABORT, 'schema_migrations is append-only');
END;
CREATE TRIGGER graph_metadata_no_update BEFORE UPDATE ON graph_metadata BEGIN
    SELECT RAISE(ABORT, 'graph_metadata is append-only');
END;
CREATE TRIGGER graph_metadata_no_delete BEFORE DELETE ON graph_metadata BEGIN
    SELECT RAISE(ABORT, 'graph_metadata is append-only');
END;
CREATE TRIGGER actors_no_update BEFORE UPDATE ON actors BEGIN
    SELECT RAISE(ABORT, 'actors is append-only');
END;
CREATE TRIGGER actors_no_delete BEFORE DELETE ON actors BEGIN
    SELECT RAISE(ABORT, 'actors is append-only');
END;
CREATE TRIGGER ingest_batches_no_update BEFORE UPDATE ON ingest_batches BEGIN
    SELECT RAISE(ABORT, 'ingest_batches is append-only');
END;
CREATE TRIGGER ingest_batches_no_delete BEFORE DELETE ON ingest_batches BEGIN
    SELECT RAISE(ABORT, 'ingest_batches is append-only');
END;
CREATE TRIGGER graph_events_no_update BEFORE UPDATE ON graph_events BEGIN
    SELECT RAISE(ABORT, 'graph_events is append-only');
END;
CREATE TRIGGER graph_events_no_delete BEFORE DELETE ON graph_events BEGIN
    SELECT RAISE(ABORT, 'graph_events is append-only');
END;
CREATE TRIGGER contract_snapshots_no_update BEFORE UPDATE ON contract_snapshots BEGIN
    SELECT RAISE(ABORT, 'contract_snapshots is append-only');
END;
CREATE TRIGGER contract_snapshots_no_delete BEFORE DELETE ON contract_snapshots BEGIN
    SELECT RAISE(ABORT, 'contract_snapshots is append-only');
END;
CREATE TRIGGER plan_snapshots_no_update BEFORE UPDATE ON plan_snapshots BEGIN
    SELECT RAISE(ABORT, 'plan_snapshots is append-only');
END;
CREATE TRIGGER plan_snapshots_no_delete BEFORE DELETE ON plan_snapshots BEGIN
    SELECT RAISE(ABORT, 'plan_snapshots is append-only');
END;
CREATE TRIGGER plan_items_no_update BEFORE UPDATE ON plan_items BEGIN
    SELECT RAISE(ABORT, 'plan_items is append-only');
END;
CREATE TRIGGER plan_items_no_delete BEFORE DELETE ON plan_items BEGIN
    SELECT RAISE(ABORT, 'plan_items is append-only');
END;
CREATE TRIGGER artifact_keys_no_update BEFORE UPDATE ON artifact_keys BEGIN
    SELECT RAISE(ABORT, 'artifact_keys is append-only');
END;
CREATE TRIGGER artifact_keys_no_delete BEFORE DELETE ON artifact_keys BEGIN
    SELECT RAISE(ABORT, 'artifact_keys is append-only');
END;
CREATE TRIGGER artifact_versions_no_update BEFORE UPDATE ON artifact_versions BEGIN
    SELECT RAISE(ABORT, 'artifact_versions is append-only');
END;
CREATE TRIGGER artifact_versions_no_delete BEFORE DELETE ON artifact_versions BEGIN
    SELECT RAISE(ABORT, 'artifact_versions is append-only');
END;
CREATE TRIGGER captures_no_update BEFORE UPDATE ON captures BEGIN
    SELECT RAISE(ABORT, 'captures is append-only');
END;
CREATE TRIGGER captures_no_delete BEFORE DELETE ON captures BEGIN
    SELECT RAISE(ABORT, 'captures is append-only');
END;
CREATE TRIGGER locators_no_update BEFORE UPDATE ON locators BEGIN
    SELECT RAISE(ABORT, 'locators is append-only');
END;
CREATE TRIGGER locators_no_delete BEFORE DELETE ON locators BEGIN
    SELECT RAISE(ABORT, 'locators is append-only');
END;
CREATE TRIGGER reader_returns_no_update BEFORE UPDATE ON reader_returns BEGIN
    SELECT RAISE(ABORT, 'reader_returns is append-only');
END;
CREATE TRIGGER reader_returns_no_delete BEFORE DELETE ON reader_returns BEGIN
    SELECT RAISE(ABORT, 'reader_returns is append-only');
END;
CREATE TRIGGER inspections_no_update BEFORE UPDATE ON inspections BEGIN
    SELECT RAISE(ABORT, 'inspections is append-only');
END;
CREATE TRIGGER inspections_no_delete BEFORE DELETE ON inspections BEGIN
    SELECT RAISE(ABORT, 'inspections is append-only');
END;
CREATE TRIGGER inspection_captures_no_update BEFORE UPDATE ON inspection_captures BEGIN
    SELECT RAISE(ABORT, 'inspection_captures is append-only');
END;
CREATE TRIGGER inspection_captures_no_delete BEFORE DELETE ON inspection_captures BEGIN
    SELECT RAISE(ABORT, 'inspection_captures is append-only');
END;
CREATE TRIGGER inspection_scopes_no_update BEFORE UPDATE ON inspection_scopes BEGIN
    SELECT RAISE(ABORT, 'inspection_scopes is append-only');
END;
CREATE TRIGGER inspection_scopes_no_delete BEFORE DELETE ON inspection_scopes BEGIN
    SELECT RAISE(ABORT, 'inspection_scopes is append-only');
END;
CREATE TRIGGER reference_scans_no_update BEFORE UPDATE ON reference_scans BEGIN
    SELECT RAISE(ABORT, 'reference_scans is append-only');
END;
CREATE TRIGGER reference_scans_no_delete BEFORE DELETE ON reference_scans BEGIN
    SELECT RAISE(ABORT, 'reference_scans is append-only');
END;
CREATE TRIGGER reference_occurrences_no_update BEFORE UPDATE ON reference_occurrences BEGIN
    SELECT RAISE(ABORT, 'reference_occurrences is append-only');
END;
CREATE TRIGGER reference_occurrences_no_delete BEFORE DELETE ON reference_occurrences BEGIN
    SELECT RAISE(ABORT, 'reference_occurrences is append-only');
END;
CREATE TRIGGER reference_resolutions_no_update BEFORE UPDATE ON reference_resolutions BEGIN
    SELECT RAISE(ABORT, 'reference_resolutions is append-only');
END;
CREATE TRIGGER reference_resolutions_no_delete BEFORE DELETE ON reference_resolutions BEGIN
    SELECT RAISE(ABORT, 'reference_resolutions is append-only');
END;
CREATE TRIGGER discoveries_no_update BEFORE UPDATE ON discoveries BEGIN
    SELECT RAISE(ABORT, 'discoveries is append-only');
END;
CREATE TRIGGER discoveries_no_delete BEFORE DELETE ON discoveries BEGIN
    SELECT RAISE(ABORT, 'discoveries is append-only');
END;
CREATE TRIGGER role_vocabulary_no_update BEFORE UPDATE ON role_vocabulary BEGIN
    SELECT RAISE(ABORT, 'role_vocabulary is append-only');
END;
CREATE TRIGGER role_vocabulary_no_delete BEFORE DELETE ON role_vocabulary BEGIN
    SELECT RAISE(ABORT, 'role_vocabulary is append-only');
END;
CREATE TRIGGER relationship_vocabulary_no_update BEFORE UPDATE ON relationship_vocabulary BEGIN
    SELECT RAISE(ABORT, 'relationship_vocabulary is append-only');
END;
CREATE TRIGGER relationship_vocabulary_no_delete BEFORE DELETE ON relationship_vocabulary BEGIN
    SELECT RAISE(ABORT, 'relationship_vocabulary is append-only');
END;
CREATE TRIGGER assertions_no_update BEFORE UPDATE ON assertions BEGIN
    SELECT RAISE(ABORT, 'assertions is append-only');
END;
CREATE TRIGGER assertions_no_delete BEFORE DELETE ON assertions BEGIN
    SELECT RAISE(ABORT, 'assertions is append-only');
END;
CREATE TRIGGER assertion_evidence_no_update BEFORE UPDATE ON assertion_evidence BEGIN
    SELECT RAISE(ABORT, 'assertion_evidence is append-only');
END;
CREATE TRIGGER assertion_evidence_no_delete BEFORE DELETE ON assertion_evidence BEGIN
    SELECT RAISE(ABORT, 'assertion_evidence is append-only');
END;
CREATE TRIGGER assertion_decisions_no_update BEFORE UPDATE ON assertion_decisions BEGIN
    SELECT RAISE(ABORT, 'assertion_decisions is append-only');
END;
CREATE TRIGGER assertion_decisions_no_delete BEFORE DELETE ON assertion_decisions BEGIN
    SELECT RAISE(ABORT, 'assertion_decisions is append-only');
END;
CREATE TRIGGER claim_revisions_no_update BEFORE UPDATE ON claim_revisions BEGIN
    SELECT RAISE(ABORT, 'claim_revisions is append-only');
END;
CREATE TRIGGER claim_revisions_no_delete BEFORE DELETE ON claim_revisions BEGIN
    SELECT RAISE(ABORT, 'claim_revisions is append-only');
END;
CREATE TRIGGER claim_evidence_no_update BEFORE UPDATE ON claim_evidence BEGIN
    SELECT RAISE(ABORT, 'claim_evidence is append-only');
END;
CREATE TRIGGER claim_evidence_no_delete BEFORE DELETE ON claim_evidence BEGIN
    SELECT RAISE(ABORT, 'claim_evidence is append-only');
END;
CREATE TRIGGER claim_questions_no_update BEFORE UPDATE ON claim_questions BEGIN
    SELECT RAISE(ABORT, 'claim_questions is append-only');
END;
CREATE TRIGGER claim_questions_no_delete BEFORE DELETE ON claim_questions BEGIN
    SELECT RAISE(ABORT, 'claim_questions is append-only');
END;
CREATE TRIGGER lead_origins_no_update BEFORE UPDATE ON lead_origins BEGIN
    SELECT RAISE(ABORT, 'lead_origins is append-only');
END;
CREATE TRIGGER lead_origins_no_delete BEFORE DELETE ON lead_origins BEGIN
    SELECT RAISE(ABORT, 'lead_origins is append-only');
END;
CREATE TRIGGER work_origins_no_update BEFORE UPDATE ON work_origins BEGIN
    SELECT RAISE(ABORT, 'work_origins is append-only');
END;
CREATE TRIGGER work_origins_no_delete BEFORE DELETE ON work_origins BEGIN
    SELECT RAISE(ABORT, 'work_origins is append-only');
END;
CREATE TRIGGER source_outcomes_no_update BEFORE UPDATE ON source_outcomes BEGIN
    SELECT RAISE(ABORT, 'source_outcomes is append-only');
END;
CREATE TRIGGER source_outcomes_no_delete BEFORE DELETE ON source_outcomes BEGIN
    SELECT RAISE(ABORT, 'source_outcomes is append-only');
END;
CREATE TRIGGER export_manifests_no_update BEFORE UPDATE ON export_manifests BEGIN
    SELECT RAISE(ABORT, 'export_manifests is append-only');
END;
CREATE TRIGGER export_manifests_no_delete BEFORE DELETE ON export_manifests BEGIN
    SELECT RAISE(ABORT, 'export_manifests is append-only');
END;
CREATE TRIGGER prior_run_imports_no_update BEFORE UPDATE ON prior_run_imports BEGIN
    SELECT RAISE(ABORT, 'prior_run_imports is append-only');
END;
CREATE TRIGGER prior_run_imports_no_delete BEFORE DELETE ON prior_run_imports BEGIN
    SELECT RAISE(ABORT, 'prior_run_imports is append-only');
END;
CREATE TRIGGER prior_run_records_no_update BEFORE UPDATE ON prior_run_records BEGIN
    SELECT RAISE(ABORT, 'prior_run_records is append-only');
END;
CREATE TRIGGER prior_run_records_no_delete BEFORE DELETE ON prior_run_records BEGIN
    SELECT RAISE(ABORT, 'prior_run_records is append-only');
END;
CREATE TRIGGER artifacts_revision BEFORE UPDATE ON artifacts BEGIN
    SELECT RAISE(ABORT, 'revision must advance by one') WHERE NEW.revision <> OLD.revision + 1;
    SELECT RAISE(ABORT, 'record identity and origin are immutable') WHERE NEW.artifact_id <> OLD.artifact_id OR NEW.created_batch_id <> OLD.created_batch_id;
END;
CREATE TRIGGER artifacts_no_delete BEFORE DELETE ON artifacts BEGIN
    SELECT RAISE(ABORT, 'artifacts retains history');
END;
CREATE TRIGGER questions_revision BEFORE UPDATE ON questions BEGIN
    SELECT RAISE(ABORT, 'revision must advance by one') WHERE NEW.revision <> OLD.revision + 1;
    SELECT RAISE(ABORT, 'record identity and origin are immutable') WHERE NEW.question_id <> OLD.question_id OR NEW.created_batch_id <> OLD.created_batch_id;
END;
CREATE TRIGGER questions_no_delete BEFORE DELETE ON questions BEGIN
    SELECT RAISE(ABORT, 'questions retains history');
END;
CREATE TRIGGER work_items_revision BEFORE UPDATE ON work_items BEGIN
    SELECT RAISE(ABORT, 'revision must advance by one') WHERE NEW.revision <> OLD.revision + 1;
    SELECT RAISE(ABORT, 'record identity and origin are immutable') WHERE NEW.work_id <> OLD.work_id OR NEW.created_batch_id <> OLD.created_batch_id;
END;
CREATE TRIGGER work_items_no_delete BEFORE DELETE ON work_items BEGIN
    SELECT RAISE(ABORT, 'work_items retains history');
END;
CREATE TRIGGER assignments_revision BEFORE UPDATE ON assignments BEGIN
    SELECT RAISE(ABORT, 'revision must advance by one') WHERE NEW.revision <> OLD.revision + 1;
    SELECT RAISE(ABORT, 'record identity and origin are immutable') WHERE NEW.assignment_id <> OLD.assignment_id OR NEW.created_batch_id <> OLD.created_batch_id;
END;
CREATE TRIGGER assignments_no_delete BEFORE DELETE ON assignments BEGIN
    SELECT RAISE(ABORT, 'assignments retains history');
END;
CREATE TRIGGER operation_links_revision BEFORE UPDATE ON operation_links BEGIN
    SELECT RAISE(ABORT, 'revision must advance by one') WHERE NEW.revision <> OLD.revision + 1;
    SELECT RAISE(ABORT, 'record identity and origin are immutable') WHERE NEW.operation_id <> OLD.operation_id OR NEW.created_batch_id <> OLD.created_batch_id;
END;
CREATE TRIGGER operation_links_no_delete BEFORE DELETE ON operation_links BEGIN
    SELECT RAISE(ABORT, 'operation_links retains history');
END;
CREATE TRIGGER claims_revision BEFORE UPDATE ON claims BEGIN
    SELECT RAISE(ABORT, 'revision must advance by one') WHERE NEW.revision <> OLD.revision + 1;
    SELECT RAISE(ABORT, 'record identity and origin are immutable') WHERE NEW.claim_id <> OLD.claim_id OR NEW.created_batch_id <> OLD.created_batch_id;
END;
CREATE TRIGGER claims_no_delete BEFORE DELETE ON claims BEGIN
    SELECT RAISE(ABORT, 'claims retains history');
END;
CREATE TRIGGER leads_revision BEFORE UPDATE ON leads BEGIN
    SELECT RAISE(ABORT, 'revision must advance by one') WHERE NEW.revision <> OLD.revision + 1;
    SELECT RAISE(ABORT, 'record identity and origin are immutable') WHERE NEW.lead_id <> OLD.lead_id OR NEW.created_batch_id <> OLD.created_batch_id;
END;
CREATE TRIGGER leads_no_delete BEFORE DELETE ON leads BEGIN
    SELECT RAISE(ABORT, 'leads retains history');
END;
CREATE TRIGGER followup_obligations_revision BEFORE UPDATE ON followup_obligations BEGIN
    SELECT RAISE(ABORT, 'revision must advance by one') WHERE NEW.revision <> OLD.revision + 1;
    SELECT RAISE(ABORT, 'record identity and origin are immutable') WHERE NEW.obligation_id <> OLD.obligation_id OR NEW.created_batch_id <> OLD.created_batch_id;
END;
CREATE TRIGGER followup_obligations_no_delete BEFORE DELETE ON followup_obligations BEGIN
    SELECT RAISE(ABORT, 'followup_obligations retains history');
END;
