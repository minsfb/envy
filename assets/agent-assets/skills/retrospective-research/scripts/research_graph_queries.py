#!/usr/bin/env python3
"""Bounded, read-only fact queries over one research graph snapshot.

query(root, name, filters=None, limit=50, cursor=None) returns the G0 response
contract and raises research_graph.GraphError on invalid input. Items are flat
facts with a ``record_type`` discriminator; page size counts facts, not sources.
Related facts retain their IDs so no inventory, origin list or evidence list is
silently truncated inside a result. Follow next_cursor to exhaust a selection.

Filters (all optional except selectors marked *):
  artifact: artifact_id*, include=[metadata,coverage,references,assertions,work]
  neighbors: artifact_id*, direction=in|out|both, relation=observed|semantic|both,
             predicate, question_id, disposition
  frontier: branch, stage, question_id, role, max_generation
  leads: kind, disposition, question_id, unresolved_only=bool
  coverage: question_id*, branch, scope_key, format_policy_sha256
  evidence: claim_id*, revision (defaults to the current claim revision)
  changes: after_sequence=0, through_sequence

Artifact include sections can be queried independently, with independent cursors.
Neighbor predicate/disposition filters apply to semantic facts; observed links
have neither property. Leads also exposes unresolved references without a lead
registration, unless an explicit lead kind/disposition filter selects lead rows. A question filter retains observed references with that
known question or no assigned question. Scope matches are historical evidence,
never a freshness decision or permission to dispatch/spend. Coverage is an
inventory of questions, candidates, observations, work and gaps, not a percentage
inferred from the presence of citations. No document body or provider is read.
"""

import base64
import binascii
import hashlib
import json
import re

import research_graph


GraphError = research_graph.GraphError
_SECTIONS = ("metadata", "coverage", "references", "assertions", "work")
_FILTERS = {
    "artifact": {"artifact_id", "include"},
    "neighbors": {"artifact_id", "direction", "relation", "predicate", "question_id", "disposition"},
    "frontier": {"branch", "stage", "question_id", "role", "max_generation"},
    "leads": {"kind", "disposition", "question_id", "unresolved_only"},
    "coverage": {"question_id", "branch", "scope_key", "format_policy_sha256"},
    "evidence": {"claim_id", "revision"},
    "changes": {"after_sequence", "through_sequence"},
}
_DISPOSITIONS = {"proposed", "accepted", "challenged", "withdrawn", "superseded"}
_SORT = tuple("s" + str(i) for i in range(7))
_CURSOR_KEYS = {"version", "query", "filters", "run_id", "sequence", "last"}


def _require(value, message, code="invalid_packet", **details):
    if not value:
        raise GraphError(code, message, **details)


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _filters(name, filters):
    _require(type(name) is str and name in _FILTERS, "Unknown graph query")
    _require(filters is None or type(filters) is dict, "Query filters must be an object")
    f = dict(filters or {})
    _require(all(type(key) is str for key in f) and not set(f) - _FILTERS[name], "Unknown query filter")
    for key, value in f.items():
        if key in {"max_generation", "revision", "after_sequence", "through_sequence"}:
            _require(type(value) is int and (1 if key == "revision" else 0) <= value <= 2**63 - 1,
                     "Query integer filter is out of range")
        elif key == "unresolved_only":
            _require(type(value) is bool, "unresolved_only must be a boolean")
        elif key == "include":
            _require(type(value) is list and bool(value) and all(type(v) is str and v in _SECTIONS for v in value),
                     "include must be a nonempty list of artifact sections")
            f[key] = sorted(set(value))
        else:
            _require(type(value) is str and 0 < len(value) <= 1000 and value.strip(), "Query text filter is invalid")
    required = {"artifact": "artifact_id", "neighbors": "artifact_id", "coverage": "question_id", "evidence": "claim_id"}.get(name)
    _require(required is None or required in f, "Query requires its identity selector")
    if name == "artifact":
        f.setdefault("include", sorted(_SECTIONS))
    if name == "neighbors":
        f.setdefault("direction", "both")
        f.setdefault("relation", "both")
        _require(f["direction"] in {"in", "out", "both"}, "Invalid neighbor direction")
        _require(f["relation"] in {"observed", "semantic", "both"}, "Invalid neighbor relation")
        _require("disposition" not in f or f["disposition"] in _DISPOSITIONS, "Invalid assertion disposition")
    if name == "frontier":
        _require("stage" not in f or f["stage"] in {"baseline", "priority", "deep"}, "Invalid work stage")
    if name == "leads":
        f.setdefault("unresolved_only", False)
        _require("kind" not in f or f["kind"] in {"vocabulary", "linked_record", "neighborhood", "observed_gap", "inferred_gap"}, "Invalid lead kind")
        _require("disposition" not in f or f["disposition"] in {"pending", "queued", "investigating", "resolved", "bounded", "excluded", "deferred"}, "Invalid lead disposition")
    if "format_policy_sha256" in f:
        _require(re.fullmatch(r"[0-9a-f]{64}", f["format_policy_sha256"]) is not None, "Invalid format policy hash")
    if name == "changes":
        f.setdefault("after_sequence", 0)
        _require("through_sequence" not in f or f["through_sequence"] >= f["after_sequence"], "Event sequence range is reversed")
    return f


