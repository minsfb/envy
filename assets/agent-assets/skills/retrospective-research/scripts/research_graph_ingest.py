#!/usr/bin/env python3
"""Normalize immutable recorder evidence, without dispatching or spending.

Extraction boundary: only adapter/reader-declared sources and references are
normalized. This module does NOT scan HTML, Markdown or provider JSON for links.
It prevents loss during normalization, not omission by the original extractor.

Optional result fields:
  sources: [{source_id, namespace, external_id, title, type, url,
             version: {revision, modified_at}}]
  captures: [{id, source_id, format, relative_path, extent,
              extraction_status, limitations}]
``sources`` retains occurrences, whereas existing ``source_ids`` lists distinct
local aliases. Namespace denotes provider identity, never a retrieval route.
Native operation requests bind ``spec.graph_assignment_id``. Capture files must
have exactly the receipt-bound stdout bytes (a viewer copy is permitted). Derived
representations and extra provider files need a future receipt/derivation adapter;
an arbitrary local file cannot become native evidence through these fields.

Native returns bind ``return.graph_assignment_id`` and optionally supply ``graph``:
  inspected_at; inspections: [{id, source_id, extent, method, limitations,
    captures: [{capture_id, extent}], scopes: [{question_id, required_scope_key,
    format_policy_sha256, coverage, basis}]}];
  locators: [{id, capture_id, kind, position, excerpt?}];
  scans: [{id, capture_id, method, extent, limitations, references:
    [{id, kind, value, label, locator_id, target_source_id?}]}];
  assertions: [{id, kind, predicate, source_id, value?, object_source_id?,
    subject_locator_id?, object_locator_id?, question_id?, basis, temporal_scope?,
    evidence: [{locator_id, inspection_id?, stance, basis, rationale}]}].
Capture IDs may be result-local or existing graph capture IDs for another
verified capture of the same source. Object endpoint locators may reference an
existing graph locator on the named target; this does not assert target reading.
Return-local IDs are scoped to their immutable return. Existing lead
IDs/wording/questions/locators are all preserved. A lead
locator matching a graph locator ID uses it; other wording is an opaque locator.
Semantic extent and full-text claims remain reader attestations with explicit
limitations; the helper only proves recorder bindings and supported containment.

The coordinator emits observation packets without acceptance/closure mutations.
Every packet of a normalization is retained before its first commit. Restart
replays the original bytes, never a new insert list derived from changed state.
A reservation retains the complete packet manifest and chunk limits before the
first packet write. Interrupted staging blocks another normalization; resuming
staging must reproduce that exact manifest before installing any missing bytes.

validate_index_result(root, operation_id, result) preflights an unsaved draft;
the recorder uses validate_prepared_index for the exact timestamped envelope.
omit_index_version appends a receipt for unsupported optional source versions
only when no normalization plan/packets or accepted index content exists. It
proves failure and corrected normalization on a disposable snapshot. Original
result bytes, every candidate occurrence, captures, pagination and indexed time
are unchanged. The effective version omission is dependency-bound and carries
no new admission, reading or budget credit. Ordinary synchronization resumes it.

validate_reader_return(root, returned) checks a disposable SQLite backup with
the real recorder and normalizer; it neither retains an original nor reserves
budget in the source bundle. The recorder must hold the shared writer across
preflight, original retention and final ingestion; preflight grants no lease.
reject_reader_return(root, id, expected_original_sha256=..., reason=...) retires
only a proven-invalid, single-packet, zero-accepted return. Immutable rejection
receipts retain the error and original packet hashes. Rejection remains an
unresolved inventory, but no longer reserves unrelated normalization identities.
The retired packet cannot be promoted through native ingest or recovery.

A new return may carry corrects={return_id, original_sha256, reason}. This is a
structural scope downgrade with unchanged inspection/interpretation/locator
history, complete original reference/lead/question retention, and no new read
credit. Accepted correction chains are bounded and have one accepted successor.
ingestion_status(root, connection=...) and return_corrections(root, connection=...)
can use an export's existing snapshot; the exporting caller also owns writer()
while retaining sidecar files. Status includes immutable_receipts (path→SHA256)
and verified return_corrections; rejected alone never makes complete true.
"""

from contextlib import contextmanager, nullcontext
import copy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile


def _sibling(name):
    path = Path(__file__).with_name(name + ".py")
    existing = sys.modules.get(name)
    if existing is not None and Path(existing.__file__).resolve() == path.resolve():
        return existing
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


graph = _sibling("research_graph")
budget = _sibling("research_budget")
GraphError = graph.GraphError
MAX_MUTATIONS = 9000
MAX_PACKET_BYTES = 15 * 1024 * 1024
LEGACY = "Historical recorder fields do not attest native graph scope or assignment; any inspection timestamp is the return recording time, not a measured read interval."
ATTESTATION = "Semantic scope and interpretation are reader attestations, not machine-proven completeness."


def _require(ok, code, message, **details):
    if not ok:
        raise GraphError(code, message, **details)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                       allow_nan=False) + "\n").encode("utf-8")


def _id(prefix, *parts):
    return prefix + "-" + _sha(_bytes(parts))[:40]


def _path(root, relative):
    _require(isinstance(relative, str) and relative and not Path(relative).is_absolute()
             and ".." not in Path(relative).parts, "invalid_packet", "Invalid retained evidence path")
    path = root / relative
    _require(path.resolve().is_relative_to(root.resolve()), "integrity_error", "Evidence path leaves the bundle")
    return path


def _read(root, relative):
    path = _path(root, relative)
    _require(path.is_file(), "pending_dependency", "Retained recorder file is missing", path=relative)
    data = path.read_bytes()
    try:
        value = graph._read_json(data)
        _require(isinstance(value, dict), "integrity_error", "Retained recorder JSON must be an object", path=relative)
        return value, _sha(data)
    except GraphError:
        raise GraphError("integrity_error", "Retained recorder JSON is invalid", path=relative) from None


def _hash(root, relative):
    path = _path(root, relative)
    _require(path.is_file(), "pending_dependency", "Retained recorder file is missing", path=relative)
    return _sha(path.read_bytes())


def _journal(root):
    state, _ = _read(root, "research-budget.json")
    _require(state.get("version") == 1 and isinstance(state.get("events"), list),
             "integrity_error", "Invalid budget journal")
    previous = None
    for number, event in enumerate(state["events"], 1):
        _require(event.get("id") == number and type(event.get("id")) is int
                 and event.get("previous") == previous
                 and event.get("sha256") == budget.sha({k: v for k, v in event.items() if k != "sha256"}),
                 "integrity_error", "Budget journal chain changed")
        previous = event["sha256"]
    return state["events"]


def _row(connection, table, column, value):
    # Table/column names below are module constants, never caller query fragments.
    row = connection.execute("SELECT * FROM " + table + " WHERE " + column + "=?", (value,)).fetchone()
    return dict(row) if row is not None else None


def _assignment(root, connection, assignment_id, actor, *, allow_completed_correction=False, allow_legacy_search=False):
    row = _row(connection, "assignments", "assignment_id", assignment_id)
    _require(row is not None, "pending_dependency", "Declared graph assignment is missing", assignment_id=assignment_id)
    _require(row["actor_id"] == actor, "scope_mismatch", "Recorder actor differs from its assignment")
    work = _row(connection, "work_items", "work_id", row["work_id"])
    plan, digest = _read(root, "plans/" + row["plan_sha256"] + ".json")
    _require(digest == row["plan_sha256"], "integrity_error", "Saved assignment plan changed")
    items = [item for item in plan.get("items", []) if item.get("id") == row["plan_item_id"]]
    _require(len(items) == 1 and items[0].get("actor") == actor,
             "scope_mismatch", "Assignment does not match saved plan actor/item")
    _require(work["question_id"] in graph.plan_item_questions(plan, items[0]),
             "scope_mismatch", "Assigned question exceeds saved plan coverage")
    ledger = "plans/" + row["plan_sha256"] + ".ledger.json"
    _require(_hash(root, ledger) == plan.get("_record", {}).get("ledger_sha256"),
             "integrity_error", "Saved assignment ledger changed")
    _sibling("research_graph_work").validate_assignment_intent(root, connection, row,
        allow_completed_correction=allow_completed_correction, allow_legacy_search=allow_legacy_search)
    return row, work, items[0]


