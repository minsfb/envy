#!/usr/bin/env python3
"""Local, transactional storage for the retrospective research graph.

No provider calls or budget counters live here. Native evidence and execution
mirrors require the recorder validator supplied by the bundle integration.
Actor labels express workflow scope; they are not authenticated identities.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import tempfile
import threading
import time


SCHEMA = Path(__file__).with_name("research_graph_schema.sql")
INSPECTION_WITHDRAWALS_SCHEMA = Path(__file__).with_name("research_graph_inspection_withdrawals.sql")
LATE_RECEIPTS_SCHEMA = Path(__file__).with_name("research_graph_late_receipts.sql")
VERSION = 1
STORAGE_REVISION = 3
MIGRATIONS = {
    2: ("research-graph-inspection-withdrawals-v2", INSPECTION_WITHDRAWALS_SCHEMA),
    3: ("research-graph-late-receipts-v3", LATE_RECEIPTS_SCHEMA),
}
MAX_PACKET = 16 * 1024 * 1024
MAX_RECORD = 256 * 1024
BUSY_MS = 5000
IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}\Z")
HASH = re.compile(r"[0-9a-f]{64}\Z")
V1_TABLES = frozenset("actors contract_snapshots plan_snapshots plan_items artifacts artifact_keys artifact_versions questions work_items work_origins assignments operation_links captures locators reader_returns inspections inspection_captures inspection_scopes reference_scans reference_occurrences reference_resolutions discoveries role_vocabulary relationship_vocabulary assertions assertion_evidence assertion_decisions claims claim_evidence claim_questions leads lead_origins source_outcomes prior_run_imports prior_run_records".split())
TABLES = V1_TABLES | {"inspection_withdrawals"}
AUTOMATIC = frozenset("graph_metadata schema_migrations ingest_batches graph_events export_manifests claim_revisions followup_obligations".split())
INJECTED = frozenset("created_batch_id updated_batch_id registration_batch_id imported_batch_id revision".split())
UPDATES = {
    "artifacts": set("title artifact_type published_at canonical_artifact_id canonical_basis".split()),
    "questions": set("text central contract_revision current_status reason".split()),
    "work_items": set("kind artifact_id question_id request_json logical_key required_scope_key format_policy_sha256 purpose stage branch generation priority state terminal_reason transition_reason".split()),
    "assignments": {"lease_expires_at", "finished_at", "state"},
    "operation_links": {"budget_event_id", "receipt_sha256", "execution_state", "verification_state", "return_id"},
    "claims": set("text basis temporal_scope_json comparison_scope_json status reason".split()),
    "leads": {"normalized_key", "significance", "disposition", "reason"},
    "followup_obligations": {"work_id", "state", "disposition", "reason"},
}
OBSERVATIONS = frozenset("artifacts artifact_keys artifact_versions discoveries captures locators reference_scans reference_occurrences assertions assertion_evidence leads lead_origins work_items work_origins source_outcomes operation_links".split())
READER_TABLES = frozenset("reader_returns inspections inspection_captures inspection_scopes".split())
REVIEWERS = {"coverage_reviewer", "evidence_reviewer"}
NATIVE_TABLES = frozenset("operation_links captures reader_returns inspections inspection_captures inspection_scopes".split())
UNITS = {"paragraph", "line", "page", "slide", "frame", "node", "comment", "revision", "seconds"}
_LAYOUT = None
_WRITER_LOCAL = threading.local()


class GraphError(Exception):
    def __init__(self, code, message, retryable=False, **details):
        super().__init__(message)
        self.code, self.message, self.retryable = code, message, retryable
        self.details = details

    def response(self, command):
        return {"status": "fail", "schema_version": VERSION, "command": command,
                "error": {"code": self.code, "message": self.message,
                          "retryable": self.retryable, **self.details}}


def _require(condition, code, message, **details):
    if not condition:
        raise GraphError(code, message, **details)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _bytes(value):
    return (_json(value) + "\n").encode("utf-8")


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, "invalid_packet", "Duplicate JSON object key")
        result[key] = value
    return result


def _read_json(data):
    try:
        result = json.loads(data, object_pairs_hook=_pairs,
                            parse_constant=lambda value: (_ for _ in ()).throw(ValueError()))
        _depth(result)
        return result
    except (UnicodeError, ValueError, RecursionError, TypeError):
        raise GraphError("invalid_packet", "Input is not bounded, finite UTF-8 JSON") from None


def _depth(value, depth=0):
    _require(depth <= 32, "invalid_packet", "JSON nesting exceeds 32 levels")
    if isinstance(value, dict):
        _require(all(isinstance(key, str) for key in value), "invalid_packet", "JSON keys must be strings")
        for child in value.values():
            _depth(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            _depth(child, depth + 1)
    elif isinstance(value, float):
        _require(math.isfinite(value), "invalid_packet", "JSON numbers must be finite")
    else:
        _require(value is None or type(value) in (str, int, bool), "invalid_packet", "Unsupported JSON value")


def _id(value, filename=False):
    _require(isinstance(value, str) and IDENTITY.fullmatch(value) is not None
             and (not filename or ":" not in value), "invalid_packet", "Invalid record identity")
    return value


def _timestamp(value):
    try:
        _require(isinstance(value, str) and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|\+00:00)", value),
                 "invalid_packet", "Timestamp must be UTC RFC 3339")
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.isoformat(timespec="microseconds").replace("+00:00", "Z")
    except ValueError:
        raise GraphError("invalid_packet", "Invalid UTC timestamp") from None


def _object(value, keys, required=()):
    _require(isinstance(value, dict) and set(value) <= set(keys) and set(required) <= set(value),
             "invalid_packet", "Unexpected or missing structured fields")


def _strings(value):
    return isinstance(value, list) and all(isinstance(v, str) and v.strip() for v in value)


def _extent(value):
    _object(value, {"body", "comments", "revisions", "visuals", "provider_metadata"})
    for key, component in value.items():
        if key == "provider_metadata":
            _require(isinstance(component, dict), "invalid_packet", "Provider metadata must be an object")
        elif isinstance(component, str):
            _require(component in {"all", "none", "not_retained", "unavailable"}, "invalid_packet", "Unknown extent component")
        else:
            _object(component, {"unit", "ids", "ranges", "attestation", "limitations", "complete"})
            if "unit" in component:
                _require(component["unit"] in UNITS, "invalid_packet", "Unknown extent unit")
            if "ids" in component:
                _require(_strings(component["ids"]), "invalid_packet", "Extent IDs must be strings")
            for key in ("attestation", "limitations"):
                if key in component:
                    _require(isinstance(component[key], str), "invalid_packet", "Extent attestation must be text")
            if "ranges" in component:
                _require(component.get("unit") in UNITS and isinstance(component["ranges"], list), "invalid_packet", "Ranges require a unit and array")
                for pair in component["ranges"]:
                    _object(pair, {"start", "end"}, {"start", "end"})
                    numbers = (int, float) if component["unit"] == "seconds" else (int,)
                    _require(type(pair["start"]) in numbers and type(pair["end"]) in numbers and 0 <= pair["start"] <= pair["end"], "invalid_packet", "Invalid extent range")
            if "complete" in component:
                _require(type(component["complete"]) is bool, "invalid_packet", "Extent completeness must be a boolean")
                if component["complete"]:
                    _require(component.get("ranges") and component.get("unit") in UNITS
                             and all(isinstance(component.get(k), str) and component[k].strip() for k in ("attestation", "limitations")),
                             "invalid_packet", "Complete captured component needs numeric bounds, completeness attestation and limitations")


def meaningful_read_extent(value, *, allow_opaque=True):
    """Require a real position in every requested component, not vacuous sets.

    Call after _extent validates the shape. Empty/unavailable capture metadata
    remains representable; it cannot itself request or establish reading credit.
    Explicit historical opaque attestations retain their original interpretation.
    """
    required = [part for key, part in value.items()
                if key != "provider_metadata" and part not in ("none", "not_retained", "unavailable")]
    for part in required:
        if part == "all":
            continue
        positions = [part[key] for key in ("ids", "ranges") if key in part]
        if positions:
            if not all(positions):
                return False
        elif not (allow_opaque and all(isinstance(part.get(key), str) and part[key].strip()
                                      for key in ("attestation", "limitations"))):
            return False
    return bool(required)


def extent_contains(available, requested):
    """Strict matching of normalized positions; no text-to-range inference."""
    for component, wanted in requested.items():
        if component == "provider_metadata" or wanted in ("none", "not_retained", "unavailable"):
            continue
        have = available.get(component)
        if have == "all" or have == wanted:
            continue
        if not isinstance(have, dict) or not isinstance(wanted, dict) or have.get("unit") != wanted.get("unit"):
            return False
        if "ids" in wanted and not set(wanted["ids"]) <= set(have.get("ids", [])):
            return False
        if "ranges" in wanted and not all(any(h["start"] <= w["start"] <= w["end"] <= h["end"] for h in have.get("ranges", [])) for w in wanted["ranges"]):
            return False
        if not any(k in wanted for k in ("ids", "ranges")) and have != wanted:
            return False
    return True


def locator_within_extent(locator_kind, position, extent):
    """Match normalized locator positions in their typed extent component.

    None means the locator is opaque and needs an explicit attestation; it
    never authorizes restoration of withdrawn credit by numeric inference.
    """
    required = {}
    if "start" in position:
        required.update(unit=position["unit"], ranges=[{"start": position["start"], "end": position["end"]}])
    if "id" in position and "unit" in position:
        required.update(unit=position["unit"], ids=[position["id"]])
    if not required:
        return None
    component = ("comments" if locator_kind == "comment" else
                 "visuals" if locator_kind in ("slide", "timestamp") else "body")
    return extent_contains(extent, {component: required})


def satisfies_scope(captured_extent, inspected_extent, requested_extent):
    """Check requested coverage against actual captured and inspected bounds.

    `all` requires an explicit complete-component capture attestation and full
    numeric inspection. It does not prove source meaning, provider completeness,
    or coverage of any other component. Format/version checks remain separate.
    """
    for extent in (captured_extent, inspected_extent, requested_extent):
        _extent(extent)
    if not meaningful_read_extent(requested_extent):
        return False
    if not extent_contains(captured_extent, inspected_extent):
        return False
    for component, wanted in requested_extent.items():
        if component == "provider_metadata" or wanted in ("none", "not_retained", "unavailable"):
            continue
        if wanted == "all":
            capture = captured_extent.get(component)
            inspected = inspected_extent.get(component)
            if not isinstance(capture, dict) or capture.get("complete") is not True or not isinstance(inspected, dict):
                return False
            if inspected.get("unit") != capture["unit"] or not set(capture.get("ids", [])) <= set(inspected.get("ids", [])):
                return False
            # Consecutive inclusive integer ranges may jointly cover the body;
            # time ranges are continuous and cannot bridge a positive gap.
            merged = []
            for pair in sorted(inspected.get("ranges", []), key=lambda value: (value["start"], value["end"])):
                adjacent = 0 if capture["unit"] == "seconds" else 1
                if merged and pair["start"] <= merged[-1][1] + adjacent:
                    merged[-1][1] = max(merged[-1][1], pair["end"])
                else:
                    merged.append([pair["start"], pair["end"]])
            if not all(any(start <= pair["start"] <= pair["end"] <= end for start, end in merged) for pair in capture["ranges"]):
                return False
        elif not extent_contains(inspected_extent, {component: wanted}):
            return False
    return True


def satisfying_read_captures(request, claimed_at, inspected_at, captures):
    """Shared current-scope predicate for native work and frozen stopping checks.

    Captures have decoded JSON fields. Callers establish native provenance,
    retained-byte integrity, latest assignment and effective inspection first.
    This predicate gives neither retrieval authority nor semantic reading proof.
    """
    if not (request.get("capture_ids") or request.get("version_id")
            or request.get("freshness_basis") == "captured_after_assignment"):
        return []
    if _timestamp(inspected_at) < _timestamp(claimed_at):
        return []
    pinned = set(normalize_capture_ids(request["capture_ids"])) if "capture_ids" in request else None
    if pinned is not None and {c["capture_id"] for c in captures} != pinned:
        return []
    valid = [c for c in captures if c["extraction_status"] in {"complete", "partial"}
             and satisfies_scope(c["capture_extent_json"], c["inspected_extent_json"], request["extent"])
             and (c["capture_id"] in pinned if pinned is not None else c["version_id"] == request["version_id"]
                  if request.get("version_id") else _timestamp(c["captured_at"]) >= _timestamp(claimed_at))]
    if pinned is not None and {c["capture_id"] for c in valid} != pinned:
        return []
    formats = request.get("formats", [request.get("format")])
    return valid if formats and all(any(c["format"] == fmt for c in valid) for fmt in formats) else []


def normalize_capture_ids(values):
    """Canonical identity set for an explicit retained-capture read binding."""
    _require(isinstance(values, list) and values and all(isinstance(value, str) for value in values),
             "invalid_packet", "Capture IDs must be a nonempty identity array")
    for value in values:
        _id(value)
    _require(len(values) == len(set(values)), "invalid_packet", "Capture IDs must be unique")
    return sorted(values)


def _json_column(table, column, value, record):
    if value is None:
        return
    if column == "value_json":
        kind = record.get("kind")
        if kind in ("role", "relevance"):
            _require(isinstance(value, str) and value.strip(), "invalid_packet", "Assessment value must be a string")
        elif kind == "finding":
            _object(value, {"text"}, {"text"})
            _require(isinstance(value["text"], str) and value["text"].strip(), "invalid_packet", "Finding text is required")
        elif kind == "contribution":
            _object(value, {"contributor", "text", "attribution_basis"}, {"contributor", "text", "attribution_basis"})
            _require(all(isinstance(v, str) and v.strip() for v in value.values()), "invalid_packet", "Contribution values must be nonempty strings")
        else:
            raise GraphError("invalid_packet", "Relationship assertions do not have values")
    elif column in {"capture_extent_json", "scanned_extent_json", "inspected_extent_json"}:
        _extent(value)
    elif column == "request_json":
        _object(value, set("provider route source_id version_id freshness_basis capture_ids format purpose formats extent query filters cursor provider_metadata".split()))
        for key in set(value) & set("provider route source_id version_id freshness_basis format purpose".split()):
            _require(isinstance(value[key], str) and value[key].strip(), "invalid_packet", "Request field must be nonempty text")
        for key in ("filters", "provider_metadata"):
            if key in value:
                _require(isinstance(value[key], dict), "invalid_packet", "Provider payload must be an object")
        if "query" in value:
            _require(isinstance(value["query"], (str, dict)) and bool(value["query"]), "invalid_packet", "Query must be nonempty text or structured data")
        if "formats" in value:
            _require(_strings(value["formats"]) and value["formats"], "invalid_packet", "Formats must be a nonempty string array")
        if "extent" in value:
            _extent(value["extent"])
        if "capture_ids" in value:
            normalize_capture_ids(value["capture_ids"])
            _require(record.get("kind") in {"read", "inspect_visuals"}, "invalid_packet", "Capture binding is only supported for read work")
            _require(not {"version_id", "freshness_basis"} & set(value), "invalid_packet",
                     "Capture IDs cannot be combined with version or freshness policy")
        if record.get("kind") in {"read", "inspect_visuals"}:
            _require(value.get("source_id") and value.get("extent") and (value.get("format") or value.get("formats")), "invalid_packet", "Read work needs source, extent and formats")
        if record.get("kind") == "search":
            _require(value.get("query") or value.get("provider_metadata"), "invalid_packet", "Search work needs its query")
    elif column == "locator_json":
        _object(value, {"id", "path", "heading", "unit", "start", "end", "provider_metadata"})
        _require(any(key in value for key in ("id", "path", "heading", "start", "provider_metadata")), "invalid_packet", "Locator needs an explicit position")
        for key in ("id", "path", "heading"):
            if key in value:
                _require(isinstance(value[key], str) and value[key].strip(), "invalid_packet", "Locator field must be text")
        if "provider_metadata" in value:
            _require(isinstance(value["provider_metadata"], dict), "invalid_packet", "Provider locator must be an object")
        if "unit" in value:
            _require(value["unit"] in UNITS, "invalid_packet", "Unknown locator unit")
        if "start" in value or "end" in value:
            _require(value.get("unit") in UNITS and "start" in value and "end" in value, "invalid_packet", "Numeric locator requires unit and bounds")
            numbers = (int, float) if value["unit"] == "seconds" else (int,)
            _require(type(value["start"]) in numbers and type(value["end"]) in numbers and 0 <= value["start"] <= value["end"], "invalid_packet", "Invalid locator range")
    elif column in {"temporal_scope_json", "comparison_scope_json"}:
        keys = {"event_start", "event_end", "precision", "note"} if column == "temporal_scope_json" else set("baseline alternative population denominator task period metric units measurement_kind limitations".split())
        _object(value, keys)
        for key, field in value.items():
            _require(_strings(field) if key == "limitations" else isinstance(field, str) and bool(field.strip()), "invalid_packet", "Invalid scope field type")
    elif column == "scope_json":
        _object(value, {"branches", "questions", "artifact_ids", "record_ids"})
        for key, field in value.items():
            if key == "record_ids":
                _object(field, {"artifact", "assertion", "inspection", "lead", "claim"})
                _require(all(_strings(ids) for ids in field.values()), "invalid_packet", "Import selections require ID arrays")
            else:
                _require(_strings(field), "invalid_packet", "Import selections require ID arrays")
        _require(any(bool(v) for v in value.values()), "invalid_packet", "Import scope cannot be unbounded")
    elif column == "lineage_json":
        _require(isinstance(value, list), "invalid_packet", "Lineage must be an array")
        for item in value:
            _object(item, {"run_id", "export_id", "manifest_sha256", "artifact_id", "original_namespace", "original_external_id", "limitations"}, {"run_id", "export_id"})
            _require(all(isinstance(v, str) and v.strip() for v in item.values()), "invalid_packet", "Lineage fields must be strings")
            if "manifest_sha256" in item:
                _require(HASH.fullmatch(item["manifest_sha256"]), "invalid_packet", "Invalid lineage hash")


def schema_bytes(revision=STORAGE_REVISION):
    """Return installed, trusted SQL for an exact supported storage revision."""
    _require(type(revision) is int and revision in {1, *MIGRATIONS}, "unsupported_schema", "Unsupported graph storage revision")
    return SCHEMA.read_bytes() + b"".join(b"\n" + MIGRATIONS[number][1].read_bytes() for number in range(2, revision + 1))


def schema_revision_from_tables(tables):
    """Read a frozen inventory's storage identity; never infer it from a new table."""
    _require(isinstance(tables, dict), "unsupported_schema", "Graph table inventory must be an object")
    migrations = tables.get("schema_migrations", [])
    _require(isinstance(migrations, list) and all(isinstance(row, dict) and type(row.get("schema_version")) is int for row in migrations),
             "unsupported_schema", "Unsupported graph migration records")
    revisions = {row["schema_version"] for row in migrations}
    _require(bool(revisions) and max(revisions) in {1, *MIGRATIONS}
             and revisions == set(range(1, max(revisions) + 1)) and len(migrations) == len(revisions),
             "unsupported_schema", "Unsupported graph migration history")
    for row in migrations:
        if row["schema_version"] == 1:
            _require(row.get("migration_id") == "research-graph-v1" and row.get("migration_sha256") == _sha(SCHEMA.read_bytes()),
                     "unsupported_schema", "Unsupported base graph schema identity")
        else:
            _migration_record(row)
    return max(revisions)


