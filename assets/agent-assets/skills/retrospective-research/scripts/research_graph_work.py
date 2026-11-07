#!/usr/bin/env python3
"""Scoped graph coordination around the original immutable plan/budget recorder.

No providers or budget reservations run here. Caller order is prepare_operation,
original recorder admission, record_admission, record_dispatch, provider call,
original retained receipt, record_completion, G2 indexing/reader ingestion, then
complete_work or bound_work. A failed mirror never refunds the admission.
Imported timeout/interruption receipts retain unknown worker state until an
explicit operation-bound stop check. The owned subprocess recorder kills and
waits before recording its timeout/interruption receipts. Raw graph lifecycle
mutations use these same journal-aware checks, including older assignments.

Saved plan items may include a graph annotation: questions, branch, stage,
purpose, extent, formats, scope_key, format_policy_sha256, version_id, capture_ids,
freshness_basis, parent_operation. New read plans require an explicit normalized
extent. Already registered historical opaque requests retain their original scope.
Supported read bindings are captured_after_assignment, an exact version_id,
or explicit native retained capture_ids requiring a new current inspection.
Actor identities, scope and reconciliation statements are workflow attestations.
"""

from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys


def _sibling(name):
    path = Path(__file__).with_name(name + ".py")
    module = sys.modules.get(name)
    if module is None or Path(module.__file__).resolve() != path.resolve():
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return module


graph = _sibling("research_graph")
ingestion = _sibling("research_graph_ingest")
bundle = _sibling("research_bundle")
budget = bundle.budget
GraphError = graph.GraphError
_ACTIVE = {"leased", "running", "reconciling"}
_UNRESOLVED = {"reserved", "admitted", "dispatched", "interrupted", "unknown"}
_GRAPH_KEYS = {"questions", "branch", "stage", "purpose", "extent", "formats", "scope_key", "format_policy_sha256", "version_id", "freshness_basis", "capture_ids", "parent_operation", "lead_id", "reference_id", "allocation", "activity_purpose"}


def enabled(root):
    """Harmless legacy-bundle detection; does not create files or acquire locks."""
    return (Path(root) / "graph" / "research.sqlite").is_file()


def operation_id(assignment_id, kind, provider, request, retry_of=None):
    """Stable optional operation identity; retries retain their original predecessor."""
    return _identifier("operation", assignment_id, kind, provider, request, retry_of)


def _require(condition, message, code="invalid_packet", **details):
    if not condition:
        raise GraphError(code, message, **details)


def _revision(value):
    _require(type(value) is int and value >= 1, "Expected revision must be a positive integer")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _identifier(prefix, *parts):
    return prefix + "-" + _hash(parts)[:32]


def _read(root, relative):
    path = graph._path(Path(root).resolve(), relative)
    data = path.read_bytes()
    try:
        return json.loads(data), hashlib.sha256(data).hexdigest()
    except (ValueError, UnicodeError):
        raise GraphError("integrity_error", "Retained recorder JSON is invalid") from None


def _row(conn, table, identity):
    return graph._get(conn, table, identity)


def _insert(table, **record):
    return {"op": "insert", "table": table, "record": record}


def _update(table, row, **changes):
    return {"op": "update", "table": table, "id": row[graph._pk(table)[0]], "expected_revision": row["revision"], "changes": changes}


def _coordinator(conn):
    return conn.execute("SELECT actor_id FROM actors WHERE role='coordinator'").fetchone()[0]


def _packet(root, conn, mutations, actor=None, assignment=None, key=None):
    actor = actor or _coordinator(conn)
    contract_hash = _read(root, "bundle.json")[0]["contract_sha256"]
    if _row(conn, "actors", actor)["role"] != "coordinator" and assignment:
        binding = _row(conn, "assignments", assignment)
        contract_hash = _row(conn, "plan_snapshots", binding["plan_sha256"])["contract_sha256"]
    body = {"schema_version": 1, "actor_id": actor, "assignment_id": assignment, "packet_kind": "reconcile",
            "contract_sha256": contract_hash, "mutations": mutations}
    identity = key or _identifier("work", body)
    if (Path(root) / "graph" / "updates" / (identity + ".json")).exists():
        retained, _ = _read(root, "graph/updates/" + identity + ".json")
        _require(all(retained.get(k) == v for k, v in body.items()), "Retained work packet differs", "batch_conflict")
        return retained
    return {**body, "batch_id": identity, "submitted_at": _now()}


def _response(conn, command, **fields):
    return {"status": "pass", "schema_version": 1, "command": command,
            "run_id": conn.execute("SELECT run_id FROM graph_metadata").fetchone()[0],
            "graph_sequence": conn.execute("SELECT MAX(sequence) FROM graph_events").fetchone()[0], **fields}


def _ingest(root, packet):
    return graph.ingest(root, packet, evidence_validator=evidence_validator)


def _events(root):
    return ingestion._journal(root)


def _artifact(conn, source_id):
    run_id = conn.execute("SELECT run_id FROM graph_metadata").fetchone()[0]
    row = conn.execute("SELECT c.canonical_id FROM artifact_keys k JOIN canonical_artifacts c USING(artifact_id) WHERE k.namespace=? AND k.external_id=?",
                       ("bundle-source:" + run_id, source_id)).fetchone()
    return row[0] if row else None


def _canonical(conn, artifact_id):
    row = conn.execute("SELECT canonical_id FROM canonical_artifacts WHERE artifact_id=?", (artifact_id,)).fetchone()
    return row[0] if row else None


def _equivalent_key(work, canonical_id):
    """Compare current canonical intent without rewriting historical bindings."""
    normalized = dict(work)
    request = dict(work["request_json"])
    if work["kind"] in {"read", "inspect_visuals"}:
        for name in ("provider", "route", "source_id"):
            request.pop(name, None)
        request["formats"] = sorted(set(request.pop("formats", [request.get("format")])) - {None})
        request.pop("format", None)
        metadata = dict(request.get("provider_metadata", {}))
        original = dict(metadata.pop("request", {}))
        for name in ("provider", "route", "source_id", "format", "extent"):
            original.pop(name, None)
        for name in ("provider", "route", "source_id"):
            metadata.pop(name, None)
        if original:
            metadata["request"] = original
        if metadata:
            request["provider_metadata"] = metadata
        else:
            request.pop("provider_metadata", None)
    normalized["request_json"] = request
    return graph.logical_key(normalized, canonical_id)


def _work_index(conn):
    matches = {}
    for row in conn.execute("SELECT * FROM work_items ORDER BY work_id"):
        work = graph._decoded(row)
        matches.setdefault(_equivalent_key(work, _canonical(conn, work["artifact_id"])), work["work_id"])
    return matches


def _plan(root, key):
    try:
        return bundle.plan_snapshot(Path(root), key)
    except (ValueError, KeyError, OSError, TypeError):
        raise GraphError("integrity_error", "Saved plan or its original ledger/contract binding is unavailable") from None


def _admission(root, operation_id, require_receipt=False):
    graph._id(operation_id, filename=True)
    events = [e for e in _events(root) if e["type"] == "admit" and e["operation"] == operation_id]
    _require(len(events) == 1, "Original budget admission is missing", "pending_dependency", operation_id=operation_id)
    request, request_hash = _read(root, "operations/" + operation_id + "/request.json")
    spec, event = request["spec"], events[0]
    if "request_intent" in event:
        _require(request == budget.admission_request(event),
                 "Original request differs from retained admission intent", "integrity_error")
    _require(spec["id"] == operation_id and spec.get("research_admission") == {"event": event["id"], "sha256": event["sha256"]}
             and event["fingerprint"] == budget.sha({k: spec[k] for k in ("kind", "provider", "request")})
             and event.get("source_id") == spec["request"].get("source_id")
             and all(spec.get(k) == event.get(k) for k in ("kind", "actor", "timeout"))
             and all(spec.get("research", {}).get(k) == event.get(k) for k in ("stage", "branch", "parent_operation")),
             "Original operation and admission disagree", "integrity_error", operation_id=operation_id)
    result = {"request": request, "request_hash": request_hash, "spec": spec, "event": event}
    if require_receipt:
        receipt, receipt_hash = _read(root, "operations/" + operation_id + "/receipt.json")
        _require(isinstance(receipt, dict) and receipt.get("outcome") in {"ok", "error", "launch-error", "timeout", "interrupted"}
                 and isinstance(receipt.get("files"), dict), "Retained receipt has invalid outcome or file bindings", "integrity_error")
        _require(receipt.get("files", {}).get("request.json") == request_hash, "Receipt request hash differs", "integrity_error")
        for name in ("stdout", "stderr"):
            path = graph._path(Path(root).resolve(), "operations/" + operation_id + "/" + name)
            _require(hashlib.sha256(path.read_bytes()).hexdigest() == receipt.get("files", {}).get(name), "Receipt output bytes differ", "integrity_error")
        _require(graph._timestamp(receipt["finished"]) >= graph._timestamp(request["started"]), "Receipt predates operation", "integrity_error")
        result.update(receipt=receipt, receipt_hash=receipt_hash)
    return result


def _generation(root, spec, *, check_retry=True, origin=None):
    info, events = spec["research"], _events(root)
    attempts = [e for e in events if e["type"] == "admit"]
    try:
        budget.validate_source_origin(root, spec, attempts, origin)
    except ValueError as error:
        raise GraphError("scope_mismatch", str(error)) from None
    fingerprint = budget.sha({k: spec[k] for k in ("kind", "provider", "request")})
    previous = [e for e in attempts if e["fingerprint"] == fingerprint]
    # A refresh keeps its ancestry, but does not waive the recorder's root
    # requirements. Check these before the same-request generation shortcut.
    if info.get("parent_operation") is None and check_retry:
        _require(info.get("stage") == "baseline" and bool(info.get("root_basis")),
                 "New root needs baseline coverage basis", "scope_mismatch")
    if previous and check_retry:
        _require(info.get("parent_operation") == previous[-1]["parent_operation"], "Retry/refresh cannot reset ancestry", "scope_mismatch")
        return previous[-1]["generation"]
    parent = info.get("parent_operation")
    if parent is None:
        _require(info.get("stage") == "baseline" and bool(info.get("root_basis")), "New root needs baseline coverage basis", "scope_mismatch")
        return 0
    original = _admission(root, parent, require_receipt=True)
    result, _ = _read(root, "operations/" + parent + "/result.json")
    _require(original["receipt"]["outcome"] == "ok" and result.get("receipt_sha256") == original["receipt_hash"], "Parent operation needs successful indexed evidence", "pending_dependency")
    old, event = original["spec"], original["event"]
    same_source = old["kind"] == spec["kind"] == "read" and old["request"]["source_id"] == spec["request"]["source_id"]
    generation = event["generation"] + int(old["kind"] == "read" and not same_source)
    if old["kind"] == "search" and spec["kind"] == "search":
        pagination = budget.is_pagination(old, spec, result)
        generation += int(not pagination)
    return generation