def _decode_cursor(cursor):
    _require(type(cursor) is str and len(cursor) <= 12000 and re.fullmatch(r"[A-Za-z0-9_-]+", cursor), "Malformed query cursor")
    try:
        value = json.loads(base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True))
    except (ValueError, UnicodeError, binascii.Error, RecursionError):
        raise GraphError("invalid_packet", "Malformed query cursor") from None
    _require(type(value) is dict and set(value) == _CURSOR_KEYS and type(value["version"]) is int and value["version"] == 1,
             "Malformed query cursor")
    _require(all(type(value[key]) is str for key in ("query", "filters", "run_id"))
             and type(value["sequence"]) is int and 0 <= value["sequence"] <= 2**63 - 1, "Malformed query cursor")
    last = value["last"]
    _require(type(last) is list and len(last) == 7 and all(type(last[i]) is int and -2**63 <= last[i] <= 2**63 - 1 for i in (0, 1, 2, 5))
             and all(type(last[i]) is str and len(last[i]) <= 4000 for i in (3, 4, 6)), "Malformed cursor sort key")
    return value


def _decode_fields(value):
    if isinstance(value, dict):
        return {key: json.loads(child) if key.endswith("_json") and isinstance(child, str) else _decode_fields(child)
                for key, child in value.items()}
    if isinstance(value, list):
        return [_decode_fields(child) for child in value]
    return value


class _Facts:
    """Build UNION branches from module-owned SQL only; values remain bound."""
    def __init__(self, conn, params):
        self.conn, self.params, self.parts, self.columns = conn, params, [], {}

    def obj(self, table, alias, extras=None):
        # table and alias are constants supplied only by the query builders below.
        if table not in self.columns:
            self.columns[table] = [r[1] for r in self.conn.execute("PRAGMA table_info(" + table + ")")]
        fields = {column: alias + "." + column for column in self.columns[table]}
        fields.update(extras or {})
        return "json_object(" + ",".join("'" + key + "'," + expression for key, expression in fields.items()) + ")"

    def add(self, kind, table, alias, key, joins="", where="1", extras=None, rank=0, sort=None):
        self.raw(kind, key, self.obj(table, alias, extras), table + " " + alias + " " + joins, where, rank, sort)

    def raw(self, kind, key, payload, source, where="1", rank=0, sort=None):
        order = sort or [str(rank), "0", "0", "''", "''", "0", key]
        self.parts.append("SELECT " + ",".join(expression + " AS " + column for expression, column in zip(order, _SORT))
                          + ", '" + kind + "' AS record_type, " + payload + " AS payload FROM " + source + " WHERE " + where)

    def page(self, limit, last):
        params = dict(self.params, _limit=limit + 1)
        predicate = "1"
        if last is not None:
            params.update({"_last" + str(i): v for i, v in enumerate(last)})
            predicate = "(" + ",".join(_SORT) + ") > (" + ",".join(":_last" + str(i) for i in range(7)) + ")"
        sql = "WITH facts AS (" + " UNION ALL ".join(self.parts) + ") SELECT * FROM facts WHERE " + predicate + " ORDER BY " + ",".join(_SORT) + " LIMIT :_limit"
        # This is the only fact materialization: at most page_size + 1 rows.
        rows = self.conn.execute(sql, params).fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
        items = [{"record_type": row["record_type"], **_decode_fields(json.loads(row["payload"]))} for row in rows]
        return items, [rows[-1][key] for key in _SORT] if has_more else None


def _locator(b, alias="loc", capture="cap"):
    return "CASE WHEN " + alias + ".locator_id IS NULL THEN NULL ELSE " + b.obj("locators", alias, {"capture": b.obj("captures", capture)}) + " END"


def _withdrawn(b, inspection_id):
    if "inspection_withdrawals" not in research_graph.storage_tables(b.conn):
        return "0"
    return "EXISTS(SELECT 1 FROM inspection_withdrawals iw WHERE iw.inspection_id=" + inspection_id + ")"