def storage_revision(connection):
    revision = connection.execute("PRAGMA user_version").fetchone()[0]
    _require(revision in {1, *MIGRATIONS}, "unsupported_schema", "Unsupported graph storage revision")
    return revision


def storage_tables(connection_or_tables):
    """Full table inventory for a live connection or a frozen graph snapshot."""
    revision = (schema_revision_from_tables(connection_or_tables) if isinstance(connection_or_tables, dict)
                else storage_revision(connection_or_tables))
    return (TABLES if revision >= 2 else V1_TABLES) | AUTOMATIC


def withdrawn_inspection_ids(connection):
    if storage_revision(connection) == 1:
        return set()
    return {row[0] for row in connection.execute("SELECT inspection_id FROM inspection_withdrawals")}


def _migration_record(record):
    _require(isinstance(record, dict) and type(record.get("schema_version")) is int and record["schema_version"] in MIGRATIONS,
             "unsupported_schema", "Unsupported graph storage migration")
    identity, source = MIGRATIONS[record["schema_version"]]
    _require(record.get("migration_id") == identity and record.get("migration_sha256") == _sha(source.read_bytes()),
             "unsupported_schema", "Unsupported graph storage migration")


def _apply_schema_migration(connection, record):
    """Apply trusted DDL without executescript's implicit transaction commit."""
    _migration_record(record)
    _require(connection.in_transaction and storage_revision(connection) == record["schema_version"] - 1,
             "integrity_error", "Storage migration must advance its preceding revision in its journal transaction")
    statement = ""
    for line in MIGRATIONS[record["schema_version"]][1].read_text(encoding="utf-8").splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            connection.execute(statement)
            statement = ""
    _require(not statement.strip(), "unsupported_schema", "Installed storage migration is incomplete")
    _fault("after_schema_ddl")


def _layout():
    global _LAYOUT
    if _LAYOUT is None:
        _require(sqlite3.sqlite_version_info >= (3, 38, 0), "unsupported_schema", "SQLite 3.38 or newer is required")
        with sqlite3.connect(":memory:") as connection:
            connection.executescript(schema_bytes().decode("utf-8"))
            _LAYOUT = {table: [dict(zip(("cid", "name", "type", "required", "default", "pk"), row)) for row in connection.execute("PRAGMA table_info(" + table + ")")]
                       for table in TABLES | AUTOMATIC}
    return _LAYOUT


def _pk(table):
    return [column["name"] for column in sorted(_layout()[table], key=lambda item: item["pk"]) if column["pk"]]


def _record_shape(table, record, insert=True, internal=False):
    _require(isinstance(record, dict), "invalid_packet", "Record must be a bounded object", table=table)
    size = len(_bytes(record))
    _require(size <= MAX_RECORD, "invalid_packet", "Record must be a bounded object",
             table=table, size_bytes=size, limit_bytes=MAX_RECORD)
    columns = {column["name"]: column for column in _layout()[table]}
    _require(set(record) <= set(columns), "invalid_packet", "Unknown record field", table=table)
    if not internal:
        _require(not set(record) & INJECTED, "permission_denied", "Audit and revision fields are controller-owned", table=table)
    if insert:
        required = {name for name, column in columns.items() if column["required"] and column["default"] is None}
        _require(required - INJECTED <= set(record), "invalid_packet", "Missing required record field", table=table)
    result = dict(record)
    for name, value in record.items():
        column = columns[name]
        if value is None:
            _require(not column["required"], "invalid_packet", "Required record field cannot be null", table=table)
            continue
        if name.endswith("_json"):
            _json_column(table, name, value, record)
            if name == "request_json" and "capture_ids" in value:
                result[name] = dict(value, capture_ids=normalize_capture_ids(value["capture_ids"]))
        elif column["type"] == "INTEGER":
            _require(type(value) is int, "invalid_packet", "Integer field cannot be coerced", table=table)
        else:
            _require(isinstance(value, str), "invalid_packet", "Text field cannot be coerced", table=table)
            if name.endswith("_sha256") or name in {"sha256", "excerpt_hash"}:
                _require(HASH.fullmatch(value), "invalid_packet", "Invalid SHA-256 value")
            elif name.endswith("_at"):
                result[name] = _timestamp(value)
            elif name.endswith("_id") and name not in {"external_id", "original_external_id", "declared_runtime_identity"}:
                _id(value, filename=name in {"operation_id", "return_id"})
    return result


def _packet(source):
    try:
        if isinstance(source, dict):
            data = _bytes(source)
        elif isinstance(source, bytes):
            data = source
        else:
            with Path(source).open("rb") as stream:
                data = stream.read(MAX_PACKET + 1)
    except (OSError, TypeError, ValueError):
        raise GraphError("invalid_packet", "Cannot read update packet") from None
    _require(len(data) <= MAX_PACKET, "invalid_packet", "Packet exceeds 16 MiB")
    packet = _read_json(data)
    keys = {"schema_version", "batch_id", "actor_id", "assignment_id", "packet_kind", "contract_sha256", "submitted_at", "mutations"}
    _object(packet, keys, keys)
    _require(type(packet["schema_version"]) is int and packet["schema_version"] == VERSION, "unsupported_schema", "Unsupported packet schema")
    _id(packet["batch_id"], filename=True)
    _id(packet["actor_id"])
    if packet["assignment_id"] is not None:
        _id(packet["assignment_id"])
    _require(isinstance(packet["contract_sha256"], str) and HASH.fullmatch(packet["contract_sha256"]), "invalid_packet", "Invalid contract hash")
    _timestamp(packet["submitted_at"])
    _require(isinstance(packet["packet_kind"], str) and packet["packet_kind"] in {"bootstrap", "observe", "reconcile", "import", "migrate"}, "invalid_packet", "Unknown packet kind")
    _require(isinstance(packet["mutations"], list) and 1 <= len(packet["mutations"]) <= 10000, "invalid_packet", "Packet needs 1..10000 mutations")
    return packet, data


def _root(root):
    try:
        path = Path(root).resolve(strict=True)
        _require(path.is_dir(), "integrity_error", "Bundle root is not a directory")
        return path
    except OSError:
        raise GraphError("integrity_error", "Bundle root is unavailable") from None


def _path(root, relative, exists=True):
    _require(isinstance(relative, str) and relative and not Path(relative).is_absolute()
             and all(part not in {"", ".", ".."} for part in relative.split("/"))
             and "\\" not in relative, "integrity_error", "Invalid bundle-relative path")
    path = root
    for part in Path(relative).parts:
        path = path / part
        _require(not path.is_symlink(), "integrity_error", "Bundle paths cannot traverse symlinks")
    if exists:
        _require(path.is_file(), "pending_dependency", "Required retained file is missing", path=relative)
    return path