def _plan_work(root, conn, plan, item, question, artifacts=None, *, allow_opaque=False, check_retry=True, contract=None):
    _require(question in graph.plan_item_questions(plan, item),
             "Work question exceeds saved plan coverage", "scope_mismatch")
    annotations = item.get("graph", {})
    prefix = "items[" + item["id"] + "].graph"
    _require(isinstance(annotations, dict), "Saved-plan graph annotation must be an object", field=prefix)
    unknown = sorted(set(annotations) - _GRAPH_KEYS)
    _require(not unknown, "Unknown saved-plan graph annotation", field=prefix + "." + unknown[0] if unknown else prefix)
    for field in ("branch", "purpose", "scope_key"):
        if field in annotations:
            _require(isinstance(annotations[field], str) and annotations[field].strip(), "Graph annotation must be nonempty text", field=prefix + "." + field)
    if "stage" in annotations:
        _require(annotations["stage"] in {"baseline", "priority", "deep"}, "Unknown graph research stage", field=prefix + ".stage")
    if "freshness_basis" in annotations:
        _require(annotations["freshness_basis"] == "captured_after_assignment", "Unsupported graph freshness policy", field=prefix + ".freshness_basis")
    if "capture_ids" in annotations:
        graph.normalize_capture_ids(annotations["capture_ids"])
        _require(item["kind"] == "read" and not {"version_id", "freshness_basis"} & set(annotations),
                 "Retained captures require read work and an exclusive capture policy", field=prefix + ".capture_ids")
    if "format_policy_sha256" in annotations:
        _require(isinstance(annotations["format_policy_sha256"], str) and graph.HASH.fullmatch(annotations["format_policy_sha256"]),
                 "Graph format policy must be a SHA-256 identity", field=prefix + ".format_policy_sha256")
    for field in ("version_id", "parent_operation", "lead_id", "reference_id"):
        if field in annotations:
            try:
                graph._id(annotations[field], filename=field == "parent_operation")
            except GraphError:
                raise GraphError("invalid_packet", "Invalid graph annotation identity", field=prefix + "." + field) from None
    if "formats" in annotations:
        _require(isinstance(annotations["formats"], list) and annotations["formats"] and all(isinstance(fmt, str) and fmt.strip() for fmt in annotations["formats"]),
                 "Requested formats must be a nonempty string list", field=prefix + ".formats")
    provider_request = item["request"]
    parent = annotations.get("parent_operation", item.get("origin", {}).get("read_id"))
    info = {"stage": annotations.get("stage", "baseline" if parent is None else "priority"),
            "branch": annotations.get("branch", item.get("search_branch", item["provider"])), "parent_operation": parent,
            "root_basis": item["reason"]}
    spec = {"kind": item["kind"], "provider": item["provider"], "request": provider_request, "research": info,
            **{field: item[field] for field in ("search_branch", "input_kind") if field in item}}
    intent = {}
    artifact = None
    historical_empty = False
    if item["kind"] == "read":
        _require("extent" in annotations or allow_opaque,
                 "Read plan needs explicit graph.extent (numeric bounds or a whole-component all request)",
                 field=prefix + ".extent")
        if "extent" in annotations:
            try:
                graph._extent(annotations["extent"])
            except GraphError as error:
                raise GraphError(error.code, str(error), field=prefix + ".extent") from None
            historical_empty = (item["status"] in {"bounded", "dropped"}
                                and not graph.meaningful_read_extent(annotations["extent"]))
            _require(graph.meaningful_read_extent(annotations["extent"], allow_opaque=allow_opaque) or historical_empty,
                     "Read scope needs nonempty positions or all in every required component", field=prefix + ".extent")
            _require(not any(isinstance(value, dict) and "complete" in value for value in annotations["extent"].values()),
                     "Completeness annotates captured components; request all or explicit numeric bounds", field=prefix + ".extent")
        artifact = (artifacts or {}).get(provider_request["source_id"]) or _artifact(conn, provider_request["source_id"])
        intent.update(source_id=provider_request["source_id"], formats=sorted(set(annotations.get("formats", [provider_request["format"]]))),
                      extent=annotations.get("extent", {"body": {"attestation": provider_request["extent"], "limitations": "Opaque saved request; reader must attest matching scope"}}))
        extra_request = {k: v for k, v in provider_request.items() if k not in {"source_id", "format", "extent", "provider", "route"}}
        if extra_request:
            intent["provider_metadata"] = {"request": extra_request}
        if "capture_ids" in annotations:
            intent["capture_ids"] = graph.normalize_capture_ids(annotations["capture_ids"])
        elif "version_id" in annotations:
            intent["version_id"] = annotations["version_id"]
        else:
            intent["freshness_basis"] = annotations.get("freshness_basis", "captured_after_assignment")
    else:
        intent.update(provider=item["provider"], query=provider_request["query"], filters=provider_request["filters"], cursor=provider_request["cursor"],
                      provider_metadata={"cap": provider_request["cap"]})
        extra_request = {k: v for k, v in provider_request.items() if k not in {"query", "filters", "cursor", "cap"}}
        if extra_request:
            intent["provider_metadata"]["request"] = extra_request
        for field in ("search_branch", "input_kind"):
            if field in item:
                intent["provider_metadata"][field] = item[field]
        if "search_branch" in item and plan.get("search_routes"):
            route = bundle.search_route_for_item(plan, item)
            intent["provider_metadata"]["search_route"] = {k: route.get(k) for k in ("id", "provider", "invocation")}
    actor = _row(conn, "actors", item["actor"])
    original_contract = contract or bundle.read(Path(root) / "contracts" / (plan["_record"]["contract_sha256"] + ".json"))
    allocation_policy = budget.allocations.config(original_contract)
    if allocation_policy:
        budget.allocations.activity_fields(allocation_policy, annotations.get("allocation"),
                                           annotations.get("activity_purpose", "research"), info["branch"])
        if annotations.get("activity_purpose") == "coverage-review":
            _require(actor and actor["role"] == "coverage_reviewer",
                     "Coverage-review allocation requires a registered coverage reviewer", "permission_denied")
        intent.setdefault("provider_metadata", {}).update(
            research_allocation=annotations["allocation"], activity_purpose=annotations.get("activity_purpose", "research"))
    purpose = {"evidence_reviewer": "Independent evidence review", "coverage_reviewer": "Independent coverage review"}.get(actor["role"] if actor else None,
               "Inspect source evidence" if item["kind"] == "read" else "Discover relevant sources")
    work = {"kind": item["kind"], "artifact_id": artifact, "question_id": question, "request_json": intent,
            "required_scope_key": annotations.get("scope_key", "scope-" + _hash(intent["extent"])[:32]) if artifact else None,
            "format_policy_sha256": annotations.get("format_policy_sha256", _hash({"formats": intent["formats"], "extent_policy": "explicit_requested_extent"})) if artifact else None,
            "purpose": annotations.get("purpose", purpose),
            "stage": info["stage"], "branch": info["branch"],
            "priority": {"high": 90, "normal": 50, "low": 10}[item["priority"]], "transition_reason": "Materialized immutable saved plan item"}
    work["logical_key"] = graph.logical_key(work, artifact)
    work["work_id"] = _identifier("work", work["logical_key"])
    if historical_empty:
        previous = _row(conn, "work_items", _work_index(conn).get(work["logical_key"]))
        _require(previous and previous["state"] in {"pending", "blocked", "deferred", "cancelled"},
                 "Closed empty scope must retain exact already released historical work", "scope_mismatch")
    if "capture_ids" in intent:
        ingestion.validate_capture_bindings(root, conn, work)
    if not check_retry and parent is None and info["stage"] != "baseline":
        # Older projections accepted a non-baseline root after a matching
        # request had already been admitted. Retain only that exact materialized
        # intent for historical replay/closure; never admit new work through it.
        existing = conn.execute(
            "SELECT w.* FROM work_items w JOIN work_origins o ON o.work_id=w.work_id "
            "WHERE w.logical_key=? AND w.stage=? AND w.branch=? AND w.generation=0 "
            "AND o.parent_operation_id IS NULL AND o.root_basis IS NOT NULL "
            "AND o.originating_generation=0 LIMIT 1",
            (work["logical_key"], info["stage"], info["branch"])).fetchone()
        _require(existing is not None, "Non-baseline root has no matching historical intent", "scope_mismatch")
        generation = 0
    else:
        generation = _generation(root, spec, check_retry=check_retry, origin=item.get("origin"))
    if "stage" not in annotations:
        work["stage"] = "baseline" if generation == 0 else "priority" if generation == 1 else "deep"
    if check_retry:
        _require(work["stage"] != "baseline" or generation == 0,
                 "Follow-up cannot be labelled baseline", "scope_mismatch")
        _require(work["stage"] != "priority" or generation <= 1,
                 "Deeper generation must use deep stage", "scope_mismatch")
    work["generation"] = generation
    if allocation_policy:
        budget.allocations.lineage(allocation_policy, annotations["allocation"],
                                   annotations.get("activity_purpose", "research"), generation)
    return work, parent


def _same_intent(conn, left, right):
    return (_equivalent_key(left, _canonical(conn, left["artifact_id"]))
            == _equivalent_key(right, _canonical(conn, right["artifact_id"]))
            and all(left[field] == right[field] for field in ("generation", "stage", "branch")))


def _legacy_search_work(conn, plan_key, item, intended):
    """Bind an old projection to its exact saved item, never a new dispatch."""
    if intended["kind"] != "search":
        return None
    request = dict(intended["request_json"], provider_metadata={"cap": item["request"]["cap"]})
    if request == intended["request_json"]:
        return None
    origin = _row(conn, "work_origins", _identifier("origin", plan_key, item["id"], intended["question_id"]))
    work = _row(conn, "work_items", origin["work_id"]) if origin else None
    if not work or work["request_json"] != request or not _same_intent(conn, dict(intended, request_json=request), work):
        return None
    event = conn.execute("SELECT payload_json FROM graph_events WHERE batch_id=? AND entity_kind='work_items' AND entity_id=?",
                         (work["created_batch_id"], graph._json(work["work_id"]))).fetchone()
    original = json.loads(event[0]) if event else {}
    if (original.get("before") is not None or not original.get("after")
            or original["after"]["request_json"] != request or not _same_intent(conn, original["after"], work)):
        return None
    return work


def _match_plan_work(root, conn, work, plan, item, *, check_retry=False, historical_plan_key=None):
    """One canonical intent check for claims, execution and evidence credit."""
    intended, _ = _plan_work(root, conn, plan, item, work["question_id"],
                             allow_opaque=True, check_retry=check_retry)
    legacy = _legacy_search_work(conn, historical_plan_key, item, intended) if historical_plan_key else None
    _require(_same_intent(conn, intended, work)
             or legacy is not None and legacy["work_id"] == work["work_id"] and _same_intent(conn, legacy, work),
             "Work differs from its saved plan intent", "scope_mismatch")