def _assertions(b, where, rank=30):
    joins = ("JOIN canonical_artifacts src ON src.artifact_id=a.subject_artifact_id "
             "LEFT JOIN canonical_artifacts dst ON dst.artifact_id=a.object_artifact_id "
             "LEFT JOIN locators sl ON sl.locator_id=a.subject_locator_id LEFT JOIN captures sc ON sc.capture_id=sl.capture_id "
             "LEFT JOIN locators ol ON ol.locator_id=a.object_locator_id LEFT JOIN captures oc ON oc.capture_id=ol.capture_id")
    b.add("assertion", "reconciled_assertions", "a", "a.assertion_id", joins, where,
          {"canonical_subject_id": "src.canonical_id", "canonical_object_id": "dst.canonical_id",
           "subject_locator": _locator(b, "sl", "sc"), "object_locator": _locator(b, "ol", "oc")}, rank)
    b.add("assertion_evidence", "assertion_evidence", "e", "e.evidence_id",
          "JOIN reconciled_assertions a USING(assertion_id) " + joins +
          " LEFT JOIN locators loc ON loc.locator_id=e.locator_id LEFT JOIN captures cap ON cap.capture_id=loc.capture_id "
          "LEFT JOIN inspections ins ON ins.inspection_id=e.inspection_id "
          "LEFT JOIN inspection_captures ic ON ic.inspection_id=e.inspection_id AND ic.capture_id=loc.capture_id", where,
          {"locator": _locator(b), "inspection": "CASE WHEN ins.inspection_id IS NULL THEN NULL ELSE " + b.obj("inspections", "ins") + " END",
           "inspection_capture": "CASE WHEN ic.inspection_id IS NULL THEN NULL ELSE " + b.obj("inspection_captures", "ic") + " END",
           "assertion_disposition": "a.disposition", "inspection_withdrawn": _withdrawn(b, "e.inspection_id")}, rank + 1)


def _references(b, where, rank=20):
    b.add("observed_reference", "effective_reference_targets", "r", "r.reference_id",
          "JOIN reference_occurrences original USING(reference_id) JOIN locators loc ON loc.locator_id=r.locator_id "
          "JOIN captures cap ON cap.capture_id=loc.capture_id JOIN reference_scans scan ON scan.scan_id=r.scan_id", where,
          {"extraction_method": "original.extraction_method", "created_batch_id": "original.created_batch_id", "locator": _locator(b),
           "scan": b.obj("reference_scans", "scan")}, rank)
    b.add("reference_resolution", "reference_resolutions", "h", "h.resolution_id",
          "JOIN effective_reference_targets r USING(reference_id)", where,
          {"current": "h.resolution_id=r.resolution_id", "effective_target_artifact_id": "r.target_artifact_id"}, rank + 1)


def _artifact(b, f):
    includes = f["include"]
    family = "c.canonical_id=:_canonical"
    if "metadata" in includes:
        b.add("artifact", "artifacts", "a", "a.artifact_id", "JOIN canonical_artifacts c USING(artifact_id)", family,
              {"canonical_id": "c.canonical_id"}, 0)
        for rank, (kind, table, key) in enumerate((("identity", "artifact_keys", "json_array(x.namespace,x.external_id)"),
                                                  ("version", "artifact_versions", "x.version_id"),
                                                  ("discovery", "discoveries", "x.discovery_id")), 1):
            b.add(kind, table, "x", key, "JOIN canonical_artifacts c USING(artifact_id)", family, {"canonical_id": "c.canonical_id"}, rank)
        # Lineage may name an assertion/inspection/claim rather than an artifact directly.
        lineage = ("LEFT JOIN assertions pa ON pa.assertion_id=p.assertion_id LEFT JOIN inspections pi ON pi.inspection_id=p.inspection_id "
                   "JOIN canonical_artifacts c ON c.artifact_id=COALESCE(p.artifact_id,pa.subject_artifact_id,pi.artifact_id) "
                   "JOIN prior_run_imports imp USING(import_id)")
        b.add("prior_run_record", "prior_run_records", "p", "p.record_id", lineage, family,
              {"import": b.obj("prior_run_imports", "imp"), "canonical_id": "c.canonical_id"}, 4)
        b.add("prior_run_import", "prior_run_imports", "imp", "imp.import_id",
              "JOIN canonical_artifacts c ON c.artifact_id=imp.source_artifact_id", family, rank=5)
    if "coverage" in includes:
        for rank, (kind, table, alias, key) in enumerate((("capture", "captures", "x", "x.capture_id"),
                                                        ("inspection", "inspections", "x", "x.inspection_id"),
                                                        ("source_outcome", "current_source_outcomes", "x", "x.outcome_id")), 10):
            extra = {"canonical_id": "c.canonical_id"}
            if table == "inspections":
                extra["withdrawn"] = _withdrawn(b, "x.inspection_id")
            b.add(kind, table, alias, key, "JOIN canonical_artifacts c USING(artifact_id)", family, extra, rank)
        b.add("inspection_scope", "inspection_scopes", "s", "json_array(s.inspection_id,s.question_id,s.required_scope_key,s.format_policy_sha256)",
              "JOIN inspections i USING(inspection_id) JOIN canonical_artifacts c USING(artifact_id)", family,
              {"canonical_id": "c.canonical_id", "provenance_basis": "i.provenance_basis", "withdrawn": _withdrawn(b, "i.inspection_id")}, 13)
        if "inspection_withdrawals" in research_graph.storage_tables(b.conn):
            b.add("inspection_withdrawal", "inspection_withdrawals", "iw", "iw.withdrawal_id",
                  "JOIN inspections i USING(inspection_id) JOIN canonical_artifacts c USING(artifact_id)", family, rank=16)
        b.add("inspection_capture", "inspection_captures", "ic", "json_array(ic.inspection_id,ic.capture_id)",
              "JOIN inspections i USING(inspection_id) JOIN canonical_artifacts c USING(artifact_id)", family, rank=14)
        b.add("reader_return", "reader_returns", "rr", "rr.return_id",
              where="EXISTS(SELECT 1 FROM inspections i JOIN canonical_artifacts c USING(artifact_id) WHERE i.return_id=rr.return_id AND " + family + ")", rank=15)
    if "references" in includes:
        _references(b, "r.from_artifact_id=:_canonical")
        b.add("reference_scan", "reference_scans", "s", "s.scan_id", "JOIN captures cap USING(capture_id) JOIN canonical_artifacts c USING(artifact_id)", family, rank=22)
    if "assertions" in includes:
        _assertions(b, "src.canonical_id=:_canonical")
    if "work" in includes:
        where = "EXISTS(SELECT 1 FROM canonical_artifacts c WHERE c.artifact_id=w.artifact_id AND c.canonical_id=:_canonical)"
        b.add("work", "work_items", "w", "w.work_id", where=where, rank=40)
        b.add("work_origin", "work_origins", "o", "o.origin_id", "JOIN work_items w USING(work_id)", where, rank=41)
        b.add("assignment", "assignments", "a", "a.assignment_id", "JOIN work_items w USING(work_id)", where, rank=42)
        b.add("operation", "operation_links", "op", "op.operation_id", "JOIN assignments a USING(assignment_id) JOIN work_items w USING(work_id)", where, rank=43)