def _sync_dir(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _retain(path, data):
    """Install complete fsynced bytes without replacing an existing pathname."""
    _require(not path.is_symlink(), "integrity_error", "Immutable file is a symlink")
    if path.exists():
        _require(path.is_file() and path.read_bytes() == data, "batch_conflict", "Immutable identity already has different bytes")
        return
    descriptor, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            _require(not path.is_symlink() and path.is_file() and path.read_bytes() == data, "batch_conflict", "Immutable identity already has different bytes")
        _sync_dir(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def _lock(root, exclusive=False, create=False, name=".research-graph.lock"):
    lock = _path(root, name, exists=False)
    flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | (os.O_CREAT if create else 0)
    try:
        descriptor = os.open(lock, flags, 0o600)
    except OSError:
        raise GraphError("integrity_error", "Graph lifecycle lock is unavailable") from None
    deadline = time.monotonic() + BUSY_MS / 1000
    try:
        while True:
            try:
                fcntl.flock(descriptor, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                _require(time.monotonic() < deadline, "busy", "Graph lifecycle is busy", retryable=True)
                time.sleep(0.025)
        yield
    finally:
        os.close(descriptor)


@contextmanager
def writer(root):
    """Serialize local normalization through ingest; nested calls are safe.

All supported writers use this lock. Do not perform provider calls or model
analysis inside it. The lifecycle lock separately protects database replacement.
"""
    root = _root(root)
    held = getattr(_WRITER_LOCAL, "held", None)
    if held is None:
        held = _WRITER_LOCAL.held = set()
    key = (os.getpid(), str(root))
    if key in held:
        yield
        return
    with _lock(root, exclusive=True, create=True, name=".research-graph-writer.lock"):
        held.add(key)
        try:
            yield
        finally:
            held.remove(key)


def _fault(point):
    """Private test seam. Production never configures a crash through input."""


def _connect(path, readonly=False, check=True, *, _bootstrap_wal=True):
    _layout()
    try:
        connection = sqlite3.connect(path.as_uri() + ("?mode=ro" if readonly else "?mode=rw"), uri=True, timeout=BUSY_MS / 1000, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=" + str(BUSY_MS))
        if readonly:
            connection.execute("PRAGMA query_only=ON")
        else:
            _require(connection.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() == "wal", "integrity_error", "WAL journaling is unavailable")
            connection.execute("PRAGMA synchronous=FULL")
            _require(connection.execute("PRAGMA synchronous").fetchone()[0] == 2, "integrity_error", "FULL synchronization is unavailable")
        _require(connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1, "integrity_error", "Foreign keys are disabled")
        if check:
            _check_version(connection)
        return connection
    except BaseException as error:
        if "connection" in locals():
            connection.close()
        # Python before 3.11 exposes neither SQLite result constants nor error
        # codes. Restrict that compatibility path to SQLite's exact open error;
        # errors with a result code always use the code, not their message.
        code = getattr(error, "sqlite_errorcode", None)
        cant_open = (code == getattr(sqlite3, "SQLITE_CANTOPEN", 14) if code is not None
                     else str(error) == "unable to open database file")
        if (readonly and _bootstrap_wal and isinstance(error, sqlite3.OperationalError)
                and cant_open
                and not path.with_name(path.name + "-wal").exists()):
            with path.open("rb") as stream:
                wal_header = stream.read(20)[18:20] == b"\x02\x02"
            if wal_header:
                # Some SQLite builds cannot create an absent WAL through mode=ro.
                # Let SQLite initialize its sidecars without executing any write,
                # then establish a real readonly reader before the opener closes.
                # Do not use immutable=1: a concurrent writer may have live WAL data.
                opener = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True,
                                         timeout=BUSY_MS / 1000, isolation_level=None)
                try:
                    opener.execute("PRAGMA query_only=ON")
                    opener.execute("PRAGMA user_version").fetchone()
                    return _connect(path, readonly=True, check=check, _bootstrap_wal=False)
                finally:
                    opener.close()
        raise


def _check_version(connection):
    revision = storage_revision(connection)
    metadata = connection.execute("SELECT schema_version FROM graph_metadata WHERE singleton=1").fetchone()
    migration = connection.execute("SELECT migration_id,migration_sha256 FROM schema_migrations WHERE schema_version=1").fetchone()
    _require(metadata is not None and metadata[0] == VERSION and migration is not None
             and migration[0] == "research-graph-v1" and migration[1] == _sha(SCHEMA.read_bytes()),
             "unsupported_schema", "Database and installed schema identities disagree")
    migrations = [dict(row) for row in connection.execute("SELECT * FROM schema_migrations ORDER BY schema_version")]
    _require([row["schema_version"] for row in migrations] == list(range(1, revision + 1)),
             "unsupported_schema", "Storage revision and migration history disagree")
    for migration in migrations[1:]:
        _migration_record(migration)
    actual = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    _require(actual == storage_tables(connection), "unsupported_schema", "Graph storage table inventory differs")


def _sql_error(error):
    message = str(error)
    if "locked" in message or "busy" in message:
        return GraphError("busy", "Graph write lock is busy", retryable=True)
    if "stale" in message or "revision" in message:
        return GraphError("revision_conflict", "Record revision changed")
    if "one_active_assignment" in message or "assignments.work_id" in message:
        return GraphError("lease_conflict", "Work already has an active assignment")
    if "FOREIGN KEY" in message:
        return GraphError("unknown_identity", "A referenced identity is unavailable")
    return GraphError("integrity_error", "Graph constraint or database integrity check failed")


@contextmanager
def snapshot(root):
    """Yield a read-only, consistent sqlite connection under the lifecycle lock."""
    root = _root(root)
    try:
        with _lock(root):
            _ready(root)
            connection = _connect(_path(root, "graph/research.sqlite"), readonly=True)
            try:
                connection.execute("BEGIN")
                # Establish the snapshot before yielding; BEGIN alone is deferred.
                connection.execute("SELECT MAX(sequence) FROM graph_events").fetchone()
                yield connection
            finally:
                connection.close()
    except sqlite3.Error as error:
        raise _sql_error(error) from None


def _decoded(row):
    if row is None:
        return None
    result = dict(row)
    for key, value in result.items():
        if key.endswith("_json") and value is not None:
            result[key] = json.loads(value)
    return result


def _identity(table, row):
    values = {key: row[key] for key in _pk(table)}
    return next(iter(values.values())) if len(values) == 1 else values


def _key(table, identity):
    keys = _pk(table)
    if len(keys) == 1:
        return {keys[0]: identity}
    _object(identity, keys, keys)
    return identity


def _get(connection, table, identity):
    key = _key(table, identity)
    return _decoded(connection.execute("SELECT * FROM " + table + " WHERE " + " AND ".join(name + "=?" for name in key), tuple(key.values())).fetchone())


def _insert(connection, table, record):
    names = list(record)
    values = [_json(record[name]) if name.endswith("_json") and record[name] is not None else record[name] for name in names]
    connection.execute("INSERT INTO " + table + " (" + ",".join(names) + ") VALUES (" + ",".join("?" for _ in names) + ")", values)


def _file(root, relative, expected, fingerprints):
    path = _path(root, relative)
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    after = path.stat()
    stamp = lambda stat: (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
    _require(stamp(before) == stamp(after) and digest.hexdigest() == expected, "integrity_error", "Retained file hash or identity changed", path=relative)
    fingerprints[relative] = stamp(after)
    return path


def _recheck_files(root, fingerprints):
    for relative, expected in fingerprints.items():
        stat = _path(root, relative).stat()
        _require((stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns) == expected,
                 "integrity_error", "Retained file changed during validation", path=relative)


def _historical_import(record, packet=None, connection=None):
    if packet is not None and packet.get("packet_kind") == "import":
        imports = [item["record"] for item in packet["mutations"] if item.get("op") == "insert" and item.get("table") == "prior_run_imports"]
    elif connection is not None and record.get("created_batch_id"):
        batch = _get(connection, "ingest_batches", record["created_batch_id"])
        if batch is None or batch["packet_kind"] != "import":
            return None
        imports = [_decoded(row) for row in connection.execute("SELECT * FROM prior_run_imports WHERE imported_batch_id=?", (batch["batch_id"],))]
    else:
        return None
    _require(len(imports) == 1, "integrity_error", "Historical records require one accepted import binding")
    _id(imports[0]["import_id"], filename=True)
    return imports[0]


def _legacy_operation(record):
    _require(record and record.get("verification_state") == "legacy_attested"
             and all(record.get(key) is None for key in ("assignment_id", "budget_event_id", "return_id")),
             "permission_denied", "Imported operation cannot represent current execution or spending")


def _import_capture_receipt(record, packet=None, connection=None):
    binding = _historical_import(record, packet, connection)
    _require(binding is not None, "permission_denied", "Historical capture path requires an import binding")
    prefix = "graph/imports/" + binding["import_id"] + "/captures/" + _id(record["capture_id"], filename=True) + "/"
    _require(record["relative_path"] == prefix + "content" and record["extraction_status"] == "legacy_attested"
             and bool(record.get("limitations", "").strip()), "permission_denied", "Imported capture must retain its exact historical path and limitations")
    if connection is not None:
        mappings = [_decoded(row) for row in connection.execute("SELECT * FROM prior_run_records WHERE import_id=? AND artifact_id=? AND prior_record_kind='artifact'", (binding["import_id"], record["artifact_id"]))]
        operation = _get(connection, "operation_links", record["operation_id"])
    else:
        mappings = [item["record"] for item in packet["mutations"] if item.get("op") == "insert" and item.get("table") == "prior_run_records"
                    and item["record"].get("import_id") == binding["import_id"] and item["record"].get("artifact_id") == record["artifact_id"]
                    and item["record"].get("prior_record_kind") == "artifact"]
        operation = next((item["record"] for item in packet["mutations"] if item.get("op") == "insert" and item.get("table") == "operation_links"
                          and item["record"].get("operation_id") == record["operation_id"]), None)
        # A later bounded import can reuse an already accepted historical operation;
        # its full structural check happens in the transaction below.
    _require(any(any(item.get("run_id") == binding["prior_run_id"] and item.get("export_id") == binding["prior_export_id"]
                         and item.get("manifest_sha256") == binding["manifest_sha256"] for item in mapping["lineage_json"])
                 for mapping in mappings), "integrity_error", "Historical capture lacks its hash-bound original artifact lineage")
    if operation is not None or connection is not None:
        _legacy_operation(operation)
        _require(operation["receipt_sha256"] == record["receipt_sha256"], "integrity_error", "Historical capture and operation receipts disagree")
    return prefix + "receipt.json"


def _registered_export_files(root, record, fingerprints=None):
    """Expand a hash-bound manifest pointer without enlarging graph records.

    Existing expanded registrations remain valid and byte-exact. New records
    bind one manifest; its complete file inventory remains outside the packet.
    This is not recursive: prior exports represented in graph.json stay history.
    """
    entries = record["files_json"]
    _require(isinstance(entries, list) and entries, "integrity_error", "Registered export inventory is missing")
    if len(entries) != 1:
        return entries
    prefix = "graph/exports/" + _id(record["export_id"], filename=True) + "/"
    binding = entries[0]
    _object(binding, {"relative_path", "sha256", "format", "size_bytes"}, {"relative_path", "sha256", "format", "size_bytes"})
    _require(binding["relative_path"] == prefix + "manifest.json" and binding["format"] == "json",
             "integrity_error", "Compact export registration must bind its own manifest")
    path = _file(root, binding["relative_path"], binding["sha256"], fingerprints if fingerprints is not None else {})
    _require(type(binding["size_bytes"]) is int and path.stat().st_size == binding["size_bytes"],
             "integrity_error", "Registered export manifest size changed")
    manifest = _read_json(path.read_bytes())
    _require(isinstance(manifest, dict) and all(manifest.get(key) == record[key]
             for key in ("export_id", "graph_sequence", "schema_version", "contract_sha256", "plan_sha256", "budget_event_head"))
             and _timestamp(manifest.get("created_at")) == record["created_at"],
             "integrity_error", "Registered export manifest describes another snapshot")
    _require(isinstance(manifest.get("files"), list) and manifest["files"], "integrity_error", "Registered export manifest has no files")
    expanded, seen = [], set()
    for entry in manifest["files"]:
        _object(entry, {"relative_path", "sha256", "format", "size_bytes"}, {"relative_path", "sha256", "format", "size_bytes"})
        name = entry["relative_path"]
        _require(isinstance(name, str) and name not in seen and name != "manifest.json",
                 "integrity_error", "Duplicate or self-referential registered export file")
        _require(isinstance(entry["sha256"], str) and HASH.fullmatch(entry["sha256"])
                 and type(entry["size_bytes"]) is int and entry["size_bytes"] >= 0
                 and isinstance(entry["format"], str) and entry["format"].strip(),
                 "integrity_error", "Invalid registered export file binding")
        _path(root, prefix + name, exists=False)
        seen.add(name)
        expanded.append({**entry, "relative_path": prefix + name})
    return sorted(expanded + [binding], key=lambda entry: entry["relative_path"])


def _external_files(root, packet, connection=None):
    fingerprints = {}
    for mutation in packet["mutations"]:
        if mutation.get("op") != "insert":
            continue
        table, record = mutation.get("table"), mutation.get("record", {})
        if table == "contract_snapshots":
            path = _file(root, record["relative_path"], record["contract_sha256"], fingerprints)
            contract = _read_json(path.read_bytes())
            _require(isinstance(contract, dict) and contract.get("version") == 1 and isinstance(contract.get("questions", []), list), "integrity_error", "Invalid retained contract")
        elif table == "plan_snapshots":
            path = _file(root, record["relative_path"], record["plan_sha256"], fingerprints)
            plan = _read_json(path.read_bytes())
            _require(isinstance(plan, dict) and isinstance(plan.get("_record"), dict)
                     and plan["_record"].get("contract_sha256") == record["contract_sha256"]
                     and isinstance(plan.get("items"), list), "integrity_error", "Plan is not a retained recorder snapshot")
            ledger_hash = plan["_record"].get("ledger_sha256")
            _require(isinstance(ledger_hash, str) and HASH.fullmatch(ledger_hash), "integrity_error", "Plan ledger binding is missing")
            _file(root, "plans/" + record["plan_sha256"] + ".ledger.json", ledger_hash, fingerprints)
        elif table == "captures":
            _file(root, record["relative_path"], record["sha256"], fingerprints)
            historical = _historical_import(record, packet, connection)
            receipt = (_import_capture_receipt(record, packet, connection) if historical or record["relative_path"].startswith("graph/imports/")
                       else "operations/" + _id(record["operation_id"], filename=True) + "/receipt.json")
            _file(root, receipt, record["receipt_sha256"], fingerprints)
        elif table == "operation_links" and packet.get("packet_kind") == "import":
            binding = _historical_import(record, packet)
            _legacy_operation(record)
            prefix = "graph/imports/" + binding["import_id"] + "/operations/" + _id(record["operation_id"], filename=True) + "/"
            _file(root, prefix + "request.json", record["request_sha256"], fingerprints)
            if record.get("receipt_sha256") is not None:
                _file(root, prefix + "receipt.json", record["receipt_sha256"], fingerprints)
        elif table == "reader_returns":
            _file(root, record["file_path"], record["payload_sha256"], fingerprints)
        elif table == "inspection_withdrawals":
            _file(root, record["evidence_path"], record["evidence_sha256"], fingerprints)
        elif table == "locators" and record.get("excerpt") is not None:
            _require(_sha(record["excerpt"].encode("utf-8")) == record.get("excerpt_hash"), "integrity_error", "Excerpt hash does not match retained text")
        elif table == "export_manifests":
            for entry in _registered_export_files(root, record, fingerprints):
                path = _file(root, entry["relative_path"], entry["sha256"], fingerprints)
                _require(path.stat().st_size == entry["size_bytes"], "integrity_error", "Registered export file size changed")
    return fingerprints


def _accepted_files(root, connection):
    """Check retained bytes bound by the audited final rows, not pending packets.

Locators contain inline positions/excerpts; their external bytes are the bound
capture. Hash integrity does not establish positional or semantic inclusion,
native capture provenance, current freshness, or a new inspection.
"""
    tables = ("contract_snapshots", "plan_snapshots", "captures", "reader_returns", "locators", "export_manifests")
    if storage_revision(connection) >= 2:
        tables += ("inspection_withdrawals",)
    records = ({"op": "insert", "table": table, "record": _decoded(row)}
               for table in tables for row in connection.execute("SELECT * FROM " + table))
    try:
        fingerprints = _external_files(root, {"mutations": records}, connection)
        for raw in connection.execute("SELECT * FROM operation_links"):
            row = _decoded(raw)
            binding = _historical_import(row, connection=connection)
            base = "operations/" + _id(row["operation_id"], filename=True) + "/"
            if binding is not None:
                _legacy_operation(row)
                captures = [_decoded(c) for c in connection.execute("SELECT * FROM captures WHERE operation_id=?", (row["operation_id"],))]
                _require(captures, "integrity_error", "Imported operation lacks a mapped historical capture")
                for capture in captures:
                    _import_capture_receipt(capture, connection=connection)
                base = "graph/imports/" + binding["import_id"] + "/" + base
            _file(root, base + "request.json", row["request_sha256"], fingerprints)
            if row["receipt_sha256"] is not None:
                _file(root, base + "receipt.json", row["receipt_sha256"], fingerprints)
        for binding in connection.execute("SELECT p.* FROM prior_run_imports p JOIN ingest_batches b ON b.batch_id=p.imported_batch_id WHERE b.packet_kind='import'"):
            _file(root, "graph/imports/" + _id(binding["import_id"], filename=True) + "/manifest.json", binding["manifest_sha256"], fingerprints)
        _recheck_files(root, fingerprints)
    except GraphError as error:
        if error.code == "pending_dependency":
            raise GraphError("integrity_error", "Accepted evidence file is missing", **error.details) from None
        raise
    return {"external_files_checked": len(fingerprints),
            "locator_excerpts_checked": connection.execute("SELECT count(*) FROM locators WHERE excerpt IS NOT NULL").fetchone()[0]}


def _normal_mutations(packet, internal=False):
    normalized = []
    for mutation in packet["mutations"]:
        _require(isinstance(mutation, dict), "invalid_packet", "Mutation must be an object")
        operation, table = mutation.get("op"), mutation.get("table")
        _require(isinstance(table, str) and table in TABLES | AUTOMATIC, "permission_denied", "Table is not writable through packets")
        if operation == "insert":
            _object(mutation, {"op", "table", "record"}, {"op", "table", "record"})
            _require(internal or table in TABLES, "permission_denied", "Automatic records cannot be inserted by callers", table=table)
            record = _record_shape(table, mutation["record"], internal=internal)
            normalized.append({"op": operation, "table": table, "record": record})
        elif operation == "update":
            _object(mutation, {"op", "table", "id", "expected_revision", "changes"}, {"op", "table", "id", "expected_revision", "changes"})
            _require(table in UPDATES, "permission_denied", "Record is append-only", table=table)
            _require(type(mutation["expected_revision"]) is int and mutation["expected_revision"] >= 1, "invalid_packet", "Expected revision must be a positive integer")
            _id(mutation["id"])
            _require(isinstance(mutation["changes"], dict) and mutation["changes"] and set(mutation["changes"]) <= UPDATES[table], "permission_denied", "Update contains protected or unknown columns", table=table)
            # Complete-row type/shape validation follows lookup inside the transaction.
            normalized.append(dict(mutation))
        else:
            raise GraphError("invalid_packet", "Unknown mutation operation")
    return normalized


def _in_branch(branch, scope):
    return scope is None or branch == scope or (isinstance(branch, str) and branch.startswith(scope + "/"))


def _context(root, connection, packet):
    actor = _get(connection, "actors", packet["actor_id"])
    _require(actor is not None, "unknown_identity", "Packet actor is not registered")
    assignment = None
    if packet["assignment_id"] is not None:
        assignment = _get(connection, "assignments", packet["assignment_id"])
        _require(assignment is not None, "unknown_identity", "Packet assignment is unavailable")
    if actor["role"] != "coordinator":
        _require(assignment is not None and assignment["actor_id"] == actor["actor_id"]
                 and assignment["state"] in {"leased", "running"}, "permission_denied", "Worker needs its own active assignment")
        _require(_timestamp(assignment["lease_expires_at"]) > _now(), "lease_conflict", "Worker lease has expired; reconcile it before ingestion")
        work = _get(connection, "work_items", assignment["work_id"])
        _require(_in_branch(work["branch"], actor["branch_scope"])
                 and actor["stage_scope"] in (None, work["stage"]), "permission_denied", "Assignment exceeds actor scope")
        plan = _get(connection, "plan_snapshots", assignment["plan_sha256"])
        _require(plan["contract_sha256"] == packet["contract_sha256"], "scope_mismatch", "Packet contract differs from its saved assignment")
    else:
        current = _read_json(_path(root, "bundle.json").read_bytes())
        _require(isinstance(current, dict) and current.get("contract_sha256") == packet["contract_sha256"], "scope_mismatch", "Coordinator packet must bind the current contract")
        _require(_sha(_path(root, "contract.json").read_bytes()) == packet["contract_sha256"], "integrity_error", "Current contract changed outside the recorder")
        work = _get(connection, "work_items", assignment["work_id"]) if assignment else None
    _require(packet["packet_kind"] != "bootstrap", "permission_denied", "Bootstrap is controller-owned")
    return actor, assignment, work


def _permission(connection, packet, mutation, record, actor, assignment, work):
    table, operation = mutation["table"], mutation["op"]
    role = actor["role"]
    if table == "inspection_withdrawals":
        _require(role == "coordinator" and packet["packet_kind"] in {"reconcile", "import"},
                 "permission_denied", "Inspection withdrawal requires coordinator reconciliation or verified historical import")
        if packet["packet_kind"] != "import":
            _require(record["actor_id"] == actor["actor_id"], "permission_denied", "Withdrawal records the reconciling coordinator")
    shared = table in {"assertion_decisions", "reference_resolutions"} or operation == "update" and table in {"artifacts", "questions", "claims", "leads", "followup_obligations", "work_items"}
    if packet["packet_kind"] == "observe" and shared:
        _require(table == "assertion_decisions" and role in REVIEWERS and record.get("disposition") == "challenged", "permission_denied", "Observation packets cannot reconcile shared state")
    if role == "coordinator":
        if table == "actors":
            _require(record["role"] != "coordinator" and record.get("parent_actor_id") is not None, "permission_denied", "A second coordinator cannot be registered")
        return
    _require(packet["packet_kind"] in {"observe", "reconcile"}, "permission_denied", "Workers cannot bootstrap, import or migrate")
    if operation == "update":
        own_execution = table in {"assignments", "operation_links"} and (
            record.get("assignment_id") == assignment["assignment_id"])
        local_schedule = role == "research_lead" and table in {"work_items", "assignments", "followup_obligations"}
        _require(own_execution or local_schedule, "permission_denied", "Worker cannot change shared state", table=table)
        if table == "work_items":
            _require(not set(mutation["changes"]) & {"stage", "branch", "generation", "priority", "question_id", "artifact_id", "purpose", "request_json", "logical_key"}, "permission_denied", "Delegated lead cannot change shared work intent or priority")
    else:
        allowed = set(OBSERVATIONS)
        if role != "searcher":
            allowed |= READER_TABLES
        if role in REVIEWERS:
            allowed |= {"assertion_decisions"}
        if role == "evidence_reviewer":
            allowed |= {"claims", "claim_evidence", "claim_questions"}
        if role == "research_lead":
            allowed |= {"actors", "assignments"}
        _require(table in allowed, "permission_denied", "Actor role cannot create this record", table=table)
    if table == "actors":
        _require(record.get("parent_actor_id") == actor["actor_id"] and record["role"] in {"searcher", "reader", "coverage_reviewer", "evidence_reviewer", "research_lead"}
                 and record.get("branch_scope") is not None and _in_branch(record["branch_scope"], actor["branch_scope"])
                 and record.get("stage_scope") == actor["stage_scope"], "permission_denied", "Child registration exceeds delegated scope")
    if table == "assertion_decisions":
        _require(role in REVIEWERS and record["disposition"] == "challenged", "permission_denied", "Reviewers may challenge, not accept shared assessments")
    if "actor_id" in record and table != "actors":
        if table == "assignments" and role == "research_lead":
            child = _get(connection, "actors", record["actor_id"])
            _require(child and child["parent_actor_id"] == actor["actor_id"], "permission_denied", "Delegated assignments require a direct child")
        else:
            _require(record["actor_id"] == actor["actor_id"], "permission_denied", "Worker cannot attest another actor's observation")
    if record.get("question_id") is not None and table != "lead_origins":
        _require(record["question_id"] == work["question_id"], "permission_denied", "Record exceeds assignment question")
    if table == "assertions" and role == "searcher":
        _require(record["basis"] == "metadata" and record["kind"] in {"role", "relevance", "finding"}, "permission_denied", "Searcher cannot attest substantive inspection")
    if table == "artifacts":
        _require(record.get("canonical_artifact_id") is None, "permission_denied", "Canonical merges require the coordinator")
    if table == "discoveries":
        _require(record.get("supplied_seed_id") is None and record.get("prior_import_id") is None, "permission_denied", "Worker discovery requires its recorded search or reference")
    if table == "claims":
        _require(record.get("status", "draft") == "draft", "permission_denied", "Reviewer claim corrections begin as drafts")
    if table == "leads":
        _require(record.get("disposition", "pending") == "pending", "permission_denied", "Worker leads begin pending")
    if table == "followup_obligations":
        _require(role == "research_lead" and record.get("work_id") is not None and record["state"] == "scheduled", "permission_denied", "Delegated routing cannot close or clear a shared obligation")
    if table == "work_origins":
        _require(record.get("root_basis") is None, "permission_denied", "Workers cannot create new baseline roots")
        if record.get("parent_operation_id"):
            parent = _get(connection, "operation_links", record["parent_operation_id"])
            _require(parent and parent["assignment_id"] == assignment["assignment_id"], "permission_denied", "Work origin belongs to another assignment")
    if table == "assignments" and operation == "insert":
        _require(record.get("parent_assignment_id") == assignment["assignment_id"], "permission_denied", "Child assignment must retain the delegating assignment")
    scoped_work = record if table == "work_items" else _get(connection, "work_items", record["work_id"]) if record.get("work_id") else None
    if scoped_work:
        _require(_in_branch(scoped_work["branch"], actor["branch_scope"]) and _in_branch(scoped_work["branch"], work["branch"])
                 and scoped_work["stage"] == work["stage"] and scoped_work["question_id"] == work["question_id"], "permission_denied", "Work exceeds delegated branch, stage or question")
        if table == "work_items" and operation == "insert":
            generation = work["generation"] + (0 if work["kind"] == "search" else 1)
            _require(record.get("state", "pending") == "pending" and record.get("priority", 50) == 50 and record["generation"] >= generation,
                     "permission_denied", "Worker proposal changes scheduling authority or ancestry")


def plan_item_questions(plan, item):
    """Question authority comes from saved coverage, narrowed by graph intent."""
    coverage = {entry["id"]: entry for entry in plan.get("coverage", [])}
    selected_coverage = item.get("coverage")
    _require(isinstance(selected_coverage, list) and selected_coverage
             and all(identity in coverage for identity in selected_coverage),
             "scope_mismatch", "Plan item needs its original saved coverage")
    questions = {question for identity in selected_coverage for question in coverage[identity]["questions"]}
    annotations = item.get("graph", {})
    _require(isinstance(annotations, dict), "invalid_packet", "Saved-plan graph annotation must be an object")
    selected = annotations.get("questions", sorted(questions))
    _require(isinstance(selected, list) and selected and all(isinstance(q, str) for q in selected)
             and set(selected) <= questions, "scope_mismatch", "Plan graph questions exceed original saved coverage")
    return sorted(set(selected))


def logical_key(work, canonical_artifact_id=None):
    """Hash normalized work intent; canonicalize the target before calling."""
    request = dict(work["request_json"])
    request.pop("source_id", None)
    if "capture_ids" in request:
        request["capture_ids"] = normalize_capture_ids(request["capture_ids"])
    return _sha(_bytes({"kind": work["kind"], "artifact_id": canonical_artifact_id or work.get("artifact_id"),
                        "question_id": work["question_id"], "purpose": work["purpose"], "request": request,
                        "required_scope_key": work.get("required_scope_key"), "format_policy_sha256": work.get("format_policy_sha256")}))


def _retirement_mutation(connection, packet, mutation):
    """Permit no-credit lifecycle changes without rewriting historical intent.

    Current authority checks still reject new invalid work and execution. An
    accepted older record must nevertheless be releasable or explicitly bounded.
    Recorder validation separately checks unresolved original operations.
    """
    before = mutation.get("before")
    actor = _get(connection, "actors", packet["actor_id"])
    if (not before or mutation["op"] != "update" or packet["packet_kind"] != "reconcile"
            or not actor or actor["role"] != "coordinator"):
        return False
    changes = mutation["changes"]
    if mutation["table"] == "work_items":
        reason = changes.get("transition_reason")
        if (before["state"] not in {"pending", "leased", "running"}
                or not set(changes) <= {"state", "terminal_reason", "transition_reason"}
                or not isinstance(reason, str) or not reason.strip()):
            return False
        # Reason-only reconciliation records no execution or evidence credit.
        return (set(changes) == {"transition_reason"}
                or changes.get("state") in {"pending", "blocked", "deferred", "cancelled"})
    if mutation["table"] != "assignments" or before["state"] not in {"leased", "running", "reconciling"}:
        return False
    if not set(changes) <= {"state", "finished_at"} or changes.get("state") not in {"released", "cancelled", "reconciling"}:
        return False
    if changes["state"] == "reconciling" and set(changes) != {"state"}:
        return False
    work = _get(connection, "work_items", before["work_id"])
    partners = [m for m in packet["mutations"] if m["table"] == "work_items"
                and m["op"] == "update" and m.get("id") == before["work_id"]]
    if len(partners) != 1 or not _retirement_mutation(connection, packet, {**partners[0], "before": work}):
        return False
    partner = partners[0]["changes"]
    return (set(partner) == {"transition_reason"} if changes["state"] == "reconciling"
            else partner.get("state") in {"pending", "blocked", "deferred", "cancelled"})


def _record_rules(root, connection, packet, mutation, record, evidence_validator):
    table = mutation["table"]
    if table == "inspection_withdrawals":
        _require(storage_revision(connection) >= 2, "unsupported_schema", "Upgrade graph storage before withdrawing an inspection")
        inspection = _get(connection, "inspections", record["inspection_id"])
        _require(inspection is not None, "unknown_identity", "Withdrawn inspection is unknown")
        _require(_timestamp(record["withdrawn_at"]) >= _timestamp(inspection["inspected_at"]),
                 "scope_mismatch", "Withdrawal cannot precede its inspection")
    if table in {"assertion_evidence", "inspection_captures", "inspection_scopes"} and record.get("inspection_id") and packet["packet_kind"] != "import":
        _require(record["inspection_id"] not in withdrawn_inspection_ids(connection),
                 "scope_mismatch", "Withdrawn inspection cannot support new inspected evidence")
    if table == "captures" and (packet["packet_kind"] == "import" or record["relative_path"].startswith("graph/imports/")):
        _import_capture_receipt(record, packet, connection)
    scan = _get(connection, "reference_scans", record["scan_id"]) if table == "reference_occurrences" else None
    lifecycle = mutation["op"] == "update" and (table == "assignments" or table == "work_items" and bool(
        set(mutation["changes"]) & {"state", "generation", "kind", "artifact_id", "question_id", "request_json", "purpose", "required_scope_key", "format_policy_sha256", "logical_key", "branch", "stage"}))
    needs_external = (table in NATIVE_TABLES or packet["packet_kind"] in {"import", "migrate"}
                      or table == "work_items" and "capture_ids" in record.get("request_json", {})
                      or lifecycle
                      or record.get("return_id") is not None
                      or scan is not None and scan.get("return_id") is not None
                      or table in {"assertions", "assertion_evidence"} and record.get("basis") == "inspected")
    if needs_external:
        _require(evidence_validator is not None, "pending_dependency", "Recorder validation is required before native evidence or execution can be admitted", table=table)
        _require(evidence_validator(root, connection, packet, mutation) is True, "pending_dependency", "Recorder validation did not establish the required evidence", table=table)
    if table == "work_items":
        if record["kind"] in {"read", "inspect_visuals"}:
            _require(meaningful_read_extent(record["request_json"]["extent"])
                     or _retirement_mutation(connection, packet, mutation), "invalid_packet",
                     "Read work needs nonempty positions or explicit opaque attestation and limitations in every required component")
        intent_fields = {"kind", "artifact_id", "question_id", "request_json", "purpose", "required_scope_key", "format_policy_sha256", "logical_key"}
        if mutation["op"] == "update":
            previous_request = mutation["before"]["request_json"]
            current_request = record["request_json"]
            if previous_request.get("capture_ids") != current_request.get("capture_ids"):
                actor = _get(connection, "actors", packet["actor_id"])
                reason = mutation["changes"].get("transition_reason")
                _require(packet["packet_kind"] == "reconcile" and actor["role"] == "coordinator"
                         and isinstance(reason, str) and reason.strip()
                         and reason != mutation["before"]["transition_reason"], "permission_denied",
                         "Changing retained capture intent requires explicit coordinator reconciliation and a changed reason")
        if mutation["op"] == "insert" or any(record.get(key) != mutation["before"].get(key) for key in intent_fields):
            canonical = None
            if record.get("artifact_id"):
                row = connection.execute("SELECT canonical_id FROM canonical_artifacts WHERE artifact_id=?", (record["artifact_id"],)).fetchone()
                _require(row is not None, "unknown_identity", "Work target is unknown")
                canonical = row[0]
            _require(record["logical_key"] == logical_key(record, canonical), "invalid_packet", "Work logical key does not match its intent")
        # State-only updates retain the accepted intent/key across later canonical
        # merges; changing current identity must not rewrite an old admission.
        if mutation["op"] == "insert":
            _require(record.get("state", "pending") == "pending", "invalid_packet", "New work must start pending")
    if table == "assignments" and mutation["op"] == "insert":
        _require(record.get("state", "leased") == "leased", "invalid_packet", "New assignments start leased")
        target = _get(connection, "work_items", record["work_id"])
        actor = _get(connection, "actors", record["actor_id"])
        _require(target and actor, "unknown_identity", "Assignment actor or work is unknown")
        _require(_in_branch(target["branch"], actor["branch_scope"]) and actor["stage_scope"] in (None, target["stage"]), "permission_denied", "Assignment exceeds registered actor scope")
        if record.get("parent_assignment_id"):
            parent = _get(connection, "assignments", record["parent_assignment_id"])
            _require(parent and parent["actor_id"] == actor["parent_actor_id"] and parent["state"] in {"leased", "running"}, "permission_denied", "Nested assignment lacks its active parent")
    if table == "leads" and mutation["op"] == "insert":
        _require(record.get("disposition", "pending") == "pending", "invalid_packet", "New leads start pending")
    if table in {"plan_items", "assignments"}:
        plan = _get(connection, "plan_snapshots", record["plan_sha256"])
        _require(plan is not None, "unknown_identity", "Saved plan is unavailable")
        data = _path(root, plan["relative_path"]).read_bytes()
        _require(_sha(data) == record["plan_sha256"], "integrity_error", "Saved plan bytes changed")
        body = _read_json(data)
        item = next((item for item in body.get("items", []) if isinstance(item, dict) and item.get("id") == record["plan_item_id"]), None)
        _require(item is not None, "scope_mismatch", "Saved plan does not contain the assigned item")
        # Minimal historical storage fixtures lack recorder coverage. Native
        # recorder plans and all operational entry points enforce this binding.
        if table == "assignments" and ("coverage" in body or "_record" in body):
            work = _get(connection, "work_items", record["work_id"])
            retirement = _retirement_mutation(connection, packet, mutation)
            _require(work["question_id"] in plan_item_questions(body, item) or retirement,
                     "scope_mismatch", "Assigned question exceeds saved plan coverage")
            _require(work["kind"] not in {"read", "inspect_visuals"}
                     or meaningful_read_extent(work["request_json"]["extent"]) or retirement,
                     "invalid_packet", "Assignment cannot authorize an empty read scope")
            if mutation["op"] == "insert":
                _require(evidence_validator is not None, "pending_dependency", "Native assignment requires saved work intent validation")
                _require(evidence_validator(root, connection, packet, mutation) is True,
                         "pending_dependency", "Native assignment intent was not established")
    if table == "assertion_decisions" and record["disposition"] == "accepted":
        assertion = _get(connection, "assertions", record["assertion_id"])
        _require(assertion is not None, "unknown_identity", "Assertion is unknown")
        if assertion["kind"] == "relationship":
            withdrawn = withdrawn_inspection_ids(connection)
            support = connection.execute("SELECT inspection_id FROM assertion_evidence WHERE assertion_id=? AND basis='inspected' AND stance IN ('support','qualify')", (record["assertion_id"],))
            _require(any(row[0] not in withdrawn for row in support), "scope_mismatch", "Accepted relationship requires effective inspected support")
    if table == "reference_resolutions":
        current = connection.execute("SELECT resolution_id FROM current_reference_resolutions WHERE reference_id=?", (record["reference_id"],)).fetchone()
        _require(record.get("replaces_resolution_id") == (current[0] if current else None), "revision_conflict", "Reference resolution predecessor changed")
    if table == "source_outcomes" and record.get("replaces_outcome_id"):
        _require(connection.execute("SELECT 1 FROM current_source_outcomes WHERE outcome_id=?", (record["replaces_outcome_id"],)).fetchone(), "revision_conflict", "Source outcome predecessor changed")


def inspected_claim_evidence(tables):
    """Return evidence IDs with current native inspection and assertion support.

    Callers pass decoded rows from one completed packet or frozen snapshot.
    Historical records stay intact; this predicate only determines usable credit.
    """
    captures = {row["capture_id"]: row for row in tables["captures"]}
    locators = {row["locator_id"]: row for row in tables["locators"]}
    inspections = {row["inspection_id"]: row for row in tables["inspections"]}
    returns = {row["return_id"]: row for row in tables["reader_returns"]}
    assertions = {row["assertion_id"]: row for row in tables["assertions"]}
    batches = {row["batch_id"]: row for row in tables["ingest_batches"]}
    withdrawn = {row["inspection_id"] for row in tables.get("inspection_withdrawals", [])}
    affected = {row["assertion_id"] for row in tables["assertion_evidence"] if row["inspection_id"] in withdrawn}
    decisions = {}
    for row in tables["assertion_decisions"]:
        if row["expected_revision"] > decisions.get(row["assertion_id"], {}).get("expected_revision", -1):
            decisions[row["assertion_id"]] = row
    native = {}
    for row in tables["inspection_captures"]:
        inspection = inspections[row["inspection_id"]]
        returned = returns[inspection["return_id"]]
        capture = captures[row["capture_id"]]
        if (row["inspection_id"] not in withdrawn and inspection["provenance_basis"] == "native"
                and returned["verification_state"] == "verified"
                and batches[returned["created_batch_id"]]["packet_kind"] not in {"import", "migrate"}
                and capture["extraction_status"] in {"complete", "partial"}
                and extent_contains(capture["capture_extent_json"], row["inspected_extent_json"])):
            native.setdefault(row["capture_id"], []).append(row)
    valid = set()
    for evidence in tables["claim_evidence"]:
        if evidence["stance"] not in {"support", "qualify"}:
            continue
        locator = locators[evidence["locator_id"]]
        capture = captures[locator["capture_id"]]
        contained = locator_within_extent(locator["locator_kind"], locator["locator_json"], capture["capture_extent_json"])
        if contained is False:
            continue
        supporting = [row for row in native.get(capture["capture_id"], [])
                      if locator_within_extent(locator["locator_kind"], locator["locator_json"], row["inspected_extent_json"]) is not False]
        if not supporting:
            continue
        aid = evidence["assertion_id"]
        if aid is not None or contained is None:
            # Opaque locators need a matching accepted reader attestation. A
            # normalized direct locator can instead establish exact containment.
            candidates = [assertions[aid]] if aid is not None else assertions.values()
            matched = False
            for assertion in candidates:
                key = assertion["assertion_id"]
                if (key in affected or assertion["basis"] != "inspected"
                        or decisions.get(key, {}).get("disposition") != "accepted"):
                    continue
                inspection_ids = {row["inspection_id"] for row in supporting
                                  if inspections[row["inspection_id"]]["return_id"] == assertion["return_id"]}
                if any(row["assertion_id"] == key and row["locator_id"] == locator["locator_id"]
                       and row["basis"] == "inspected" and row["stance"] in {"support", "qualify"}
                       and row["inspection_id"] in inspection_ids for row in tables["assertion_evidence"]):
                    matched = True
                    break
            if not matched:
                continue
        valid.add(evidence["evidence_id"])
    return valid


def _claim_postconditions(connection, mutations, packet):
    if (packet or {}).get("packet_kind") in {"import", "migrate"}:
        return  # Preserve explicitly historical records without current credit.
    touched = {mutation["id"] if mutation["op"] == "update" else mutation["record"]["claim_id"]
               for mutation in mutations if mutation["table"] == "claims"}
    for mutation in mutations:
        if mutation["table"] == "claim_evidence":
            row = mutation["record"]
            claim = _get(connection, "claims", row["claim_id"])
            if claim and row["claim_revision"] == claim["revision"]:
                touched.add(row["claim_id"])
    accepted = [row for key in touched for row in [_get(connection, "claims", key)]
                if row and row["status"] == "accepted" and row["basis"] == "documented"]
    if not accepted:
        return
    names = {"captures", "locators", "inspections", "reader_returns", "assertions", "ingest_batches",
             "inspection_captures", "assertion_evidence", "assertion_decisions", "claim_evidence"}
    if storage_revision(connection) >= 2:
        names.add("inspection_withdrawals")
    tables = {name: [_decoded(row) for row in connection.execute("SELECT * FROM " + name)] for name in names}
    valid = inspected_claim_evidence(tables)
    for claim in accepted:
        support = [row for row in tables["claim_evidence"] if row["claim_id"] == claim["claim_id"]
                   and row["claim_revision"] == claim["revision"] and row["stance"] in {"support", "qualify"}]
        _require(support and all(row["evidence_id"] in valid for row in support), "scope_mismatch",
                 "Accepted documented claim needs current inspected support for every supporting locator",
                 claim_id=claim["claim_id"])


def _postconditions(connection, mutations, actor=None, assignment=None, packet=None):
    _claim_postconditions(connection, mutations, packet)
    scans = set()
    withdrawn = withdrawn_inspection_ids(connection)
    for mutation in mutations:
        table = mutation["table"]
        if mutation["op"] != "insert":
            continue
        row = mutation["record"]
        if table == "leads":
            _require(connection.execute("SELECT 1 FROM lead_origins WHERE lead_id=?", (row["lead_id"],)).fetchone(), "integrity_error", "Lead is missing its original question/provenance mapping")
        elif table == "artifacts":
            _require(connection.execute("SELECT 1 FROM discoveries WHERE artifact_id=?", (row["artifact_id"],)).fetchone(), "integrity_error", "Artifact is missing its discovery occurrence")
        elif table == "reference_scans":
            scans.add(row["scan_id"])
        elif table == "reference_occurrences":
            scans.add(row["scan_id"])
        elif table == "inspections":
            _require(connection.execute("SELECT 1 FROM inspection_captures WHERE inspection_id=?", (row["inspection_id"],)).fetchone(), "scope_mismatch", "Inspection has no retained capture mapping")
        elif table == "assertions" and row["basis"] == "inspected":
            supported = connection.execute("""SELECT i.inspection_id FROM assertion_evidence e JOIN inspections i USING (inspection_id)
                WHERE e.assertion_id=? AND e.basis='inspected' AND e.stance IN ('support','qualify')
                  AND i.return_id=? AND i.provenance_basis='native'""", (row["assertion_id"], row.get("return_id")))
            _require(any(item[0] not in withdrawn for item in supported), "scope_mismatch", "Inspected assertion lacks matching effective inspected support and return")
        elif table == "assertion_evidence" and row.get("inspection_id") in withdrawn and (packet or {}).get("packet_kind") != "import":
            raise GraphError("scope_mismatch", "New evidence cannot depend on an inspection withdrawn in the same packet")
        elif table == "assertion_decisions" and row["disposition"] == "accepted" and withdrawn:
            dependencies = connection.execute("SELECT inspection_id FROM assertion_evidence WHERE assertion_id=?", (row["assertion_id"],))
            _require(not any(item[0] in withdrawn for item in dependencies), "scope_mismatch",
                     "Assertion acceptance cannot depend on a withdrawn inspection; create a replacement assertion")
        elif table == "work_items":
            _require(connection.execute("SELECT 1 FROM work_origins WHERE work_id=?", (row["work_id"],)).fetchone(), "integrity_error", "Work is missing its execution ancestry")
        elif table == "operation_links" and packet is not None and packet["packet_kind"] == "import":
            _require(connection.execute("SELECT 1 FROM captures WHERE operation_id=?", (row["operation_id"],)).fetchone(), "integrity_error", "Imported operation lacks a mapped historical capture")
    for scan_id in scans:
        actual = connection.execute("SELECT count(*) FROM reference_occurrences WHERE scan_id=?", (scan_id,)).fetchone()[0]
        expected = connection.execute("SELECT extracted_count FROM reference_scans WHERE scan_id=?", (scan_id,)).fetchone()[0]
        _require(actual == expected, "integrity_error", "Reference scan count does not match retained occurrences")
    if actor and actor["role"] != "coordinator":
        permitted = {row[0] for row in connection.execute("""SELECT w.artifact_id FROM assignments a JOIN work_items w USING(work_id) WHERE a.assignment_id=?
            UNION SELECT d.artifact_id FROM discoveries d JOIN operation_links o USING(operation_id) WHERE o.assignment_id=?
            UNION SELECT d.artifact_id FROM discoveries d JOIN reference_occurrences r ON r.reference_id=d.origin_reference_id
            JOIN reference_scans s USING(scan_id) JOIN captures c ON c.capture_id=s.capture_id
            JOIN operation_links o USING(operation_id) WHERE o.assignment_id=?""", (assignment["assignment_id"],) * 3)}
        for mutation in mutations:
            row = mutation.get("record", {})
            for field in ("artifact_id", "subject_artifact_id", "from_artifact_id"):
                if row.get(field) is not None and mutation["table"] not in {"work_items", "work_origins"}:
                    _require(row[field] in permitted, "permission_denied", "Worker record lacks a discovery path within its assignment")
            if row.get("operation_id") and mutation["table"] == "discoveries":
                operation = _get(connection, "operation_links", row["operation_id"])
                _require(operation and operation["assignment_id"] == assignment["assignment_id"], "permission_denied", "Discovery belongs to another assignment")


def _watches(table, record):
    if table == "reference_occurrences":
        return [("followup_obligations", "reference_id=?", (record["reference_id"],))]
    if table == "leads":
        return [("followup_obligations", "lead_id=?", (record["lead_id"],))]
    if table in {"lead_origins", "work_origins"}:
        return [("followup_obligations", "lead_id=? OR reference_id=?", (record.get("lead_id"), record.get("reference_id"))),
                ("leads", "lead_id=?", (record.get("lead_id"),))]
    if table in {"assertions", "inspection_scopes", "artifacts"}:
        return [("followup_obligations", "state='terminal'", ())]
    if table == "claims":
        return [("claim_revisions", "claim_id=? AND revision=?", (record["claim_id"], record.get("revision", 1)))]
    return []


def _watched(connection, watches, previous=None):
    rows = {}
    for table, where, arguments in watches:
        for row in connection.execute("SELECT * FROM " + table + " WHERE " + where, arguments):
            row = _decoded(row)
            rows[(table, _json(_identity(table, row)))] = row
    # A trigger may change the predicate used to select the original rows.
    for (table, identity), row in (previous or {}).items():
        rows[(table, identity)] = _get(connection, table, json.loads(identity))
    return rows


def _reopen_after_merge(connection, batch_id):
    connection.execute("""UPDATE followup_obligations SET state='pending', work_id=NULL,
        disposition=NULL, reason='Canonical identity introduced unfinished question scope',
        revision=revision+1, updated_batch_id=? WHERE state='terminal' AND EXISTS (
        SELECT 1 FROM obligation_scope_status s WHERE s.obligation_id=followup_obligations.obligation_id
          AND s.status IN ('unassigned','pending'))""", (batch_id,))


def _apply_one(root, connection, packet, mutation, context, evidence_validator, internal=False):
    table = mutation["table"]
    before = None
    if mutation["op"] == "insert":
        record = dict(mutation["record"])
        for column in _layout()[table]:
            if column["name"] in INJECTED:
                record[column["name"]] = 1 if column["name"] == "revision" else packet["batch_id"]
        identity = _identity(table, record)
    else:
        identity = mutation["id"]
        before = _get(connection, table, identity)
        _require(before is not None, "unknown_identity", "Updated record does not exist", table=table, id=identity)
        _require(before["revision"] == mutation["expected_revision"], "revision_conflict", "Expected revision is stale", table=table, id=identity)
        record = {**before, **mutation["changes"], "revision": before["revision"] + 1, "updated_batch_id": packet["batch_id"]}
        record = _record_shape(table, record, internal=True)
    if not internal:
        _permission(connection, packet, mutation, record, *context)
        checked = {**mutation, "before": before, "after": record}
        _record_rules(root, connection, packet, checked, record, evidence_validator)
        if packet["packet_kind"] == "observe":
            _require(not (table == "claims" and record.get("status", "draft") != "draft")
                     and not (table == "questions" and record.get("current_status", "open") != "open"),
                     "permission_denied", "Observations cannot finalize claims or questions")
    watches = _watches(table, record)
    derived_before = _watched(connection, watches)
    if mutation["op"] == "insert":
        if table == "schema_migrations" and record["schema_version"] > 1:
            _require(internal, "permission_denied", "Storage migration is controller-owned")
            _apply_schema_migration(connection, record)
        _insert(connection, table, record)
    else:
        fields = {name: record[name] for name in set(mutation["changes"]) | {"revision", "updated_batch_id"}}
        values = [_json(value) if name.endswith("_json") and value is not None else value for name, value in fields.items()]
        changed = connection.execute("UPDATE " + table + " SET " + ",".join(name + "=?" for name in fields)
                                     + " WHERE " + _pk(table)[0] + "=? AND revision=?", values + [identity, before["revision"]]).rowcount
        _require(changed == 1, "revision_conflict", "Expected revision changed", table=table, id=identity)
    if table == "artifacts" and before and before["canonical_artifact_id"] != record.get("canonical_artifact_id"):
        _reopen_after_merge(connection, packet["batch_id"])
    after = _get(connection, table, identity)
    derived_after = _watched(connection, watches, derived_before)
    derived = []
    for key in sorted(set(derived_before) | set(derived_after)):
        old, new = derived_before.get(key), derived_after.get(key)
        if old != new:
            derived.append({"table": key[0], "id": json.loads(key[1]), "before": old, "after": new})
    return {"before": before, "after": after, "derived": derived}


def _head(connection):
    return connection.execute("SELECT COALESCE(MAX(sequence),0) FROM graph_events").fetchone()[0]


def _response(connection, command, **fields):
    metadata = connection.execute("SELECT run_id FROM graph_metadata WHERE singleton=1").fetchone()
    return {"status": "pass", "schema_version": VERSION, "run_id": metadata[0],
            "graph_sequence": _head(connection), "command": command, "storage_revision": storage_revision(connection), **fields}


def _receipt(connection, batch_id, idempotent):
    batch = _get(connection, "ingest_batches", batch_id)
    affected = []
    for event in connection.execute("SELECT entity_kind,payload_json FROM graph_events WHERE batch_id=? ORDER BY sequence", (batch_id,)):
        payload = json.loads(event["payload_json"])
        record = payload["after"]
        affected.append({"table": event["entity_kind"], "id": _identity(event["entity_kind"], record), "revision": record.get("revision", 1)})
        for derived in payload.get("derived", []):
            affected.append({"table": derived["table"], "id": derived["id"], "revision": derived["after"].get("revision", 1)})
    return _response(connection, "initialize" if batch["packet_kind"] == "bootstrap" else "ingest",
                     graph_sequence=batch["resulting_sequence"], batch_id=batch_id,
                     payload_sha256=batch["payload_sha256"], accepted_at=batch["accepted_at"],
                     affected=affected, idempotent=idempotent)


def _apply_packet(root, connection, packet, data, mutations, fingerprints,
                  evidence_validator=None, internal=False):
    connection.execute("BEGIN IMMEDIATE")
    try:
        existing = _get(connection, "ingest_batches", packet["batch_id"])
        if existing:
            _require(existing["payload_sha256"] == _sha(data), "batch_conflict", "Batch ID was accepted with different bytes")
            connection.rollback()
            return _receipt(connection, packet["batch_id"], True)
        context = (None, None, None) if internal else _context(root, connection, packet)
        _recheck_files(root, fingerprints)
        _fault("after_begin")
        head = _head(connection)
        _insert(connection, "ingest_batches", {"batch_id": packet["batch_id"], "actor_id": packet["actor_id"],
            "assignment_id": packet["assignment_id"], "packet_kind": packet["packet_kind"], "payload_sha256": _sha(data),
            "packet_path": "graph/updates/" + packet["batch_id"] + ".json", "accepted_at": _now(),
            "schema_version": VERSION, "resulting_sequence": head + len(mutations)})
        for offset, mutation in enumerate(mutations, 1):
            payload = _apply_one(root, connection, packet, mutation, context, evidence_validator, internal)
            previous = payload["before"]
            revision = previous.get("revision", 1) if previous else None
            _insert(connection, "graph_events", {"sequence": head + offset, "batch_id": packet["batch_id"],
                "event_kind": "create" if previous is None else "revise", "entity_kind": mutation["table"],
                "entity_id": _json(_identity(mutation["table"], payload["after"])),
                "prior_revision": revision, "next_revision": revision + 1 if revision else 1,
                "payload_json": payload})
        _postconditions(connection, mutations, context[0], context[1], packet)
        _require(connection.execute("SELECT 1 FROM contract_snapshots WHERE contract_sha256=?", (packet["contract_sha256"],)).fetchone(), "unknown_identity", "Packet contract has no retained graph binding")
        _require(not connection.execute("PRAGMA foreign_key_check").fetchall(), "integrity_error", "Batch has unresolved foreign keys")
        _recheck_files(root, fingerprints)
        _fault("after_mutations")
        connection.commit()
        _fault("after_commit")
        return _receipt(connection, packet["batch_id"], False)
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise


def initialize(root, actor_id, run_id):
    """Atomically add current graph storage using the v1 packet protocol."""
    root = _root(root)
    _id(actor_id)
    _id(run_id)
    _layout()
    try:
        with writer(root), _lock(root, exclusive=True, create=True):
            graph = _path(root, "graph", exists=False)
            if graph.exists():
                _ready(root)
                connection = _connect(_path(root, "graph/research.sqlite"), readonly=True)
                try:
                    metadata = connection.execute("SELECT run_id FROM graph_metadata").fetchone()
                    actor = _get(connection, "actors", actor_id)
                    _require(metadata[0] == run_id and actor and actor["role"] == "coordinator", "batch_conflict", "A different graph is already initialized")
                    return _receipt(connection, "bootstrap", True)
                finally:
                    connection.close()
            bundle = _read_json(_path(root, "bundle.json").read_bytes())
            contract_data = _path(root, "contract.json").read_bytes()
            contract = _read_json(contract_data)
            contract_hash = _sha(contract_data)
            _require(isinstance(bundle, dict) and bundle.get("contract_sha256") == contract_hash, "integrity_error", "Bundle contract hash does not match")
            retained = "contracts/" + contract_hash + ".json"
            fingerprints = {}
            _file(root, retained, contract_hash, fingerprints)
            _require(isinstance(contract, dict) and contract.get("version") == 1 and isinstance(contract.get("questions"), list), "integrity_error", "Contract questions are unavailable")
            now = _now()
            records = [
                ("schema_migrations", {"schema_version": VERSION, "migration_id": "research-graph-v1", "migration_sha256": _sha(SCHEMA.read_bytes()), "applied_at": now}),
                ("actors", {"actor_id": actor_id, "parent_actor_id": None, "role": "coordinator"}),
                ("contract_snapshots", {"contract_sha256": contract_hash, "contract_revision": 1, "relative_path": retained}),
                ("graph_metadata", {"singleton": 1, "run_id": run_id, "schema_version": VERSION, "initial_contract_sha256": contract_hash, "created_at": now}),
            ]
            for revision in range(2, STORAGE_REVISION + 1):
                identity, source = MIGRATIONS[revision]
                records.insert(revision - 1, ("schema_migrations", {"schema_version": revision,
                    "migration_id": identity, "migration_sha256": _sha(source.read_bytes()), "applied_at": now}))
            for question in contract["questions"]:
                _require(isinstance(question, dict) and isinstance(question.get("id"), str) and isinstance(question.get("text"), str)
                         and type(question.get("central")) is bool, "integrity_error", "Contract question lacks identity, text or explicit central status")
                records.append(("questions", {"question_id": question["id"], "text": question["text"], "central": int(question["central"]), "contract_revision": 1}))
            packet = {"schema_version": VERSION, "batch_id": "bootstrap", "actor_id": actor_id, "assignment_id": None,
                      "packet_kind": "bootstrap", "contract_sha256": contract_hash, "submitted_at": now,
                      "mutations": [{"op": "insert", "table": table, "record": row} for table, row in records]}
            data = _bytes(packet)
            mutations = _normal_mutations(packet, internal=True)
            temporary = Path(tempfile.mkdtemp(prefix=".research-graph-init-", dir=root))
            try:
                for name in ("updates", "checkpoints", "exports"):
                    (temporary / name).mkdir(mode=0o700)
                _retain(temporary / "updates/bootstrap.json", data)
                connection = sqlite3.connect(temporary / "research.sqlite", isolation_level=None)
                connection.close()
                connection = _connect(temporary / "research.sqlite", check=False)
                try:
                    connection.executescript(SCHEMA.read_text(encoding="utf-8"))
                    receipt = _apply_packet(root, connection, packet, data, mutations, fingerprints, internal=True)
                    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    _check_version(connection)
                finally:
                    connection.close()
                with (temporary / "research.sqlite").open("rb") as stream:
                    os.fsync(stream.fileno())
                _sync_dir(temporary)
                _fault("init_before_install")
                os.rename(temporary, graph)
                _sync_dir(root)
                return receipt
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary)
    except sqlite3.Error as error:
        raise _sql_error(error) from None
    except OSError:
        raise GraphError("integrity_error", "Graph initialization could not retain its files") from None
    except (TypeError, ValueError, KeyError):
        raise GraphError("invalid_packet", "Initialization metadata has invalid field types") from None


def upgrade_schema(root, *, target_revision=None):
    """Explicitly append ordered migrations, preserving each historical packet.

    A retained revision-2 retry stops at revision 2. An ordinary upgrade targets
    the current revision; interruption may leave a committed earlier migration,
    which a repeated call preserves and continues without rewriting its event.
    """
    root = _root(root)
    target_revision = STORAGE_REVISION if target_revision is None else target_revision
    _require(type(target_revision) is int and target_revision in MIGRATIONS,
             "unsupported_schema", "Unsupported target graph storage revision")
    try:
        with writer(root), _lock(root):
            _ready(root)
            connection = _connect(_path(root, "graph/research.sqlite"))
            try:
                _audit(root, connection)
                if storage_revision(connection) >= target_revision:
                    return _response(connection, "upgrade-schema", idempotent=True, upgraded=False)
                for revision in range(storage_revision(connection) + 1, target_revision + 1):
                    identity, source = MIGRATIONS[revision]
                    batch_id = identity.replace("research-graph-", "schema-upgrade-", 1)
                    relative = "graph/updates/" + batch_id + ".json"
                    path = _path(root, relative, exists=False)
                    actor = connection.execute("SELECT actor_id FROM actors WHERE role='coordinator'").fetchone()[0]
                    if path.exists():
                        packet, data = _packet(path)
                        _normal_mutations(packet, internal=True)
                        _require(packet["batch_id"] == batch_id and packet["actor_id"] == actor and packet["assignment_id"] is None
                                 and packet["packet_kind"] == "migrate" and len(packet["mutations"]) == 1,
                                 "batch_conflict", "Retained storage migration has different intent")
                        mutation = packet["mutations"][0]
                        _require(mutation.get("op") == "insert" and mutation.get("table") == "schema_migrations"
                                 and mutation.get("record", {}).get("schema_version") == revision,
                                 "batch_conflict", "Retained storage migration has different intent")
                        _migration_record(mutation.get("record", {}))
                        _require(mutation["record"].get("applied_at") == packet["submitted_at"],
                                 "batch_conflict", "Retained migration timestamp differs")
                    else:
                        now = _now()
                        bundle = _read_json(_path(root, "bundle.json").read_bytes())
                        _require(bundle["contract_sha256"] == _sha(_path(root, "contract.json").read_bytes()),
                                 "integrity_error", "Current contract changed outside the recorder")
                        packet = dict(schema_version=VERSION, batch_id=batch_id, actor_id=actor, assignment_id=None,
                            packet_kind="migrate", contract_sha256=bundle["contract_sha256"], submitted_at=now,
                            mutations=[dict(op="insert", table="schema_migrations", record=dict(schema_version=revision,
                                migration_id=identity, migration_sha256=_sha(source.read_bytes()), applied_at=now))])
                        data = _bytes(packet)
                    mutations = _normal_mutations(packet, internal=True)
                    _retain(path, data)
                    _fault("after_packet_fsync")
                    result = _apply_packet(root, connection, packet, data, mutations, {}, internal=True)
                    _check_version(connection)
                return dict(result, command="upgrade-schema", upgraded=True)
            finally:
                connection.close()
    except sqlite3.Error as error:
        raise _sql_error(error) from None
    except OSError:
        raise GraphError("integrity_error", "Storage migration files are unavailable or changed") from None


def ingest(root, packet_or_path, *, evidence_validator=None):
    """Admit a strict packet; validator callbacks inspect retained local facts.

The trusted G2 callback receives (root, connection, packet, mutation), with
normalized before/after rows added to mutation, and must return exactly True.
It must not call providers, change the database, or reserve budget. No callback
is selected by data inside a packet.
Assignment and work lifecycle updates also require the recorder-aware validator;
the absence of a graph operation mirror is not proof of no original admission.
"""
    root = _root(root)
    packet, data = _packet(packet_or_path)
    try:
        with writer(root), _lock(root):
            _ready(root)
            connection = _connect(_path(root, "graph/research.sqlite"))
            try:
                existing = _get(connection, "ingest_batches", packet["batch_id"])
                if existing:
                    _require(existing["payload_sha256"] == _sha(data), "batch_conflict", "Batch ID was accepted with different bytes")
                    _retain(_path(root, existing["packet_path"], exists=False), data)
                    return _receipt(connection, packet["batch_id"], True)
                mutations = _normal_mutations(packet)
                _retain(_path(root, "graph/updates/" + packet["batch_id"] + ".json", exists=False), data)
                _fault("after_packet_fsync")
                fingerprints = _external_files(root, packet)
                return _apply_packet(root, connection, packet, data, mutations, fingerprints, evidence_validator)
            finally:
                connection.close()
    except sqlite3.Error as error:
        raise _sql_error(error) from None
    except OSError:
        raise GraphError("integrity_error", "Graph packet files are unavailable or changed") from None
    except (TypeError, ValueError, KeyError):
        raise GraphError("invalid_packet", "Packet or recorder validation contains invalid typed fields") from None


def _accepted(connection):
    return [_decoded(row) for row in connection.execute("SELECT * FROM ingest_batches ORDER BY resulting_sequence")]


def _packet_bytes(root, batch, checkpoint_dir=None):
    if checkpoint_dir is None:
        path = _path(root, batch["packet_path"])
    else:
        path = _path(checkpoint_dir, "packets/" + _id(batch["batch_id"], filename=True) + ".json")
    _require(path.stat().st_size <= MAX_PACKET, "integrity_error", "Accepted packet exceeds its size bound")
    data = path.read_bytes()
    _require(_sha(data) == batch["payload_sha256"], "integrity_error", "Accepted packet hash changed", batch_id=batch["batch_id"])
    packet, _ = _packet(data)
    for field in ("batch_id", "actor_id", "assignment_id", "packet_kind", "schema_version"):
        _require(packet[field] == batch[field], "integrity_error", "Accepted packet identity disagrees with its receipt", batch_id=batch["batch_id"])
    return packet, data


def _audit(root, connection, checkpoint_dir=None, compare_rows=True):
    _check_version(connection)
    _require(connection.execute("PRAGMA quick_check").fetchone()[0] == "ok", "integrity_error", "SQLite integrity check failed")
    if compare_rows:
        _require(not connection.execute("PRAGMA foreign_key_check").fetchall(), "integrity_error", "Graph foreign keys are inconsistent")
    batches = _accepted(connection)
    expected = {}
    sequence = 0
    for batch in batches:
        packet, _ = _packet_bytes(root, batch, checkpoint_dir)
        events = connection.execute("SELECT * FROM graph_events WHERE batch_id=? ORDER BY sequence", (batch["batch_id"],)).fetchall()
        _require(len(events) == len(packet["mutations"]) > 0, "integrity_error", "Accepted mutation/event counts disagree")
        for mutation, event in zip(packet["mutations"], events):
            sequence += 1
            _require(event["sequence"] == sequence and event["entity_kind"] == mutation["table"], "integrity_error", "Accepted event order or entity changed")
            payload = _read_json(event["payload_json"])
            _object(payload, {"before", "after", "derived"}, {"before", "after", "derived"})
            table = event["entity_kind"]
            _require(table in TABLES | AUTOMATIC and isinstance(payload["after"], dict) and isinstance(payload["derived"], list), "integrity_error", "Invalid event snapshot")
            identity = _identity(table, payload["after"])
            _require(event["entity_id"] == _json(identity), "integrity_error", "Event identity disagrees with its snapshot")
            values = mutation.get("record") if mutation.get("op") == "insert" else mutation.get("changes")
            _require(isinstance(values, dict), "integrity_error", "Accepted mutation shape is invalid")
            for field, value in values.items():
                if field.endswith("_at") and value is not None:
                    value = _timestamp(value)
                if field == "request_json" and isinstance(value, dict) and "capture_ids" in value:
                    value = dict(value, capture_ids=normalize_capture_ids(value["capture_ids"]))
                _require(payload["after"].get(field) == value, "integrity_error", "Event snapshot differs from accepted packet")
            previous = payload["before"]
            old_revision = previous.get("revision", 1) if previous else None
            _require(event["prior_revision"] == old_revision and event["next_revision"] == (old_revision + 1 if old_revision else 1), "integrity_error", "Event revision chain changed")
            changes = [{"table": table, "id": identity, "before": previous, "after": payload["after"]}] + payload["derived"]
            for change in changes:
                _object(change, {"table", "id", "before", "after"}, {"table", "id", "before", "after"})
                _require(change["table"] in TABLES | AUTOMATIC and isinstance(change["after"], dict), "integrity_error", "Derived event row is invalid")
                key = (change["table"], _json(change["id"]))
                _require(change["id"] == _identity(change["table"], change["after"]), "integrity_error", "Derived row identity changed")
                _require(expected.get(key) == change["before"], "integrity_error", "Event history does not preserve the prior row")
                expected[key] = change["after"]
        _require(batch["resulting_sequence"] == sequence, "integrity_error", "Batch tail does not match its maximum event")
    _require(sequence == _head(connection), "integrity_error", "Event sequence contains an unreceipted tail")
    if compare_rows:
        for (table, identity), expected_row in expected.items():
            _require(_get(connection, table, json.loads(identity)) == expected_row, "integrity_error", "Current state differs from accepted history", table=table)
        for table in storage_tables(connection) - {"graph_events", "ingest_batches"}:
            if table in {"graph_events", "ingest_batches"}:
                continue
            for row in connection.execute("SELECT * FROM " + table):
                record = _decoded(row)
                key = (table, _json(_identity(table, record)))
                if key not in expected:
                    _require(table in {"role_vocabulary", "relationship_vocabulary"} and _builtin_row(table, record), "integrity_error", "Database contains a row without accepted history", table=table)
    return {"accepted_batches": len(batches), "events": sequence}


_BUILTINS = None


def _builtin_row(table, record):
    global _BUILTINS
    if _BUILTINS is None:
        with sqlite3.connect(":memory:") as connection:
            connection.row_factory = sqlite3.Row
            connection.executescript(SCHEMA.read_text(encoding="utf-8"))
            _BUILTINS = {name: [dict(row) for row in connection.execute("SELECT * FROM " + name)] for name in ("role_vocabulary", "relationship_vocabulary")}
    return record in _BUILTINS[table]


def _pending(root, connection):
    accepted = {row[0] for row in connection.execute("SELECT batch_id FROM ingest_batches")}
    directory = root / "graph/updates"
    _require(directory.is_dir() and not directory.is_symlink(), "integrity_error", "Packet directory is unavailable")
    result = []
    for path in sorted(directory.iterdir()):
        if not path.name.endswith(".json"):
            continue
        _require(not path.is_symlink() and path.is_file(), "integrity_error", "Packet path is not a regular file")
        if path.stem not in accepted:
            try:
                packet, data = _packet(path)
                _require(packet["batch_id"] == path.stem, "integrity_error", "Packet filename differs from its batch ID")
                result.append({"batch_id": packet["batch_id"], "payload_sha256": _sha(data), "state": "pending", "path": str(path.relative_to(root))})
            except GraphError as error:
                result.append({"batch_id": path.stem, "state": "invalid", "error": error.code})
    return result


def validate(root):
    """Verify accepted history, rows and retained file hashes; never inspect anew.

Only final accepted identities bind external files. Pending packet dependencies
are not checked. Native execution/inspection attestations and semantic locator
inclusion are not independently re-established by this integrity audit.
"""
    root = _root(root)
    try:
        with snapshot(root) as connection:
            counts = _audit(root, connection)
            files = _accepted_files(root, connection)
            return _response(connection, "validate", **counts, **files,
                             validation_scope="accepted_history_and_retained_file_integrity",
                             fresh_evidence_validation=False, pending_packets=_pending(root, connection))
    except (KeyError, TypeError, ValueError):
        raise GraphError("integrity_error", "Graph history contains invalid structured records") from None
    except OSError:
        raise GraphError("integrity_error", "Graph history files are unavailable") from None


def _export_snapshot_rows(connection, sequence):
    """Reconstruct the represented rows from already-audited accepted history."""
    revision = _storage_revision_at(connection, sequence)
    rows = {table: {} for table in (TABLES if revision >= 2 else V1_TABLES) | AUTOMATIC}
    def add(table, row):
        rows[table][_json(_identity(table, row))] = row
    _builtin_row("role_vocabulary", {})
    for table, values in _BUILTINS.items():
        for row in values:
            add(table, row)
    for batch in connection.execute("SELECT * FROM ingest_batches WHERE resulting_sequence<=?", (sequence,)):
        add("ingest_batches", _decoded(batch))
    for raw in connection.execute("SELECT * FROM graph_events WHERE sequence<=? ORDER BY sequence", (sequence,)):
        event = _decoded(raw)
        add("graph_events", event)
        add(event["entity_kind"], event["payload_json"]["after"])
        for derived in event["payload_json"]["derived"]:
            add(derived["table"], derived["after"])
    return rows


def _storage_revision_at(connection, sequence):
    return connection.execute("""SELECT COALESCE(MAX(json_extract(payload_json,'$.after.schema_version')),1)
        FROM graph_events WHERE sequence<=? AND entity_kind='schema_migrations'""", (sequence,)).fetchone()[0]


def _export_registration(root, connection, export_id, relative, expected_hash):
    prefix = "graph/exports/" + export_id + "/"
    _require(relative == prefix + "manifest.json", "integrity_error", "Export manifest must use its identity directory")
    fingerprints = {}
    path = _file(root, relative, expected_hash, fingerprints)
    manifest = _read_json(path.read_bytes())
    fields = {"schema_version", "export_id", "run_id", "graph_sequence", "created_at", "contract_sha256", "plan_sha256", "budget_event_head", "schema_sha256", "files", "limitations"}
    _object(manifest, fields, fields)
    _require(type(manifest["schema_version"]) is int and manifest["schema_version"] == VERSION,
             "unsupported_schema", "Export packet schema differs from the graph")
    _require(manifest["export_id"] == export_id and manifest["run_id"] == connection.execute("SELECT run_id FROM graph_metadata").fetchone()[0],
             "scope_mismatch", "Export identity or run differs from this graph")
    sequence = manifest["graph_sequence"]
    _require(type(sequence) is int and connection.execute("SELECT 1 FROM ingest_batches WHERE resulting_sequence=?", (sequence,)).fetchone(),
             "scope_mismatch", "Export sequence is not an accepted batch boundary")
    _require(manifest["schema_sha256"] == _sha(schema_bytes(_storage_revision_at(connection, sequence))),
             "unsupported_schema", "Export schema differs from its represented graph storage")
    for name in ("contract_sha256", "plan_sha256", "budget_event_head"):
        value = manifest[name]
        _require(value is None and name != "contract_sha256" or isinstance(value, str) and HASH.fullmatch(value), "integrity_error", "Invalid export identity hash")
    created = _timestamp(manifest["created_at"])
    _require(_strings(manifest["limitations"]) and isinstance(manifest["files"], list) and manifest["files"], "integrity_error", "Invalid export file inventory")
    seen, files = set(), []
    for entry in manifest["files"]:
        _object(entry, {"relative_path", "sha256", "format", "size_bytes"}, {"relative_path", "sha256", "format", "size_bytes"})
        name = entry["relative_path"]
        _require(isinstance(name, str) and name not in seen and name != "manifest.json", "integrity_error", "Duplicate or self-referential export file")
        _require(isinstance(entry["sha256"], str) and HASH.fullmatch(entry["sha256"])
                 and type(entry["size_bytes"]) is int and entry["size_bytes"] >= 0
                 and isinstance(entry["format"], str) and entry["format"].strip(), "integrity_error", "Invalid export file binding")
        file_path = _file(root, prefix + name, entry["sha256"], fingerprints)
        _require(file_path.stat().st_size == entry["size_bytes"], "integrity_error", "Export file size differs from its manifest")
        seen.add(name)
        files.append({**entry, "relative_path": prefix + name})
    _require({"graph.json", "schema.sql", "ledger.json", "sources.json", "references.json", "worklist.json", "evidence/contract.json"} <= seen,
             "integrity_error", "Export lacks a required inventory or retained contract")
    _file(root, prefix + "schema.sql", manifest["schema_sha256"], fingerprints)
    _file(root, prefix + "evidence/contract.json", manifest["contract_sha256"], fingerprints)
    represented = _read_json(_path(root, prefix + "graph.json").read_bytes())
    _object(represented, {"schema_version", "run_id", "graph_sequence", "tables"}, {"schema_version", "run_id", "graph_sequence", "tables"})
    _require(all(type(represented[key]) is type(manifest[key]) and represented[key] == manifest[key]
                 for key in ("schema_version", "run_id", "graph_sequence")), "scope_mismatch", "Export inventory describes another snapshot")
    expected = _export_snapshot_rows(connection, sequence)
    _require(isinstance(represented["tables"], dict) and set(represented["tables"]) == set(expected), "integrity_error", "Export table inventory is incomplete")
    for table, values in represented["tables"].items():
        _require(isinstance(values, list) and all(isinstance(row, dict) for row in values), "integrity_error", "Invalid export table rows")
        actual = {_json(_identity(table, row)): row for row in values}
        _require(len(actual) == len(values) and actual == expected[table], "integrity_error", "Export rows differ from represented accepted history", table=table)
    _require(_json(manifest["contract_sha256"]) in expected["contract_snapshots"]
             and (manifest["plan_sha256"] is None or _json(manifest["plan_sha256"]) in expected["plan_snapshots"]),
             "scope_mismatch", "Export contract or plan was not bound at the represented sequence")
    # A self-consistent file manifest must not wrap different bytes than the
    # represented evidence rows. Previous exports are deliberately nonrecursive.
    evidence_root = _path(root, prefix + "evidence", exists=False)
    evidence_tables = ("contract_snapshots", "plan_snapshots", "captures", "reader_returns", "locators")
    if "inspection_withdrawals" in expected:
        evidence_tables += ("inspection_withdrawals",)
    evidence_rows = ({"op": "insert", "table": table, "record": row}
                     for table in evidence_tables for row in expected[table].values())
    retained = _external_files(evidence_root, {"mutations": evidence_rows}, connection)
    for batch in expected["ingest_batches"].values():
        _file(evidence_root, batch["packet_path"], batch["payload_sha256"], retained)
    for operation in expected["operation_links"].values():
        base = "operations/" + _id(operation["operation_id"], filename=True) + "/"
        binding = _historical_import(operation, connection=connection)
        if binding is not None:
            _legacy_operation(operation)
            base = "graph/imports/" + binding["import_id"] + "/" + base
        _file(evidence_root, base + "request.json", operation["request_sha256"], retained)
        if operation["receipt_sha256"] is not None:
            _file(evidence_root, base + "receipt.json", operation["receipt_sha256"], retained)
    for binding in expected["prior_run_imports"].values():
        batch = expected["ingest_batches"][_json(binding["imported_batch_id"])]
        if batch["packet_kind"] == "import":
            _file(evidence_root, "graph/imports/" + _id(binding["import_id"], filename=True) + "/manifest.json", binding["manifest_sha256"], retained)
    for relative_name, stamp in retained.items():
        _require("evidence/" + relative_name in seen, "integrity_error", "Represented evidence file is absent from export manifest")
        fingerprints[prefix + "evidence/" + relative_name] = stamp
    files.append({"relative_path": relative, "sha256": expected_hash, "format": "json", "size_bytes": path.stat().st_size})
    record = {"export_id": export_id, "graph_sequence": sequence, "schema_version": VERSION,
              "contract_sha256": manifest["contract_sha256"], "plan_sha256": manifest["plan_sha256"],
              "budget_event_head": manifest["budget_event_head"], "files_json": sorted(files, key=lambda item: item["relative_path"]), "created_at": created}
    return record, fingerprints


def _verify_export_projection(directory):
    # The storage controller owns first-registration admission. Load the
    # projection renderer lazily so normal graph storage has no export imports.
    name = "research_graph_exports"
    path = Path(__file__).with_name(name + ".py")
    module = sys.modules.get(name)
    if module is None or Path(module.__file__).resolve() != path.resolve():
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    try:
        _require(module._verify_generated_ledger(directory) is True, "integrity_error",
                 "Export renderer did not verify the generated ledger")
    except module.GraphError as error:
        # Storage is also supported when loaded under an isolated module name.
        raise GraphError(error.code, error.message, error.retryable, **error.details) from None


def register_export(root, export_id, manifest_relative_path, manifest_sha256):
    """Register one retained export through a controller-owned packet and event.

Only this narrow entrypoint can create export_manifests. A pending registration
is retried here; ordinary ingest cannot promote controller-owned records. The
represented snapshot precedes the registration event and is not changed by it.
"""
    root = _root(root)
    _id(export_id, filename=True)
    _require(isinstance(manifest_sha256, str) and HASH.fullmatch(manifest_sha256), "invalid_packet", "Invalid manifest hash")
    try:
        with writer(root), _lock(root):
            _ready(root)
            connection = _connect(_path(root, "graph/research.sqlite"))
            try:
                _audit(root, connection)
                record, fingerprints = _export_registration(root, connection, export_id, manifest_relative_path, manifest_sha256)
                batch_id = "export-" + _sha(export_id.encode("utf-8"))
                pending = _path(root, "graph/updates/" + batch_id + ".json", exists=False)
                # Retry old accepted or crash-retained expanded packets exactly.
                # _retain below rejects every other difference in their bytes.
                legacy = False
                if pending.exists():
                    original = _read_json(pending.read_bytes())
                    _require(isinstance(original, dict) and isinstance(original.get("mutations"), list)
                             and len(original["mutations"]) == 1 and isinstance(original["mutations"][0], dict)
                             and isinstance(original["mutations"][0].get("record"), dict),
                             "integrity_error", "Retained export registration is malformed")
                    legacy = original["mutations"][0]["record"]["files_json"] == record["files_json"]
                else:
                    _verify_export_projection((root / manifest_relative_path).parent)
                if not legacy:
                    record["files_json"] = [entry for entry in record["files_json"] if entry["relative_path"] == manifest_relative_path]
                actors = connection.execute("SELECT actor_id FROM actors WHERE role='coordinator' AND parent_actor_id IS NULL").fetchall()
                _require(len(actors) == 1, "integrity_error", "Graph needs exactly one root coordinator")
                packet = {"schema_version": VERSION, "batch_id": batch_id,
                          "actor_id": actors[0][0], "assignment_id": None, "packet_kind": "reconcile",
                          "contract_sha256": record["contract_sha256"], "submitted_at": record["created_at"],
                          "mutations": [{"op": "insert", "table": "export_manifests", "record": record}]}
                packet, data = _packet(packet)
                mutations = _normal_mutations(packet, internal=True)
                _retain(_path(root, "graph/updates/" + packet["batch_id"] + ".json", exists=False), data)
                _fault("after_packet_fsync")
                receipt = _apply_packet(root, connection, packet, data, mutations, fingerprints, internal=True)
                return {**receipt, "command": "register-export", "export_id": export_id,
                        "exported_graph_sequence": record["graph_sequence"], "manifest_sha256": manifest_sha256}
            finally:
                connection.close()
    except sqlite3.Error as error:
        raise _sql_error(error) from None
    except (KeyError, TypeError, ValueError):
        raise GraphError("integrity_error", "Export manifest or inventory contains invalid structured records") from None
    except OSError:
        raise GraphError("integrity_error", "Export files could not be read or retained") from None


def _checkpoint_manifest(directory):
    manifest_path = _path(directory, "manifest.json")
    hash_path = _path(directory, "manifest.sha256")
    data = manifest_path.read_bytes()
    _require(hash_path.read_text(encoding="ascii").strip() == _sha(data), "integrity_error", "Checkpoint manifest hash changed")
    manifest = _read_json(data)
    _object(manifest, {"schema_version", "checkpoint_id", "run_id", "graph_sequence", "schema_sha256", "created_at", "database", "batches"},
            {"schema_version", "checkpoint_id", "run_id", "graph_sequence", "schema_sha256", "created_at", "database", "batches"})
    _require(manifest["schema_version"] == VERSION and manifest["schema_sha256"] in {_sha(schema_bytes(number)) for number in {1, *MIGRATIONS}},
             "unsupported_schema", "Checkpoint uses an unsupported storage schema")
    _object(manifest["database"], {"relative_path", "sha256", "size_bytes"}, {"relative_path", "sha256", "size_bytes"})
    database = _file(directory, manifest["database"]["relative_path"], manifest["database"]["sha256"], {})
    _require(database.stat().st_size == manifest["database"]["size_bytes"], "integrity_error", "Checkpoint database size changed")
    return manifest, database


def _check_checkpoint(root, directory):
    manifest, database = _checkpoint_manifest(directory)
    connection = _connect(database, readonly=True)
    try:
        connection.execute("BEGIN")
        _audit(root, connection, checkpoint_dir=directory)
        _require(manifest["schema_sha256"] == _sha(schema_bytes(storage_revision(connection))),
                 "integrity_error", "Checkpoint schema identity differs from its database")
        _require(_head(connection) == manifest["graph_sequence"] and _accepted(connection) == manifest["batches"]
                 and connection.execute("SELECT run_id FROM graph_metadata").fetchone()[0] == manifest["run_id"],
                 "integrity_error", "Checkpoint manifest disagrees with its accepted database")
        return manifest
    finally:
        connection.close()


def checkpoint(root, checkpoint_id):
    """Back up a read snapshot, plus its exact accepted packets and manifest."""
    root = _root(root)
    _id(checkpoint_id, filename=True)
    try:
        with snapshot(root) as source:
            destination = _path(root, "graph/checkpoints/" + checkpoint_id, exists=False)
            if destination.exists():
                manifest = _check_checkpoint(root, destination)
                return {"status": "pass", "schema_version": VERSION, "run_id": manifest["run_id"], "command": "checkpoint",
                        "graph_sequence": manifest["graph_sequence"], "checkpoint_id": checkpoint_id,
                        "manifest_sha256": _sha((destination / "manifest.json").read_bytes()), "idempotent": True}
            _audit(root, source)
            temporary = Path(tempfile.mkdtemp(prefix=".checkpoint-", dir=destination.parent))
            try:
                (temporary / "packets").mkdir(mode=0o700)
                target = sqlite3.connect(temporary / "research.sqlite")
                try:
                    source.backup(target)
                    target.execute("PRAGMA journal_mode=DELETE")
                    target.commit()
                finally:
                    target.close()
                batches = _accepted(source)
                for batch in batches:
                    _, data = _packet_bytes(root, batch)
                    _retain(temporary / "packets" / (batch["batch_id"] + ".json"), data)
                database = temporary / "research.sqlite"
                with database.open("rb") as stream:
                    os.fsync(stream.fileno())
                manifest = {"schema_version": VERSION, "checkpoint_id": checkpoint_id,
                            "run_id": source.execute("SELECT run_id FROM graph_metadata").fetchone()[0],
                            "graph_sequence": _head(source), "schema_sha256": _sha(schema_bytes(storage_revision(source))), "created_at": _now(),
                            "database": {"relative_path": "research.sqlite", "sha256": _sha(database.read_bytes()), "size_bytes": database.stat().st_size},
                            "batches": batches}
                data = _bytes(manifest)
                _retain(temporary / "manifest.json", data)
                _retain(temporary / "manifest.sha256", (_sha(data) + "\n").encode("ascii"))
                _sync_dir(temporary)
                _check_checkpoint(root, temporary)
                _fault("checkpoint_before_install")
                try:
                    os.rename(temporary, destination)
                except OSError:
                    _require(destination.is_dir(), "integrity_error", "Checkpoint installation failed")
                    existing = _check_checkpoint(root, destination)
                    _require(existing["graph_sequence"] == manifest["graph_sequence"], "batch_conflict", "Checkpoint ID already denotes another snapshot")
                    manifest = existing
                _sync_dir(destination.parent)
                return {"status": "pass", "schema_version": VERSION, "run_id": manifest["run_id"], "command": "checkpoint",
                        "graph_sequence": manifest["graph_sequence"], "checkpoint_id": checkpoint_id,
                        "manifest_sha256": _sha((destination / "manifest.json").read_bytes()), "idempotent": False}
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary)
    except sqlite3.Error as error:
        raise _sql_error(error) from None
    except OSError:
        raise GraphError("integrity_error", "Checkpoint files could not be retained") from None


def _replay(root, source, target, packet_directory=None):
    """Replay trusted accepted event snapshots, preserving acceptance times.

The source is a validated live DB history or a hash-bound checkpoint database.
An update file on its own is never sufficient authority for this function.
"""
    _audit(root, source, checkpoint_dir=packet_directory, compare_rows=False)
    for batch in _accepted(source):
        target.execute("BEGIN IMMEDIATE")
        try:
            _insert(target, "ingest_batches", batch)
            for raw in source.execute("SELECT * FROM graph_events WHERE batch_id=? ORDER BY sequence", (batch["batch_id"],)):
                event = _decoded(raw)
                payload = event["payload_json"]
                table, before, after = event["entity_kind"], payload["before"], payload["after"]
                identity = _identity(table, after)
                _require(_get(target, table, identity) == before, "integrity_error", "Replay predecessor differs from accepted history")
                if before is None:
                    if table == "schema_migrations" and after["schema_version"] > 1:
                        _apply_schema_migration(target, after)
                    _insert(target, table, after)
                else:
                    names = [name for name in after if name not in _pk(table)]
                    values = [_json(after[name]) if name.endswith("_json") and after[name] is not None else after[name] for name in names]
                    key = _key(table, identity)
                    target.execute("UPDATE " + table + " SET " + ",".join(name + "=?" for name in names) + " WHERE " + " AND ".join(name + "=?" for name in key), values + list(key.values()))
                    if table == "artifacts" and before["canonical_artifact_id"] != after["canonical_artifact_id"]:
                        _reopen_after_merge(target, batch["batch_id"])
                _require(_get(target, table, identity) == after, "integrity_error", "Replay result differs from accepted history")
                for derived in payload["derived"]:
                    _require(_get(target, derived["table"], derived["id"]) == derived["after"], "integrity_error", "Replay trigger result differs from accepted history")
                _insert(target, "graph_events", event)
            target.commit()
        except BaseException:
            target.rollback()
            raise


def _recovery_source(root):
    """Prefer complete accepted live history; fall back only to verified backups."""
    pending = _install_candidate(root)
    if pending is not None:
        _, directory = pending
        _, database = _checkpoint_manifest(directory)
        connection = _connect(database, readonly=True)
        connection.execute("BEGIN")
        return connection, directory, "pending_recovery_install"
    path = _path(root, "graph/research.sqlite", exists=False)
    live_error = None
    if path.exists():
        connection = None
        try:
            connection = _connect(path, readonly=True)
            connection.execute("BEGIN")
            _audit(root, connection, compare_rows=False)
            return connection, None, "accepted_database"
        except (GraphError, sqlite3.Error) as error:
            if connection:
                connection.close()
            if isinstance(error, GraphError) and error.code == "unsupported_schema":
                raise
            live_error = error
    directory = _path(root, "graph/checkpoints", exists=False)
    _require(directory.is_dir(), "pending_dependency", "No accepted database or checkpoint is available")
    candidates = []
    for item in directory.iterdir():
        if not item.is_dir() or item.name.startswith(".") or item.is_symlink():
            continue
        try:
            manifest = _check_checkpoint(root, item)
            candidates.append((manifest["graph_sequence"], manifest["created_at"], item))
        except (GraphError, sqlite3.Error, OSError):
            continue
    _require(candidates, "pending_dependency", "No verified accepted checkpoint can establish recovery order")
    _, _, selected = max(candidates, key=lambda item: (item[0], item[1], item[2].name))
    manifest, database = _checkpoint_manifest(selected)
    connection = _connect(database, readonly=True)
    connection.execute("BEGIN")
    return connection, selected, "checkpoint_after_unavailable_history" if live_error else "checkpoint"


def _ready(root):
    marker = _path(root, "graph/recovery-install.json", exists=False)
    _require(not marker.exists(), "pending_dependency", "Recovery installation is incomplete; resume recovery before ordinary access")


def _install_candidate(root):
    path = _path(root, "graph/recovery-install.json", exists=False)
    if not path.exists():
        return None
    marker = _read_json(path.read_bytes())
    _object(marker, {"schema_version", "candidate", "manifest_sha256"}, {"schema_version", "candidate", "manifest_sha256"})
    _require(marker["schema_version"] == VERSION and isinstance(marker["candidate"], str)
             and marker["candidate"].startswith("graph/checkpoints/recovery-"), "integrity_error", "Recovery install marker is invalid")
    candidate = _path(root, marker["candidate"], exists=False)
    _require(candidate.is_dir(), "integrity_error", "Prepared recovery candidate is missing")
    _require(_sha(_path(candidate, "manifest.json").read_bytes()) == marker["manifest_sha256"], "integrity_error", "Recovery candidate manifest changed")
    manifest = _check_checkpoint(root, candidate)
    return manifest, candidate


def _prepare_install(root, source, database, packet_directory):
    """Keep a complete accepted checkpoint before any live WAL is touched."""
    temporary = Path(tempfile.mkdtemp(prefix=".recovery-", dir=root / "graph/checkpoints"))
    candidate = temporary.with_name(temporary.name[1:])
    try:
        (temporary / "packets").mkdir(mode=0o700)
        shutil.copyfile(database, temporary / "research.sqlite")
        with (temporary / "research.sqlite").open("rb") as stream:
            os.fsync(stream.fileno())
        batches = _accepted(source)
        for batch in batches:
            _, data = _packet_bytes(root, batch, packet_directory)
            _retain(temporary / "packets" / (batch["batch_id"] + ".json"), data)
            _retain(_path(root, batch["packet_path"], exists=False), data)
        manifest = {"schema_version": VERSION, "checkpoint_id": candidate.name,
                    "run_id": source.execute("SELECT run_id FROM graph_metadata").fetchone()[0],
                    "graph_sequence": _head(source), "schema_sha256": _sha(schema_bytes(storage_revision(source))), "created_at": _now(),
                    "database": {"relative_path": "research.sqlite", "sha256": _sha((temporary / "research.sqlite").read_bytes()), "size_bytes": (temporary / "research.sqlite").stat().st_size},
                    "batches": batches}
        data = _bytes(manifest)
        _retain(temporary / "manifest.json", data)
        _retain(temporary / "manifest.sha256", (_sha(data) + "\n").encode("ascii"))
        _sync_dir(temporary)
        _check_checkpoint(root, temporary)
        os.rename(temporary, candidate)
        _sync_dir(candidate.parent)
        _retain(root / "graph/recovery-install.json", _bytes({"schema_version": VERSION,
            "candidate": str(candidate.relative_to(root)), "manifest_sha256": _sha(data)}))
        return candidate
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def _finish_install(root):
    manifest, candidate = _install_candidate(root)
    for batch in manifest["batches"]:
        _, data = _packet_bytes(root, batch, candidate)
        _retain(_path(root, batch["packet_path"], exists=False), data)
    live = _path(root, "graph/research.sqlite", exists=False)
    displaced = Path(tempfile.mkdtemp(prefix=".displaced-", dir=root / "graph"))
    for suffix in ("", "-wal", "-shm"):
        old = _path(root, "graph/research.sqlite" + suffix, exists=False)
        if old.exists():
            _retain(displaced / ("research.sqlite" + suffix), old.read_bytes())
    _sync_dir(displaced)
    descriptor, temporary = tempfile.mkstemp(prefix=".install-", dir=live.parent)
    try:
        with os.fdopen(descriptor, "wb") as destination, (candidate / "research.sqlite").open("rb") as source:
            shutil.copyfileobj(source, destination)
            destination.flush()
            os.fsync(destination.fileno())
        _fault("recovery_before_install")
        for suffix in ("-wal", "-shm"):
            _path(root, "graph/research.sqlite" + suffix, exists=False).unlink(missing_ok=True)
        _sync_dir(live.parent)
        _fault("recovery_after_wal")
        os.replace(temporary, live)
        _sync_dir(live.parent)
        _fault("recovery_after_replace")
        _path(root, "graph/recovery-install.json").unlink()
        _sync_dir(live.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


def recover(root, batch_id=None, apply=False, *, evidence_validator=None):
    """Inspect recovery state, retry one explicit packet, or rebuild accepted history.

With apply=False nothing is promoted or rewritten. apply=True without a batch
replays only accepted DB/checkpoint history. Other packet files remain unproven.
The validator is used only for an explicit pending packet retry through ingest;
historical replay preserves accepted attestations without presenting new proof.
"""
    root = _root(root)
    _require(type(apply) is bool, "invalid_packet", "Recovery apply must be boolean")
    if batch_id is not None:
        _id(batch_id, filename=True)
        if apply:
            migration = next((revision for revision, (identity, _) in MIGRATIONS.items()
                              if batch_id == identity.replace("research-graph-", "schema-upgrade-", 1)), None)
            if migration is not None:
                result = upgrade_schema(root, target_revision=migration)
                return {**result, "command": "recover", "recovery_action": "explicit_schema_upgrade_retry"}
            result = ingest(root, _path(root, "graph/updates/" + batch_id + ".json"),
                            evidence_validator=evidence_validator)
            return {**result, "command": "recover", "recovery_action": "explicit_packet_retry"}
    try:
        with writer(root), _lock(root, exclusive=apply):
            source, packet_directory, basis = _recovery_source(root)
            try:
                manifest = _response(source, "recover", recovery_basis=basis,
                                     accepted_batches=len(_accepted(source)), applied=False)
                pending = _pending(root, source)
                if packet_directory is not None:
                    for packet in pending:
                        if packet["state"] == "pending":
                            packet["state"] = "unproven_after_checkpoint"
                if batch_id is not None:
                    accepted = _get(source, "ingest_batches", batch_id)
                    _require(accepted is not None or any(p["batch_id"] == batch_id for p in pending), "unknown_identity", "Recovery batch is unknown")
                    manifest["batch"] = {"batch_id": batch_id, "state": "accepted", "accepted_at": accepted["accepted_at"], "resulting_sequence": accepted["resulting_sequence"]} if accepted else next(p for p in pending if p["batch_id"] == batch_id)
                manifest["pending_packets"] = pending
                if not apply:
                    return manifest
                if basis == "pending_recovery_install":
                    source.close()
                    source = None
                    _finish_install(root)
                    manifest.update(applied=True, recovery_action="resumed_accepted_install")
                    return manifest
                temporary = Path(tempfile.mkdtemp(prefix=".research-graph-replay-", dir=root))
                try:
                    database = temporary / "research.sqlite"
                    sqlite3.connect(database).close()
                    target = _connect(database, check=False)
                    try:
                        target.executescript(SCHEMA.read_text(encoding="utf-8"))
                        _replay(root, source, target, packet_directory)
                        _audit(root, target, checkpoint_dir=packet_directory)
                        target.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                        target.execute("PRAGMA journal_mode=DELETE")
                    finally:
                        target.close()
                    _prepare_install(root, source, database, packet_directory)
                    source.close()
                    source = None
                    _finish_install(root)
                    manifest.update(applied=True, recovery_action="accepted_history_replay")
                    return manifest
                finally:
                    shutil.rmtree(temporary)
            finally:
                if source:
                    source.close()
    except sqlite3.Error as error:
        raise _sql_error(error) from None
    except OSError:
        raise GraphError("integrity_error", "Recovery files are unavailable or could not be retained") from None