def _operation(root, operation_id, connection=None):
    graph._id(operation_id, filename=True)
    base = "operations/" + operation_id + "/"
    request, request_hash = _read(root, base + "request.json")
    receipt, receipt_hash = _read(root, base + "receipt.json")
    spec = request.get("spec", {})
    _require(spec.get("id") == operation_id, "integrity_error", "Operation identity differs from retained request")
    for name in ("request.json", "stdout", "stderr"):
        _require(receipt.get("files", {}).get(name) == _hash(root, base + name),
                 "integrity_error", "Operation receipt does not bind retained bytes", operation_id=operation_id)
    result, result_hash = _read(root, base + "result.json")
    _require(result.get("receipt_sha256") == receipt_hash, "integrity_error", "Result index belongs to another receipt")
    ids = result.get("source_ids")
    _require(isinstance(ids, list) and all(isinstance(v, str) and v.strip() for v in ids)
             and len(set(ids)) == len(ids), "invalid_packet", "Result source IDs must be distinct nonempty strings")
    assignment_id = spec.get("graph_assignment_id")
    event = None
    if (root / "research-budget.json").exists():
        events = _journal(root)
        admission = spec.get("research_admission", {})
        matches = [e for e in events if e["type"] == "admit" and e.get("operation") == operation_id]
        _require(len(matches) == 1, "pending_dependency", "Operation admission is missing or ambiguous", operation_id=operation_id)
        event = matches[0]
        _require(admission == {"event": event["id"], "sha256": event["sha256"]}
                 and event.get("fingerprint") == budget.sha({k: spec[k] for k in ("kind", "provider", "request")})
                 and all(event.get(k) == spec.get(k) for k in ("kind", "actor"))
                 and event.get("source_id") == spec["request"].get("source_id")
                 and event.get("timeout") == spec.get("timeout"),
                 "integrity_error", "Operation request differs from budget admission")
        info = spec.get("research", {})
        _require(all(event.get(k) == info.get(k) for k in ("stage", "branch", "parent_operation")),
                 "integrity_error", "Operation ancestry differs from budget admission")
    if assignment_id:
        _require(event is not None, "pending_dependency", "Native operation needs its budget admission")
        if connection is not None:
            # This path validates an already retained, receipt-bound operation.
            # A completed capture-policy correction preserves that old receipt;
            # it cannot authorize a fresh operation through this read-only path.
            assignment, work, item = _assignment(root, connection, assignment_id, spec.get("actor"),
                allow_completed_correction=True, allow_legacy_search=True)
            _require(spec.get("plan_sha256") == assignment["plan_sha256"]
                     and spec.get("plan_item") == assignment["plan_item_id"]
                     and all(spec.get(k) == item.get(k) for k in ("actor", "kind", "provider", "request")),
                     "scope_mismatch", "Native operation differs from its saved assignment")
            recorder = _sibling("research_bundle")
            plan = _row(connection, "plan_snapshots", "plan_sha256", assignment["plan_sha256"])
            try:
                recorder.check_planned_operation(root, spec, recorder.read(root / "contracts" / (plan["contract_sha256"] + ".json")))
            except ValueError:
                raise GraphError("scope_mismatch", "Native operation differs from its saved plan route") from None
            _require(work["generation"] == event["generation"] == assignment["generation_at_claim"]
                     and work["stage"] == event["stage"] and work["branch"] == event["branch"],
                     "scope_mismatch", "Native operation ancestry differs from graph work")
            intent = json.loads(work["request_json"])
            _require(work["kind"] == spec["kind"] or (work["kind"] == "inspect_visuals" and spec["kind"] == "read"),
                     "scope_mismatch", "Native operation kind differs from graph work")
            _require(graph._timestamp(request["started"]) >= graph._timestamp(assignment["claimed_at"]),
                     "scope_mismatch", "Native operation predates its assignment")
            if spec["kind"] == "read":
                _require(spec["request"]["format"] in intent.get("formats", [intent.get("format")]),
                         "scope_mismatch", "Native read differs from its graph format request")
                artifact = _source(connection, spec["request"]["source_id"])
                _require(artifact and _canonical(connection, artifact) == _canonical(connection, work["artifact_id"]),
                         "scope_mismatch", "Native read assignment targets another artifact")
    data = {"request": request, "request_hash": request_hash, "receipt": receipt,
            "receipt_hash": receipt_hash, "result": result, "result_hash": result_hash,
            "spec": spec, "event": event, "native": bool(assignment_id)}
    omission = _index_omission(root, connection, data)
    if omission:
        data["result"] = _omit_versions(result, omission["omissions"])
        data["index_omission"] = omission
    return data


def _omission_path(operation_id):
    return "operations/" + operation_id + "/index-version-omission.json"


def _unsupported_version_fields(version):
    if not isinstance(version, dict):
        return {"version"} if version is not None else set()
    unsupported = set()
    if version.get("revision") is not None and not isinstance(version["revision"], str):
        unsupported.add("revision")
    if version.get("modified_at") is not None:
        try:
            graph._timestamp(version["modified_at"])
        except GraphError:
            unsupported.add("modified_at")
    return unsupported


def _omit_versions(result, omissions):
    """Only downgrade unsupported optional metadata; never replace observations."""
    _require(isinstance(omissions, list) and omissions, "invalid_packet", "Version omissions must be a nonempty array")
    corrected, seen = copy.deepcopy(result), set()
    sources = corrected.get("sources", [])
    _require(isinstance(sources, list), "invalid_packet", "Structured sources must be an array")
    for omission in omissions:
        graph._object(omission, {"source_index", "source_id", "fields"}, {"source_index", "source_id", "fields"})
        index, fields = omission["source_index"], omission["fields"]
        _require(type(index) is int and 0 <= index < len(sources) and index not in seen,
                 "invalid_packet", "Omission must name one distinct original source occurrence")
        seen.add(index)
        source = sources[index]
        _require(isinstance(source, dict) and source.get("source_id") == omission["source_id"]
                 and source.get("source_id") in result["source_ids"],
                 "scope_mismatch", "Omission source identity differs from the original occurrence")
        _require(isinstance(fields, list) and fields and all(isinstance(field, str) for field in fields)
                 and len(set(fields)) == len(fields) and set(fields) <= {"version", "revision", "modified_at"},
                 "invalid_packet", "Only optional source version fields may be omitted")
        version = source.get("version")
        unsupported = _unsupported_version_fields(version)
        if fields == ["version"]:
            _require(unsupported, "invalid_packet", "A supported source version cannot be omitted")
            del source["version"]
        else:
            _require(isinstance(version, dict) and set(fields) <= unsupported,
                     "invalid_packet", "Only unsupported version fields may be omitted")
            for field in fields:
                del version[field]
    return corrected


def _index_accepted_before(connection, operation_id, sequence):
    return connection.execute("""SELECT 1 FROM graph_events WHERE sequence<=? AND
        ((entity_kind IN ('discoveries','captures') AND json_extract(payload_json,'$.after.operation_id')=?)
         OR batch_id=?) LIMIT 1""", (sequence, operation_id, _id("normalize", "operation", operation_id, 0))).fetchone() is not None


def _index_omission(root, connection, data):
    relative = _omission_path(data["spec"]["id"])
    if not _path(root, relative).exists():
        return None
    record, digest = _read(root, relative)
    _require(record.get("record_sha256") == _sha(_bytes({k: v for k, v in record.items() if k != "record_sha256"})),
             "integrity_error", "Index version omission receipt is incomplete or changed")
    _require(record.get("version") == 1 and record.get("kind") == "index-version-omission"
             and record.get("operation_id") == data["spec"]["id"]
             and record.get("original_sha256") == data["result_hash"]
             and isinstance(record.get("reason"), str) and record["reason"].strip(),
             "integrity_error", "Index version omission differs from its original")
    error = record.get("error", {})
    _require(error.get("code") == "invalid_packet" and error.get("retryable") is False
             and record.get("error_sha256") == _sha(_bytes(error)),
             "integrity_error", "Index version omission lacks its structural validation failure")
    _require(record.get("dependencies") == _dependencies(root, connection, "operation", data["spec"]["id"], data),
             "integrity_error", "Index version omission dependencies changed")
    if connection is None:
        with graph.snapshot(root) as snapshot:
            return _index_omission(root, snapshot, data)
    sequence = record.get("validation_sequence")
    _require(record.get("run_id") == _run(connection) and type(sequence) is int
             and 0 <= sequence <= graph._head(connection)
             and not _index_accepted_before(connection, data["spec"]["id"], sequence),
             "integrity_error", "Index version omission has no wholly unaccepted original snapshot")
    _omit_versions(data["result"], record.get("omissions"))
    return dict(record, relative_path=relative, sha256=digest)


def _returned(root, return_id, connection=None):
    graph._id(return_id, filename=True)
    original, digest = _read(root, "reader-returns/" + return_id + ".json")
    returned = original.get("return", {})
    _require(returned.get("id") == return_id, "integrity_error", "Return identity differs from original file")
    events = _journal(root)
    matches = [e for e in events if e["type"] == "reader-return" and e.get("return_id") == return_id]
    _require(len(matches) == 1, "pending_dependency", "Return budget event is missing or ambiguous", return_id=return_id)
    event = matches[0]
    payload = {k: v for k, v in original.items() if k != "event"}
    _require(original.get("event") == event["id"] and event.get("payload_sha256") == budget.sha(payload),
             "pending_dependency", "Original return and budget event do not match", return_id=return_id)
    operation = _operation(root, returned.get("read_id"), connection)
    _require(operation["spec"]["kind"] == "read" and operation["receipt"]["outcome"] == "ok"
             and original.get("source_id") == operation["spec"]["request"]["source_id"]
             and original.get("receipt_sha256") == operation["receipt_hash"],
             "scope_mismatch", "Original return source or receipt differs from read operation")
    actor = returned.get("actor")
    _require(any((e["type"] == "admit" and e.get("operation") == returned["read_id"] and e.get("assess"))
                 or (e["type"] == "assess" and e.get("read_id") == returned["read_id"])
                 for e in events if e["id"] < event["id"]),
             "pending_dependency", "Return has no prior substantive assessment admission")
    native = "graph" in returned
    if native:
        _require(isinstance(returned["graph"], dict) and returned.get("graph_assignment_id"),
                 "pending_dependency", "Structured native return needs its reader assignment")
        _require(operation["native"], "pending_dependency", "Native return needs a verified retrieval assignment")
        inspected_at = returned["graph"].get("inspected_at")
        if inspected_at:
            _require(graph._timestamp(inspected_at) >= graph._timestamp(operation["receipt"]["finished"]),
                     "scope_mismatch", "Native inspection predates the retained capture")
    if connection is not None:
        _require(_row(connection, "actors", "actor_id", actor) is not None,
                 "pending_dependency", "Original reader actor is not registered", actor_id=actor)
        if native:
            assignment, work, item = _assignment(root, connection, returned["graph_assignment_id"], actor, allow_completed_correction=True)
            artifact = _source(connection, original["source_id"])
            _require(artifact and work["artifact_id"] and _canonical(connection, artifact) == _canonical(connection, work["artifact_id"]),
                     "scope_mismatch", "Reader assignment concerns a different source")
            planned_source = _source(connection, item.get("request", {}).get("source_id"))
            _require(planned_source and _canonical(connection, planned_source) == _canonical(connection, artifact),
                     "scope_mismatch", "Reader assignment plan concerns another source")
    observed_event = event
    if returned.get("corrects") is not None:
        _require(connection is not None, "pending_dependency", "Correction needs a graph snapshot")
        correction = budget.correction_binding(root, returned)
        predecessor = correction["return_id"]
        rejected = _rejection(root, connection, predecessor)
        _require(rejected is not None, "pending_dependency", "Correction predecessor must be an explicitly rejected return")
        for row in connection.execute("SELECT return_id,file_path,payload_sha256 FROM reader_returns WHERE return_id != ?", (return_id,)):
            accepted, accepted_hash = _read(root, row["file_path"])
            _require(accepted_hash == row["payload_sha256"], "integrity_error", "Accepted correction file changed")
            _require(accepted.get("return", {}).get("corrects", {}).get("return_id") != predecessor,
                     "scope_mismatch", "Rejected return already has an accepted correction", return_id=predecessor)
        seen = {return_id}
        previous = returned
        previous_event_id = event["id"]
        while previous.get("corrects") is not None:
            binding = budget.correction_binding(root, previous)
            key = binding["return_id"]
            _require(key not in seen and len(seen) < 32, "invalid_packet", "Correction chain is cyclic or too long")
            seen.add(key)
            candidate = binding["original"]
            prior = [e for e in events if e["type"] == "reader-return" and e.get("return_id") == key]
            _require(len(prior) == 1 and prior[0]["id"] == candidate["event"] < previous_event_id
                     and prior[0]["payload_sha256"] == budget.sha({k: v for k, v in candidate.items() if k != "event"}),
                     "integrity_error", "Correction original journal binding differs")
            _require(candidate["source_id"] == original["source_id"] and candidate["receipt_sha256"] == original["receipt_sha256"],
                     "scope_mismatch", "Correction source or receipt differs")
            observed_event, previous_event_id, previous = prior[0], prior[0]["id"], candidate["return"]
    return {"original": original, "returned": returned, "hash": digest, "event": event,
            "observed_event": observed_event, "operation": operation, "native": native}