def _neighbors(b, f):
    directions = {"out": "r.from_artifact_id=:_canonical", "in": "r.target_artifact_id=:_canonical",
                  "both": "(r.from_artifact_id=:_canonical OR r.target_artifact_id=:_canonical)"}
    if f["relation"] in {"observed", "both"}:
        where = directions[f["direction"]]
        if "question_id" in f:
            where += (" AND (EXISTS(SELECT 1 FROM obligation_question_scopes qs JOIN followup_obligations fo USING(obligation_id) "
                      "WHERE fo.reference_id=r.reference_id AND qs.question_id=:question_id) OR NOT EXISTS(SELECT 1 FROM obligation_question_scopes qs "
                      "JOIN followup_obligations fo USING(obligation_id) WHERE fo.reference_id=r.reference_id))")
        _references(b, where)
    if f["relation"] in {"semantic", "both"}:
        where = {"out": "src.canonical_id=:_canonical", "in": "dst.canonical_id=:_canonical",
                 "both": "(src.canonical_id=:_canonical OR dst.canonical_id=:_canonical)"}[f["direction"]]
        where = "a.kind='relationship' AND " + where
        for key in ("predicate", "question_id", "disposition"):
            if key in f:
                where += " AND a." + key + "=:" + key
        _assertions(b, where)


def _frontier(b, f):
    where = "1"
    for key in ("branch", "stage", "question_id"):
        if key in f:
            where += " AND w." + key + "=:" + key
    if "max_generation" in f:
        where += " AND w.generation<=:max_generation"
    if "role" in f:
        # Do not turn a proposed/challenged or differently scoped role into an accepted whole-source role.
        where += (" AND EXISTS(SELECT 1 FROM artifact_roles ar JOIN canonical_artifacts ac ON ac.canonical_id=ar.artifact_id "
                  "WHERE ac.artifact_id=w.artifact_id AND ar.role=:role AND (ar.question_id=w.question_id OR ar.question_id IS NULL))")
    order = ["CASE w.stage WHEN 'baseline' THEN 0 WHEN 'priority' THEN 1 ELSE 2 END", "w.generation", "-w.priority", "w.branch", "w.work_id"]
    def add(kind, table, alias, key, joins="", extra=None, rank=0, condition=where):
        b.add(kind, table, alias, key, joins, condition, extra, sort=order + [str(rank), key])
    add("work", "pending_frontier", "w", "w.work_id", extra={"canonical_artifact_id": "(SELECT canonical_id FROM canonical_artifacts WHERE artifact_id=w.artifact_id)"})
    add("work_origin", "work_origins", "o", "o.origin_id", "JOIN pending_frontier w USING(work_id)", rank=1)
    matching = ("JOIN canonical_artifacts wc ON wc.canonical_id=s.artifact_id JOIN pending_frontier w ON w.artifact_id=wc.artifact_id "
                "AND w.question_id=s.question_id AND w.required_scope_key=s.required_scope_key AND w.format_policy_sha256=s.format_policy_sha256")
    add("historical_scope_match", "sufficient_inspection", "s", "s.inspection_id", matching,
        {"work_id": "w.work_id", "request_json": "w.request_json", "freshness_checked": "json('false')"}, 2)
    add("source_outcome", "current_source_outcomes", "o", "o.outcome_id",
        "JOIN canonical_artifacts oc ON oc.artifact_id=o.artifact_id JOIN canonical_artifacts wc ON wc.canonical_id=oc.canonical_id "
        "JOIN pending_frontier w ON w.artifact_id=wc.artifact_id AND w.question_id=o.question_id "
        "AND (o.required_scope_key=w.required_scope_key OR o.required_scope_key IS NULL)", {"work_id": "w.work_id"}, 3)
    add("role_assessment", "artifact_roles", "ar", "ar.assertion_id",
        "JOIN canonical_artifacts wc ON wc.canonical_id=ar.artifact_id JOIN pending_frontier w ON w.artifact_id=wc.artifact_id "
        "AND (ar.question_id=w.question_id OR ar.question_id IS NULL)", {"work_id": "w.work_id"}, 4)
    add("operation", "operation_links", "op", "op.operation_id", "JOIN assignments a USING(assignment_id) JOIN pending_frontier w USING(work_id)",
        {"work_id": "w.work_id", "assignment_state": "a.state"}, 5)