def _capture_policy_correction(before, after):
    """Only an exact fresh-read to retained-capture completion can be corrected."""
    if (before["kind"] not in {"read", "inspect_visuals"} or after.get("state") != "completed"
            or before.get("state") not in {"leased", "running"}):
        return False
    old, new = before["request_json"], after["request_json"]
    if (old.get("freshness_basis") != "captured_after_assignment" or "capture_ids" in old
            or "version_id" in old or not new.get("capture_ids") or "freshness_basis" in new or "version_id" in new):
        return False
    if {k: v for k, v in old.items() if k != "freshness_basis"} != {k: v for k, v in new.items() if k != "capture_ids"}:
        return False
    changing = {"request_json", "logical_key", "state", "terminal_reason", "transition_reason", "revision", "updated_batch_id"}
    return {k: v for k, v in before.items() if k not in changing} == {k: v for k, v in after.items() if k not in changing}


def _completed_capture_correction(root, conn, assignment, work, plan, item):
    """Recognize accepted correction history solely for completed read evidence."""
    if work.get("state") != "completed" or assignment.get("state") != "completed":
        return False
    event = conn.execute("""SELECT e.payload_json FROM graph_events e JOIN ingest_batches b USING(batch_id)
        JOIN actors a ON a.actor_id=b.actor_id WHERE e.batch_id=? AND e.entity_kind='work_items'
        AND e.entity_id=? AND b.packet_kind='reconcile' AND a.role='coordinator'""",
        (work["updated_batch_id"], graph._json(work["work_id"]))).fetchone()
    paired = conn.execute("SELECT payload_json FROM graph_events WHERE batch_id=? AND entity_kind='assignments' AND entity_id=?",
                          (work["updated_batch_id"], graph._json(assignment["assignment_id"]))).fetchone()
    if not event or not paired:
        return False
    change, assigned = json.loads(event[0]), json.loads(paired[0])
    if (not change.get("before") or not _capture_policy_correction(change["before"], change["after"])
            or not _same_intent(conn, change["after"], work) or not assigned.get("before")
            or assigned["before"]["state"] not in {"running", "reconciling"}
            or assigned["after"] != assignment):
        return False
    _match_plan_work(root, conn, change["before"], plan, item)
    return True


def validate_assignment_intent(root, conn, assignment, *, work=None, allow_completed_correction=False, allow_legacy_search=False):
    """Strict execution binding; historical read evidence may name a proved correction."""
    work = work if work is not None else _row(conn, "work_items", assignment["work_id"])
    _require(work, "Assignment work is unknown", "unknown_identity")
    plan, _, _ = _plan(root, assignment["plan_sha256"])
    item = next((i for i in plan["items"] if i["id"] == assignment["plan_item_id"]), None)
    _require(item and item["actor"] == assignment["actor_id"] and item["status"] in {"queued", "active"},
             "Saved plan assignment differs", "scope_mismatch")
    try:
        _match_plan_work(root, conn, work, plan, item,
                         historical_plan_key=assignment["plan_sha256"] if allow_legacy_search else None)
    except GraphError as error:
        if (error.code != "scope_mismatch" or not allow_completed_correction
                or not _completed_capture_correction(root, conn, assignment, work, plan, item)):
            raise
    _require(assignment["generation_at_claim"] == work["generation"],
             "Assignment generation differs from saved work", "scope_mismatch")
    return True


def _project_plan(root, conn, plan, ledger, key, contract_hash, *, allow_opaque=False):
    """Shared candidate projection for preflight and immutable registration."""
    mutations, bindings = [], []
    registered = _row(conn, "plan_snapshots", key) is not None
    contract = bundle.read(root / "contracts" / (contract_hash + ".json"))
    binding = _row(conn, "contract_snapshots", contract_hash)
    revision = binding["contract_revision"] if binding else conn.execute("SELECT MAX(contract_revision)+1 FROM contract_snapshots").fetchone()[0]
    if not binding:
        mutations.append(_insert("contract_snapshots", contract_sha256=contract_hash, contract_revision=revision, relative_path="contracts/" + contract_hash + ".json"))
    current = _read(root, "bundle.json")[0]["contract_sha256"] == contract_hash
    for question in contract["questions"]:
        previous = _row(conn, "questions", question["id"])
        if previous is None:
            mutations.append(_insert("questions", question_id=question["id"], text=question["text"], central=int(question["central"]), contract_revision=revision))
        elif current and (previous["text"], previous["central"], previous["contract_revision"]) != (question["text"], int(question["central"]), revision):
            changes = dict(text=question["text"], central=int(question["central"]), contract_revision=revision)
            if (previous["text"], previous["central"]) != (question["text"], int(question["central"])):
                changes.update(current_status="reopened", reason="Original contract amendment changed this question")
            mutations.append(_update("questions", previous, **changes))
    if not _row(conn, "plan_snapshots", key):
        mutations.append(_insert("plan_snapshots", plan_sha256=key, contract_sha256=contract_hash, relative_path="plans/" + key + ".json"))
    run_id = conn.execute("SELECT run_id FROM graph_metadata").fetchone()[0]
    artifacts = {}
    discoveries = {r[0] for r in conn.execute("SELECT discovery_id FROM discoveries")}
    for source in ledger["sources"]:
        artifact = _artifact(conn, source["id"])
        if "_graph" in ledger:
            # Registered exports can name imported/reference-only artifacts
            # before they have a source alias in this run. Bind that local name
            # to the existing graph identity, without inventing provider keys,
            # supplied seeds or current inspections. Later operations use the
            # same alias through the ordinary recorder/ingestion path.
            projected = _canonical(conn, source.get("graph_artifact_id"))
            _require(projected and artifact in {None, projected},
                     "Generated source identity differs from the graph", "scope_mismatch")
            if artifact is None:
                artifact = projected
                mutations.append(_insert("artifact_keys", namespace="bundle-source:" + run_id,
                                         external_id=source["id"], artifact_id=artifact,
                                         basis="Local source name from authenticated generated inventory; existing graph identity"))
        # An authenticated generated ledger only projects existing evidence.
        # This also prevents older seed:graph-* compatibility labels from
        # becoming new supplied-seed discoveries when a saved plan is replayed.
        seeds = [] if "_graph" in ledger else [origin for origin in source["discovered_by"] if origin.startswith("seed:")]
        if artifact is None:
            _require(seeds, "Index original discoveries before registering derived plan work", "pending_dependency", source_id=source["id"])
            artifact = _identifier("artifact", "bundle-source:" + run_id, source["id"])
            mutations += [_insert("artifacts", artifact_id=artifact, title=source["title"], artifact_type="unknown"),
                          _insert("artifact_keys", namespace="bundle-source:" + run_id, external_id=source["id"], artifact_id=artifact,
                                  basis="Original saved-plan ledger supplied seed; provider identity unverified")]
        artifacts[source["id"]] = artifact
        for index, seed in enumerate(seeds):
            try:
                graph._id(seed)
            except GraphError:
                raise GraphError("invalid_packet", "Supplied seed provenance must be a graph identity without spaces",
                                 field="sources[" + source["id"] + "].discovered_by[" + str(index) + "]") from None
            identity = _identifier("seed", run_id, source["id"], seed, index)
            if identity not in discoveries:
                mutations.append(_insert("discoveries", discovery_id=identity, artifact_id=artifact, supplied_seed_id=seed,
                                         observed_title=source["title"], observed_url=source["url"]))
                discoveries.add(identity)
    known_actors = {r[0] for r in conn.execute("SELECT actor_id FROM actors")}
    known_work = _work_index(conn)
    known_origins = {r[0] for r in conn.execute("SELECT origin_id FROM work_origins")}
    open_work, closed_work = set(), {}
    for item in plan["items"]:
        try:
            graph._id(item["actor"])
        except GraphError:
            raise GraphError("invalid_packet", "Invalid graph actor identity", field="items[" + item["id"] + "].actor") from None
        if not conn.execute("SELECT 1 FROM plan_items WHERE plan_sha256=? AND plan_item_id=?", (key, item["id"])).fetchone():
            mutations.append(_insert("plan_items", plan_sha256=key, plan_item_id=item["id"]))
        if item["actor"] not in known_actors:
            mutations.append(_insert("actors", actor_id=item["actor"], parent_actor_id=_coordinator(conn), role="searcher" if item["kind"] == "search" else "reader"))
            known_actors.add(item["actor"])
        annotations = item.get("graph", {})
        for question in graph.plan_item_questions(plan, item):
            # Closed intent and exact registered-plan replay do not dispatch.
            # Preserve their original ancestry after a later admission of the
            # same request through another retained path. New active candidates
            # and claim/operation validation still enforce retry identity.
            work, parent = _plan_work(root, conn, plan, item, question, artifacts, allow_opaque=allow_opaque,
                                      check_retry=not registered and item["status"] in {"queued", "active"}, contract=contract)
            legacy = _legacy_search_work(conn, key, item, work) if registered else None
            if legacy:
                # Replaying this exact saved registration is historical; keep
                # its original identity without authorizing another claim.
                work.update({field: legacy[field] for field in ("request_json", "logical_key", "work_id")})
            _require(item["kind"] != "read" or work["artifact_id"], "Read source identity is not registered", "pending_dependency")
            identity = known_work.get(work["logical_key"], work["work_id"])
            if item["status"] not in {"queued", "active"}:
                if item["status"] in {"bounded", "dropped"} and work["logical_key"] in known_work:
                    closed_work[identity] = item
                continue
            open_work.add(identity)
            if work["logical_key"] not in known_work:
                mutations.append(_insert("work_items", **work))
                known_work[work["logical_key"]] = identity
            origin_id = _identifier("origin", key, item["id"], question)
            if origin_id not in known_origins:
                origin = {"origin_id": origin_id, "work_id": identity, "originating_generation": work["generation"]}
                if parent:
                    _require(_row(conn, "operation_links", parent), "Index the original parent operation first", "pending_dependency")
                    origin["parent_operation_id"] = parent
                for field in ("lead_id", "reference_id"):
                    if field in annotations:
                        _require(parent, "Linked follow-up needs its original admitted parent operation", "scope_mismatch")
                        _require(_row(conn, "leads" if field == "lead_id" else "reference_occurrences", annotations[field]),
                                 "Linked follow-up identity is not registered", "unknown_identity", field="items[" + item["id"] + "].graph." + field)
                        origin[field] = annotations[field]
                if not parent:
                    origin["root_basis"] = "Saved plan " + key + " item " + item["id"] + ": " + item["reason"]
                mutations.append(_insert("work_origins", **origin))
                known_origins.add(origin_id)
            existing = _row(conn, "work_items", identity)
            state = existing["state"] if existing else "pending"
            bindings.append({"plan_item_id": item["id"], "question_id": question, "work_id": identity, "state": state,
                             "reopen_required": state in {"completed", "blocked", "deferred", "cancelled"}})
    for identity, item in closed_work.items():
        existing = _row(conn, "work_items", identity)
        if identity not in open_work and existing and existing["state"] == "pending":
            mutations.append(_update("work_items", existing, state="deferred" if item["status"] == "bounded" else "cancelled",
                                     terminal_reason=item["reason"], transition_reason="Original saved plan bound: " + item["reason"]))
    return mutations, bindings