def _run(connection):
    return connection.execute("SELECT run_id FROM graph_metadata").fetchone()[0]


def _source(connection, source_id):
    row = connection.execute("SELECT artifact_id FROM artifact_keys WHERE namespace=? AND external_id=?",
                             ("bundle-source:" + _run(connection), source_id)).fetchone()
    return row[0] if row else None


def _canonical(connection, artifact_id):
    row = connection.execute("SELECT canonical_id FROM canonical_artifacts WHERE artifact_id=?", (artifact_id,)).fetchone()
    return row[0] if row else artifact_id


def receipt_execution_state(request, receipt):
    """A remote/tool interruption records lost observation, not worker exit.

    The existing subprocess recorder kills its owned process group and waits
    before writing these receipts. Tool-import receipts have no such boundary.
    """
    if receipt["outcome"] == "ok":
        return "succeeded"
    if receipt["outcome"] in {"interrupted", "timeout"} and not (
            request.get("transport") == "subprocess" and receipt.get("provenance") == "captured-subprocess"):
        return "unknown"
    return "failed"


def _insert(table, **record):
    return {"op": "insert", "table": table, "record": record}


def _caps(operation):
    result, spec = operation["result"], operation["spec"]
    declared = result.get("captures")
    if declared is None:
        if spec["kind"] != "read" or operation["receipt"]["outcome"] != "ok":
            return []
        return [{"id": "stdout", "source_id": spec["request"]["source_id"],
                 "format": spec["request"]["format"], "relative_path": "operations/" + spec["id"] + "/stdout",
                 "extent": {"body": {"attestation": "Retained response; captured extent was not declared.",
                                       "limitations": "Request extent does not prove captured completeness."}},
                 "extraction_status": "legacy_attested", "limitations": LEGACY}]
    _require(isinstance(declared, list), "invalid_packet", "Captured formats must be an array")
    _require(len({c.get("id") for c in declared}) == len(declared), "invalid_packet", "Capture IDs must be distinct")
    return declared


def capture_id(operation_id, local_id="stdout"):
    """Stable graph ID for a result-local capture; useful to callers and exports."""
    return _id("capture", operation_id, local_id)


def _capture_record(root, operation, capture, artifact_id, version_id=None):
    spec = operation["spec"]
    _require(spec["kind"] == "read" and operation["receipt"]["outcome"] == "ok"
             and capture.get("source_id") == spec["request"]["source_id"],
             "scope_mismatch", "Capture must name the successful read's source")
    _require(capture.get("format") == spec["request"].get("format"),
             "scope_mismatch", "Capture format differs from the recorded request")
    relative = capture.get("relative_path", "operations/" + spec["id"] + "/stdout")
    digest = _hash(root, relative)
    _require(digest == operation["receipt"]["files"]["stdout"],
             "integrity_error", "Capture is not the original receipt-bound stdout bytes")
    extent = capture.get("extent")
    _require(isinstance(extent, dict) and extent, "invalid_packet", "Capture needs its explicitly declared extent")
    return dict(capture_id=capture_id(spec["id"], capture["id"]), artifact_id=artifact_id,
                operation_id=spec["id"], version_id=version_id, format=capture["format"], relative_path=relative,
                sha256=digest, capture_extent_json=extent,
                extraction_status=capture.get("extraction_status", "partial"),
                receipt_sha256=operation["receipt_hash"], captured_at=operation["receipt"]["finished"],
                limitations=capture.get("limitations", "") or ATTESTATION)


def _operation_groups(root, connection, operation):
    spec, result = operation["spec"], operation["result"]
    operation_id = spec["id"]
    verification = "verified" if operation["native"] else "legacy_attested"
    mirror = dict(operation_id=operation_id, assignment_id=spec.get("graph_assignment_id"),
                  budget_event_id=str(operation["event"]["id"]) if operation["event"] else None,
                  request_sha256=operation["request_hash"], receipt_sha256=operation["receipt_hash"],
                  execution_state=receipt_execution_state(operation["request"], operation["receipt"]),
                  verification_state=verification)
    groups = []
    existing = _row(connection, "operation_links", "operation_id", operation_id)
    if not existing:
        groups.append([_insert("operation_links", **mirror)])
    else:
        _require(all(existing[k] == mirror[k] for k in ("assignment_id", "request_sha256")),
                 "integrity_error", "Existing operation mirror has different identity")
        if mirror["execution_state"] == "unknown" and existing["execution_state"] in {"cancelled", "failed"}:
            # A later explicit worker-stop reconciliation remains authoritative;
            # replaying the earlier interruption receipt cannot undo it.
            _require(existing["receipt_sha256"] == mirror["receipt_sha256"], "integrity_error", "Reconciled interruption receipt differs")
            # Historical failed mirrors also remain unchanged. G4 checks the
            # original ambiguous receipt and demands explicit worker-stop proof.
            mirror["execution_state"] = existing["execution_state"]
        changes = {k: v for k, v in mirror.items() if existing.get(k) != v and k not in ("operation_id", "assignment_id", "request_sha256")}
        if changes:
            revision = existing["revision"]
            updates = []
            if existing["execution_state"] in {"reserved", "admitted"} and mirror["execution_state"] in {"succeeded", "failed"}:
                # A retained tool-import receipt can establish the result without
                # an observed dispatch event. Preserve that gap via unknown;
                # never manufacture a dispatched observation to traverse states.
                updates.append({"op": "update", "table": "operation_links", "id": operation_id,
                                "expected_revision": revision, "changes": dict(changes, execution_state="unknown")})
                revision += 1
                changes = {"execution_state": mirror["execution_state"]}
            updates.append({"op": "update", "table": "operation_links", "id": operation_id,
                            "expected_revision": revision, "changes": changes})
            groups.append(updates)
    observations = result.get("sources", [])
    _require(isinstance(observations, list) and all(isinstance(s, dict) and s.get("source_id") in result["source_ids"] for s in observations),
             "invalid_packet", "Structured sources must map to original source IDs")
    observed = {s["source_id"] for s in observations}
    observations = observations + [{"source_id": sid} for sid in result["source_ids"] if sid not in observed]
    keys = {(row[0], row[1]): row[2] for row in connection.execute("SELECT namespace,external_id,artifact_id FROM artifact_keys")}
    aliases, versions, observed_versions = {}, {}, set()
    run_id = _run(connection)
    for rank, source in enumerate(observations):
        sid = source["source_id"]
        _require((source.get("namespace") is None) == (source.get("external_id") is None),
                 "invalid_packet", "Stable source identity needs both namespace and external ID")
        stable = (source.get("namespace", "legacy-source:" + run_id), source.get("external_id", sid))
        alias = ("bundle-source:" + run_id, sid)
        candidates = {keys[k] for k in (stable, alias) if k in keys}
        _require(len({_canonical(connection, aid) for aid in candidates}) <= 1, "scope_mismatch", "Source alias identifies different artifacts; reconcile identities first")
        # Keep the local alias's original row binding. Stable provider keys may
        # retain an older raw ID after a canonical merge without conflicting.
        artifact = keys.get(alias, keys.get(stable, _id("artifact", *stable)))
        group = []
        if not candidates:
            group.append(_insert("artifacts", artifact_id=artifact, title=source.get("title", ""),
                                 artifact_type=source.get("type", "unknown")))
        for key in (stable, alias):
            if key not in keys:
                group.append(_insert("artifact_keys", namespace=key[0], external_id=key[1], artifact_id=artifact,
                                     basis="Declared stable provider identity" if key == stable and "namespace" in source
                                     else "Original local source ID; provider identity/title may be unknown"))
                keys[key] = artifact
        aliases[sid] = artifact
        version = source.get("version")
        if version is not None:
            _require(isinstance(version, dict), "invalid_packet", "Source version must be an object")
            revision = version.get("revision")
            vid = _id("version", artifact, revision) if revision else _id("version", operation_id, rank)
            known = connection.execute("SELECT version_id FROM artifact_versions WHERE artifact_id=? AND provider_revision=?", (artifact, revision)).fetchone() if revision else None
            if known:
                vid = known[0]
            if vid not in observed_versions and not _row(connection, "artifact_versions", "version_id", vid):
                group.append(_insert("artifact_versions", version_id=vid, artifact_id=artifact,
                                     provider_revision=revision, modified_at=version.get("modified_at"),
                                     first_observed_at=result["indexed"], version_basis="Result-declared provider revision" if revision else "Provider revision unknown"))
            observed_versions.add(vid)
            versions.setdefault(sid, set()).add(vid)
        group.append(_insert("discoveries", discovery_id=_id("discovery", operation_id, rank), artifact_id=artifact,
                             operation_id=operation_id, rank=rank, observed_title=source.get("title", ""), observed_url=source.get("url")))
        groups.append(group)
    for capture in _caps(operation):
        sid = capture.get("source_id")
        _require(sid in aliases, "scope_mismatch", "Captured source is absent from the complete result index")
        candidates = versions.get(sid, set())
        _require(len(candidates) <= 1, "scope_mismatch", "Result declares ambiguous captured source versions")
        record = _capture_record(root, operation, capture, aliases[sid], next(iter(candidates), None))
        if not _row(connection, "captures", "capture_id", record["capture_id"]):
            groups.append([_insert("captures", **record)])
    return groups