def _lead_where(f):
    where = "1"
    for key in ("kind", "disposition"):
        if key in f:
            where += " AND l." + key + "=:" + key
    if "question_id" in f:
        where += (" AND EXISTS(SELECT 1 FROM obligation_question_scopes qs JOIN followup_obligations fo USING(obligation_id) "
                  "WHERE fo.lead_id=l.lead_id AND qs.question_id=:question_id)")
    if f.get("unresolved_only"):
        where += (" AND (l.disposition IN ('pending','queued','investigating') OR EXISTS(SELECT 1 FROM followup_obligations fo "
                  "LEFT JOIN obligation_scope_status qs USING(obligation_id) WHERE fo.lead_id=l.lead_id "
                  "AND (fo.state<>'terminal' OR qs.status IN ('unassigned','pending') OR qs.question_id IS NULL)))")
    return where


def _leads(b, f):
    where = _lead_where(f)
    b.add("lead", "leads", "l", "l.lead_id", where=where)
    b.add("lead_origin", "lead_origins", "o", "o.origin_id", "JOIN leads l USING(lead_id)", where, rank=1)
    b.add("obligation", "followup_obligations", "o", "o.obligation_id", "JOIN leads l USING(lead_id)", where, rank=2)
    b.add("obligation_scope", "obligation_scope_status", "s", "json_array(s.obligation_id,s.question_id)",
          "JOIN followup_obligations o USING(obligation_id) JOIN leads l USING(lead_id)", where, rank=3)
    b.add("work_origin", "work_origins", "wo", "wo.origin_id", "JOIN leads l USING(lead_id) JOIN work_items w USING(work_id)", where,
          {"work": b.obj("work_items", "w")}, 4)
    reference_where = "1"
    if "kind" in f or "disposition" in f:
        reference_where = "EXISTS(SELECT 1 FROM lead_origins lo JOIN leads l USING(lead_id) WHERE lo.reference_id=r.reference_id AND " + where + ")"
    elif "question_id" in f:
        reference_where = ("(EXISTS(SELECT 1 FROM followup_obligations fo JOIN obligation_question_scopes qs USING(obligation_id) "
                           "WHERE fo.reference_id=r.reference_id AND qs.question_id=:question_id) OR NOT EXISTS(SELECT 1 FROM followup_obligations fo "
                           "JOIN obligation_question_scopes qs USING(obligation_id) WHERE fo.reference_id=r.reference_id))")
    b.add("unresolved_reference", "unresolved_references", "r", "r.reference_id", where=reference_where, rank=5)
    b.add("reference_obligation", "followup_obligations", "fo", "fo.obligation_id", "JOIN unresolved_references r USING(reference_id)", reference_where, rank=7)
    b.add("reference_obligation_scope", "obligation_scope_status", "qs", "json_array(qs.obligation_id,qs.question_id)",
          "JOIN followup_obligations fo USING(obligation_id) JOIN unresolved_references r USING(reference_id)", reference_where, rank=8)
    b.add("reference_work_origin", "work_origins", "wo", "wo.origin_id", "JOIN unresolved_references r USING(reference_id) JOIN work_items w USING(work_id)",
          reference_where, {"work": b.obj("work_items", "w")}, 9)
    b.add("prior_run_record", "prior_run_records", "p", "p.record_id", "JOIN leads l USING(lead_id) JOIN prior_run_imports imp USING(import_id)",
          where, {"import": b.obj("prior_run_imports", "imp")}, 6)