def validate_plan(root, plan, ledger):
    """Pure graph preflight, called after ordinary plan checks and before saving.

    Uses the exact registration projection with a nonpersistent candidate hash.
    No plan/packet file, graph mutation, budget event or provider call is made.
    Admission and registration still recheck mutable dependencies afterwards.
    """
    def validate():
        path = Path(root).resolve()
        key = _hash({"candidate_plan": plan, "candidate_ledger": ledger})
        with graph.snapshot(path) as conn:
            contract_hash = _read(path, "bundle.json")[0]["contract_sha256"]
            mutations, bindings = _project_plan(path, conn, plan, ledger, key, contract_hash)
            if mutations:
                packet, data = graph._packet(_packet(path, conn, mutations))
                normalized = graph._normal_mutations(packet)
                # Only this controller's plan projection may use the SQL-only
                # simulation. The future plan files do not exist yet; all other
                # records derive from this snapshot and original recorder files.
                allowed = {"contract_snapshots", "questions", "plan_snapshots", "plan_items", "actors", "artifacts", "artifact_keys", "discoveries", "work_items", "work_origins"}
                _require(all(m["table"] in allowed for m in normalized), "Unexpected plan projection table")
                for mutation in normalized:
                    if mutation["op"] == "update" and mutation["table"] == "work_items":
                        _require(not _work_unfinished(path, conn, mutation["id"]), "Original work admissions require reconciliation before plan closure", "pending_dependency")
                with closing(sqlite3.connect(":memory:", isolation_level=None)) as candidate:
                    candidate.row_factory = sqlite3.Row
                    conn.backup(candidate)
                    candidate.execute("PRAGMA foreign_keys=ON")
                    try:
                        graph._apply_packet(path, candidate, packet, data, normalized, {}, internal=True)
                    except sqlite3.Error as error:
                        raise graph._sql_error(error) from None
            return _response(conn, "validate_plan", bindings=bindings, mutation_count=len(mutations),
                             plan_saved=False, dispatch_authorized=False)
    return ingestion._safe(validate)


def register_plan(root, saved_plan_hash):
    """Register original saved-plan bindings and its pending work automatically."""
    root = Path(root).resolve()
    plan, ledger, key = _plan(root, saved_plan_hash)
    # Revalidate through the original plan checker; no synthetic graph-only plans.
    try:
        operations = {}
        for operation_id, (spec, receipt, result) in bundle.plan_operations(root).items():
            request, _ = _read(root, "operations/" + operation_id + "/request.json")
            if request["started"] <= plan["_record"]["saved"]:
                operations[operation_id] = (spec, receipt if receipt and receipt["finished"] <= plan["_record"]["saved"] else None,
                                            result if result and result["indexed"] <= plan["_record"]["saved"] else None)
        context = bundle.graph_ledger_context(root, ledger, historical=True)
        bundle.checked_plan(plan, bundle.read(root / "contracts" / (plan["_record"]["contract_sha256"] + ".json")), ledger, operations,
                            graph_context=context)
    except (ValueError, KeyError, TypeError):
        raise GraphError("scope_mismatch", "Original saved plan validation failed") from None
    with graph.writer(root):
        with graph.snapshot(root) as conn:
            mutations, bindings = _project_plan(root, conn, plan, ledger, key, plan["_record"]["contract_sha256"],
                                                allow_opaque=_row(conn, "plan_snapshots", key) is not None)
            packet = _packet(root, conn, mutations) if mutations else None
            response = _response(conn, "register_plan", idempotent=True)
        receipt = _ingest(root, packet) if packet else response
        return {**receipt, "bindings": bindings, "plan_sha256": key}


def register_actor(root, actor_id, role, parent_actor_id, *, branch_scope=None, stage_scope=None, by_actor_id=None, parent_assignment_id=None, declared_runtime_identity=None):
    with graph.writer(root):
        with graph.snapshot(root) as conn:
            parent = _row(conn, "actors", parent_actor_id)
            _require(parent and parent["role"] in {"coordinator", "research_lead"}, "Only a coordinator or research lead can delegate", "permission_denied")
            _require(role in {"research_lead", "searcher", "reader", "coverage_reviewer", "evidence_reviewer"}, "Invalid delegated role", "permission_denied")
            if parent["role"] == "research_lead":
                _require(branch_scope and graph._in_branch(branch_scope, parent["branch_scope"]) and stage_scope == parent["stage_scope"], "Child exceeds parent scope", "permission_denied")
                _require(by_actor_id == parent_actor_id and parent_assignment_id, "Delegated registration needs the original lead assignment", "permission_denied")
                assignment, work = _assignment(conn, parent_assignment_id)
                _require(assignment["actor_id"] == parent_actor_id and assignment["state"] in {"leased", "running"}
                         and graph._timestamp(assignment["lease_expires_at"]) > _now()
                         and graph._in_branch(branch_scope, work["branch"]) and stage_scope in (None, work["stage"]),
                         "Child registration exceeds its active parent assignment", "permission_denied")
            packet = _packet(root, conn, [_insert("actors", actor_id=actor_id, parent_actor_id=parent_actor_id, role=role, branch_scope=branch_scope,
                                                       stage_scope=stage_scope, declared_runtime_identity=declared_runtime_identity)], by_actor_id, parent_assignment_id)
        return _ingest(root, packet)


def _fair(conn, limit, actor=None, protected=False):
    # A wave waits for active/reconciling earlier work; expiry never grants another lease.
    rows = conn.execute("""SELECT w.*, (SELECT COUNT(*) FROM assignments a JOIN work_items x ON x.work_id=a.work_id WHERE x.branch=w.branch) branch_claims,
        (SELECT COUNT(*) FROM assignments a JOIN work_items x ON x.work_id=a.work_id WHERE x.question_id=w.question_id) question_claims
        FROM pending_frontier w ORDER BY CASE stage WHEN 'baseline' THEN 0 WHEN 'priority' THEN 1 ELSE 2 END,generation,branch,work_id""").fetchall()
    wave = conn.execute("SELECT stage,generation FROM work_items WHERE state IN ('pending','leased','running') ORDER BY CASE stage WHEN 'baseline' THEN 0 WHEN 'priority' THEN 1 ELSE 2 END,generation LIMIT 1").fetchone()
    followup = conn.execute("SELECT MIN(generation) FROM work_items WHERE generation>0 AND state IN ('pending','leased','running')").fetchone()[0] if protected else None
    candidates = []
    for row in rows:
        work = dict(row)
        eligible_wave = work["generation"] in (0, followup) if protected else wave is not None and (work["stage"], work["generation"]) == tuple(wave)
        if not eligible_wave or actor and (not graph._in_branch(work["branch"], actor["branch_scope"]) or actor["stage_scope"] not in (None, work["stage"])):
            continue
        roles = [r[0] for r in conn.execute("SELECT role FROM artifact_roles WHERE artifact_id=? AND (question_id=? OR question_id IS NULL) AND disposition IN ('proposed','accepted','challenged') ORDER BY role", (_canonical(conn, work["artifact_id"]), work["question_id"]))]
        work["roles"] = roles or ["unassessed"]
        candidates.append(work)
    selected, branch_counts, question_counts, role_counts = [], {}, {}, {}
    for row in conn.execute("""SELECT ar.role,COUNT(DISTINCT a.assignment_id) FROM assignments a JOIN work_items w USING(work_id)
        JOIN canonical_artifacts c ON c.artifact_id=w.artifact_id JOIN artifact_roles ar ON ar.artifact_id=c.canonical_id
        AND (ar.question_id=w.question_id OR ar.question_id IS NULL) WHERE ar.disposition IN ('proposed','accepted','challenged') GROUP BY ar.role"""):
        role_counts[row[0]] = row[1]
    role_counts["unassessed"] = conn.execute("""SELECT COUNT(*) FROM assignments a JOIN work_items w USING(work_id) WHERE NOT EXISTS(
        SELECT 1 FROM artifact_roles ar JOIN canonical_artifacts c ON c.canonical_id=ar.artifact_id WHERE c.artifact_id=w.artifact_id
        AND (ar.question_id=w.question_id OR ar.question_id IS NULL) AND ar.disposition IN ('proposed','accepted','challenged'))""").fetchone()[0]
    while candidates and len(selected) < limit:
        candidates.sort(key=lambda w: (w["branch_claims"] + branch_counts.get(w["branch"], 0),
                         w["question_claims"] + question_counts.get(w["question_id"], 0), min(role_counts.get(role, 0) for role in w["roles"]),
                         -w["priority"], w["branch"], w["question_id"], w["work_id"]))
        work = candidates.pop(0)
        selected.append(work)
        branch_counts[work["branch"]] = branch_counts.get(work["branch"], 0) + 1
        question_counts[work["question_id"]] = question_counts.get(work["question_id"], 0) + 1
        for role in work["roles"]:
            role_counts[role] = role_counts.get(role, 0) + 1
    return selected


def fair_frontier(root, limit=50, actor_id=None):
    _require(type(limit) is int and 1 <= limit <= 200, "Limit must be in 1..200")
    with graph.snapshot(root) as conn:
        actor = _row(conn, "actors", actor_id) if actor_id else None
        _require(not actor_id or actor, "Actor is unknown", "unknown_identity")
        return {"status": "pass", "schema_version": 1, "command": "fair_frontier", "run_id": conn.execute("SELECT run_id FROM graph_metadata").fetchone()[0],
                "graph_sequence": conn.execute("SELECT MAX(sequence) FROM graph_events").fetchone()[0], "items": _fair(conn, limit, actor, bool(budget.allocations.config(bundle.contract_at(root)))), "dispatch_authorized": False}