def _return_groups(root, connection, data, *, enforce_intent=True):
    returned, original, native = data["returned"], data["original"], data["native"]
    rid, read_id, actor = returned["id"], returned["read_id"], returned["actor"]
    artifact = _source(connection, original["source_id"])
    _require(artifact is not None, "pending_dependency", "Original return source is not indexed")
    captures = {row["capture_id"]: dict(row) for row in connection.execute("SELECT * FROM captures WHERE operation_id=?", (read_id,))}
    _require(captures, "pending_dependency", "Original return has no retained source capture")
    capture_aliases = {entry["id"]: capture_id(read_id, entry["id"]) for entry in _caps(data["operation"])}
    canonical_artifact = _canonical(connection, artifact)
    checked_captures = set()
    pinned = None
    if native and enforce_intent:
        _, work, _ = _assignment(root, connection, returned["graph_assignment_id"], actor, allow_completed_correction=True)
        request = json.loads(work["request_json"])
        if "capture_ids" in request:
            pinned = set(validate_capture_bindings(root, connection, work))

    def capture(value):
        key = capture_aliases.get(value, value)
        _require(pinned is None or key in pinned, "scope_mismatch", "Return capture is outside the assignment's retained capture binding")
        if key not in captures:
            existing = _row(connection, "captures", "capture_id", key)
            if existing:
                captures[key] = existing
        _require(key in captures, "scope_mismatch", "Return references a capture outside its original read")
        # This function only builds declarations against one stable snapshot.
        # Hundreds of locators can share a capture; its identity need only be
        # checked once here. Scope and evidence validation still run per record.
        if key not in checked_captures:
            _require(_canonical(connection, captures[key]["artifact_id"]) == canonical_artifact,
                     "scope_mismatch", "Return capture belongs to a different source")
            checked_captures.add(key)
        return key

    def source(value):
        aid = _source(connection, value)
        _require(aid is not None, "pending_dependency", "Return names an unindexed source", source_id=value)
        return aid

    graph_return = returned.get("graph", {})
    observed_at = graph_return.get("inspected_at") or datetime.fromtimestamp(data.get("observed_event", data["event"])["at"], timezone.utc).isoformat()
    rows = [_insert("reader_returns", return_id=rid, actor_id=actor,
                    assignment_id=returned.get("graph_assignment_id") if native else None,
                    file_path="reader-returns/" + rid + ".json", payload_sha256=data["hash"],
                    receipt_sha256=original["receipt_sha256"], recorded_budget_event=str(data["event"]["id"]),
                    verification_state="verified" if native else "legacy_attested", limitations=ATTESTATION if native else LEGACY)]
    locators = {}
    for item in graph_return.get("locators", []):
        local = item["id"]
        _require(local not in locators, "invalid_packet", "Return locator IDs must be distinct")
        locators[local] = _id("locator", rid, local)
        excerpt = item.get("excerpt")
        rows.append(_insert("locators", locator_id=locators[local], capture_id=capture(item["capture_id"]),
                            locator_kind=item["kind"], locator_json=item["position"], excerpt=excerpt,
                            excerpt_hash=_sha(excerpt.encode("utf-8")) if excerpt is not None else None))

    def locator(local):
        _require(local in locators, "invalid_packet", "Return references an undeclared locator")
        return locators[local]

    inspections = graph_return.get("inspections", []) if native else [{
        "id": "legacy", "source_id": original["source_id"], "extent": returned["inspection"] + ": " + returned["coverage"],
        "method": "historical reader attestation", "limitations": LEGACY,
        "captures": [{"capture_id": next(iter(captures)), "extent": {"body": {
            "attestation": returned["coverage"], "limitations": LEGACY}}}], "scopes": []}]
    inspection_ids = set()
    for item in inspections:
        local = item["id"]
        _require(local not in inspection_ids, "invalid_packet", "Return inspection IDs must be distinct")
        inspection_ids.add(local)
        aid = source(item["source_id"])
        _require(_canonical(connection, aid) == _canonical(connection, artifact),
                 "scope_mismatch", "Inspection source differs from original reader return")
        iid = _id("inspection", rid, local)
        time_limit = "" if graph_return.get("inspected_at") else " Inspection time uses return recording time because the exact read time was not declared."
        rows.append(_insert("inspections", inspection_id=iid, artifact_id=aid, return_id=rid, actor_id=actor,
                            inspection_extent=item["extent"], method=item["method"], inspected_at=observed_at,
                            provenance_basis="native" if native else "legacy_attested",
                            limitations=(item.get("limitations", "") + " " + (ATTESTATION if native else LEGACY) + time_limit).strip()))
        for entry in item.get("captures", []):
            rows.append(_insert("inspection_captures", inspection_id=iid, capture_id=capture(entry["capture_id"]), inspected_extent_json=entry["extent"]))
        for scope in item.get("scopes", []):
            rows.append(_insert("inspection_scopes", inspection_id=iid, **scope))
    scan_ids = set()
    for scan in graph_return.get("scans", []):
        local = scan["id"]
        _require(local not in scan_ids, "invalid_packet", "Return scan IDs must be distinct")
        scan_ids.add(local)
        sid = _id("scan", rid, local)
        refs = scan["references"]
        rows.append(_insert("reference_scans", scan_id=sid, capture_id=capture(scan["capture_id"]), return_id=rid,
                            method=scan["method"], scanned_extent_json=scan["extent"], extracted_count=len(refs),
                            limitations=scan.get("limitations", "") or ATTESTATION))
        reference_ids = set()
        for ref in refs:
            _require(ref["id"] not in reference_ids, "invalid_packet", "Scan occurrence IDs must be distinct")
            reference_ids.add(ref["id"])
            target = _source(connection, ref.get("target_source_id")) if ref.get("target_source_id") else None
            # Unknown IDs stay observable in the immutable return and in the
            # occurrence label; an unresolved candidate is never invented.
            label = ref.get("label", "")
            if ref.get("target_source_id") and target is None:
                label += " [unresolved source_id: " + ref["target_source_id"] + "]"
            rows.append(_insert("reference_occurrences", reference_id=_id("reference", rid, local, ref["id"]),
                                from_artifact_id=artifact, locator_id=locator(ref["locator_id"]), scan_id=sid,
                                local_reference_key=ref["id"], kind=ref["kind"], original_value=ref["value"], label=label,
                                target_artifact_id=target if ref["kind"] == "link" else None, extraction_method=scan["method"]))
    assertion_ids = set()
    for item in graph_return.get("assertions", []):
        local = item["id"]
        _require(local not in assertion_ids, "invalid_packet", "Return assertion IDs must be distinct")
        assertion_ids.add(local)
        aid = source(item["source_id"])
        _require(_canonical(connection, aid) == _canonical(connection, artifact),
                 "scope_mismatch", "Assertion subject differs from original reader return source")
        assertion = dict(assertion_id=_id("assertion", rid, local), kind=item["kind"], predicate=item["predicate"],
                         subject_artifact_id=aid, question_id=item.get("question_id"), basis=item["basis"],
                         return_id=rid, temporal_scope_json=item.get("temporal_scope", {}), assessed_at=observed_at)
        if "value" in item:
            assertion["value_json"] = item["value"]
        if item.get("object_source_id"):
            assertion["object_artifact_id"] = source(item["object_source_id"])
        for field in ("subject_locator_id", "object_locator_id"):
            if item.get(field):
                if field == "object_locator_id" and item[field] not in locators:
                    _require(_row(connection, "locators", "locator_id", item[field]) is not None,
                             "pending_dependency", "Relationship target locator is not indexed")
                    assertion[field] = item[field]
                else:
                    assertion[field] = locator(item[field])
        rows.append(_insert("assertions", **assertion))
        for index, evidence in enumerate(item.get("evidence", [])):
            inspection = evidence.get("inspection_id")
            _require(inspection is None or inspection in inspection_ids, "invalid_packet", "Evidence names an undeclared inspection")
            rows.append(_insert("assertion_evidence", evidence_id=_id("evidence", rid, local, index),
                                assertion_id=assertion["assertion_id"], locator_id=locator(evidence["locator_id"]),
                                inspection_id=_id("inspection", rid, inspection) if inspection else None,
                                stance=evidence["stance"], basis=evidence["basis"], rationale=evidence["rationale"]))
    kinds = {"vocabulary": "vocabulary", "linked-record": "linked_record", "neighborhood": "neighborhood", "question-gap": "observed_gap"}
    lead_ids = set()
    for lead in returned.get("leads", []):
        _require(lead["id"] not in lead_ids, "invalid_packet", "Returned lead IDs must be distinct")
        lead_ids.add(lead["id"])
        lid = _id("lead", rid, lead["id"])
        kind = lead.get("graph_kind", kinds.get(lead["kind"]))
        _require(kind in set(kinds.values()) | {"inferred_gap"}, "invalid_packet", "Unknown returned lead kind")
        rows.append(_insert("leads", lead_id=lid, kind=kind, original_value=lead["value"],
                            normalized_key=" ".join(lead["value"].casefold().split()), significance=lead["reason"], disposition="pending"))
        if lead["locator"] in locators:
            lead_locator = locator(lead["locator"])
        else:
            lead_locator = _id("lead-locator", rid, lead["id"])
            rows.append(_insert("locators", locator_id=lead_locator, capture_id=next(iter(captures)), locator_kind="metadata",
                                locator_json={"provider_metadata": {"original_locator": lead["locator"], "limitations": "Opaque original reader locator; position not machine-verified."}}))
        for question in dict.fromkeys(lead["questions"]):
            rows.append(_insert("lead_origins", origin_id=_id("lead-origin", rid, lead["id"], question), lead_id=lid,
                                return_id=rid, returned_lead_id=lead["id"], locator_id=lead_locator, question_id=question,
                                original_value=lead["value"], origin_basis=lead["reason"]))
    # Reader records are one atomic observation: no half scan, unsupported
    # assertion, or unmapped lead can satisfy SQL postconditions.
    return [rows]