def _coverage(b, f):
    work_condition = "w.question_id=:question_id"
    for key, column in (("branch", "branch"), ("scope_key", "required_scope_key"), ("format_policy_sha256", "format_policy_sha256")):
        if key in f:
            work_condition += " AND w." + column + "=:" + key
    # Candidate selection is deliberately inclusive; conflicting/excluding assessments
    # are returned separately rather than interpreted as global truth by this query.
    candidate = ("(EXISTS(SELECT 1 FROM work_items w JOIN canonical_artifacts wc ON wc.artifact_id=w.artifact_id "
                 "WHERE wc.canonical_id=c.canonical_id AND " + work_condition + ") "
                 "OR EXISTS(SELECT 1 FROM reconciled_assertions a JOIN canonical_artifacts ac ON ac.artifact_id=a.subject_artifact_id "
                 "WHERE ac.canonical_id=c.canonical_id AND a.question_id=:question_id) "
                 "OR EXISTS(SELECT 1 FROM discoveries d JOIN canonical_artifacts dc ON dc.artifact_id=d.artifact_id "
                 "LEFT JOIN operation_links dop ON dop.operation_id=d.operation_id LEFT JOIN assignments da ON da.assignment_id=dop.assignment_id "
                 "LEFT JOIN work_items dw ON dw.work_id=da.work_id WHERE dc.canonical_id=c.canonical_id "
                 "AND (d.supplied_seed_id IS NOT NULL OR d.prior_import_id IS NOT NULL OR dw.question_id=:question_id)) "
                 "OR EXISTS(SELECT 1 FROM source_outcomes so JOIN canonical_artifacts oc ON oc.artifact_id=so.artifact_id "
                 "WHERE oc.canonical_id=c.canonical_id AND so.question_id=:question_id) "
                 "OR EXISTS(SELECT 1 FROM inspections ci JOIN inspection_scopes cis USING(inspection_id) JOIN canonical_artifacts ic ON ic.artifact_id=ci.artifact_id "
                 "WHERE ic.canonical_id=c.canonical_id AND cis.question_id=:question_id) "
                 "OR EXISTS(SELECT 1 FROM effective_reference_targets r JOIN followup_obligations fo USING(reference_id) "
                 "JOIN obligation_question_scopes qs USING(obligation_id) WHERE r.target_artifact_id=c.canonical_id AND qs.question_id=:question_id))")
    if "branch" in f:
        candidate += (" AND EXISTS(SELECT 1 FROM work_items w JOIN canonical_artifacts wc ON wc.artifact_id=w.artifact_id "
                      "WHERE wc.canonical_id=c.canonical_id AND w.question_id=:question_id AND w.branch=:branch)")
    b.add("question", "questions", "q", "q.question_id", where="q.question_id=:question_id")
    scope = "s.question_id=:question_id"
    for key, column in (("scope_key", "required_scope_key"), ("format_policy_sha256", "format_policy_sha256")):
        if key in f:
            scope += " AND s." + column + "=:" + key
    root_basis = ("EXISTS(SELECT 1 FROM discoveries d JOIN canonical_artifacts dc USING(artifact_id) WHERE dc.canonical_id=c.canonical_id AND d.supplied_seed_id IS NOT NULL) "
                  "OR EXISTS(SELECT 1 FROM work_items w JOIN canonical_artifacts wc USING(artifact_id) WHERE wc.canonical_id=c.canonical_id AND w.question_id=:question_id AND w.generation=0)")
    b.add("candidate_artifact", "artifacts", "a", "a.artifact_id", "JOIN canonical_artifacts c USING(artifact_id)",
          "a.artifact_id=c.canonical_id AND " + candidate,
          {"is_root": "(" + root_basis + ")", "historical_scope_match_count": "(SELECT COUNT(*) FROM sufficient_inspection s WHERE s.artifact_id=c.canonical_id AND " + scope + ")",
           "freshness_checked": "json('false')"}, 1)
    _assertions(b, "(a.question_id=:question_id OR a.question_id IS NULL) AND EXISTS(SELECT 1 FROM canonical_artifacts c WHERE c.artifact_id=a.subject_artifact_id AND " + candidate + ")", 2)
    b.add("inspection_scope", "inspection_scopes", "s", "json_array(s.inspection_id,s.question_id,s.required_scope_key,s.format_policy_sha256)",
          "JOIN inspections i USING(inspection_id) JOIN canonical_artifacts c USING(artifact_id) JOIN reader_returns rr USING(return_id)",
          scope + " AND " + candidate,
          {"canonical_artifact_id": "c.canonical_id", "inspection": b.obj("inspections", "i"), "return_verification": "rr.verification_state",
           "withdrawn": _withdrawn(b, "i.inspection_id")}, 4)
    ref_where = ("EXISTS(SELECT 1 FROM followup_obligations fo JOIN obligation_question_scopes qs USING(obligation_id) "
                 "WHERE fo.reference_id=r.reference_id AND qs.question_id=:question_id)")
    if "branch" in f:
        ref_where += " AND EXISTS(SELECT 1 FROM canonical_artifacts c WHERE c.artifact_id=r.from_artifact_id AND " + candidate + ")"
    _references(b, ref_where, 5)
    obligation_where = ("(EXISTS(SELECT 1 FROM obligation_question_scopes qs WHERE qs.obligation_id=o.obligation_id AND qs.question_id=:question_id) "
                        "OR NOT EXISTS(SELECT 1 FROM obligation_question_scopes qs WHERE qs.obligation_id=o.obligation_id))")
    if "branch" in f:
        obligation_where += (" AND (EXISTS(SELECT 1 FROM work_origins wo JOIN work_items w USING(work_id) WHERE "
                             "(wo.lead_id=o.lead_id OR wo.reference_id=o.reference_id) AND w.question_id=:question_id AND w.branch=:branch) "
                             "OR EXISTS(SELECT 1 FROM effective_reference_targets r JOIN canonical_artifacts c ON c.artifact_id=r.from_artifact_id "
                             "WHERE r.reference_id=o.reference_id AND " + candidate + "))")
    b.add("obligation", "followup_obligations", "o", "o.obligation_id", where=obligation_where,
          extras={"question_id": "s.question_id", "scope_status": "COALESCE(s.status,'unassigned')"},
          joins="LEFT JOIN obligation_scope_status s ON s.obligation_id=o.obligation_id AND s.question_id=:question_id", rank=7)
    lead_condition = _lead_where({"question_id": f["question_id"]})
    if "branch" in f:
        lead_condition += (" AND (EXISTS(SELECT 1 FROM work_origins wo JOIN work_items w USING(work_id) WHERE wo.lead_id=l.lead_id "
                           "AND w.question_id=:question_id AND w.branch=:branch) OR EXISTS(SELECT 1 FROM lead_origins lo "
                           "JOIN locators ll USING(locator_id) JOIN captures lc USING(capture_id) JOIN canonical_artifacts c ON c.artifact_id=lc.artifact_id "
                           "WHERE lo.lead_id=l.lead_id AND lo.question_id=:question_id AND " + candidate + "))")
    b.add("lead", "leads", "l", "l.lead_id", where=lead_condition, rank=8)
    b.add("work", "work_items", "w", "w.work_id", where=work_condition, rank=9)
    b.add("work_origin", "work_origins", "wo", "wo.origin_id", "JOIN work_items w USING(work_id)", work_condition,
          {"question_id": "w.question_id"}, 10)
    outcome_where = "o.question_id=:question_id AND " + candidate
    if "scope_key" in f:
        outcome_where += " AND (o.required_scope_key=:scope_key OR o.required_scope_key IS NULL)"
    b.add("source_outcome", "current_source_outcomes", "o", "o.outcome_id", "JOIN canonical_artifacts c USING(artifact_id)", outcome_where,
          {"canonical_artifact_id": "c.canonical_id"}, 11)
    counts = "(SELECT w.branch,COUNT(*) AS total," + ",".join("SUM(w.state='" + state + "') AS " + state for state in ("pending", "leased", "running", "completed", "blocked", "deferred", "cancelled")) + " FROM work_items w WHERE " + work_condition + " GROUP BY w.branch) counts"
    b.raw("branch_counts", "counts.branch", "json_object('branch',counts.branch,'total',counts.total," + ",".join("'" + state + "',counts." + state for state in ("pending", "leased", "running", "completed", "blocked", "deferred", "cancelled")) + ")", counts, rank=12)
    claim_condition = "EXISTS(SELECT 1 FROM claim_questions cq WHERE cq.claim_id=p.claim_id AND cq.question_id=:question_id)"
    if "branch" in f:
        claim_condition += (" AND EXISTS(SELECT 1 FROM claim_evidence ce JOIN locators cl USING(locator_id) JOIN captures cc USING(capture_id) "
                            "JOIN canonical_artifacts c ON c.artifact_id=cc.artifact_id WHERE ce.claim_id=p.claim_id AND " + candidate + ")")
    b.add("prior_run_record", "prior_run_records", "p", "p.record_id",
          "LEFT JOIN assertions pa ON pa.assertion_id=p.assertion_id LEFT JOIN inspections pi ON pi.inspection_id=p.inspection_id "
          "LEFT JOIN canonical_artifacts c ON c.artifact_id=COALESCE(p.artifact_id,pa.subject_artifact_id,pi.artifact_id) JOIN prior_run_imports imp USING(import_id)",
          candidate + " OR EXISTS(SELECT 1 FROM leads l WHERE l.lead_id=p.lead_id AND " + lead_condition + ") OR (" + claim_condition + ")",
          {"import": b.obj("prior_run_imports", "imp")}, 13)