def claim(root, work_id, actor_id, plan_sha256, plan_item_id, *, expected_revision, assignment_id=None, lease_seconds=900, by_actor_id=None, parent_assignment_id=None, enforce_fairness=True, fairness_override_reason=None):
    _revision(expected_revision)
    _require(type(lease_seconds) is int and 1 <= lease_seconds <= 86400, "Lease duration is invalid")
    with graph.writer(root):
        with graph.snapshot(root) as conn:
            work, actor = _row(conn, "work_items", work_id), _row(conn, "actors", actor_id)
            _require(work and actor, "Work or actor is unknown", "unknown_identity")
            _require(work["revision"] == expected_revision, "Work revision changed", "revision_conflict")
            _require(work["state"] == "pending" and not conn.execute("SELECT 1 FROM assignments WHERE work_id=? AND state IN ('leased','running','reconciling')", (work_id,)).fetchone(), "Work already has an active claim", "lease_conflict")
            _require(not _work_unfinished(root, conn, work_id), "Earlier original work admissions require reconciliation before a replacement claim", "pending_dependency")
            _require(type(enforce_fairness) is bool, "Fairness selection must be boolean")
            if not enforce_fairness:
                authority = _row(conn, "actors", by_actor_id or _coordinator(conn))
                _require(authority and authority["role"] == "coordinator" and isinstance(fairness_override_reason, str) and fairness_override_reason.strip(),
                         "Fairness override requires an explicit coordinator policy reason", "permission_denied")
            if enforce_fairness:
                choices = _fair(conn, 1, actor, bool(budget.allocations.config(bundle.contract_at(root))))
                _require(choices and choices[0]["work_id"] == work_id, "Earlier wave or less-served branch/question remains eligible", "pending_dependency")
            plan, _, _ = _plan(root, plan_sha256)
            item = next((i for i in plan["items"] if i["id"] == plan_item_id), None)
            _require(item and item["actor"] == actor_id and item["status"] in {"queued", "active"}, "Saved plan assignment differs", "scope_mismatch")
            _match_plan_work(root, conn, work, plan, item, check_retry=True)
            if parent_assignment_id:
                parent, parent_work = _assignment(conn, parent_assignment_id)
                _require(parent["actor_id"] == actor["parent_actor_id"] and parent["state"] in {"leased", "running"}
                         and graph._timestamp(parent["lease_expires_at"]) > _now() and graph._in_branch(work["branch"], parent_work["branch"])
                         and work["stage"] == parent_work["stage"] and work["question_id"] == parent_work["question_id"]
                         and work["generation"] >= parent_work["generation"], "Nested claim exceeds active parent scope", "permission_denied")
            now = _now()
            identity = assignment_id or _identifier("assignment", work_id, actor_id, expected_revision)
            record = dict(assignment_id=identity, work_id=work_id, actor_id=actor_id, parent_assignment_id=parent_assignment_id,
                          claimed_at=now, lease_expires_at=(datetime.fromisoformat(now.replace("Z", "+00:00")) + timedelta(seconds=lease_seconds)).isoformat(),
                          generation_at_claim=work["generation"], plan_item_id=plan_item_id, plan_sha256=plan_sha256)
            packet = _packet(root, conn, [_insert("assignments", **record), _update("work_items", work, state="leased", transition_reason="Atomic scoped claim" + ("; coordinator fairness override: " + fairness_override_reason if not enforce_fairness else ""))], by_actor_id, parent_assignment_id)
        result = _ingest(root, packet)
        return {**result, "assignment_id": identity, "work_id": work_id}


def _assignment(conn, assignment_id, expected_revision=None):
    if expected_revision is not None:
        _revision(expected_revision)
    assignment = _row(conn, "assignments", assignment_id)
    _require(assignment, "Assignment is unknown", "unknown_identity")
    _require(expected_revision is None or assignment["revision"] == expected_revision, "Assignment revision changed", "revision_conflict")
    return assignment, _row(conn, "work_items", assignment["work_id"])


def start_assignment(root, assignment_id, *, expected_revision):
    with graph.writer(root):
        with graph.snapshot(root) as conn:
            assignment, work = _assignment(conn, assignment_id, expected_revision)
            _require(assignment["state"] == "leased" and graph._timestamp(assignment["lease_expires_at"]) > _now(), "Assignment must have an unexpired lease", "lease_conflict")
            packet = _packet(root, conn, [_update("assignments", assignment, state="running"), _update("work_items", work, state="running", transition_reason="Claimed worker started")])
        return _ingest(root, packet)