def _contains(available, requested):
    """Conservative numeric containment; semantic components remain attestations."""
    for component, wanted in requested.items():
        if component == "provider_metadata" or wanted in ("none", "not_retained", "unavailable"):
            continue
        have = available.get(component)
        if have == "all":
            continue
        if have == wanted:
            continue
        if not isinstance(have, dict) or not isinstance(wanted, dict):
            return False
        if have.get("unit") != wanted.get("unit"):
            return False
        if "ids" in wanted and not set(wanted["ids"]) <= set(have.get("ids", [])):
            return False
        if "ranges" in wanted and not all(any(h["start"] <= w["start"] <= w["end"] <= h["end"] for h in have.get("ranges", [])) for w in wanted["ranges"]):
            return False
        if not any(k in wanted for k in ("ids", "ranges")) and have != wanted:
            return False
    return True


def _matches(actual, expected):
    timestamps = {"captured_at", "inspected_at", "assessed_at"}
    return all(actual.get(key) == (graph._timestamp(value) if key in timestamps else value)
               for key, value in expected.items())


def _declared_return_record(root, connection, data, table, record):
    primary = graph._pk(table)
    candidates = [m["record"] for group in _return_groups(root, connection, data) for m in group
                  if m["table"] == table and all(m["record"].get(k) == record.get(k) for k in primary)]
    _require(len(candidates) == 1 and _matches(record, candidates[0]),
             "scope_mismatch", "Graph observation differs from the immutable reader declaration", return_id=data["returned"]["id"])


def _retained_capture(root, connection, capture):
    _require(capture is not None, "pending_dependency", "Referenced capture is missing")
    operation = _operation(root, capture["operation_id"], connection)
    _require(_hash(root, capture["relative_path"]) == capture["sha256"]
             and operation["receipt_hash"] == capture["receipt_sha256"],
             "integrity_error", "Previously captured evidence changed")
    return operation


def validate_capture_bindings(root, connection, work):
    """Check explicit native capture selection without reading or spending anew."""
    request = work["request_json"]
    if isinstance(request, str):
        request = json.loads(request)
    if "capture_ids" not in request:
        return {}
    graph._json_column("work_items", "request_json", request, work)
    identities = graph.normalize_capture_ids(request["capture_ids"])
    artifact = _canonical(connection, work.get("artifact_id"))
    _require(artifact is not None, "pending_dependency", "Retained read target is not indexed")
    formats = request.get("formats", [request.get("format")])
    selected = {}
    for identity in identities:
        capture = _row(connection, "captures", "capture_id", identity)
        _require(capture is not None, "pending_dependency", "Pinned capture is not indexed", capture_id=identity)
        _require(_canonical(connection, capture["artifact_id"]) == artifact, "scope_mismatch", "Pinned capture belongs to another source")
        operation = _row(connection, "operation_links", "operation_id", capture["operation_id"])
        _require(operation and operation["verification_state"] == "verified" and operation["execution_state"] == "succeeded"
                 and capture["extraction_status"] in {"complete", "partial"},
                 "scope_mismatch", "Pinned capture requires successful native retrieval provenance")
        proof = _retained_capture(root, connection, capture)
        _require(proof["native"] and proof["receipt"]["outcome"] == "ok", "scope_mismatch", "Historical capture cannot gain native retained-read credit")
        captured = json.loads(capture["capture_extent_json"])
        _require(capture["format"] in formats and graph.satisfies_scope(captured, captured, request["extent"]),
                 "scope_mismatch", "Pinned capture does not support the requested format and extent")
        selected[identity] = capture
    _require(all(any(capture["format"] == fmt for capture in selected.values()) for fmt in formats),
             "scope_mismatch", "Pinned captures are missing a requested format")
    return selected


def _validate_evidence(root, connection, packet, mutation):
    """G1 callback: local recorder facts only; never writes DB/files or spends."""
    root = Path(root)
    table, row = mutation["table"], mutation.get("after", mutation.get("record", {}))
    if table == "operation_links":
        if ((mutation.get("before") or {}).get("execution_state") == "cancelled"
                and row.get("execution_state") in {"succeeded", "failed"}):
            # Direct index/sync and completion share the same late-receipt gate.
            return _sibling("research_graph_work").evidence_validator(root, connection, packet, mutation)
        operation = _operation(root, row["operation_id"], connection)
        expected = dict(assignment_id=operation["spec"].get("graph_assignment_id"),
                        request_sha256=operation["request_hash"], receipt_sha256=operation["receipt_hash"],
                        budget_event_id=str(operation["event"]["id"]) if operation["event"] else None,
                        verification_state="verified" if operation["native"] else "legacy_attested",
                        execution_state=receipt_execution_state(operation["request"], operation["receipt"]))
        if (mutation.get("op") == "update" and row.get("execution_state") == "unknown"
                and (mutation.get("before") or {}).get("execution_state") in {"reserved", "admitted"}):
            expected["execution_state"] = "unknown"
        _require(_matches(row, expected), "scope_mismatch", "Graph operation does not match authoritative recorder evidence")
        return True
    if table == "captures":
        operation = _operation(root, row["operation_id"], connection)
        candidates = [c for c in _caps(operation) if capture_id(row["operation_id"], c["id"]) == row["capture_id"]]
        _require(len(candidates) == 1, "scope_mismatch", "Capture was not declared in the original result")
        source = _source(connection, candidates[0]["source_id"])
        _require(source == row["artifact_id"], "scope_mismatch", "Capture source differs from result identity")
        versions = []
        for rank, observation in enumerate(operation["result"].get("sources", [])):
            if observation["source_id"] == candidates[0]["source_id"] and observation.get("version") is not None:
                revision = observation["version"].get("revision")
                known = connection.execute("SELECT version_id FROM artifact_versions WHERE artifact_id=? AND provider_revision=?", (source, revision)).fetchone() if revision else None
                versions.append(known[0] if known else _id("version", operation["spec"]["id"], rank))
        _require(len(set(versions)) <= 1, "scope_mismatch", "Result declares ambiguous captured source versions")
        expected = _capture_record(root, operation, candidates[0], source, versions[-1] if versions else None)
        _require(_matches(row, expected), "scope_mismatch", "Capture differs from the original result declaration")
        return True
    rid = row.get("return_id")
    if not rid and row.get("inspection_id"):
        inspection = _row(connection, "inspections", "inspection_id", row["inspection_id"])
        rid = inspection["return_id"] if inspection else None
    if not rid and row.get("assertion_id"):
        assertion = _row(connection, "assertions", "assertion_id", row["assertion_id"])
        rid = assertion["return_id"] if assertion else None
    if not rid and row.get("scan_id"):
        scan = _row(connection, "reference_scans", "scan_id", row["scan_id"])
        rid = scan["return_id"] if scan else None
    if rid:
        _require(_rejection(root, connection, rid) is None, "scope_mismatch", "Rejected return packet is retired; submit an explicitly linked correction", return_id=rid)
        data = _returned(root, rid, connection)
        _declared_return_record(root, connection, data, table, row)
        if table == "reference_scans":
            capture = _row(connection, "captures", "capture_id", row["capture_id"])
            _retained_capture(root, connection, capture)
            _require(capture and _contains(json.loads(capture["capture_extent_json"]), row["scanned_extent_json"]),
                     "scope_mismatch", "Reference scan exceeds explicitly captured scope")
        if table == "inspection_captures" and data["native"]:
            _, work, _ = _assignment(root, connection, data["returned"]["graph_assignment_id"], data["returned"]["actor"], allow_completed_correction=True)
            request = json.loads(work["request_json"])
            if "capture_ids" in request:
                _require(row["capture_id"] in request["capture_ids"], "scope_mismatch", "Inspected capture is outside the retained capture binding")
            capture = _row(connection, "captures", "capture_id", row["capture_id"])
            captured_operation = _retained_capture(root, connection, capture)
            _require(capture and capture["extraction_status"] != "legacy_attested"
                     and captured_operation["native"] and captured_operation["receipt"]["outcome"] == "ok"
                     and _contains(json.loads(capture["capture_extent_json"]), row["inspected_extent_json"]),
                     "scope_mismatch", "Inspected extent exceeds explicitly captured scope")
        if table == "inspection_scopes" and data["native"]:
            assignment, work, _ = _assignment(root, connection, data["returned"]["graph_assignment_id"], data["returned"]["actor"], allow_completed_correction=True)
            _require(row["question_id"] == work["question_id"] and row["required_scope_key"] == work["required_scope_key"]
                     and row["format_policy_sha256"] == work["format_policy_sha256"],
                     "scope_mismatch", "Inspection scope differs from its assignment")
            if row["coverage"] == "sufficient":
                request = json.loads(work["request_json"])
                inspected = list(connection.execute("SELECT c.*,i.inspected_extent_json FROM inspection_captures i JOIN captures c USING(capture_id) WHERE inspection_id=?", (row["inspection_id"],)))
                formats = request.get("formats", [request.get("format")])
                _require(all(any(c["format"] == fmt and graph.satisfies_scope(json.loads(c["capture_extent_json"]),
                         json.loads(c["inspected_extent_json"]), request.get("extent", {})) for c in inspected) for fmt in formats),
                         "scope_mismatch", "Sufficient scope is missing requested formats or inspected extent")
                if request.get("version_id"):
                    _require(all(c["version_id"] == request["version_id"] for c in inspected), "scope_mismatch", "Inspected source revision differs from assignment")
                if "capture_ids" in request:
                    _require({c["capture_id"] for c in inspected} == set(request["capture_ids"]),
                             "scope_mismatch", "Sufficient inspection must include every pinned capture")
        if table == "assertion_evidence" and row["basis"] == "inspected":
            locator = _row(connection, "locators", "locator_id", row["locator_id"])
            inspected = connection.execute("SELECT inspected_extent_json FROM inspection_captures WHERE inspection_id=? AND capture_id=?", (row["inspection_id"], locator["capture_id"])).fetchone()
            _require(inspected is not None, "scope_mismatch", "Assertion locator was not inspected")
            _require(graph.locator_within_extent(locator["locator_kind"], json.loads(locator["locator_json"]),
                                                  json.loads(inspected[0])) is not False,
                     "scope_mismatch", "Assertion locator lies outside inspected extent")
        return True
    raise GraphError("pending_dependency", "Recorder validation cannot establish this mutation's original evidence", table=table)