def _evidence(b, f):
    b.add("claim", "claim_revisions", "cr", "json_array(cr.claim_id,cr.revision)",
          "JOIN claims current USING(claim_id)", "cr.claim_id=:claim_id AND cr.revision=:_revision",
          {"current_revision": "current.revision", "current_status": "current.status"})
    b.add("claim_question", "claim_questions", "cq", "json_array(cq.claim_id,cq.claim_revision,cq.question_id)",
          where="cq.claim_id=:claim_id AND cq.claim_revision=:_revision", rank=1)
    b.add("claim_evidence", "claim_evidence", "e", "e.evidence_id",
          "LEFT JOIN locators loc ON loc.locator_id=e.locator_id LEFT JOIN captures cap ON cap.capture_id=loc.capture_id "
          "LEFT JOIN reconciled_assertions a ON a.assertion_id=e.assertion_id",
          "e.claim_id=:claim_id AND e.claim_revision=:_revision",
          {"locator": _locator(b), "assertion": "CASE WHEN a.assertion_id IS NULL THEN NULL ELSE " + b.obj("reconciled_assertions", "a") + " END"}, 2)
    _assertions(b, "EXISTS(SELECT 1 FROM claim_evidence ce WHERE ce.claim_id=:claim_id AND ce.claim_revision=:_revision AND ce.assertion_id=a.assertion_id)", 3)
    b.add("prior_run_record", "prior_run_records", "p", "p.record_id",
          "JOIN prior_run_imports imp USING(import_id) LEFT JOIN inspections pi ON pi.inspection_id=p.inspection_id "
          "LEFT JOIN canonical_artifacts pc ON pc.artifact_id=COALESCE(p.artifact_id,pi.artifact_id)",
          "p.claim_id=:claim_id OR EXISTS(SELECT 1 FROM claim_evidence ce WHERE ce.claim_id=:claim_id AND ce.claim_revision=:_revision AND ce.assertion_id=p.assertion_id) "
          "OR EXISTS(SELECT 1 FROM claim_evidence ce JOIN locators cl ON cl.locator_id=ce.locator_id JOIN captures cc ON cc.capture_id=cl.capture_id "
          "JOIN canonical_artifacts ec ON ec.artifact_id=cc.artifact_id WHERE ce.claim_id=:claim_id AND ce.claim_revision=:_revision AND ec.canonical_id=pc.canonical_id)",
          {"import": b.obj("prior_run_imports", "imp")}, 5)