def renew_lease(root, assignment_id, *, expected_revision, lease_seconds=900):
    _require(type(lease_seconds) is int and 1 <= lease_seconds <= 86400, "Lease duration is invalid")
    with graph.writer(root):
        with graph.snapshot(root) as conn:
            assignment, _ = _assignment(conn, assignment_id, expected_revision)
            _require(assignment["state"] in {"leased", "running"} and graph._timestamp(assignment["lease_expires_at"]) > _now(), "Expired or reconciling worker must be reconciled before renewal", "lease_conflict")
            expires = (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat()
            _require(graph._timestamp(expires) > graph._timestamp(assignment["lease_expires_at"]), "Renewal must extend the existing lease")
            packet = _packet(root, conn, [_update("assignments", assignment, lease_expires_at=expires)])
        return _ingest(root, packet)


def reconcile_assignment(root, assignment_id, *, expected_revision, reason):
    _require(isinstance(reason, str) and reason.strip(), "Reconciliation needs a reason")
    with graph.writer(root):
        with graph.snapshot(root) as conn:
            assignment, work = _assignment(conn, assignment_id, expected_revision)
            _require(assignment["state"] in {"leased", "running"}, "Only an original active worker can enter reconciliation", "lease_conflict")
            packet = _packet(root, conn, [_update("assignments", assignment, state="reconciling"), _update("work_items", work, transition_reason="Original worker reconciliation: " + reason)])
        return _ingest(root, packet)


def resume_assignment(root, assignment_id, *, expected_revision, worker_identity, reason, lease_seconds=900):
    """Resume the same reconciled worker; this does not authorize new dispatch."""
    _require(type(lease_seconds) is int and 1 <= lease_seconds <= 86400 and isinstance(reason, str) and reason.strip(), "Resume needs a bounded lease and reason")
    with graph.writer(root):
        with graph.snapshot(root) as conn:
            assignment, work = _assignment(conn, assignment_id, expected_revision)
            actor = _row(conn, "actors", assignment["actor_id"])
            _require(assignment["state"] == "reconciling" and worker_identity == (actor["declared_runtime_identity"] or actor["actor_id"]),
                     "Resume must identify the same original worker", "permission_denied")
            expires = (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat()
            packet = _packet(root, conn, [_update("assignments", assignment, state="running", lease_expires_at=expires),
                                        _update("work_items", work, state="running", transition_reason="Same original worker resumed: " + reason)])
        return _ingest(root, packet)


def reconcile_operation(root, operation_id, *, expected_revision, worker_identity, worker_stopped, reason, cancel=False):
    """Retain explicit original-worker reconciliation; cancellation never refunds.

    worker_stopped is a coordinator attestation based on checking the original
    worker, not an inference from lease expiry. Unknown operations stay active
    unless that check explicitly supports cancellation or a receipt establishes
    termination. Imported interruption/timeout receipts cannot establish it.
    """
    _revision(expected_revision)
    _require(type(worker_stopped) is bool and type(cancel) is bool and isinstance(reason, str) and reason.strip(), "Original worker reconciliation needs explicit evidence")
    with graph.writer(root):
        has_receipt = (Path(root) / "operations" / operation_id / "receipt.json").is_file()
        original = _admission(root, operation_id, has_receipt)
        with graph.snapshot(root) as conn:
            operation = _row(conn, "operation_links", operation_id)
            _require(operation and operation["revision"] == expected_revision, "Operation revision changed", "revision_conflict")
            assignment, work = _assignment(conn, operation["assignment_id"])
            actor = _row(conn, "actors", assignment["actor_id"])
            uncertain_receipt = has_receipt and ingestion.receipt_execution_state(original["request"], original["receipt"]) == "unknown"
            historical_failed = operation["execution_state"] == "failed" and uncertain_receipt
            _require((operation["execution_state"] in _UNRESOLVED or historical_failed) and worker_identity == (actor["declared_runtime_identity"] or actor["actor_id"]),
                     "Reconcile the same original worker/operation", "permission_denied")
            _require(not cancel or worker_stopped, "Cancellation needs an explicit check that the original worker stopped", "pending_dependency")
            _require(not has_receipt or ingestion.receipt_execution_state(original["request"], original["receipt"]) == "unknown", "A terminal receipt must be reconciled through record_completion", "pending_dependency")
            proof = {"operation_id": operation_id, "request_sha256": original["request_hash"], "worker_identity": worker_identity,
                     "receipt_sha256": original.get("receipt_hash"), "worker_stopped": worker_stopped, "reason": reason, "checked_at": _now()}
            mutations = []
            if cancel and operation["execution_state"] == "dispatched":
                mutations.append(_update("operation_links", operation, execution_state="unknown"))
                operation = dict(operation, revision=operation["revision"] + 1, execution_state="unknown")
            if not historical_failed:
                mutations.append(_update("operation_links", operation, execution_state="cancelled" if cancel else "unknown", receipt_sha256=original.get("receipt_hash")))
            # Earlier versions incorrectly marked imported interruptions failed.
            # Retain that history and append the checked termination statement;
            # the frozen schema intentionally forbids rewriting failed states.
            mutations.append(_update("work_items", work, transition_reason="Worker reconciliation: " + json.dumps(proof, sort_keys=True)))
            packet = _packet(root, conn, mutations)
        return _ingest(root, packet)


def recovery_status(root):
    """Read original admissions, including missing mirrors; do not restart workers."""
    with graph.snapshot(root) as conn:
        items = []
        for assignment in conn.execute("SELECT * FROM assignments ORDER BY assignment_id"):
            unresolved = _unfinished(root, conn, assignment["assignment_id"])
            expired = graph._timestamp(assignment["lease_expires_at"]) <= _now()
            if unresolved or assignment["state"] in _ACTIVE and (expired or assignment["state"] == "reconciling"):
                items.append({"assignment_id": assignment["assignment_id"], "actor_id": assignment["actor_id"], "state": assignment["state"],
                              "lease_expired": expired, "unresolved_operation_ids": unresolved, "replacement_authorized": False})
        return _response(conn, "recovery_status", items=items, dispatch_authorized=False)


def recover_admission(root, operation_id, *, worker_identity, worker_stopped, reason):
    """Materialize interrupted request persistence, then cancel without refund.

    The journal already holds the exact intent. Restoration is explicitly
    recorded before writing; it does not assert that a provider ran or returned.
    A coordinator must verify that the original worker has stopped first.
    """
    graph._id(operation_id, filename=True)
    _require(worker_stopped is True and isinstance(reason, str) and reason.strip(),
             "Admission recovery requires an explicit original-worker stop check", "pending_dependency")
    root = Path(root)
    with graph.writer(root):
        events = [e for e in _events(root) if e["type"] == "admit" and e["operation"] == operation_id]
        _require(len(events) == 1, "Original admission is missing", "pending_dependency")
        event = events[0]
        try:
            request = budget.admission_request(event)
        except (ValueError, KeyError, TypeError):
            raise GraphError("pending_dependency", "Original admission has no valid retained request intent; do not reconstruct from guesses") from None
        spec = request["spec"]
        with graph.snapshot(root) as conn:
            assignment, _ = _binding(root, conn, spec, False)
            actor = _row(conn, "actors", assignment["actor_id"])
            _require(worker_identity == (actor["declared_runtime_identity"] or actor["actor_id"]),
                     "Recovery must identify the original worker", "permission_denied")
        directory = graph._path(root.resolve(), "operations/" + operation_id, exists=False)
        retained = directory / "request.json"
        proof_path = graph._path(root.resolve(), "admission-recoveries/" + operation_id + ".json", exists=False)
        _require(not directory.exists() or {p.name for p in directory.iterdir()} <= {"request.json"},
                 "Original output exists; reconcile the existing request/receipt instead", "pending_dependency")
        _require(not retained.exists() or proof_path.is_file(),
                 "Existing request needs ordinary operation reconciliation", "pending_dependency")
        expected = (json.dumps(request, ensure_ascii=False, indent=2) + "\n").encode()
        proof = dict(operation_id=operation_id, budget_event_sha256=event["sha256"],
                     request_sha256=hashlib.sha256(expected).hexdigest(), worker_identity=worker_identity,
                     worker_stopped=True, reason=reason, checked_at=_now(),
                     basis="Restored declared request from its original admission intent; no provider receipt")
        if proof_path.exists():
            previous = bundle.read(proof_path)
            _require(all(previous.get(key) == proof[key] for key in
                         ("operation_id", "budget_event_sha256", "request_sha256", "worker_identity", "worker_stopped")),
                     "Original admission recovery proof differs", "integrity_error")
        else:
            proof_path.parent.mkdir(mode=0o700, exist_ok=True)
            bundle.write(proof_path, proof)
        if retained.exists():
            _require(retained.read_bytes() == expected, "Restored request changed", "integrity_error")
        else:
            directory.mkdir(mode=0o700, exist_ok=True)
            bundle.write(retained, request)
        record_admission(root, operation_id)
        with graph.snapshot(root) as conn:
            operation = _row(conn, "operation_links", operation_id)
            assignment, work = _assignment(conn, operation["assignment_id"])
            if operation["execution_state"] == "cancelled":
                _require(_historical_worker_stopped(conn, _admission(root, operation_id), work["work_id"]),
                         "Cancelled admission lacks worker-stop evidence", "pending_dependency")
                return _response(conn, "recover_admission", operation_id=operation_id, idempotent=True,
                                 dispatch_authorized=False, refunded=False)
        result = reconcile_operation(root, operation_id, expected_revision=operation["revision"],
                                     worker_identity=worker_identity, worker_stopped=True, cancel=True, reason=reason)
        return dict(result, command="recover_admission", operation_id=operation_id, idempotent=False,
                    dispatch_authorized=False, refunded=False)


def recover_retry(root, operation_id, *, expected_revision, reason):
    """Allow a bounded retry after checked cancellation of a request-only admission.

    This records a local orchestration failure, not a provider failure/receipt.
    The original worker must already have been reconciled as stopped. No attempt
    is refunded, no fresh epoch begins, and normal plan/lease/ancestry checks
    still apply when admitting the retry. Request-only state alone proves neither
    worker termination nor that an external tool was never invoked.
    """
    _revision(expected_revision)
    _require(isinstance(reason, str) and reason.strip(), "Retry recovery needs an observed local failure basis")
    budget.enabled(Path(root))
    with graph.writer(root):
        with graph.snapshot(root) as conn:
            operation = _row(conn, "operation_links", operation_id)
            _require(operation and operation["revision"] == expected_revision,
                     "Operation revision changed", "revision_conflict")
            _require(operation["execution_state"] == "cancelled" and operation["receipt_sha256"] is None,
                     "Retry recovery needs a cancelled request-only admission", "pending_dependency")
            original = _admission(root, operation_id)
            _require(all(operation.get(k) == v for k, v in _operation_row(original, "cancelled").items()),
                     "Cancelled mirror differs from its original admission", "integrity_error")
            directory = Path(root) / "operations" / operation_id
            _require({p.name for p in directory.iterdir()} == {"request.json"},
                     "Retry recovery cannot replace retained outputs or receipts", "pending_dependency")
            assignment, _ = _assignment(conn, operation["assignment_id"])
            _require(_historical_worker_stopped(conn, original, assignment["work_id"]),
                     "Retry recovery needs retained original-worker stop reconciliation", "pending_dependency")
            proof = {"request_sha256": original["request_hash"], "operation_revision": expected_revision,
                     "assignment_id": operation["assignment_id"],
                     "graph_sequence": conn.execute("SELECT MAX(sequence) FROM graph_events").fetchone()[0]}
            with budget.locked(Path(root)) as state:
                previous = [e for e in state["events"] if e["type"] == "classify" and e["operation"] == operation_id]
                prior_proof = previous[-1].get("cancelled_admission", {}) if previous else {}
                if all(prior_proof.get(k) == v for k, v in proof.items() if k != "graph_sequence"):
                    event, idempotent = previous[-1], True
                else:
                    event = budget.append(Path(root), state, "classify", operation=operation_id,
                                          classification="transient", reason=reason, cancelled_admission=proof)
                    idempotent = False
            return _response(conn, "recover_retry", operation_id=operation_id, budget_event_id=event["id"],
                             budget_event_sha256=event["sha256"], idempotent=idempotent,
                             dispatch_authorized=False, fresh_epoch=False)


def _binding(root, conn, spec, active, *, retiring=False):
    assignment, work = _assignment(conn, spec.get("graph_assignment_id"))
    _require(spec.get("actor") == assignment["actor_id"] and spec.get("plan_sha256") == assignment["plan_sha256"]
             and spec.get("plan_item") == assignment["plan_item_id"], "Operation differs from assignment identity/plan", "scope_mismatch")
    if active:
        _require(assignment["state"] == "running" and work["state"] == "running" and graph._timestamp(assignment["lease_expires_at"]) > _now(), "Original worker must be running under an unexpired lease", "lease_conflict")
    request = work["request_json"]
    _require(not active or work["kind"] not in {"read", "inspect_visuals"}
             or graph.meaningful_read_extent(request["extent"]),
             "Empty historical scope cannot authorize another read", "scope_mismatch")
    try:
        plan, _, _ = _plan(root, spec["plan_sha256"])
        item = next(i for i in plan["items"] if i["id"] == spec["plan_item"])
        _require(work["question_id"] in graph.plan_item_questions(plan, item) or retiring and not active,
                 "Assigned question exceeds saved plan coverage", "scope_mismatch")
        if not retiring or active:
            try:
                _match_plan_work(root, conn, work, plan, item, historical_plan_key=assignment["plan_sha256"] if not active else None)
            except GraphError as error:
                if (active or error.code != "scope_mismatch"
                        or not _completed_capture_correction(root, conn, assignment, work, plan, item)):
                    raise
                # Passive mirroring may revisit the original operation after a
                # completed self-pinning correction. Require its exact retained
                # admission/receipt, never an unadmitted prospective request.
                original = _admission(root, spec["id"], require_receipt=True)
                _require(original["spec"] == spec, "Operation differs from its retained pre-correction original", "scope_mismatch")
        contract = bundle.contract_at(Path(root)) if active else bundle.read(Path(root) / "contracts" / (plan["_record"]["contract_sha256"] + ".json"))
        bundle.check_planned_operation(Path(root), spec, contract, starting=active)
    except (ValueError, KeyError, TypeError, OSError):
        raise GraphError("scope_mismatch", "Operation differs from its original saved plan") from None
    _require(not active or "capture_ids" not in request, "Retained-capture work cannot admit or dispatch a provider operation", "scope_mismatch")
    _require(work["kind"] == spec["kind"] or work["kind"] == "inspect_visuals" and spec["kind"] == "read", "Operation kind differs from work", "scope_mismatch")
    _require(request.get("provider", spec["provider"]) == spec["provider"], "Operation provider differs from work", "scope_mismatch")
    if "request" in request.get("provider_metadata", {}):
        _require(all(spec["request"].get(k) == v for k, v in request["provider_metadata"]["request"].items()), "Provider request differs from graph intent", "scope_mismatch")
    if spec["kind"] == "read":
        _require(_artifact(conn, spec["request"]["source_id"]) == _canonical(conn, work["artifact_id"])
                 and spec["request"]["format"] in request.get("formats", [request.get("format")]), "Read source/format differs from work", "scope_mismatch")
    info = spec.get("research", {})
    _require(info.get("stage") == work["stage"] and info.get("branch") == work["branch"] and assignment["generation_at_claim"] == work["generation"], "Operation wave differs from original assignment", "scope_mismatch")
    _require(_generation(root, spec) == work["generation"], "Operation-derived generation differs; graph shortcuts cannot reset it", "scope_mismatch")
    return assignment, work


def _stop_proof(proof, original, actor):
    if not isinstance(proof, dict):
        return False
    try:
        checked = graph._timestamp(proof.get("checked_at"))
    except (GraphError, TypeError):
        return False
    return (proof.get("operation_id") == original["spec"]["id"] and proof.get("request_sha256") == original["request_hash"]
            and proof.get("receipt_sha256") == original.get("receipt_hash") and proof.get("worker_stopped") is True
            and proof.get("worker_identity") == (actor["declared_runtime_identity"] or actor["actor_id"])
            and isinstance(proof.get("reason"), str) and bool(proof["reason"].strip())
            and graph._timestamp(original.get("receipt", {}).get("finished", original["request"]["started"])) <= checked <= _now())


def _historical_worker_stopped(conn, original, work_id):
    actor = _row(conn, "actors", original["spec"]["actor"])
    for event in conn.execute("""SELECT e.payload_json FROM graph_events e JOIN ingest_batches b USING(batch_id) JOIN actors a ON a.actor_id=b.actor_id
        WHERE e.entity_kind='work_items' AND e.entity_id=? AND a.role='coordinator' ORDER BY e.sequence""", (graph._json(work_id),)):
        reason = json.loads(event[0]).get("after", {}).get("transition_reason", "")
        if reason.startswith("Worker reconciliation: "):
            try:
                proof = json.loads(reason[len("Worker reconciliation: "):])
            except ValueError:
                continue
            if _stop_proof(proof, original, actor):
                return True
    return False


def _late_receipt(conn, original, before):
    """Validate a new terminal observation of an already cancelled original.

    The accepted stop proof predates this receipt and binds the request-only
    cancellation. It is retained as history, not reinterpreted as a proof that
    the late receipt was observed then. No dispatched event is manufactured,
    including when the original request was restored from admission intent.
    """
    _require(graph.storage_revision(conn) >= 3,
             "Late receipt reconciliation requires storage revision 3; run graph upgrade-schema, then retry the record-completion work action or graph sync",
             "unsupported_schema", required_storage_revision=3)
    intent = {key: value for key, value in original.items() if key not in {"receipt", "receipt_hash"}}
    _require(before.get("receipt_sha256") is None and all(
        before.get(key) == value for key, value in _operation_row(intent, "cancelled").items()),
        "Late receipt requires the exact original request-only cancellation", "integrity_error")
    receipt, request = original["receipt"], original["request"]
    _require(ingestion.receipt_execution_state(request, receipt) in {"succeeded", "failed"},
             "Late receipt does not establish a terminal outcome", "pending_dependency")
    _require((request.get("transport") == "tool-import" and receipt.get("provenance") == "agent-attested-tool-output"
              and receipt["outcome"] in {"ok", "error"})
             or (request.get("transport") == "subprocess" and receipt.get("provenance") == "captured-subprocess"),
             "Late receipt provenance differs from its original transport", "integrity_error")
    assignment = _row(conn, "assignments", before["assignment_id"])
    _require(assignment and _historical_worker_stopped(conn, intent, assignment["work_id"]),
             "Late receipt requires retained original-worker cancellation evidence", "pending_dependency")


def _unfinished(root, conn, assignment_id):
    unresolved = {r[0] for r in conn.execute("SELECT operation_id FROM operation_links WHERE assignment_id=? AND execution_state IN ('reserved','admitted','dispatched','interrupted','unknown')", (assignment_id,))}
    assignment, _ = _assignment(conn, assignment_id)
    for event in _events(root):
        if event["type"] != "admit":
            continue
        path = Path(root) / "operations" / event["operation"] / "request.json"
        if not path.exists():
            intent = event.get("request_intent", {}).get("spec", {})
            if intent.get("graph_assignment_id") == assignment_id or not intent and event["actor"] == assignment["actor_id"]:
                unresolved.add(event["operation"])
            continue
        request, _ = _read(root, "operations/" + event["operation"] + "/request.json")
        if request["spec"].get("graph_assignment_id") == assignment_id:
            mirror = _row(conn, "operation_links", event["operation"])
            if not mirror or mirror["execution_state"] in _UNRESOLVED:
                unresolved.add(event["operation"])
            elif mirror["execution_state"] == "failed" and (Path(root) / "operations" / event["operation"] / "receipt.json").is_file():
                original = _admission(root, event["operation"], True)
                if ingestion.receipt_execution_state(original["request"], original["receipt"]) == "unknown" and not _historical_worker_stopped(conn, original, assignment["work_id"]):
                    unresolved.add(event["operation"])
    return sorted(unresolved)


def _work_unfinished(root, conn, work_id, excluding=None):
    """Original admissions outlive assignment state, including pre-fix gaps."""
    unresolved = set()
    for assignment in conn.execute("SELECT assignment_id FROM assignments WHERE work_id=?", (work_id,)):
        unresolved.update(_unfinished(root, conn, assignment[0]))
    return sorted(unresolved - ({excluding} if excluding else set()))


def prepare_assessment(root, read_id, actor_id, activity, assignment_id=None):
    """Bind a retained-content assessment to its actual current read assignment."""
    original = _admission(root, read_id, require_receipt=True)
    with graph.snapshot(root) as conn:
        artifact = _artifact(conn, original["spec"]["request"]["source_id"])
        choices = []
        for row in conn.execute("SELECT * FROM assignments WHERE actor_id=? AND state='running'", (actor_id,)):
            assigned = graph._decoded(row)
            work = _row(conn, "work_items", assigned["work_id"])
            if ((not assignment_id or assigned["assignment_id"] == assignment_id)
                    and _canonical(conn, work["artifact_id"]) == artifact and work["state"] == "running"
                    and graph._timestamp(assigned["lease_expires_at"]) > _now()):
                choices.append((assigned, work))
        _require(len(choices) == 1, "Assessment needs one current matching graph assignment; specify --assignment when ambiguous", "scope_mismatch")
        assigned, work = choices[0]
        plan, _, _ = _plan(root, assigned["plan_sha256"])
        item = next(i for i in plan["items"] if i["id"] == assigned["plan_item_id"])
        _match_plan_work(root, conn, work, plan, item)
        metadata = work["request_json"].get("provider_metadata", {})
        _require(activity["allocation"] == metadata.get("research_allocation")
                 and activity["purpose"] == metadata.get("activity_purpose", "research")
                 and activity["branch"] == work["branch"], "Assessment activity differs from saved work allocation", "scope_mismatch")
        if assigned["assignment_id"] != original["spec"]["graph_assignment_id"]:
            selected = work["request_json"].get("capture_ids", [])
            _require(any(row[0] in selected for row in conn.execute("SELECT capture_id FROM captures WHERE operation_id=?", (read_id,))),
                     "Retained assessment needs selected captures from that original read", "scope_mismatch")
        return {"assignment_id": assigned["assignment_id"], "generation": work["generation"], "branch": work["branch"]}


def prepare_operation(root, spec):
    """Validate before original recorder admission. Does not reserve or dispatch."""
    _require(isinstance(spec, dict), "Operation spec must be an object")
    graph._id(spec.get("id"), filename=True)
    with graph.snapshot(root) as conn:
        assignment, work = _binding(root, conn, spec, True)
        bundle.graph_component("research_followups").admission_check(root, conn, work)
        _require(not any(e["type"] == "admit" and e["operation"] == spec["id"] for e in _events(root)), "Original operation was already admitted; reconcile it", "pending_dependency", operation_id=spec["id"])
        _require(not _work_unfinished(root, conn, work["work_id"]), "Original work has unresolved admissions from an earlier or current assignment", "pending_dependency", assignment_id=assignment["assignment_id"])
        if work["artifact_id"]:
            terminal = conn.execute("""SELECT o.outcome_id FROM current_source_outcomes o JOIN canonical_artifacts c USING(artifact_id)
                WHERE c.canonical_id=? AND o.question_id=? AND o.required_scope_key=? AND o.readability IN ('access_blocked','unreadable','retry_exhausted')""",
                (_canonical(conn, work["artifact_id"]), work["question_id"], work["required_scope_key"])).fetchone()
            _require(not terminal, "Terminal source outcome needs explicit changed-basis reconciliation", "pending_dependency")
        return _response(conn, "prepare_operation", operation_id=spec["id"], assignment_id=assignment["assignment_id"], generation=work["generation"], dispatch_authorized=False)


def _operation_row(original, state):
    return dict(operation_id=original["spec"]["id"], assignment_id=original["spec"]["graph_assignment_id"], budget_event_id=str(original["event"]["id"]),
                request_sha256=original["request_hash"], receipt_sha256=original.get("receipt_hash"), execution_state=state, verification_state="verified")


def record_admission(root, spec):
    operation_id = spec["id"] if isinstance(spec, dict) else spec
    with graph.writer(root):
        original = _admission(root, operation_id)
        with graph.snapshot(root) as conn:
            assignment, _ = _binding(root, conn, original["spec"], False)
            state = "admitted" if assignment["state"] == "running" and graph._timestamp(assignment["lease_expires_at"]) > _now() else "unknown"
            previous = _row(conn, "operation_links", operation_id)
            if previous:
                _require(previous["request_sha256"] == original["request_hash"], "Operation mirror identity changed", "integrity_error")
                return _response(conn, "record_admission", operation_id=operation_id, idempotent=True, execution_state=previous["execution_state"])
            packet = _packet(root, conn, [_insert("operation_links", **_operation_row(original, state))])
        return _ingest(root, packet)


def record_dispatch(root, operation_id):
    """Retain dispatch intent immediately before launch, never claim launch success."""
    with graph.writer(root):
        _require(not (Path(root) / "admission-recoveries" / (operation_id + ".json")).exists(),
                 "Recovered admission is for cancellation, never redispatch", "pending_dependency")
        original = _admission(root, operation_id)
        with graph.snapshot(root) as conn:
            _binding(root, conn, original["spec"], True)
            previous = _row(conn, "operation_links", operation_id)
            _require(previous and previous["execution_state"] == "admitted", "Dispatch requires its original admitted mirror; reconcile all other states", "pending_dependency")
            _require(not (Path(root) / "operations" / operation_id / "receipt.json").exists(), "Receipt already exists; do not dispatch again", "pending_dependency")
            packet = _packet(root, conn, [_update("operation_links", previous, execution_state="dispatched")])
        return _ingest(root, packet)


def record_completion(root, operation_id):
    """Mirror a retained receipt, including a late result after cancellation."""
    with graph.writer(root):
        original = _admission(root, operation_id, True)
        state = ingestion.receipt_execution_state(original["request"], original["receipt"])
        with graph.snapshot(root) as conn:
            previous = _row(conn, "operation_links", operation_id)
            changes = _operation_row(original, state)
            if not previous:
                mutations = [_insert("operation_links", **changes)]
            else:
                _require(previous["request_sha256"] == original["request_hash"], "Operation identity differs", "integrity_error")
                if state == "unknown" and previous["execution_state"] == "cancelled" and previous["receipt_sha256"] == original["receipt_hash"]:
                    return _response(conn, "record_completion", operation_id=operation_id, idempotent=True, execution_state="cancelled")
                if state == "unknown" and previous["execution_state"] == "failed" and previous["receipt_sha256"] == original["receipt_hash"]:
                    assigned = _row(conn, "assignments", previous["assignment_id"])
                    _require(_historical_worker_stopped(conn, original, assigned["work_id"]), "Historical failed mirror still needs original-worker reconciliation for its imported interruption", "pending_dependency")
                    return _response(conn, "record_completion", operation_id=operation_id, idempotent=True, execution_state="failed", worker_reconciled=True)
                _binding(root, conn, original["spec"], False)
                if previous["execution_state"] == state and previous["receipt_sha256"] == original["receipt_hash"]:
                    return _response(conn, "record_completion", operation_id=operation_id, idempotent=True)
                if previous["execution_state"] == "cancelled":
                    _late_receipt(conn, original, previous)
                mutations = []
                if previous["execution_state"] in {"reserved", "admitted"}:
                    mutations.append(_update("operation_links", previous, execution_state="unknown"))
                    previous = dict(previous, revision=previous["revision"] + 1, execution_state="unknown")
                mutations.append(_update("operation_links", previous, execution_state=state, receipt_sha256=original["receipt_hash"], verification_state="verified", budget_event_id=str(original["event"]["id"])))
            packet = _packet(root, conn, mutations)
        return _ingest(root, packet)


def _completion(root, conn, work, *, correction_packet=None):
    latest = conn.execute("SELECT a.* FROM assignments a JOIN ingest_batches b ON b.batch_id=a.created_batch_id WHERE a.work_id=? ORDER BY b.resulting_sequence DESC,a.assignment_id DESC LIMIT 1", (work["work_id"],)).fetchone()
    _require(latest is not None, "Completed work has no original assignment", "pending_dependency")
    assignment = dict(latest)
    _require(work["kind"] not in {"read", "inspect_visuals"} or graph.meaningful_read_extent(work["request_json"]["extent"]),
             "Empty historical scope cannot establish completion", "pending_dependency")
    previous = _row(conn, "work_items", work["work_id"])
    if not _same_intent(conn, previous, work):
        paired = [m for m in (correction_packet or {}).get("mutations", []) if m["table"] == "assignments"
                  and m["op"] == "update" and m.get("id") == assignment["assignment_id"] and m["changes"].get("state") == "completed"]
        _require(correction_packet and correction_packet["packet_kind"] == "reconcile"
                 and _row(conn, "actors", correction_packet["actor_id"])["role"] == "coordinator"
                 and len(paired) == 1 and _capture_policy_correction(previous, work),
                 "Completion cannot substitute a different saved work intent", "scope_mismatch")
        validate_assignment_intent(root, conn, assignment, allow_legacy_search=True)
    else:
        validate_assignment_intent(root, conn, assignment, work=work, allow_completed_correction=True, allow_legacy_search=True)
    _require(not _unfinished(root, conn, assignment["assignment_id"]), "Original operations need reconciliation before completion", "pending_dependency")
    if work["kind"] in {"read", "inspect_visuals"}:
        request = work["request_json"]
        _require(request.get("capture_ids") or request.get("version_id") or request.get("freshness_basis") == "captured_after_assignment", "Completion needs an explicit capture, version or freshness policy", "scope_mismatch")
        if "capture_ids" in request:
            ingestion.validate_capture_bindings(root, conn, work)
        inspections = conn.execute("""SELECT i.*,r.assignment_id FROM inspections i JOIN reader_returns r USING(return_id) JOIN inspection_scopes s USING(inspection_id)
            JOIN canonical_artifacts c USING(artifact_id) WHERE c.canonical_id=? AND r.assignment_id=? AND r.verification_state='verified' AND i.provenance_basis='native'
            AND s.question_id=? AND s.required_scope_key=? AND s.format_policy_sha256=? AND s.coverage='sufficient'""",
             (_canonical(conn, work["artifact_id"]), assignment["assignment_id"], work["question_id"], work["required_scope_key"], work["format_policy_sha256"])).fetchall()
        withdrawn = graph.withdrawn_inspection_ids(conn)
        for inspection in inspections:
            if inspection["inspection_id"] in withdrawn:
                continue
            ingestion._returned(root, inspection["return_id"], conn)
            captures = conn.execute("SELECT c.*,ic.inspected_extent_json FROM inspection_captures ic JOIN captures c USING(capture_id) WHERE ic.inspection_id=?", (inspection["inspection_id"],)).fetchall()
            valid = graph.satisfying_read_captures(request, assignment["claimed_at"], inspection["inspected_at"],
                                                 [graph._decoded(c) for c in captures])
            if valid:
                for capture in valid:
                    ingestion._retained_capture(root, conn, dict(capture))
                return True
        raise GraphError("pending_dependency", "Latest assignment lacks actual sufficient inspection of every requested format, extent and version")
    if work["kind"] == "search":
        operations = conn.execute("SELECT operation_id FROM operation_links WHERE assignment_id=? AND execution_state='succeeded' AND verification_state='verified'", (assignment["assignment_id"],)).fetchall()
        for row in operations:
            operation = ingestion._operation(root, row[0], conn)
            result = operation["result"]
            expected = len(result.get("sources", [])) + len(set(result["source_ids"]) - {s["source_id"] for s in result.get("sources", [])})
            actual = conn.execute("SELECT count(*) FROM discoveries WHERE operation_id=?", (row[0],)).fetchone()[0]
            if actual == expected and result["pagination"] == "complete" and result.get("next_cursor") is None:
                cap = operation["spec"]["request"]["cap"]
                _require(cap is None or len(result["source_ids"]) <= cap, "Search index exceeds the declared cap", "integrity_error")
                return True
        raise GraphError("pending_dependency", "Search needs a complete indexed result; capped/more/unknown pagination must remain explicitly bounded or pending")
    _require(work["kind"] == "reconcile", "Review work needs explicit evidence completion", "pending_dependency")
    return True


def complete_work(root, assignment_id, *, expected_revision, reason):
    return _finish(root, assignment_id, expected_revision, "completed", reason)


def bound_work(root, assignment_id, *, expected_revision, reason, state="deferred"):
    _require(state in {"blocked", "deferred", "cancelled"}, "Invalid work bound")
    return _finish(root, assignment_id, expected_revision, state, reason)


def release_assignment(root, assignment_id, *, expected_revision, reason):
    return _finish(root, assignment_id, expected_revision, "pending", reason)


def _finish(root, assignment_id, revision, state, reason):
    _require(isinstance(reason, str) and reason.strip(), "Work transition needs a reason")
    with graph.writer(root):
        with graph.snapshot(root) as conn:
            assignment, work = _assignment(conn, assignment_id, revision)
            _require(assignment["state"] in _ACTIVE, "Assignment is already terminal", "lease_conflict")
            _require(not _unfinished(root, conn, assignment_id), "Original worker operations are unresolved; no replacement or refund is inferred", "pending_dependency")
            if state == "completed":
                _require(assignment["state"] in {"running", "reconciling"}, "Unstarted assignment cannot complete", "lease_conflict")
                _completion(root, conn, work)
            terminal = state != "pending"
            packet = _packet(root, conn, [_update("assignments", assignment, state="completed" if state == "completed" else "released", finished_at=_now()),
                                        _update("work_items", work, state=state, terminal_reason=reason if terminal else None, transition_reason=reason)])
        return _ingest(root, packet)


def reopen_work(root, work_id, *, expected_revision, reason):
    _revision(expected_revision)
    _require(isinstance(reason, str) and reason.strip(), "Reopening needs a changed basis/reason")
    with graph.writer(root):
        with graph.snapshot(root) as conn:
            work = _row(conn, "work_items", work_id)
            _require(work and work["revision"] == expected_revision, "Work revision changed", "revision_conflict")
            _require(work["state"] in {"completed", "blocked", "deferred", "cancelled"}, "Only terminal work can reopen")
            if work["artifact_id"]:
                blocked = conn.execute("""SELECT 1 FROM current_source_outcomes o JOIN canonical_artifacts c USING(artifact_id) WHERE c.canonical_id=?
                    AND o.question_id=? AND o.required_scope_key=? AND o.readability IN ('access_blocked','unreadable','retry_exhausted')""",
                    (_canonical(conn, work["artifact_id"]), work["question_id"], work["required_scope_key"])).fetchone()
                _require(not blocked, "Reconcile the terminal source outcome chain with changed_basis before reopening", "pending_dependency")
            packet = _packet(root, conn, [_update("work_items", work, state="pending", terminal_reason=None, transition_reason=reason)])
        return _ingest(root, packet)


def evidence_validator(root, connection, packet, mutation):
    """Compose verified recorder evidence with work/operation lifecycle checks."""
    table, row, before = mutation["table"], mutation.get("after", mutation.get("record", {})), mutation.get("before")
    if table == "assignments":
        plan = _row(connection, "plan_snapshots", row["plan_sha256"])
        body = _read(root, plan["relative_path"])[0] if plan else {}
        if ("coverage" in body or "_record" in body) and not graph._retirement_mutation(connection, packet, mutation):
            validate_assignment_intent(root, connection, row, allow_legacy_search=bool(before and row.get("state") == "completed"))
        if row.get("state") in {"completed", "released", "cancelled"}:
            _require(not _unfinished(root, connection, row["assignment_id"]), "Original worker operations are unresolved; raw transitions cannot authorize replacement", "pending_dependency")
        if row.get("state") in {"leased", "running"} and before:
            if before["state"] != "reconciling" and (row["state"] != before["state"] or row["lease_expires_at"] != before["lease_expires_at"]):
                _require(graph._timestamp(before["lease_expires_at"]) > _now(), "Expired original worker needs reconciliation before starting or renewal", "lease_conflict")
            _require(graph._timestamp(row["lease_expires_at"]) > _now(), "Active assignment needs an unexpired lease", "lease_conflict")
        return True
    if table == "work_items":
        if "capture_ids" in row.get("request_json", {}):
            ingestion.validate_capture_bindings(root, connection, row)
        if row.get("state") in {"pending", "completed", "blocked", "deferred", "cancelled"} or before and any(
                row.get(key) != before.get(key) for key in ("generation", "kind", "artifact_id", "question_id", "request_json", "purpose", "required_scope_key", "format_policy_sha256", "logical_key", "branch", "stage")):
            _require(not _work_unfinished(root, connection, row["work_id"]), "Original work admissions require reconciliation before releasing or changing its intent", "pending_dependency")
        if row.get("state") == "completed":
            return _completion(root, connection, row, correction_packet=packet)
        if row.get("state") in {"leased", "running"}:
            bundle.graph_component("research_followups").admission_check(root, connection, row)
            for old in connection.execute("SELECT assignment_id FROM assignments WHERE work_id=? AND state NOT IN ('leased','running','reconciling')", (row["work_id"],)):
                _require(not _unfinished(root, connection, old[0]), "Prior terminal assignment still has an unresolved original admission", "pending_dependency")
        return True
    if table != "operation_links":
        return ingestion.evidence_validator(root, connection, packet, mutation)
    original = _admission(root, row["operation_id"], bool(row.get("receipt_sha256")) or row["execution_state"] in {"succeeded", "failed"})
    # An existing invalid historical assignment may retire its original
    # admission; this exception cannot admit, dispatch, or accept new evidence.
    retiring = (before is not None and mutation["op"] == "update"
                and row["execution_state"] in {"unknown", "cancelled"}
                and packet["packet_kind"] == "reconcile"
                and _row(connection, "actors", packet["actor_id"])["role"] == "coordinator")
    _binding(root, connection, original["spec"], row["execution_state"] == "dispatched", retiring=retiring)
    expected = _operation_row(original, row["execution_state"])
    _require(all(row.get(key) == value for key, value in expected.items()), "Operation mirror differs from authoritative request/admission/receipt", "scope_mismatch")
    _require(original["event"]["generation"] == _row(connection, "work_items", _row(connection, "assignments", row["assignment_id"])["work_id"])["generation"], "Admission generation differs from original work", "scope_mismatch")
    if row["execution_state"] in {"succeeded", "failed"}:
        _require(row["execution_state"] == ingestion.receipt_execution_state(original["request"], original["receipt"]), "Receipt does not establish this terminal state or that the imported worker stopped", "scope_mismatch")
        if before and before["execution_state"] == "cancelled":
            _late_receipt(connection, original, before)
    else:
        _require(row["execution_state"] in {"admitted", "dispatched", "unknown", "cancelled"}, "Unverified reservation is not supported", "pending_dependency")
        _require(row["execution_state"] != "dispatched" or before and before["execution_state"] == "admitted", "Dispatch requires original admission", "pending_dependency")
        if row["execution_state"] == "dispatched":
            assigned = _row(connection, "assignments", row["assignment_id"])
            _require(not _work_unfinished(root, connection, assigned["work_id"], excluding=row["operation_id"]), "Earlier original work operations remain unresolved", "pending_dependency")
            _require(not (Path(root) / "operations" / row["operation_id"] / "receipt.json").exists(), "A retained receipt cannot authorize dispatch again", "pending_dependency")
        if row["execution_state"] == "cancelled":
            assigned = _row(connection, "assignments", row["assignment_id"])
            _require("receipt" not in original or ingestion.receipt_execution_state(original["request"], original["receipt"]) == "unknown", "A terminal receipt needs result reconciliation, not cancellation", "pending_dependency")
            reasons = [m.get("changes", {}).get("transition_reason", "") for m in packet["mutations"] if m["table"] == "work_items" and m.get("id") == assigned["work_id"]]
            try:
                proofs = [json.loads(reason[len("Worker reconciliation: "):]) for reason in reasons if isinstance(reason, str) and reason.startswith("Worker reconciliation: ")]
            except ValueError:
                raise GraphError("pending_dependency", "Original-worker reconciliation statement is invalid") from None
            actor = _row(connection, "actors", original["spec"]["actor"])
            _require(any(_stop_proof(p, original, actor) for p in proofs),
                     "Cancellation needs retained original-worker reconciliation", "pending_dependency")
    return True