def _safe(function, *args):
    try:
        return function(*args)
    except (TypeError, ValueError, KeyError, AttributeError, OverflowError):
        raise GraphError("invalid_packet", "Recorder evidence or structured graph fields have invalid types") from None
    except OSError:
        raise GraphError("integrity_error", "Retained normalization or evidence files could not be accessed") from None


def evidence_validator(root, connection, packet, mutation):
    """Validate local recorder evidence; unsupported dispatch/completion stays pending."""
    return _safe(_validate_evidence, root, connection, packet, mutation)


def _fsync(directory):
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _save(path, data):
    missing, parent = [], path.parent
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
        _fsync(directory.parent)
    if path.exists():
        _require(path.read_bytes() == data, "batch_conflict", "Retained normalization bytes differ")
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.link(temporary, path)
        _fsync(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _normalization_path(kind, key):
    return "graph/normalizations/" + _id(kind, key) + "/plan.json"


def _read_plan(root, relative):
    plan, digest = _read(root, relative)
    checksum = _path(root, str(Path(relative).with_suffix(".sha256")))
    _require(checksum.is_file() and checksum.read_text(encoding="ascii").strip() == digest,
             "integrity_error", "Complete normalization plan is missing or changed")
    reservation = _path(root, str(Path(relative).parent / "reservation.json"))
    if reservation.exists():
        _require(_read_reservation(root, relative)["plan"] == plan, "integrity_error",
                 "Normalization plan differs from its original reservation")
    return plan


def _read_reservation(root, relative):
    reservation, _ = _read(root, str(Path(relative).parent / "reservation.json"))
    plan, limits = reservation.get("plan", {}), reservation.get("limits", {})
    _require(reservation.get("version") == 1 and set(limits) == {"max_mutations", "max_packet_bytes"}
             and all(type(value) is int and value > 0 for value in limits.values())
             and limits["max_packet_bytes"] <= graph.MAX_PACKET
             and plan.get("kind") in {"operation", "return"}
             and _normalization_path(plan["kind"], plan.get("original_id")) == relative,
             "integrity_error", "Incomplete normalization reservation is invalid")
    return reservation


def _rejection(root, connection, return_id):
    relative = str(Path(_normalization_path("return", return_id)).parent / "rejection.json")
    path = _path(root, relative)
    if not path.exists():
        return None
    record, digest = _read(root, relative)
    _require(record.get("record_sha256") == _sha(_bytes({k: v for k, v in record.items() if k != "record_sha256"})),
             "integrity_error", "Return rejection receipt is incomplete or changed")
    plan_path = _normalization_path("return", return_id)
    plan = _read_plan(root, plan_path)
    _require(record.get("version") == 1 and record.get("kind") == "return" and record.get("original_id") == return_id
             and record.get("original_sha256") == plan["original_sha256"] == _hash(root, plan["original_path"])
             and record.get("plan_sha256") == _hash(root, plan_path) and record.get("packets") == plan["packets"]
             and len(plan["packets"]) == 1 and record.get("accepted_batches") == []
             and isinstance(record.get("reason"), str) and record["reason"].strip(),
             "integrity_error", "Return rejection differs from retained original or plan")
    error = record.get("error", {})
    _require(error.get("code") in {"invalid_packet", "scope_mismatch"} and error.get("retryable") is False
             and record.get("error_sha256") == _sha(_bytes(error)) and record.get("run_id") == _run(connection),
             "integrity_error", "Return rejection has no bound validation failure")
    sequence = connection.execute("SELECT COALESCE(MAX(sequence),0) FROM graph_events").fetchone()[0]
    _require(type(record.get("validation_sequence")) is int and 0 <= record["validation_sequence"] <= sequence,
             "integrity_error", "Return rejection belongs to a later graph snapshot")
    _require(record.get("dependencies") == plan["dependencies"] and
             all(_hash(root, p) == digest for p, digest in plan["dependencies"].items()),
             "integrity_error", "Rejected original dependencies changed")
    for packet in plan["packets"]:
        _require(_hash(root, packet["path"]) == packet["sha256"] and
                 _row(connection, "ingest_batches", "batch_id", packet["batch_id"]) is None and
                 not connection.execute("SELECT 1 FROM graph_events WHERE batch_id=?", (packet["batch_id"],)).fetchone(),
                 "integrity_error", "Rejected return packet changed or has accepted history")
    return dict(record, relative_path=relative, sha256=digest)


@contextmanager
def _validation_clone(root, connection, extra_paths=()):
    """Real validators on one disposable SQLite snapshot and retained originals.

    All retained files are copied by value; the live database uses backup. No
exports/checkpoints or providers participate. Final ingestion rechecks races.
"""
    root = Path(root).resolve()
    with tempfile.TemporaryDirectory(prefix="research-return-preflight-") as temporary:
        target = Path(temporary)
        (target / ".research-graph.lock").touch(mode=0o600)
        for name in ("operations", "reader-returns", "contracts", "plans", "graph/updates", "graph/normalizations", "graph/imports", "graph/exports", "graph/checkpoints"):
            (target / name).mkdir(parents=True, exist_ok=True)
        for name in ("bundle.json", "contract.json", "research-budget.json", "plan.json", "plan.md"):
            if (root / name).is_file():
                shutil.copyfile(_path(root, name), target / name)
        paths = set(extra_paths)
        for name in ("operations", "reader-returns", "contracts", "plans", "graph/updates", "graph/normalizations", "graph/imports"):
            directory = root / name
            if directory.exists():
                paths.update(str(p.relative_to(root)) for p in directory.rglob("*") if p.is_file())
        paths.update(row[0] for row in connection.execute("SELECT relative_path FROM captures"))
        if "inspection_withdrawals" in graph.storage_tables(connection):
            paths.update(row[0] for row in connection.execute("SELECT evidence_path FROM inspection_withdrawals"))
        for relative in sorted(paths):
            if relative in {"graph/research.sqlite", "graph/research.sqlite-wal", "graph/research.sqlite-shm"}:
                continue
            source = _path(root, relative)
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
        cloned = sqlite3.connect(target / "graph/research.sqlite")
        try:
            connection.backup(cloned)
            # A backup of a WAL database has no source WAL/SHM companions.
            # Finish a standalone private database before its first read-only open.
            cloned.execute("PRAGMA journal_mode=DELETE")
        finally:
            cloned.close()
        yield target


def _capture_paths(operation):
    base = "operations/" + operation["spec"]["id"] + "/"
    return [capture.get("relative_path", base + "stdout") for capture in _caps(operation)]


def _validate_prepared_index(root, operation_id, prepared):
    root = Path(root).resolve()
    graph._id(operation_id, filename=True)
    with graph.writer(root), graph.snapshot(root) as connection:
        _require(not _path(root, "operations/" + operation_id + "/result.json").exists(),
                 "batch_conflict", "Original result is already retained; synchronize or repair it")
        request, _ = _read(root, "operations/" + operation_id + "/request.json")
        receipt, _ = _read(root, "operations/" + operation_id + "/receipt.json")
        extra = _capture_paths(dict(spec=request["spec"], receipt=receipt, result=prepared))
        stamp = graph._response(connection, "check-index")
        with _validation_clone(root, connection, extra) as clone:
            _sibling("research_bundle").write(clone / "operations" / operation_id / "result.json", prepared)
            result = index_operation(clone, operation_id)
        return dict(stamp, operation_id=operation_id, valid=True, normalization_state=result["state"],
                    original_retained=False, budget_reserved=False,
                    limitations=["Preflight validates this snapshot; final original ingestion rechecks current state."])


def validate_prepared_index(root, operation_id, prepared):
    """Recorder boundary for the exact timestamped index it will retain."""
    return _safe(_validate_prepared_index, root, operation_id, prepared)


def validate_index_result(root, operation_id, result):
    """Public draft preflight; neither retains the original nor reserves work."""
    with graph.writer(root):
        prepared = _safe(_sibling("research_bundle").prepare_index_result, Path(root), operation_id, result)
        return validate_prepared_index(root, operation_id, prepared)


def _omit_index_version(root, operation_id, expected_original_sha256, omissions, reason):
    root = Path(root).resolve()
    graph._id(operation_id, filename=True)
    _require(isinstance(reason, str) and reason.strip(), "invalid_packet", "Version omission requires an explicit reason")
    with graph.writer(root):
        with graph.snapshot(root) as connection:
            data = _operation(root, operation_id, connection)
            _require(data["result_hash"] == expected_original_sha256,
                     "batch_conflict", "Original result hash differs from the requested omission")
            existing = data.get("index_omission")
            if existing:
                _require(existing["omissions"] == omissions and existing["reason"] == reason,
                         "batch_conflict", "Index version omission already has different retained intent")
                record = existing
            else:
                directory = _path(root, _normalization_path("operation", operation_id)).parent
                _require(not directory.exists(), "invalid_packet", "Only an index without a retained normalization plan or packets may be repaired")
                sequence = graph._head(connection)
                _require(not _index_accepted_before(connection, operation_id, sequence),
                         "invalid_packet", "Accepted index observations cannot be repaired by omission")
                _omit_versions(data["result"], omissions)
                record = dict(version=1, kind="index-version-omission", operation_id=operation_id,
                              original_sha256=data["result_hash"], reason=reason, omissions=omissions,
                              dependencies=_dependencies(root, connection, "operation", operation_id, data),
                              run_id=_run(connection), validation_sequence=sequence)
                with _validation_clone(root, connection, _capture_paths(data)) as clone:
                    failure = None
                    try:
                        index_operation(clone, operation_id)
                    except GraphError as error:
                        _require(error.code == "invalid_packet" and not error.retryable,
                                 "pending_dependency", "Original indexing must have a nonretryable structural failure",
                                 validation_error=error.response("omit-index-version")["error"])
                        failure = error.response("omit-index-version")["error"]
                    _require(failure is not None, "invalid_packet", "Original index already validates; synchronize it instead")
                    _require(not _path(clone, _normalization_path("operation", operation_id)).parent.exists(),
                             "invalid_packet", "Only structural failures before normalization planning may be repaired")
                    record.update(error=failure, error_sha256=_sha(_bytes(failure)))
                    record["record_sha256"] = _sha(_bytes(record))
                    _save(_path(clone, _omission_path(operation_id)), _bytes(record))
                    index_operation(clone, operation_id)
                _save(_path(root, _omission_path(operation_id)), _bytes(record))
        result = index_operation(root, operation_id)
        return dict(result, command="omit-index-version", omission_path=_omission_path(operation_id),
                    original_sha256=data["result_hash"], omission_sha256=_hash(root, _omission_path(operation_id)))


def omit_index_version(root, operation_id, *, expected_original_sha256, omissions, reason):
    """Append a proven optional-version downgrade to an unaccepted original index."""
    return _safe(_omit_index_version, root, operation_id, expected_original_sha256, omissions, reason)


def _validate_reader_return(root, returned):
    root = Path(root).resolve()
    with graph.writer(root), graph.snapshot(root) as connection:
        stamp = graph._response(connection, "validate-reader-return")
        with _validation_clone(root, connection) as clone:
            budget.reader_return(clone, returned)
            result = ingest_reader_return(clone, returned["id"])
        return dict(stamp, return_id=returned["id"], valid=True, normalization_state=result["state"],
                    original_retained=False, budget_reserved=False,
                    limitations=["Preflight validates this snapshot; final original ingestion rechecks current state."])


def validate_reader_return(root, returned):
    """Validate with the real recorder/normalizer without changing this bundle."""
    return _safe(_validate_reader_return, root, returned)


def _reject_reader_return(root, return_id, expected_original_sha256, reason):
    root = Path(root).resolve()
    graph._id(return_id, filename=True)
    _require(isinstance(reason, str) and reason.strip(), "invalid_packet", "Explicit rejection requires a reason")
    with graph.writer(root), graph.snapshot(root) as connection:
        existing = _rejection(root, connection, return_id)
        if existing:
            _require(existing["original_sha256"] == expected_original_sha256 and existing["reason"] == reason,
                     "batch_conflict", "Return rejection already has different retained intent")
            return graph._response(connection, "reject-reader-return", state="rejected", idempotent=True, rejection=existing)
        relative = _normalization_path("return", return_id)
        plan = _read_plan(root, relative)
        _require(plan["kind"] == "return" and plan["original_id"] == return_id and len(plan["packets"]) == 1,
                 "invalid_packet", "Only one atomic return normalization may be rejected")
        data = _returned(root, return_id, connection)
        _require(data["hash"] == expected_original_sha256 == plan["original_sha256"] and
                 _dependencies(root, connection, "return", return_id, data) == plan["dependencies"],
                 "integrity_error", "Rejected original or normalization dependencies differ")
        status = _plan_status(root, connection, plan)
        _require(not status["accepted_batches"] and not any(connection.execute("SELECT 1 FROM graph_events WHERE batch_id=?", (p["batch_id"],)).fetchone() for p in plan["packets"]),
                 "invalid_packet", "Accepted or partially accepted normalization cannot be rejected")
        failure = None
        with _validation_clone(root, connection) as clone:
            try:
                graph.ingest(clone, clone / plan["packets"][0]["path"], evidence_validator=evidence_validator)
            except GraphError as error:
                _require(error.code in {"invalid_packet", "scope_mismatch"} and not error.retryable,
                         "pending_dependency", "This failure remains resumable and cannot be rejected", validation_error=error.response("reject-reader-return")["error"])
                failure = error.response("reject-reader-return")["error"]
        _require(failure is not None, "invalid_packet", "Return now validates; synchronize it instead of rejecting it")
        stamp = graph._response(connection, "reject-reader-return")
        record = dict(version=1, kind="return", original_id=return_id, original_sha256=data["hash"],
                      plan_sha256=_hash(root, relative), packets=plan["packets"], dependencies=plan["dependencies"],
                      accepted_batches=[], run_id=_run(connection), validation_sequence=stamp["graph_sequence"],
                      reason=reason, error=failure, error_sha256=_sha(_bytes(failure)))
        record["record_sha256"] = _sha(_bytes(record))
        path = _path(root, str(Path(relative).parent / "rejection.json"))
        payload = _bytes(record)
        _save(path, payload)
        return dict(stamp, state="rejected", idempotent=False, rejection=_rejection(root, connection, return_id))


def reject_reader_return(root, return_id, *, expected_original_sha256, reason):
    """Explicitly retire only a proven-invalid, wholly unaccepted atomic return."""
    return _safe(_reject_reader_return, root, return_id, expected_original_sha256, reason)


def _dependencies(root, connection, kind, key, data):
    operation = data if kind == "operation" else data["operation"]
    base = "operations/" + operation["spec"]["id"] + "/"
    paths = [base + name for name in ("request.json", "receipt.json", "stdout", "stderr", "result.json")]
    for capture in _caps(operation):
        paths.append(capture.get("relative_path", base + "stdout"))
    if operation.get("index_omission"):
        paths.append(operation["index_omission"]["relative_path"])
    if kind == "return":
        paths.append("reader-returns/" + key + ".json")
        groups = _return_groups(root, connection, data, enforce_intent=False)
        capture_ids = {mutation["record"]["capture_id"] for group in groups for mutation in group if "capture_id" in mutation["record"]}
        for cid in capture_ids:
            captured = _row(connection, "captures", "capture_id", cid)
            _retained_capture(root, connection, captured)
            base = "operations/" + captured["operation_id"] + "/"
            paths.extend(base + name for name in ("request.json", "receipt.json", "stdout", "stderr", "result.json"))
            if _path(root, _omission_path(captured["operation_id"])).exists():
                paths.append(_omission_path(captured["operation_id"]))
            paths.append(captured["relative_path"])
    return {path: _hash(root, path) for path in dict.fromkeys(paths)}


def _packets(connection, kind, key, groups, submitted_at, contract_sha256, *, max_mutations=None, max_packet_bytes=None):
    max_mutations = MAX_MUTATIONS if max_mutations is None else max_mutations
    max_packet_bytes = MAX_PACKET_BYTES if max_packet_bytes is None else max_packet_bytes
    coordinator = connection.execute("SELECT actor_id FROM actors WHERE role='coordinator' AND parent_actor_id IS NULL").fetchone()[0]
    chunks, chunk, size = [], [], 0
    envelope = dict(schema_version=1, actor_id=coordinator, assignment_id=None, packet_kind="observe",
                    submitted_at=submitted_at, contract_sha256=contract_sha256)
    for group in groups:
        group_size = len(_bytes(group))
        _require(len(group) <= max_mutations and group_size < max_packet_bytes - 2048,
                 "invalid_packet", "Atomic observation exceeds packet limits; split the original return by scope")
        if chunk and (len(chunk) + len(group) > max_mutations or size + group_size > max_packet_bytes - 2048):
            chunks.append(chunk)
            chunk, size = [], 0
        chunk.extend(group)
        size += group_size
    if chunk:
        chunks.append(chunk)
    return [dict(envelope, batch_id=_id("normalize", kind, key, index), mutations=chunk) for index, chunk in enumerate(chunks)]


def _plan_status(root, connection, plan):
    accepted, pending = [], []
    for packet in plan["packets"]:
        retained = _path(root, packet["path"])
        _require(retained.is_file() and _sha(retained.read_bytes()) == packet["sha256"],
                 "integrity_error", "Normalization packet is missing or changed")
        existing = _row(connection, "ingest_batches", "batch_id", packet["batch_id"])
        if existing:
            _require(existing["payload_sha256"] == packet["sha256"], "batch_conflict", "Accepted normalization differs from retained bytes")
            accepted.append(packet["batch_id"])
        else:
            pending.append(packet["batch_id"])
    result = dict(kind=plan["kind"], original_id=plan["original_id"], original_sha256=plan["original_sha256"],
                state="pending" if pending else "complete", accepted_batches=accepted, pending_batches=pending,
                limitations=plan.get("limitations", []))
    if plan["kind"] == "return":
        rejected = _rejection(root, connection, plan["original_id"])
        if rejected:
            original, _ = _read(root, plan["original_path"])
            result.update(state="rejected", rejection=rejected,
                          unaccepted_inventory={"graph": original["return"].get("graph", {}), "leads": original["return"].get("leads", [])})
    return result


def _normalize(root, kind, key):
    root = Path(root).resolve()
    graph._id(key, filename=True)
    with graph.writer(root):
        relative = _normalization_path(kind, key)
        plan_path = _path(root, relative)
        reservation_path = _path(root, str(Path(relative).parent / "reservation.json"))
        original_path = "operations/" + key + "/result.json" if kind == "operation" else "reader-returns/" + key + ".json"
        original_hash = _hash(root, original_path)
        with graph.snapshot(root) as connection:
            if plan_path.exists():
                plan = _read_plan(root, relative)
                _require(kind != "return" or _rejection(root, connection, key) is None,
                         "scope_mismatch", "Rejected return is retired; submit an explicitly linked correction", return_id=key)
                _require(plan["original_sha256"] == original_hash, "batch_conflict", "Original payload changed after normalization planning")
                data = _operation(root, key, connection) if kind == "operation" else _returned(root, key, connection)
                _require(_dependencies(root, connection, kind, key, data) == plan["dependencies"],
                         "integrity_error", "Original normalization dependencies changed")
                _plan_status(root, connection, plan)
            else:
                # An interrupted normalization owns its pending identity inserts.
                # Finish it before another normalizer plans overlapping identities.
                reservation = _read_reservation(root, relative) if reservation_path.exists() else None
                owns_partial = reservation is not None or any(plan_path.parent.glob("packet-*.json"))
                if not owns_partial:
                    for directory in sorted((root / "graph/normalizations").glob("*")):
                        directory = _path(root, str(directory.relative_to(root)))
                        other = directory / "plan.json"
                        if other.exists():
                            pending = _read_plan(root, str(other.relative_to(root)))
                            state = _plan_status(root, connection, pending)
                            _require(state["state"] in {"complete", "rejected"}, "pending_dependency", "Resume the earlier incomplete normalization first", original_id=pending["original_id"])
                        else:
                            _require(not (directory / "reservation.json").exists() and not any(directory.glob("packet-*.json")),
                                     "pending_dependency", "Resume the earlier staged normalization first", normalization=str(directory.relative_to(root)))
                if kind == "operation":
                    data = _operation(root, key, connection)
                    groups = _operation_groups(root, connection, data)
                    timestamp = data["result"]["indexed"]
                    limitations = [] if data["native"] else [LEGACY]
                else:
                    data = _returned(root, key, connection)
                    groups = _return_groups(root, connection, data)
                    timestamp = datetime.fromtimestamp(data["event"]["at"], timezone.utc).isoformat()
                    limitations = [ATTESTATION] if data["native"] else [LEGACY]
                contract, contract_hash = _read(root, "contract.json")
                _require(_row(connection, "contract_snapshots", "contract_sha256", contract_hash) is not None,
                         "pending_dependency", "Current recorder contract needs a graph binding before normalization")
                limits = reservation["limits"] if reservation else dict(max_mutations=MAX_MUTATIONS, max_packet_bytes=MAX_PACKET_BYTES)
                packets = _packets(connection, kind, key, groups, timestamp, contract_hash, **limits)
                for packet in packets:
                    graph._normal_mutations(packet)
                plan = dict(version=1, kind=kind, original_id=key, original_path=original_path,
                            original_sha256=original_hash, dependencies=_dependencies(root, connection, kind, key, data),
                            limitations=limitations, packets=[])
                payloads = []
                for index, packet in enumerate(packets):
                    payload = _bytes(packet)
                    path = str(Path(relative).parent / ("packet-%05d.json" % index))
                    plan["packets"].append(dict(path=path, sha256=_sha(payload), batch_id=packet["batch_id"]))
                    payloads.append((path, payload))
                # Reserve the complete packet identities and original chunking
                # before the first packet can survive an interrupted install.
                # A retry must reproduce this exact plan before writing more.
                _save(reservation_path, _bytes(dict(version=1, plan=plan, limits=limits)))
                for path, payload in payloads:
                    _save(_path(root, path), payload)
                plan_bytes = _bytes(plan)
                _save(plan_path.with_suffix(".sha256"), (_sha(plan_bytes) + "\n").encode("ascii"))
                _save(plan_path, plan_bytes)
        receipts = [graph.ingest(root, _path(root, entry["path"]), evidence_validator=evidence_validator) for entry in plan["packets"]]
        with graph.snapshot(root) as connection:
            return graph._response(connection, "index-operation" if kind == "operation" else "ingest-reader-return",
                                   **_plan_status(root, connection, plan),
                                   idempotent=all(r["idempotent"] for r in receipts), batches=receipts)


def index_operation(root, operation_id):
    """Index every original result occurrence, capture and truthful execution mirror."""
    return _safe(_normalize, root, "operation", operation_id)


def _ingest_reader_return(root, return_id):
    """Reconcile a complete original file/event pair; leave missing dependencies pending."""
    root = Path(root)
    original, _ = _read(root, "reader-returns/" + graph._id(return_id, filename=True) + ".json")
    # The read index is an independent original and can be safely resumed first.
    index_operation(root, original["return"]["read_id"])
    return _normalize(root, "return", return_id)


def ingest_reader_return(root, return_id):
    """Normalize the original file/event pair, preserving missing dependencies."""
    return _safe(_ingest_reader_return, root, return_id)


def _return_corrections(root, connection=None):
    root = Path(root).resolve()
    mappings = {}
    with (graph.snapshot(root) if connection is None else nullcontext(connection)) as conn:
        for row in conn.execute("SELECT return_id,file_path,payload_sha256 FROM reader_returns ORDER BY return_id"):
            payload, _ = _read(root, row["file_path"])
            if payload.get("return", {}).get("corrects") is None:
                continue
            data = _returned(root, row["return_id"], conn)
            _require(data["hash"] == row["payload_sha256"], "integrity_error", "Accepted return bytes changed")
            current, chain = data["returned"], [row["return_id"]]
            while current.get("corrects") is not None:
                binding = budget.correction_binding(root, current)
                key = binding["return_id"]
                _require(key not in chain and len(chain) < 32, "integrity_error", "Invalid correction chain")
                rejection = _rejection(root, conn, key)
                _require(rejection is not None and key not in mappings, "integrity_error", "Correction has no unique rejected original")
                chain.insert(0, key)
                original = binding["original"]
                mappings[key] = dict(return_id=key, original_sha256=binding["original_sha256"],
                    correction_return_id=row["return_id"], correction_sha256=data["hash"], chain=list(chain),
                    read_id=current["read_id"], source_id=original["source_id"], receipt_sha256=original["receipt_sha256"],
                    actor_id=current["actor"], assignment_id=current.get("graph_assignment_id"),
                    rejection_sha256=rejection["sha256"], accepted_batch_id=_row(conn, "reader_returns", "return_id", row["return_id"])["created_batch_id"])
                current = original["return"]
    return mappings


def return_corrections(root, *, connection=None):
    """Verified rejected-original to accepted-correction bindings for a snapshot."""
    return _safe(_return_corrections, root, connection)


def _cancelled_admission(root, connection, key):
    """A checked request-only cancellation is accounted for, never executed work."""
    operation = _row(connection, "operation_links", "operation_id", key)
    if not operation or operation["execution_state"] != "cancelled" or operation["receipt_sha256"] is not None:
        return None
    work = _sibling("research_graph_work")
    original = work._admission(root, key)
    _require(all(operation.get(k) == v for k, v in work._operation_row(original, "cancelled").items()),
             "integrity_error", "Cancelled operation differs from its original admission")
    _require({p.name for p in (root / "operations" / key).iterdir()} == {"request.json"},
             "pending_dependency", "Cancelled admission has other retained evidence to reconcile")
    assignment = _row(connection, "assignments", "assignment_id", operation["assignment_id"])
    _require(work._historical_worker_stopped(connection, original, assignment["work_id"]),
             "pending_dependency", "Cancelled admission needs retained original-worker stop reconciliation")
    return dict(kind="operation", original_id=key, state="cancelled", original_sha256=original["request_hash"],
                limitations=["Request and checked worker cancellation only; no provider result, inspection or executed follow-up credit."])


def _ingestion_status(root, connection=None, status_version=2):
    """Read-only inventory of unreflected originals and incomplete recorder writes."""
    root = Path(root).resolve()
    _require(status_version in {1, 2}, "unsupported_schema", "Unsupported original synchronization status version")
    items, receipts = [], {}
    with (graph.snapshot(root) if connection is None else nullcontext(connection)) as connection:
        corrections = _return_corrections(root, connection)
        originals = {( "operation", p.parent.name): str(p.relative_to(root)) for p in (root / "operations").glob("*/result.json")}
        originals.update({("return", p.stem): str(p.relative_to(root)) for p in (root / "reader-returns").glob("*.json")})
        if (root / "research-budget.json").exists():
            for event in _journal(root):
                if event["type"] == "reader-return":
                    originals.setdefault(("return", event["return_id"]), "reader-returns/" + event["return_id"] + ".json")
                elif event["type"] == "admit":
                    originals.setdefault(("operation", event["operation"]), "operations/" + event["operation"] + "/result.json")
        for (kind, key), path in sorted(originals.items()):
            try:
                cancelled = _cancelled_admission(root, connection, key) if kind == "operation" and status_version == 2 else None
                if cancelled is not None:
                    items.append(cancelled)
                    continue
                data = _safe(_operation if kind == "operation" else _returned, root, key, connection)
                digest = data["result_hash"] if kind == "operation" else data["hash"]
                relative = _normalization_path(kind, key)
                if _path(root, relative).exists():
                    plan = _read_plan(root, relative)
                    _require(digest == plan["original_sha256"], "batch_conflict", "Original payload changed after normalization")
                    _require(_dependencies(root, connection, kind, key, data) == plan["dependencies"],
                             "integrity_error", "Original normalization dependencies changed")
                    item = _plan_status(root, connection, plan)
                    if item["state"] == "rejected" and key in corrections:
                        item.update(state="corrected", correction=corrections[key])
                else:
                    directory = _path(root, relative).parent
                    staged = (directory / "reservation.json").exists() or any(directory.glob("packet-*.json"))
                    item = dict(kind=kind, original_id=key, state="pending" if staged else "unindexed", original_sha256=digest)
                    if staged:
                        item["normalization_staging"] = "Original packet installation must resume before another normalization"
                operation = data if kind == "operation" else data["operation"]
                if operation.get("index_omission"):
                    omission = operation["index_omission"]
                    receipts[omission["relative_path"]] = omission["sha256"]
                    item["index_omission"] = omission
            except GraphError as error:
                item = dict(kind=kind, original_id=key, state="pending", error=error.response("ingestion-status")["error"])
            items.append(item)
        for plan_path in sorted((root / "graph/normalizations").glob("*/plan.json")):
            relative = str(plan_path.relative_to(root))
            try:
                plan = _read_plan(root, relative)
                files = [relative, str(Path(relative).with_suffix(".sha256"))] + [p["path"] for p in plan["packets"]]
                reservation = str(Path(relative).parent / "reservation.json")
                if _path(root, reservation).exists():
                    files.append(reservation)
                for packet in plan["packets"]:
                    update = "graph/updates/" + packet["batch_id"] + ".json"
                    if _path(root, update).exists():
                        _require(_hash(root, update) == packet["sha256"], "integrity_error", "Retained update differs from normalization packet")
                        files.append(update)
                if plan["kind"] == "return":
                    rejected = _rejection(root, connection, plan["original_id"])
                    if rejected:
                        files.append(rejected["relative_path"])
                receipts.update({p: _hash(root, p) for p in files})
            except GraphError:
                # The corresponding original item already reports this failure;
                # no unverified receipt is advertised for an export.
                continue
        return graph._response(connection, "ingestion-status", items=items, return_corrections=corrections,
                               immutable_receipts=receipts,
                               complete=all(item["state"] in {"complete", "corrected", "cancelled"} for item in items),
                               **({"status_version": status_version} if status_version == 2 else {}))


def ingestion_status(root, *, connection=None, status_version=2):
    """Read-only status for originals, including incomplete recorder writes."""
    return _safe(_ingestion_status, root, connection, status_version)