def _changes(b, f):
    b.add("event", "graph_events", "e", "CAST(e.sequence AS TEXT)", "JOIN ingest_batches batch USING(batch_id)",
          "e.sequence>:after_sequence AND e.sequence<=:_through", {"batch": b.obj("ingest_batches", "batch")},
          sort=["e.sequence", "0", "0", "''", "''", "0", "CAST(e.sequence AS TEXT)"])


_BUILDERS = {"artifact": _artifact, "neighbors": _neighbors, "frontier": _frontier,
             "leads": _leads, "coverage": _coverage, "evidence": _evidence, "changes": _changes}


def query(root, name, filters=None, limit=50, cursor=None):
    """Return a bounded page from a consistent snapshot; never mutate or dispatch."""
    f = _filters(name, filters)
    _require(type(limit) is int and 1 <= limit <= 200, "Page size must be an integer in 1..200")
    previous = None if cursor is None else _decode_cursor(cursor)
    digest = hashlib.sha256(_json(f).encode("utf-8")).hexdigest()
    if previous is not None:
        _require(previous["query"] == name and previous["filters"] == digest, "Cursor belongs to a different query or filter selection")
    with research_graph.snapshot(root) as conn:
        metadata = conn.execute("SELECT run_id,schema_version FROM graph_metadata WHERE singleton=1").fetchone()
        sequence = conn.execute("SELECT COALESCE(MAX(sequence),0) FROM graph_events").fetchone()[0]
        if previous is not None:
            _require(previous["run_id"] == metadata["run_id"] and previous["sequence"] == sequence,
                     "Graph changed since this cursor was issued; restart the query", "stale_cursor")
        params = dict(f)
        if "artifact_id" in f:
            canonical = conn.execute("SELECT canonical_id FROM canonical_artifacts WHERE artifact_id=?", (f["artifact_id"],)).fetchone()
            _require(canonical is not None, "Artifact identity is unknown", "unknown_identity", artifact_id=f["artifact_id"])
            params["_canonical"] = canonical[0]
        if "question_id" in f:
            _require(conn.execute("SELECT 1 FROM questions WHERE question_id=?", (f["question_id"],)).fetchone() is not None,
                     "Question identity is unknown", "unknown_identity", question_id=f["question_id"])
        if name == "evidence":
            current = conn.execute("SELECT revision FROM claims WHERE claim_id=?", (f["claim_id"],)).fetchone()
            _require(current is not None, "Claim identity is unknown", "unknown_identity", claim_id=f["claim_id"])
            params["_revision"] = f.get("revision", current[0])
            _require(conn.execute("SELECT 1 FROM claim_revisions WHERE claim_id=? AND revision=?", (f["claim_id"], params["_revision"])).fetchone() is not None,
                     "Retained claim revision is unknown", "unknown_identity", claim_id=f["claim_id"], revision=params["_revision"])
        if name == "changes":
            params["_through"] = min(f.get("through_sequence", sequence), sequence)
        # Lists affect builder selection, never SQL values.
        params.pop("include", None)
        b = _Facts(conn, params)
        _BUILDERS[name](b, f)
        items, last = b.page(limit, previous["last"] if previous else None)
        next_cursor = None
        if last is not None:
            token = {"version": 1, "query": name, "filters": digest, "run_id": metadata["run_id"], "sequence": sequence, "last": last}
            next_cursor = base64.urlsafe_b64encode(_json(token).encode("utf-8")).decode("ascii").rstrip("=")
        limits = {"filters": f, "page_size": limit, "page_unit": "facts", "snapshot_bound": True,
                  "scope_matches_prove_freshness": False, "dispatch_authorized": False}
        if name == "coverage":
            limits["coverage_basis"] = "Recorded candidates, scoped observations and obligations; citations do not establish domain completeness"
        return {"status": "pass", "schema_version": metadata["schema_version"], "run_id": metadata["run_id"],
                "graph_sequence": sequence, "command": name, "items": items, "next_cursor": next_cursor, "limits": limits}
