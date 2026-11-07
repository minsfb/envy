#!/usr/bin/env python3
"""One-sequence graph exports, historical migration, and bounded prior reuse.

Exports are generated views, never a second writable ledger. Compatibility
ledger fields retain original recorder source/read/return IDs; graph-only scope,
provenance and pending obligations are additional fields. An export can be valid
while research and compatibility validation remain incomplete.

Public entrypoints: export_snapshot(root, export_id), verify_export(directory),
validate_ledger(root, ledger),
migrate_legacy(source_root, destination_root, *, run_id, actor_id, ledger_path=None),
import_prior(root, export_directory, import_id, selection).

Prior reuse requires the current contract's explicit prior_research policy:
{policy: allow|exclude, excluded_run_ids: [], excluded_export_ids: [],
 excluded_artifact_ids: [], excluded_artifact_keys: [{namespace,external_id}]}.
Selection is bounded: {policy: leads_only|attested_evidence, artifact_ids: [],
 record_ids: {assertion: [], inspection: [], lead: [], claim: []},
 question_map: {old_question: current_question}, reason: nonempty text}.
Prior inspections remain prior_run; previous conclusions never become fresh
inspection or independent corroboration. No provider or budget dispatch occurs.
Source identity is deduplicated by original provider keys, including known
canonical aliases. Other records are scoped to an explicit import, because its
question mappings may differ; their original lineage identifies repeated reuse.
Only declared references are retained: this module adds no document extraction.
"""

import importlib.util
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile


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
GraphError = graph.GraphError
_require = graph._require
_bytes = graph._bytes
_sha = graph._sha
_id = ingestion._id
_insert = ingestion._insert
MAX_IMPORT_RECORDS = 5000
HISTORICAL = "Historical evidence only; scope, access and interpretations require current-run reconciliation."


def _path(root, relative, exists=True):
    _require(isinstance(relative, str) and relative and not Path(relative).is_absolute()
             and ".." not in Path(relative).parts and str(Path(relative)) == relative, "integrity_error", "Invalid manifest-relative path")
    path = root / relative
    _require(path.resolve().is_relative_to(root.resolve()), "integrity_error", "Manifest path leaves its retained directory")
    current = path
    while current != root:
        _require(not current.is_symlink(), "integrity_error", "Symlinks are not retained export evidence")
        current = current.parent
    _require(not exists or path.is_file(), "pending_dependency", "Retained manifest file is missing", path=relative)
    return path


def _read(root, relative):
    data = _path(root, relative).read_bytes()
    return graph._read_json(data), _sha(data)


def _save(path, data):
    ingestion._save(path, data)


def _stamp(connection):
    return {"schema_version": 1, "run_id": connection.execute("SELECT run_id FROM graph_metadata").fetchone()[0],
            "graph_sequence": graph._head(connection)}


def _all(connection, table):
    order = ",".join(graph._pk(table))
    return [graph._decoded(row) for row in connection.execute("SELECT * FROM " + table + " ORDER BY " + order)]


def _budget_head(root):
    if not (root / "research-budget.json").exists():
        return None
    events = ingestion._journal(root)
    return events[-1]["sha256"] if events else None


def _originals(root, tables):
    """Select retained proof by graph records, avoiding export/checkpoint recursion."""
    paths = {"contract.json", "bundle.json"}
    if (root / "research-budget.json").exists():
        paths.add("research-budget.json")
    for name in ("plan.json", "plan.md"):
        if (root / name).is_file():
            paths.add(name)
    for row in tables["contract_snapshots"]:
        paths.add(row["relative_path"])
    for row in tables["plan_snapshots"]:
        paths.update((row["relative_path"], "plans/" + row["plan_sha256"] + ".ledger.json"))
    for row in tables["captures"]:
        paths.add(row["relative_path"])
        if row["relative_path"].startswith("graph/imports/"):
            paths.add(str(Path(row["relative_path"]).parent / "receipt.json"))
    paths.update(row["file_path"] for row in tables["reader_returns"])
    paths.update(row["evidence_path"] for row in tables.get("inspection_withdrawals", []))
    paths.update(row["packet_path"] for row in tables["ingest_batches"])
    # Retain incomplete original recorder writes too. They remain limitations;
    # an export never manufactures their missing receipts or admissions.
    for directory in (root / "operations", root / "reader-returns", root / "followup-checkpoints"):
        if directory.is_dir():
            for path in directory.rglob("*"):
                if path.is_file():
                    paths.add(str(path.relative_to(root)))
    for directory in (root / "migration", root / "graph/imports", root / "graph/normalizations", root / "graph/updates"):
        if directory.is_dir():
            for path in directory.rglob("*"):
                if path.is_file():
                    paths.add(str(path.relative_to(root)))
    return sorted(paths)


def _operations(root):
    result = {}
    directory = root / "operations"
    if directory.is_dir():
        for operation in sorted(directory.iterdir()):
            if operation.is_dir() and (operation / "request.json").is_file():
                spec = bundle.read(operation / "request.json").get("spec", {})
                receipt = bundle.read(operation / "receipt.json") if (operation / "receipt.json").is_file() else None
                index = bundle.read(operation / "result.json") if (operation / "result.json").is_file() else None
                result[operation.name] = (spec, receipt, index)
    return result


def _lead_scopes(tables, lead_id):
    """The lead-only portion of the frozen obligation_scope_status SQL view."""
    ids = {row["work_id"] for row in tables["work_origins"] if row["lead_id"] == lead_id}
    works = [row for row in tables["work_items"] if row["work_id"] in ids]
    questions = {row["question_id"] for row in tables["lead_origins"] if row["lead_id"] == lead_id}
    questions |= {row["question_id"] for row in works}
    scopes = []
    for question in sorted(questions):
        relevant = [row for row in works if row["question_id"] == question]
        states = {row["state"] for row in relevant}
        status = ("unassigned" if not states else "pending" if states & {"pending", "leased", "running"}
                  else "bounded" if states & {"blocked", "deferred", "cancelled"} else "resolved")
        scopes.append({"question_id": question, "status": status,
                       "work": [{"work_id": row["work_id"], "state": row["state"], "reason": row["terminal_reason"] or row["transition_reason"]} for row in relevant]})
    return scopes


def _display_metadata(discoveries, artifact, operations, accepted_operations, sequences):
    """Prefer retained successful observations, never discovery-ID ordering."""
    def order(row):
        operation = row.get("operation_id")
        _, receipt, _ = operations.get(operation, (None, None, None))
        accepted = accepted_operations.get(operation, {})
        current = bool(receipt and receipt.get("outcome") == "ok"
                       and accepted.get("execution_state") == "succeeded"
                       and accepted.get("receipt_sha256") and not row.get("prior_import_id"))
        finished = 0
        if current and receipt.get("finished"):
            try:
                parsed = datetime.fromisoformat(receipt["finished"].replace("Z", "+00:00"))
                if parsed.utcoffset() is not None:
                    finished = parsed.timestamp()
            except (ValueError, TypeError):
                pass  # Older imports may lack usable retrieval-time metadata.
        return (current, finished, sequences.get(row["created_batch_id"], 0),
                -(row.get("rank") or 0), row["discovery_id"])

    ordered = sorted(discoveries, key=order, reverse=True)
    selected = {field: next((row for row in ordered if row.get("observed_" + field)), None)
                for field in ("title", "url")}
    title = selected["title"]["observed_title"] if selected["title"] else artifact.get("title")
    url = selected["url"]["observed_url"] if selected["url"] else None
    provenance = {field: row["discovery_id"] if row else None for field, row in selected.items()}
    return title, url, provenance


def _withdrawal_impact(tables):
    """Keep original facts visible while withholding their invalid read credit.

    A fresh inspection can support a direct locator again, but it does not
    endorse an earlier assertion whose declared evidence was withdrawn.
    """
    withdrawn = {r["inspection_id"] for r in tables.get("inspection_withdrawals", [])}
    if not withdrawn:
        return set(), set(), set()
    inspections = {r["inspection_id"]: r for r in tables["inspections"]}
    affected_assertions = {r["assertion_id"] for r in tables["assertion_evidence"]
                           if r["inspection_id"] in withdrawn}
    affected_captures = {r["capture_id"] for r in tables["inspection_captures"]
                         if r["inspection_id"] in withdrawn}
    live_extents = {}
    for row in tables["inspection_captures"]:
        if row["inspection_id"] not in withdrawn and inspections[row["inspection_id"]]["provenance_basis"] == "native":
            live_extents.setdefault(row["capture_id"], []).append(row["inspected_extent_json"])
    locators = {r["locator_id"]: r for r in tables["locators"]}

    def reinspected(locator):
        return any(graph.locator_within_extent(locator["locator_kind"], locator["locator_json"], extent) is True
                   for extent in live_extents.get(locator["capture_id"], []))

    claims = {r["claim_id"]: r for r in tables["claims"]}
    sequences = {r["batch_id"]: r["resulting_sequence"] for r in tables["ingest_batches"]}
    withdrawal_sequence = {r["inspection_id"]: sequences[r["created_batch_id"]]
                           for r in tables.get("inspection_withdrawals", [])}
    capture_withdrawn_at = {}
    for row in tables["inspection_captures"]:
        if row["inspection_id"] in withdrawn:
            capture_withdrawn_at[row["capture_id"]] = max(capture_withdrawn_at.get(row["capture_id"], 0),
                                                         withdrawal_sequence[row["inspection_id"]])
    affected_claims = set()
    for row in tables["claim_evidence"]:
        claim = claims[row["claim_id"]]
        if row["claim_revision"] != claim["revision"]:
            continue
        locator = locators[row["locator_id"]]
        if (row["assertion_id"] in affected_assertions
                or locator["capture_id"] in affected_captures and (
                    not reinspected(locator) or sequences[claim["updated_batch_id"]] <= capture_withdrawn_at[locator["capture_id"]])):
            affected_claims.add(row["claim_id"])
    return withdrawn, affected_assertions, affected_claims


def _compatibility_ledger(root, tables, stamp):
    limits = []
    canonical = {}
    artifacts = {row["artifact_id"]: row for row in tables["artifacts"]}
    for aid in artifacts:
        current = aid
        while artifacts[current]["canonical_artifact_id"]:
            current = artifacts[current]["canonical_artifact_id"]
        canonical[aid] = current
    keys = {}
    for row in tables["artifact_keys"]:
        if row["namespace"] == "bundle-source:" + stamp["run_id"]:
            keys[row["external_id"]] = canonical[row["artifact_id"]]
    original_operations = _operations(root)
    accepted_operations = {row["operation_id"]: row for row in tables["operation_links"]}
    operations = {key: value for key, value in original_operations.items() if key in accepted_operations}
    sequences = {row["batch_id"]: row["resulting_sequence"] for row in tables["ingest_batches"]}
    if set(original_operations) - set(operations):
        limits.append("Original operations remain outside this graph sequence and are retained as external evidence; synchronize them before treating this inventory as complete.")
    for operation, (spec, receipt, index) in original_operations.items():
        if any(sid not in keys for sid in (index or {}).get("source_ids", [])):
            limits.append("Original result " + operation + " has source IDs not normalized at this graph sequence; its complete original index is retained.")
    represented = set(keys.values())
    for aid in sorted(set(canonical.values()) - represented):
        keys[aid] = aid
    returns = {row["return_id"]: row for row in tables["reader_returns"]}
    prior_batches = {row["imported_batch_id"] for row in tables["prior_run_imports"] if row["policy"] != "legacy_attested"}
    prior_returns = {row["return_id"] for row in returns.values() if row["created_batch_id"] in prior_batches}
    captures = {row["capture_id"]: row for row in tables["captures"]}
    locators = {row["locator_id"]: row for row in tables["locators"]}
    inspections = {row["inspection_id"]: row for row in tables["inspections"]}
    withdrawn, affected_assertions, affected_claims = _withdrawal_impact(tables)
    lead_by_id = {row["lead_id"]: row for row in tables["leads"]}
    obligation_by_id = {row["obligation_id"]: row for row in tables["followup_obligations"]}
    decisions = {}
    for decision in tables["assertion_decisions"]:
        if decision["expected_revision"] >= decisions.get(decision["assertion_id"], {}).get("expected_revision", -1):
            decisions[decision["assertion_id"]] = decision
    source_rows = []
    for sid, aid in sorted(keys.items()):
        artifact = artifacts.get(aid, {})
        discovery_rows = [r for r in tables["discoveries"] if canonical[r["artifact_id"]] == aid]
        title, url, metadata_provenance = _display_metadata(discovery_rows, artifact, operations, accepted_operations, sequences)
        found = [op for op, (_, _, index) in operations.items() if sid in (index or {}).get("source_ids", [])]
        if not found:
            # A reference, prior import, or supplied seed can exist without an
            # indexed provider result. Point to its retained graph provenance;
            # a compatibility projection must not manufacture a supplied seed.
            found = ["graph:" + aid]
        reads = [op for op, (spec, receipt, _) in operations.items() if spec.get("kind") == "read"
                 and spec["request"].get("source_id") == sid and receipt and receipt.get("outcome") == "ok"
                 and accepted_operations[op]["execution_state"] == "succeeded" and accepted_operations[op]["receipt_sha256"]]
        inspected, expansion_reviews = [], {}
        for inspection in inspections.values():
            if (canonical[inspection["artifact_id"]] != aid or inspection["provenance_basis"] == "prior_run"
                    or inspection["inspection_id"] in withdrawn):
                continue
            returned = returns[inspection["return_id"]]
            original, _ = _read(root, returned["file_path"])
            raw = original.get("return", {})
            read_id = raw.get("read_id")
            if read_id in reads and original.get("source_id") == sid:
                inspected.append({"read_id": read_id, "return_id": returned["return_id"], "actor": raw.get("actor", inspection["actor_id"]),
                                  "coverage": inspection["inspection_extent"], "graph_inspection_id": inspection["inspection_id"],
                                  "provenance_basis": inspection["provenance_basis"], "limitations": inspection["limitations"]})
                review = raw.get("expansion_review")
                if isinstance(review, str) and review.strip():
                    expansion_reviews[returned["return_id"]] = dict(return_id=returned["return_id"], read_id=read_id,
                        actor=raw.get("actor", inspection["actor_id"]), text=review,
                        provenance_basis=inspection["provenance_basis"])
        assertions = [r for r in tables["assertions"] if canonical[r["subject_artifact_id"]] == aid]
        accepted = [r for r in assertions if decisions.get(r["assertion_id"], {}).get("disposition") == "accepted"
                    and r["assertion_id"] not in affected_assertions]
        findings = [r["value_json"]["text"] for r in accepted if r["kind"] == "finding"]
        contributions = [r["value_json"]["text"] for r in accepted if r["kind"] == "contribution"]
        complete = bool(inspected and findings)
        source = dict(id=sid, title=title or "Unknown title (original source ID: " + sid + ")",
                      url=url or "Not recorded; see graph identity keys.",
                      coverage="; ".join(i["coverage"] for i in inspected) or "No current-run inspection attested; capture and prior evidence remain separate.",
                      finding="; ".join(findings) or "No accepted source finding; proposed and historical assertions remain in the companion.",
                      contributor_basis="; ".join(contributions) or "No accepted contributor evidence.",
                      discovered_by=found, reads=reads, inspections=inspected,
                      analysis={"status": "complete" if complete else "pending", "reason": "Accepted inspected source finding." if complete else "Current source interpretation has not been accepted."},
                      disposition={"status": "pending", "reason": "Current scoped dispositions are retained separately; no whole-source disposition inferred."},
                      leads=[], graph_artifact_id=aid, graph_sequence=stamp["graph_sequence"],
                      graph_assertion_ids=[r["assertion_id"] for r in assertions])
        source["graph_metadata_discoveries"] = metadata_provenance
        source["graph_withdrawn_inspection_ids"] = sorted(iid for iid in withdrawn
            if canonical[inspections[iid]["artifact_id"]] == aid)
        # Preserve actual reader follow-up assessments, including their identity.
        # These remain attestations; neither accepted findings nor a successful
        # fetch invent a missing review. Unaccepted/prior returns add no credit.
        source["graph_expansion_reviews"] = [expansion_reviews[key] for key in sorted(expansion_reviews)]
        if expansion_reviews:
            source["expansion_review"] = "\n".join(
                row["return_id"] + " (" + row["actor"] + "): " + row["text"] for row in source["graph_expansion_reviews"])
        seen = set()
        for origin in tables["lead_origins"]:
            locator = locators.get(origin["locator_id"])
            captured = captures.get(locator["capture_id"]) if locator else None
            if not captured or canonical[captured["artifact_id"]] != aid:
                continue
            lead = lead_by_id[origin["lead_id"]]
            token = (origin["return_id"], origin["returned_lead_id"]) if origin["return_id"] else (None, lead["lead_id"])
            if token in seen:
                continue
            seen.add(token)
            raw = None
            historical_return = origin["return_id"] in prior_returns
            if origin["return_id"]:
                original, _ = _read(root, returns[origin["return_id"]]["file_path"])
                if not historical_return and original.get("source_id") != sid:
                    continue
                raw = next((r for r in original.get("return", {}).get("leads", []) if r["id"] == origin["returned_lead_id"]), None)
            questions = [scope["question_id"] for scope in _lead_scopes(tables, lead["lead_id"])]
            exported = dict(id=lead["lead_id"], kind=(raw or {}).get("kind", {"linked_record": "linked-record", "observed_gap": "question-gap", "inferred_gap": "question-gap"}.get(lead["kind"], lead["kind"])),
                            value=(raw or {}).get("value", lead["original_value"]), locator=(raw or {}).get("locator", json.dumps(locator["locator_json"], ensure_ascii=False)),
                            questions=questions, reason=lead["reason"] or lead["significance"],
                            status="pending", graph_lead_id=lead["lead_id"], graph_obligation_id="lead:" + lead["lead_id"], return_refs=[])
            exported["graph_disposition"] = lead["disposition"]
            exported["graph_obligation_state"] = obligation_by_id[exported["graph_obligation_id"]]["state"]
            if raw:
                # Historical returns retain their source wording and origins,
                # but cannot bind to this run's native budget/return journal.
                field = "graph_prior_return_refs" if historical_return else "return_refs"
                exported[field] = [{"return_id": origin["return_id"], "lead_id": raw["id"]}]
            source["leads"].append(exported)
        if source["leads"]:
            limits.append("Pending graph leads require verified obligation-aware plan validation; no destination or bound was invented.")
        source_rows.append(source)
    operation_proofs = _operation_proofs(tables, lambda path: _read(root, path))
    claims = []
    inspected_evidence = graph.inspected_claim_evidence(tables)
    for claim in tables["claims"]:
        if claim["claim_id"] in affected_claims:
            if claim["status"] == "accepted":
                limits.append("Claim " + claim["claim_id"] + " depends on withdrawn inspection evidence; retained in graph companion only.")
            continue
        evidence = [r for r in tables["claim_evidence"] if r["claim_id"] == claim["claim_id"] and r["claim_revision"] == claim["revision"]]
        support = [row for row in evidence if row["stance"] in {"support", "qualify"}]
        if (claim["basis"] == "documented" and claim["status"] == "accepted"
                and (not support or any(row["evidence_id"] not in inspected_evidence for row in support))):
            limits.append("Claim " + claim["claim_id"] + " lacks current inspected locator support; retained in graph companion only.")
            continue
        read_ids, positions = set(), []
        for row in evidence:
            locator = locators[row["locator_id"]]
            capture = captures[locator["capture_id"]]
            proof = operation_proofs.get(capture["operation_id"])
            if (row["evidence_id"] in inspected_evidence and proof and proof["kind"] == "read"
                    and proof["outcome"] == "ok" and proof["execution_state"] == "succeeded"):
                read_ids.add(capture["operation_id"])
            positions.append(json.dumps({"locator_id": row["locator_id"], "position": locator["locator_json"], "stance": row["stance"]}, ensure_ascii=False))
        # Only the current accepted revision belongs in a synthesis projection.
        # Conflicting and historical evidence remains in the frozen graph.
        if claim["status"] == "accepted" and read_ids:
            claims.append(dict(id=claim["claim_id"], text=claim["text"], basis=claim["basis"], reads=sorted(read_ids),
                               locator="; ".join(positions), graph_status=claim["status"], graph_revision=claim["revision"]))
        elif claim["status"] == "accepted":
            limits.append("Claim " + claim["claim_id"] + " has no current-run supporting reads; retained in graph companion only.")
    revisions = {c["id"]: c["graph_revision"] for c in claims}
    assignments = {a["assignment_id"]: a for a in tables["assignments"]}
    work = {w["work_id"]: w for w in tables["work_items"]}
    questions = [dict(id=q["question_id"], status=q["current_status"], reason=q["reason"] or "Research remains open.",
                      claims=sorted({r["claim_id"] for r in tables["claim_questions"] if r["question_id"] == q["question_id"]
                                     and revisions.get(r["claim_id"]) == r["claim_revision"] and r["relationship"] in {"answers", "partly_answers"}}),
                      followups=sorted(op for op, proof in operation_proofs.items()
                                       if _terminal_followup(proof) and work[assignments[proof["assignment_id"]]["work_id"]]["question_id"] == q["question_id"]))
                 for q in tables["questions"]]
    return {"sources": source_rows, "claims": claims, "questions": questions,
            "_graph": dict(stamp, generated=True, read_only=True, limitations=sorted(set(limits)))}, limits


def _verify_generated_ledger(directory):
    """Verify a first registration against only the retained graph and evidence.

    Existing hash-bound registrations keep their original renderer's bytes.
    Unregistered files cannot acquire generated-view authority from a manifest
    whose ledger digest was merely recomputed after an edit.
    """
    directory = Path(directory)
    snapshot, _ = _read(directory, "graph.json")
    stamp = {key: snapshot[key] for key in ("schema_version", "run_id", "graph_sequence")}
    expected, _ = _compatibility_ledger(directory / "evidence", snapshot["tables"], stamp)
    _require(_path(directory, "ledger.json").read_bytes() == _bytes(expected), "integrity_error",
             "Unregistered ledger differs from the generated frozen graph projection")
    return True


def _readable(stamp, tables, ingestion_status=None, ledger=None):
    lines = ["# Research graph companion", "", "Run: " + stamp["run_id"], "Sequence: " + str(stamp["graph_sequence"]), "",
             "Capture, inspection, prior evidence and accepted interpretation are separate records.", "", "## Sources", ""]
    titles = {row["graph_artifact_id"]: row["title"] for row in (ledger or {}).get("sources", [])}
    for row in tables["artifacts"]:
        lines.append("- " + row["artifact_id"] + ": " + (titles.get(row["artifact_id"]) or row["title"] or "Unknown title") + " (" + row["artifact_type"] + ")")
    if tables.get("inspection_withdrawals"):
        lines.extend(["", "## Withdrawn inspection credit", ""])
        for row in tables["inspection_withdrawals"]:
            lines.append("- " + row["inspection_id"] + ": " + row["reason"] + "; original preserved, coverage and dependent evidence withheld.")
    lines.extend(["", "## References", ""])
    for row in tables["reference_occurrences"]:
        lines.append("- " + row["reference_id"] + ": " + row["from_artifact_id"] + " → " + (row["target_artifact_id"] or "unresolved") + "; " + row["kind"] + ": " + row["original_value"])
    lines.extend(["", "## Questions and unfinished work", ""])
    for row in tables["questions"]:
        lines.append("- " + row["question_id"] + ": " + row["text"] + " [" + row["current_status"] + "]")
    for row in tables["work_items"]:
        lines.append("- " + row["work_id"] + ": " + row["kind"] + " / " + row["branch"] + " / " + row["state"] + "; " + row["purpose"])
    for row in tables["leads"]:
        lines.append("- " + row["lead_id"] + ": " + row["original_value"] + " [" + row["disposition"] + "]; " + row["significance"])
    lines.extend(["", "## Claims and scope", ""])
    for row in tables["claims"]:
        lines.append("- " + row["claim_id"] + " revision " + str(row["revision"]) + ": " + row["text"] + " [" + row["status"] + "; " + row["basis"] + "]")
    if ingestion_status is not None:
        lines.extend(["", "## Original evidence synchronization", ""])
        for item in ingestion_status["items"]:
            if item["state"] != "complete":
                lines.append("- " + item["kind"] + " " + item["original_id"] + ": " + item["state"])
        lines.append("Rejected originals and their unaccepted references remain in ingestion.json; accepted corrections preserve their lineage and do not add inspection credit.")
    lines.extend(["", "Exact scopes, evidence stances, corrections, historical provenance and every occurrence are retained in the JSON inventories.", ""])
    return "\n".join(lines).encode("utf-8")


def _file_entry(relative, data, format_name):
    return {"relative_path": relative, "sha256": _sha(data), "format": format_name, "size_bytes": len(data)}


def _prior_operation_prefix(tables, operation):
    imported = next((r for r in tables["prior_run_imports"] if r["imported_batch_id"] == operation["created_batch_id"] and r["policy"] != "legacy_attested"), None)
    return ("graph/imports/" + imported["import_id"] + "/operations/" if imported else "operations/") + operation["operation_id"] + "/"


def _verified_ingestion(directory, manifest, tables):
    """Recheck correction authority using only this export's retained bytes."""
    entries = {entry["relative_path"]: entry for entry in manifest["files"]}
    if "ingestion.json" not in entries:
        # Older exports carry no correction authority or synchronization claim.
        return None
    status, _ = _read(directory, "ingestion.json")
    _require(all(status.get(k) == manifest[k] for k in ("schema_version", "run_id", "graph_sequence")),
             "integrity_error", "Original synchronization inventory is from another snapshot")
    _require(isinstance(status.get("immutable_receipts"), dict), "integrity_error", "Original synchronization receipts are missing")
    for relative, expected in status["immutable_receipts"].items():
        copied = "evidence/" + relative
        _require(copied in entries and entries[copied]["sha256"] == expected
                 and _sha(_path(directory, copied).read_bytes()) == expected,
                 "integrity_error", "Original synchronization receipt is missing or changed", path=copied)
    connection = _frozen_connection(tables)
    try:
        verified = ingestion.ingestion_status(directory / "evidence", connection=connection,
                                             status_version=status.get("status_version", 1))
    finally:
        connection.close()
    _require(status == verified, "integrity_error", "Original synchronization inventory differs from its retained proof")
    return status


def _verify_export(directory):
    directory = Path(directory).resolve()
    manifest, digest = _read(directory, "manifest.json")
    _require(manifest.get("schema_version") == 1 and isinstance(manifest.get("files"), list), "unsupported_schema", "Unsupported export manifest")
    graph._id(manifest["export_id"], filename=True)
    graph._id(manifest["run_id"])
    _require(type(manifest["graph_sequence"]) is int and manifest["graph_sequence"] > 0, "integrity_error", "Invalid represented graph sequence")
    seen = set()
    for entry in manifest["files"]:
        relative = entry["relative_path"]
        _require(relative not in seen and relative != "manifest.json", "integrity_error", "Duplicate or self-referential manifest file")
        seen.add(relative)
        data = _path(directory, relative).read_bytes()
        _require(type(entry["size_bytes"]) is int and len(data) == entry["size_bytes"] and _sha(data) == entry["sha256"], "integrity_error", "Export file hash or size changed", path=relative)
    _require({"graph.json", "ledger.json", "sources.json", "references.json", "worklist.json", "schema.sql", "companion.md", "evidence/contract.json", "evidence/bundle.json"} <= seen,
             "integrity_error", "Export is missing a required inventory")
    snapshot, _ = _read(directory, "graph.json")
    for key in ("schema_version", "run_id", "graph_sequence"):
        _require(snapshot.get(key) == manifest[key], "integrity_error", "Inventory is from a different graph snapshot")
    _require(_sha(_path(directory, "schema.sql").read_bytes()) == manifest["schema_sha256"], "integrity_error", "Export schema hash differs")
    tables = snapshot.get("tables", {})
    revision = graph.schema_revision_from_tables(tables)
    _require(manifest["schema_sha256"] == _sha(graph.schema_bytes(revision)), "unsupported_schema", "Export schema is not the supported frozen schema")
    _require(set(tables) == graph.storage_tables(tables) and all(isinstance(rows, list) and all(isinstance(row, dict) for row in rows) for rows in tables.values()),
             "integrity_error", "Export table inventory is incomplete or malformed")
    metadata = tables["graph_metadata"]
    _require(len(metadata) == 1 and metadata[0]["run_id"] == manifest["run_id"] and metadata[0]["schema_version"] == manifest["schema_version"],
             "integrity_error", "Graph metadata disagrees with the export")
    sequences = [event["sequence"] for event in tables["graph_events"]]
    _require(len(sequences) == manifest["graph_sequence"] and all(value == number for number, value in enumerate(sequences, 1)), "integrity_error", "Export audit sequence is incomplete")
    entries = {entry["relative_path"]: entry for entry in manifest["files"]}

    def bound(relative, expected):
        path = "evidence/" + relative
        _require(path in entries and entries[path]["sha256"] == expected, "integrity_error", "Export evidence disagrees with its graph record", path=path)

    for row in tables["contract_snapshots"]:
        bound(row["relative_path"], row["contract_sha256"])
    for row in tables["plan_snapshots"]:
        bound(row["relative_path"], row["plan_sha256"])
        saved, _ = _read(directory, "evidence/" + row["relative_path"])
        bound("plans/" + row["plan_sha256"] + ".ledger.json", saved["_record"]["ledger_sha256"])
    for row in tables["captures"]:
        bound(row["relative_path"], row["sha256"])
        receipt = str(Path(row["relative_path"]).parent / "receipt.json") if row["relative_path"].startswith("graph/imports/") else "operations/" + row["operation_id"] + "/receipt.json"
        bound(receipt, row["receipt_sha256"])
    for row in tables["reader_returns"]:
        bound(row["file_path"], row["payload_sha256"])
    for row in tables.get("inspection_withdrawals", []):
        bound(row["evidence_path"], row["evidence_sha256"])
    for row in tables["operation_links"]:
        prefix = _prior_operation_prefix(tables, row)
        if row["request_sha256"]:
            bound(prefix + "request.json", row["request_sha256"])
        if row["receipt_sha256"]:
            bound(prefix + "receipt.json", row["receipt_sha256"])
    for name in ("sources.json", "references.json", "worklist.json"):
        inventory, _ = _read(directory, name)
        _require(all(inventory.get(k) == manifest[k] for k in ("schema_version", "run_id", "graph_sequence")), "integrity_error", "Export inventories disagree on their snapshot")
    ledger, _ = _read(directory, "ledger.json")
    _require(all(ledger.get("_graph", {}).get(k) == manifest[k] for k in ("schema_version", "run_id", "graph_sequence")), "integrity_error", "Compatibility ledger is from another snapshot")
    _require(_sha(_path(directory, "evidence/contract.json").read_bytes()) == manifest["contract_sha256"], "integrity_error", "Export current contract differs")
    original_metadata, _ = _read(directory, "evidence/bundle.json")
    _require(original_metadata["contract_sha256"] == manifest["contract_sha256"] and original_metadata.get("plan_sha256") == manifest.get("plan_sha256"),
             "integrity_error", "Export recorder metadata disagrees with its contract or saved plan")
    if manifest.get("plan_sha256"):
        plan = "evidence/plans/" + manifest["plan_sha256"] + ".json"
        _require(plan in seen and _sha(_path(directory, plan).read_bytes()) == manifest["plan_sha256"], "integrity_error", "Export saved plan is not retained by hash")
    _require(_budget_head(directory / "evidence") == manifest.get("budget_event_head"), "integrity_error", "Export budget head disagrees with its retained journal")
    _verified_ingestion(directory, manifest, tables)
    return manifest, snapshot, digest


def verify_export(directory):
    manifest, snapshot, digest = ingestion._safe(_verify_export, directory)
    return {"status": "pass", "command": "verify-export", "schema_version": 1, "run_id": manifest["run_id"],
            "graph_sequence": manifest["graph_sequence"], "export_id": manifest["export_id"], "manifest_sha256": digest,
            "files": len(manifest["files"]), "limitations": manifest.get("limitations", [])}


def _accepted_worker_stop(tables, operation, request, receipt, request_hash, receipt_hash):
    """Find the original-bound coordinator attestation in this frozen history.

    Hashes bind provenance. They do not authenticate a remote process check.
    The accepted batch time bounds the attestation without consulting live time.
    """
    assignments = {r["assignment_id"]: r for r in tables["assignments"]}
    actors = {r["actor_id"]: r for r in tables["actors"]}
    batches = {r["batch_id"]: r for r in tables["ingest_batches"]}
    assigned = assignments[operation["assignment_id"]]
    actor = actors[assigned["actor_id"]]
    worker = actor["declared_runtime_identity"] or actor["actor_id"]
    earliest = graph._timestamp(receipt.get("finished", request["started"]))
    for event in tables["graph_events"]:
        batch = batches[event["batch_id"]]
        if (event["entity_kind"] != "work_items" or event["entity_id"] != graph._json(assigned["work_id"])
                or actors[batch["actor_id"]]["role"] != "coordinator"):
            continue
        reason = event["payload_json"].get("after", {}).get("transition_reason", "")
        if not isinstance(reason, str) or not reason.startswith("Worker reconciliation: "):
            continue
        try:
            proof = graph._read_json(reason[len("Worker reconciliation: "):].encode("utf-8"))
            checked = graph._timestamp(proof.get("checked_at"))
        except (GraphError, AttributeError, TypeError):
            continue
        if (proof.get("operation_id") == operation["operation_id"] and proof.get("request_sha256") == request_hash
                and proof.get("receipt_sha256") == receipt_hash and proof.get("worker_identity") == worker
                and proof.get("worker_stopped") is True and isinstance(proof.get("reason"), str) and proof["reason"].strip()
                and earliest <= checked <= graph._timestamp(batch["accepted_at"])):
            return dict(statement=proof, event_sequence=event["sequence"], batch_id=batch["batch_id"],
                        accepted_at=batch["accepted_at"], coordinator_actor_id=batch["actor_id"], work_id=assigned["work_id"])
    return None


def _terminal_followup(proof):
    return bool(proof["receipt_sha256"]) and proof["execution_state"] in {"succeeded", "failed", "cancelled"} and proof["worker_termination_confirmed"]


def _operation_proofs(tables, read):
    """Frozen native receipt and termination facts; never infer a new dispatch."""
    proofs = {}
    for row in tables["operation_links"]:
        if row["verification_state"] != "verified" or not row["receipt_sha256"] and row["execution_state"] != "cancelled":
            continue
        base = "operations/" + row["operation_id"] + "/"
        request, request_hash = read(base + "request.json")
        receipt, receipt_hash = read(base + "receipt.json") if row["receipt_sha256"] else ({}, None)
        spec = request["spec"]
        _require(request_hash == row["request_sha256"] and receipt_hash == row["receipt_sha256"]
                 and (not receipt or receipt["files"]["request.json"] == request_hash) and spec["id"] == row["operation_id"]
                 and spec.get("graph_assignment_id") == row["assignment_id"], "integrity_error", "Frozen operation proof differs from its accepted graph binding")
        receipt_state = ingestion.receipt_execution_state(request, receipt) if receipt else None
        stop_proof = _accepted_worker_stop(tables, row, request, receipt, request_hash, receipt_hash) if receipt_state in {None, "unknown"} else None
        terminated = receipt_state not in {None, "unknown"} or stop_proof is not None
        limitations = []
        if not receipt:
            limitations.append("Cancelled request only; no provider result or executed follow-up credit. Missing output does not prove a tool was never invoked.")
        if receipt_state == "unknown":
            limitations.append("Imported interruption/timeout records lost observation; the receipt alone does not establish worker termination.")
        if stop_proof:
            limitations.append("Worker termination is coordinator-attested with hash-bound provenance; no independent process authentication is claimed.")
        proofs[row["operation_id"]] = dict(operation_id=row["operation_id"], assignment_id=row["assignment_id"],
            kind=spec["kind"], source_id=spec["request"].get("source_id"), outcome=receipt.get("outcome"),
            execution_state=row["execution_state"], request_sha256=request_hash, receipt_sha256=receipt_hash,
            receipt_execution_state=receipt_state, worker_termination_confirmed=terminated,
            termination_basis="receipt" if receipt_state not in {None, "unknown"} else "coordinator_attestation" if stop_proof else "unresolved",
            worker_stop_proof=stop_proof, limitations=limitations)
    return proofs


def _frozen_connection(tables):
    """Evaluate shipped views over trusted registered rows in private memory.

    No exported SQL is executed. Trigger-derived rows are already in the
    accepted snapshot, so replaying mutation triggers here would duplicate them.
    """
    allowed = graph.storage_tables(tables)
    _require(set(tables) == allowed, "integrity_error", "Unsupported frozen table inventory")
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    try:
        connection.executescript(graph.schema_bytes(graph.schema_revision_from_tables(tables)).decode("utf-8"))
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall():
            connection.execute('DROP TRIGGER "' + row[0] + '"')
        for table in sorted(allowed):
            connection.execute("DELETE FROM " + table)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(" + table + ")")}
            for record in tables[table]:
                _require(set(record) == columns, "integrity_error", "Unsupported frozen row shape", table=table)
                graph._insert(connection, table, record)
        _require(not connection.execute("PRAGMA foreign_key_check").fetchall(), "integrity_error", "Frozen snapshot has broken references")
        connection.execute("PRAGMA query_only=ON")
        return connection
    except BaseException:
        connection.close()
        raise


def _inspection_capture_bindings(tables):
    captures = {row["capture_id"]: row for row in tables["captures"]}
    bindings = {}
    for row in tables["inspection_captures"]:
        bindings.setdefault(row["inspection_id"], []).append(
            dict(captures[row["capture_id"]], inspected_extent_json=row["inspected_extent_json"]))
    return bindings


def _retained_plan_completions(tables, retained_file, operation_proofs):
    """Bind a retained-only plan result to this snapshot's current reading.

    No new retrieval is needed. Original plan definitions, latest assignments,
    native returns and effective inspections supply the completion evidence.
    Imported history and withdrawn readings cannot complete current plan work.
    """
    batches = {r["batch_id"]: r for r in tables["ingest_batches"]}
    returns = {r["return_id"]: r for r in tables["reader_returns"]}
    plans = {r["plan_sha256"]: r for r in tables["plan_snapshots"]}
    withdrawn = {r["inspection_id"] for r in tables.get("inspection_withdrawals", [])}
    capture_bindings = _inspection_capture_bindings(tables)
    latest = {}
    for assigned in tables["assignments"]:
        old = latest.get(assigned["work_id"])
        if old is None or (batches[assigned["created_batch_id"]]["resulting_sequence"], assigned["assignment_id"]) > (
                batches[old["created_batch_id"]]["resulting_sequence"], old["assignment_id"]):
            latest[assigned["work_id"]] = assigned
    result, saved_plans = [], {}
    for work in tables["work_items"]:
        pins = work["request_json"].get("capture_ids")
        assigned = latest.get(work["work_id"])
        if work["kind"] != "read" or work["state"] != "completed" or not pins or not assigned or assigned["state"] != "completed":
            continue
        if any(batches[row["created_batch_id"]]["packet_kind"] in {"import", "migrate"} for row in (work, assigned)):
            continue
        inspected = []
        for inspection in tables["inspections"]:
            iid = inspection["inspection_id"]
            returned = returns[inspection["return_id"]]
            if (iid in withdrawn or inspection["provenance_basis"] != "native" or returned["verification_state"] != "verified"
                    or returned["assignment_id"] != assigned["assignment_id"]
                    or batches[returned["created_batch_id"]]["packet_kind"] in {"import", "migrate"}):
                continue
            if not any(scope["inspection_id"] == iid and scope["coverage"] == "sufficient" and all(
                    scope[key] == work[key] for key in ("question_id", "required_scope_key", "format_policy_sha256"))
                    for scope in tables["inspection_scopes"]):
                continue
            selected = graph.satisfying_read_captures(work["request_json"], assigned["claimed_at"],
                                                     inspection["inspected_at"], capture_bindings.get(iid, []))
            if not selected or not all(operation_proofs.get(c["operation_id"], {}).get("outcome") == "ok" for c in selected):
                continue
            inspected.append(iid)
        if not inspected:
            continue
        key = assigned["plan_sha256"]
        if key not in saved_plans:
            saved_plans[key], _ = retained_file(plans[key]["relative_path"])
        item = next(i for i in saved_plans[key]["items"] if i["id"] == assigned["plan_item_id"])
        annotations = dict(item.get("graph", {}))
        intent = work["request_json"]
        if (item["actor"] != assigned["actor_id"] or item["kind"] != "read" or
                item["request"]["source_id"] != intent["source_id"] or annotations.get("extent") != intent["extent"] or
                sorted(set(annotations.get("formats", [item["request"]["format"]]))) != intent["formats"]):
            continue
        annotations.pop("freshness_basis", None)
        annotations.pop("version_id", None)
        annotations["capture_ids"] = sorted(pins)
        result.append(dict(work_id=work["work_id"], question_id=work["question_id"], assignment_id=assigned["assignment_id"],
                           plan_sha256=key, inspection_ids=sorted(inspected), item=dict(item, graph=annotations)))
    return result


def _scope_bindings(tables, ledger, operation_proofs):
    """Structural stopping facts, not semantic adequacy or current freshness."""
    connection = _frozen_connection(tables)
    try:
        canonical = {r["artifact_id"]: r["canonical_id"] for r in connection.execute("SELECT * FROM canonical_artifacts")}
        outcome_tails = [graph._decoded(r) for r in connection.execute("SELECT * FROM current_source_outcomes ORDER BY outcome_id")]
        scope_rows = [dict(r) for r in connection.execute("SELECT * FROM obligation_scope_status ORDER BY obligation_id, question_id")]
        targets = {r["reference_id"]: r["target_artifact_id"] for r in connection.execute("SELECT * FROM effective_reference_targets")}
    finally:
        connection.close()
    work = {r["work_id"]: r for r in tables["work_items"]}
    assignments = {r["assignment_id"]: r for r in tables["assignments"]}
    operations = {r["operation_id"]: r for r in tables["operation_links"]}
    batches = {r["batch_id"]: r for r in tables["ingest_batches"]}
    locators = {r["locator_id"]: r for r in tables["locators"]}
    captures = {r["capture_id"]: r for r in tables["captures"]}
    inspections = {r["inspection_id"]: r for r in tables["inspections"]}
    withdrawn, affected_assertions, affected_claims = _withdrawal_impact(tables)
    references = {r["reference_id"]: r for r in tables["reference_occurrences"]}
    pending_work = {"pending", "leased", "running"}
    bound_work = {"blocked", "deferred", "cancelled"}
    stops = dict(pending_work_ids=sorted(wid for wid, w in work.items() if w["state"] in pending_work),
        active_assignment_ids=sorted(aid for aid, a in assignments.items() if a["state"] in {"leased", "running", "reconciling"}),
        unresolved_operation_ids=sorted(oid for oid, op in operations.items() if op["verification_state"] != "legacy_attested"
            and (op["execution_state"] in {"reserved", "admitted", "dispatched", "interrupted", "unknown"}
                 or op["execution_state"] in {"succeeded", "failed", "cancelled"} and oid not in operation_proofs
                 or oid in operation_proofs and not operation_proofs[oid]["worker_termination_confirmed"])),
        open_question_ids=sorted(q["question_id"] for q in tables["questions"] if q["current_status"] in {"open", "reopened"}),
         nonterminal_obligation_ids=[], unassigned_obligation_ids=[], unresolved_obligation_scopes=[],
         unassessed_source_ids=[], unresolved_source_scopes=[], conflicting_source_scopes=[])
    if withdrawn:
        latest_decisions = {}
        for decision in tables["assertion_decisions"]:
            old = latest_decisions.get(decision["assertion_id"])
            if old is None or decision["expected_revision"] > old["expected_revision"]:
                latest_decisions[decision["assertion_id"]] = decision
        stops["withdrawn_evidence_accepted_assertion_ids"] = sorted(aid for aid in affected_assertions
            if latest_decisions.get(aid, {}).get("disposition") == "accepted")
        stops["withdrawn_evidence_accepted_claim_ids"] = sorted(c["claim_id"] for c in tables["claims"]
            if c["status"] == "accepted" and c["claim_id"] in affected_claims)
        returns = {r["return_id"]: r for r in tables["reader_returns"]}
        impacted_work = {assignments[r["assignment_id"]]["work_id"] for iid in withdrawn
                         for r in [returns[inspections[iid]["return_id"]]] if r["assignment_id"] in assignments}
        invalidated = []
        capture_bindings = _inspection_capture_bindings(tables)
        for wid in sorted(impacted_work):
            w = work[wid]
            if w["state"] != "completed" or w["kind"] not in {"read", "inspect_visuals"}:
                continue
            latest = max((a for a in assignments.values() if a["work_id"] == wid),
                         key=lambda a: (batches[a["created_batch_id"]]["resulting_sequence"], a["assignment_id"]))
            valid = False
            for scope in tables["inspection_scopes"]:
                iid = scope["inspection_id"]
                inspection = inspections[iid]
                returned = returns[inspection["return_id"]]
                if (iid in withdrawn or scope["coverage"] != "sufficient"
                        or any(scope[key] != w[key] for key in ("question_id", "required_scope_key", "format_policy_sha256"))
                        or inspection["provenance_basis"] != "native" or returned["verification_state"] != "verified"
                        or returned["assignment_id"] != latest["assignment_id"]
                        or canonical[inspection["artifact_id"]] != canonical[w["artifact_id"]]):
                    continue
                selected = graph.satisfying_read_captures(w["request_json"], latest["claimed_at"],
                                                         inspection["inspected_at"], capture_bindings.get(iid, []))
                if selected and all(operation_proofs.get(c["operation_id"], {}).get("outcome") == "ok" for c in selected):
                    valid = True
                    break
            if not valid:
                invalidated.append(wid)
        stops["withdrawn_inspection_completed_work_ids"] = invalidated
    work_bindings = [dict(w, assignments=[dict(a, operations=[dict(op, receipt_proof=operation_proofs.get(op["operation_id"]))
        for op in operations.values() if op["assignment_id"] == a["assignment_id"]]) for a in assignments.values() if a["work_id"] == w["work_id"]])
        for w in work.values()]
    obligation_bindings = []
    for obligation in tables["followup_obligations"]:
        scopes = []
        origin_work = {o["work_id"] for o in tables["work_origins"]
                       if obligation["lead_id"] is not None and o["lead_id"] == obligation["lead_id"]
                       or obligation["reference_id"] is not None and o["reference_id"] == obligation["reference_id"]}
        for scope in scope_rows:
            if scope["obligation_id"] != obligation["obligation_id"]:
                continue
            scoped_work = [work[wid] for wid in sorted(origin_work) if work[wid]["question_id"] == scope["question_id"]]
            scopes.append(dict(scope, work=[dict(work_id=w["work_id"], state=w["state"], reason=w["terminal_reason"] or w["transition_reason"]) for w in scoped_work]))
            if scope["status"] in {"unassigned", "pending"}:
                stops["unresolved_obligation_scopes"].append(scope)
        if obligation["state"] != "terminal":
            stops["nonterminal_obligation_ids"].append(obligation["obligation_id"])
        if not scopes:
            stops["unassigned_obligation_ids"].append(obligation["obligation_id"])
        obligation_bindings.append(dict(obligation, question_scopes=scopes, work_ids=sorted(origin_work)))
    projected_claims = {r["id"] for r in ledger["claims"]}
    claim_bindings = []
    for claim in tables["claims"]:
        evidence = [e for e in tables["claim_evidence"] if e["claim_id"] == claim["claim_id"] and e["claim_revision"] == claim["revision"]]
        claim_bindings.append(dict(claim, projected=claim["claim_id"] in projected_claims,
            withdrawal_affected=claim["claim_id"] in affected_claims, evidence=evidence,
            questions=[q for q in tables["claim_questions"] if q["claim_id"] == claim["claim_id"] and q["claim_revision"] == claim["revision"]]))
    question_bindings = []
    for question in tables["questions"]:
        qid = question["question_id"]
        linked = [c for c in claim_bindings if any(q["question_id"] == qid for q in c["questions"])]
        question_bindings.append(dict(question, work_ids=[wid for wid, w in work.items() if w["question_id"] == qid],
            followups=[p for p in operation_proofs.values() if _terminal_followup(p)
                       and work[assignments[p["assignment_id"]]["work_id"]]["question_id"] == qid],
            claims_by_status={status: [c["claim_id"] for c in linked if c["status"] == status] for status in ("accepted", "challenged", "draft", "withdrawn", "superseded")},
            projected_claim_ids=[c["claim_id"] for c in linked if c["projected"]],
            obligation_ids=[o["obligation_id"] for o in obligation_bindings if any(s["question_id"] == qid for s in o["question_scopes"])]))
    source_bindings = []
    for aid in sorted(set(canonical.values())):
        aliases = {key for key, value in canonical.items() if value == aid}
        sources = [s["id"] for s in ledger["sources"] if s["graph_artifact_id"] == aid]
        discoveries = [d for d in tables["discoveries"] if d["artifact_id"] in aliases]
        related_work = [w for w in work.values() if w["artifact_id"] in aliases]
        scoped_inspections = [dict(s, **{k: i[k] for k in ("artifact_id", "return_id", "provenance_basis", "limitations", "inspected_at")})
            for s in tables["inspection_scopes"] for i in [inspections[s["inspection_id"]]] if i["artifact_id"] in aliases]
        outcomes = [o for o in outcome_tails if o["artifact_id"] in aliases]
        known_questions = {w["question_id"] for w in related_work}
        known_questions |= {a["question_id"] for a in tables["assertions"] if a["subject_artifact_id"] in aliases and a["question_id"] is not None}
        for discovery in discoveries:
            op = operations.get(discovery["operation_id"])
            assignment = assignments.get(op["assignment_id"]) if op else None
            if assignment:
                known_questions.add(work[assignment["work_id"]]["question_id"])
        for origin in tables["lead_origins"]:
            locator = locators.get(origin["locator_id"])
            capture = captures.get(locator["capture_id"]) if locator else None
            if capture and capture["artifact_id"] in aliases:
                known_questions.add(origin["question_id"])
        for obligation in obligation_bindings:
            reference = references.get(obligation["reference_id"])
            if reference and (reference["from_artifact_id"] in aliases or targets[reference["reference_id"]] == aid):
                known_questions.update(s["question_id"] for s in obligation["question_scopes"])
        scope_keys = {(w["question_id"], w["required_scope_key"]) for w in related_work if w["required_scope_key"]}
        scope_keys |= {(o["question_id"], o["required_scope_key"]) for o in outcomes}
        scope_keys |= {(i["question_id"], i["required_scope_key"]) for i in scoped_inspections}
        for qid in known_questions - {q for q, _ in scope_keys}:
            scope_keys.add((qid, None))
        scopes = []
        for qid, key in sorted(scope_keys, key=lambda item: (item[0], item[1] or "")):
            matching_work = [w for w in related_work if w["question_id"] == qid and w["required_scope_key"] == key]
            tails = [o for o in outcomes if o["question_id"] == qid and o["required_scope_key"] == key]
            historical = [o for o in tails if batches[o["created_batch_id"]]["packet_kind"] in {"import", "migrate"}]
            current = [o for o in tails if o not in historical]
            conflict = len({(o["readability"], o["disposition"]) for o in current}) > 1
            bounds = [dict(work_id=w["work_id"], state=w["state"], reason=w["terminal_reason"]) for w in matching_work if w["state"] in bound_work]
            outcome_settled = bool(current) and all(o["disposition"] in {"excluded", "deferred"}
                or o["readability"] in {"access_blocked", "unreadable", "retry_exhausted"}
                or o["readability"] == "readable" and o["disposition"] in {"used", "contextual"} for o in current)
            explicitly_bounded = bool(bounds) and all(w["state"] in bound_work for w in matching_work)
            settled = not conflict and (outcome_settled or explicitly_bounded)
            identity = dict(artifact_id=aid, question_id=qid, required_scope_key=key)
            if conflict:
                stops["conflicting_source_scopes"].append(identity)
            if not settled:
                stops["unresolved_source_scopes"].append(identity)
            scopes.append(dict(identity, work_ids=[w["work_id"] for w in matching_work],
                format_policy_sha256s=sorted({w["format_policy_sha256"] for w in matching_work if w["format_policy_sha256"]}),
                current_outcomes=current, historical_outcomes=historical, conflicting=conflict, explicit_bounds=bounds,
                outcome_or_bound=settled, freshness_checked=False))
        if not scopes:
            stops["unassessed_source_ids"].append(aid)
        source_bindings.append(dict(artifact_id=aid, artifact_aliases=sorted(aliases), source_ids=sources,
            discovery_ids=[d["discovery_id"] for d in discoveries], work_ids=[w["work_id"] for w in related_work],
            required_scopes=scopes, inspections=[dict(i, withdrawn=i["inspection_id"] in withdrawn,
                scopes=[s for s in tables["inspection_scopes"] if s["inspection_id"] == i["inspection_id"]])
                for i in inspections.values() if i["artifact_id"] in aliases],
            all_required_scopes_settled=bool(scopes) and all(s["outcome_or_bound"] for s in scopes), freshness_checked=False))
    stops["settled"] = not any(stops.values())
    return dict(source_bindings=source_bindings, question_bindings=question_bindings, work_bindings=work_bindings,
                obligation_bindings=obligation_bindings, claim_bindings=claim_bindings, stop_checks=stops)


def _non_inspecting_return_proofs(tables, retained):
    """Account for native returns without granting them effective read credit.

    Call only after verifying the complete frozen export. Neither a live
    successor nor a ledger's self-declared withdrawal flags confer authority.
    """
    withdrawals = {row["inspection_id"]: row for row in tables.get("inspection_withdrawals", [])}
    result = {}
    for row in tables["reader_returns"]:
        rid = row["return_id"]
        if row["verification_state"] != "verified" or row["file_path"] != "reader-returns/" + rid + ".json":
            continue  # Imported and legacy attestations confer no current authority.
        inspections = [i for i in tables["inspections"] if i["return_id"] == rid]
        if any(i["provenance_basis"] != "native" or i["inspection_id"] not in withdrawals for i in inspections):
            continue
        original, digest = retained(row["file_path"])
        raw = original["return"]
        declared = raw.get("graph", {}).get("inspections", [])
        expected = {ingestion._id("inspection", rid, i["id"]) for i in declared}
        actual = {i["inspection_id"] for i in inspections}
        _require(digest == row["payload_sha256"] and raw["id"] == rid
                 and expected == actual and len(declared) == len(actual),
                 "integrity_error", "Non-inspecting return differs from its frozen native declaration")
        result[rid] = dict(return_id=rid, original_sha256=digest,
            disposition="withdrawn" if inspections else "no_inspections",
            source_id=original["source_id"], read_id=raw["read_id"], actor_id=raw["actor"],
            assignment_id=raw.get("graph_assignment_id"), receipt_sha256=original["receipt_sha256"],
            inspection_ids=sorted(actual), withdrawals=[withdrawals[iid] for iid in sorted(actual)])
    return result


def _validate_ledger(root, ledger):
    """Bind a parsed projection to a registered immutable export, not its flags.

    Returns frozen scope bindings and structural stop_checks without rereading captures.
    Staleness ignores export registration events, which do not alter research.
    The caller decides whether its operation permits an older represented view.
    """
    root = Path(root).resolve()
    _require(isinstance(ledger, dict) and isinstance(ledger.get("_graph"), dict), "integrity_error", "Plan ledger is not a registered graph projection")
    stamp = ledger["_graph"]
    with graph.snapshot(root) as connection:
        current = _stamp(connection)
        _require(stamp.get("run_id") == current["run_id"] and stamp.get("schema_version") == current["schema_version"]
                 and type(stamp.get("graph_sequence")) is int, "integrity_error", "Ledger names a different graph run or schema")
        registered = [_ for _ in _all(connection, "export_manifests") if _["graph_sequence"] == stamp["graph_sequence"]]
        research_head = connection.execute("SELECT COALESCE(MAX(sequence),0) FROM graph_events WHERE entity_kind <> 'export_manifests'").fetchone()[0]
    _require(registered, "integrity_error", "Ledger does not name a registered export sequence")
    payload = _bytes(ledger)
    selected = None
    for record in registered:
        prefix = "graph/exports/" + record["export_id"] + "/"
        entries = {entry["relative_path"]: entry for entry in graph._registered_export_files(root, record)}

        def checked(relative):
            path = prefix + relative
            _require(path in entries, "integrity_error", "Registered ledger proof file is missing")
            data = _path(root, path).read_bytes()
            _require(_sha(data) == entries[path]["sha256"] and len(data) == entries[path]["size_bytes"], "integrity_error", "Registered ledger proof changed", path=path)
            return graph._read_json(data)

        manifest = checked("manifest.json")
        projected = checked("ledger.json")
        if _bytes(projected) != payload:
            continue
        snapshot = checked("graph.json")
        _require(all(snapshot[k] == stamp[k] == manifest[k] for k in ("schema_version", "run_id", "graph_sequence")), "integrity_error", "Ledger and graph proof disagree on represented snapshot")
        selected = record, snapshot
        break
    _require(selected is not None, "integrity_error", "Ledger differs from the exact registered generated projection")
    record, snapshot = selected
    tables = snapshot["tables"]
    _require(manifest["schema_sha256"] == _sha(graph.schema_bytes(graph.schema_revision_from_tables(tables))),
             "integrity_error", "Ledger uses an unsupported schema identity")
    leads = {row["lead_id"]: row for row in tables["leads"]}
    obligations = {row["obligation_id"]: row for row in tables["followup_obligations"]}
    bindings = []
    for source in ledger.get("sources", []):
        for lead in source.get("leads", []):
            lid, oid = lead.get("graph_lead_id"), lead.get("graph_obligation_id")
            _require(lid in leads and oid == "lead:" + lid and oid in obligations, "integrity_error", "Projected lead has no retained graph obligation")
            obligation = obligations[oid]
            scopes = _lead_scopes(tables, lid)
            questions = [scope["question_id"] for scope in scopes]
            _require(lead["questions"] == questions and lead["graph_disposition"] == leads[lid]["disposition"]
                     and lead["graph_obligation_state"] == obligation["state"], "integrity_error", "Projected lead scope or state differs from its snapshot")
            bindings.append(dict(source_id=source["id"], lead_id=lead["id"], graph_lead_id=lid, graph_obligation_id=oid,
                                 disposition=leads[lid]["disposition"], obligation_state=obligation["state"], questions=questions,
                                 question_scopes=scopes))
    def retained_operation(relative):
        path = "evidence/" + relative
        value = checked(path)
        return value, entries[prefix + path]["sha256"]

    operation_proofs = _operation_proofs(tables, retained_operation)
    context = _scope_bindings(tables, ledger, operation_proofs)
    context["retained_plan_completions"] = _retained_plan_completions(tables, retained_operation, operation_proofs)
    # Validate the registered export's complete retained proof before conveying
    # correction authority to the budget validator. Never inspect live successors.
    directory = root / prefix
    verified_manifest, _, _ = _verify_export(directory)
    _require(verified_manifest == manifest, "integrity_error", "Registered manifest differs from retained proof")
    status = checked("ingestion.json") if prefix + "ingestion.json" in entries else None
    context["ingestion_status"] = status
    context["return_corrections"] = status["return_corrections"] if status else {}
    context["non_inspecting_returns"] = _non_inspecting_return_proofs(tables, retained_operation)
    frozen_contract, contract_hash = retained_operation("contract.json")
    context["navigation_exclusions"] = _navigation_exclusions(tables, ledger, frozen_contract, contract_hash)
    # Only the new, independently revalidated inventory grants request-only
    # cancellation authority. Older frozen pending inventories stay pending.
    context["cancelled_operations"] = {item["original_id"]: operation_proofs[item["original_id"]]
        for item in status["items"] if item["kind"] == "operation" and item["state"] == "cancelled"
        and item["original_id"] in operation_proofs
        and operation_proofs[item["original_id"]]["worker_termination_confirmed"]} if status else {}
    stops = context["stop_checks"]
    stops["unhandled_originals"] = [dict(kind=item["kind"], original_id=item["original_id"], state=item["state"])
        for item in status["items"] if item["state"] not in {"complete", "corrected"}
        and not (item["kind"] == "operation" and item["original_id"] in context["cancelled_operations"])] if status else []
    stops["settled"] = not any(value for key, value in stops.items() if key != "settled")
    return dict(context, **{"status": "pass", "command": "validate-ledger", "schema_version": 1, "run_id": current["run_id"],
            "contract_sha256": contract_hash, "plan_sha256": manifest.get("plan_sha256"),
            "graph_sequence": current["graph_sequence"], "represented_sequence": stamp["graph_sequence"],
            "current_research_sequence": research_head, "stale": research_head > stamp["graph_sequence"],
            "export_id": record["export_id"], "ledger_sha256": _sha(payload), "lead_bindings": bindings})


def validate_ledger(root, ledger):
    return ingestion._safe(_validate_ledger, root, ledger)


def _export_snapshot(root, export_id):
    root = Path(root).resolve()
    graph._id(export_id, filename=True)
    destination = root / "graph/exports" / export_id
    with graph.writer(root):
        if destination.exists():
            manifest, _, digest = _verify_export(destination)
            _require(manifest["export_id"] == export_id, "batch_conflict", "Export ID has different retained content")
        else:
            temporary = Path(tempfile.mkdtemp(prefix=".export-", dir=root / "graph/exports"))
            try:
                with graph.snapshot(root) as connection:
                    stamp = _stamp(connection)
                    tables = {name: _all(connection, name) for name in sorted(graph.storage_tables(connection))}
                    schema_data = graph.schema_bytes(graph.storage_revision(connection))
                    budget_start = _budget_head(root)
                    metadata, _ = _read(root, "bundle.json")
                    contract_hash = _sha(_path(root, "contract.json").read_bytes())
                    _require(contract_hash == metadata["contract_sha256"], "integrity_error", "Current contract changed outside the recorder")
                    plan_hash = metadata.get("plan_sha256")
                    historical_plan = (root / "migration/manifest.json").is_file()
                    _require(plan_hash is None or any(p["plan_sha256"] == plan_hash for p in tables["plan_snapshots"]),
                             "pending_dependency", "Current saved plan is not bound in the graph")
                    ledger, limitations = _compatibility_ledger(root, tables, stamp)
                    synchronization = ingestion.ingestion_status(root, connection=connection)
                    limitations.append("Budget head is the retained external journal head; graph rows come from one SQLite snapshot.")
                    for item in synchronization["items"]:
                        if item["state"] not in {"complete", "corrected"}:
                            limitations.append("Original " + item["kind"] + " " + item["original_id"] + " remains " + item["state"] + "; see ingestion.json for retained obligations.")
                    if historical_plan and plan_hash:
                        limitations.append("The original legacy plan is retained by hash; migration does not register current work or infer execution authority from it.")
                    generated = {
                        "graph.json": _bytes(dict(stamp, tables=tables)),
                        "ledger.json": _bytes(ledger),
                        "ingestion.json": _bytes(synchronization),
                        "sources.json": _bytes(dict(stamp, artifacts=tables["artifacts"], keys=tables["artifact_keys"], versions=tables["artifact_versions"], discoveries=tables["discoveries"], captures=tables["captures"], inspections=tables["inspections"], inspection_scopes=tables["inspection_scopes"], outcomes=tables["source_outcomes"])),
                        "references.json": _bytes(dict(stamp, scans=tables["reference_scans"], occurrences=tables["reference_occurrences"], resolutions=tables["reference_resolutions"], assertions=tables["assertions"], evidence=tables["assertion_evidence"], decisions=tables["assertion_decisions"])),
                        "worklist.json": _bytes(dict(stamp, work=tables["work_items"], assignments=tables["assignments"], origins=tables["work_origins"], leads=tables["leads"], lead_origins=tables["lead_origins"], obligations=tables["followup_obligations"])),
                        "companion.md": _readable(stamp, tables, synchronization, ledger), "schema.sql": schema_data}
                    if "inspection_withdrawals" in tables:
                        sources = graph._read_json(generated["sources.json"])
                        sources["inspection_withdrawals"] = tables["inspection_withdrawals"]
                        generated["sources.json"] = _bytes(sources)
                    files = []
                    for relative, data in generated.items():
                        _save(temporary / relative, data)
                        files.append(_file_entry(relative, data, Path(relative).suffix.lstrip(".")))
                    for relative in sorted(set(_originals(root, tables)) | set(synchronization["immutable_receipts"])):
                        data = _path(root, relative).read_bytes()
                        if relative in synchronization["immutable_receipts"]:
                            _require(_sha(data) == synchronization["immutable_receipts"][relative], "integrity_error",
                                     "Original synchronization proof changed during export", path=relative)
                        copied = "evidence/" + relative
                        _save(temporary / copied, data)
                        files.append(_file_entry(copied, data, Path(relative).suffix.lstrip(".") or "bytes"))
                    budget_end = _budget_head(root)
                    if budget_start != budget_end:
                        limitations.append("The external budget journal advanced during export; its retained file/head is an observed journal snapshot, not an atomic graph-budget transaction.")
                    budget_file = temporary / "evidence/research-budget.json"
                    retained_budget = graph._read_json(budget_file.read_bytes()) if budget_file.exists() else None
                    budget_head = retained_budget["events"][-1]["sha256"] if retained_budget and retained_budget["events"] else None
                    manifest = dict(stamp, export_id=export_id, created_at=graph._now(), contract_sha256=contract_hash,
                                    plan_sha256=plan_hash, budget_event_head=budget_head, schema_sha256=_sha(schema_data),
                                    files=sorted(files, key=lambda f: f["relative_path"]), limitations=sorted(set(limitations)))
                    _save(temporary / "manifest.json", _bytes(manifest))
                    _verify_export(temporary)
                graph._sync_dir(temporary)
                os.rename(temporary, destination)
                graph._sync_dir(destination.parent)
                digest = _sha((destination / "manifest.json").read_bytes())
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary)
        _require(hasattr(graph, "register_export"), "pending_dependency", "Controller export registration is not yet available; complete files are retained")
        receipt = graph.register_export(root, export_id, str((destination / "manifest.json").relative_to(root)), digest)
        return dict(receipt, command="export", export_id=export_id, exported_graph_sequence=manifest["graph_sequence"],
                    manifest_path=str(destination / "manifest.json"), manifest_sha256=digest, limitations=manifest.get("limitations", []))


def export_snapshot(root, export_id):
    return ingestion._safe(_export_snapshot, root, export_id)


def _families(tables):
    artifacts = {row["artifact_id"]: row for row in tables["artifacts"]}
    result = {}
    for aid in artifacts:
        current, seen = aid, set()
        while artifacts[current]["canonical_artifact_id"]:
            _require(current not in seen, "integrity_error", "Prior canonical identity contains a cycle")
            seen.add(current)
            current = artifacts[current]["canonical_artifact_id"]
            _require(current in artifacts, "integrity_error", "Prior canonical identity is missing")
        result[aid] = current
    return result


def _navigation_exclusions(tables, ledger, contract, contract_hash):
    """Only explicit frozen identities authorize an unlinked inventory row.

    A whole-run reuse policy or a claimed source disposition is insufficient.
    Known aliases inherit the same exclusion; titles and URL substrings do not.
    This affects navigation only, never inspection or research completion.
    """
    policy = contract.get("prior_research", {})
    if policy.get("policy") not in {"allow", "exclude"}:
        return {}
    family = _families(tables)
    excluded = {family[aid] for aid in policy.get("excluded_artifact_ids", []) if aid in family}
    keys = {(item["namespace"], item["external_id"]) for item in policy.get("excluded_artifact_keys", [])}
    excluded.update(family[row["artifact_id"]] for row in tables["artifact_keys"]
                    if (row["namespace"], row["external_id"]) in keys)
    return {source["id"]: {"contract_sha256": contract_hash,
                            "basis": "explicit frozen contract identity exclusion"}
            for source in ledger["sources"] if family[source["graph_artifact_id"]] in excluded}


def _reuse_policy(contract, manifest, snapshot, artifact_ids, records=None):
    policy = contract.get("prior_research")
    _require(isinstance(policy, dict) and policy.get("policy") in {"allow", "exclude"},
             "permission_denied", "Contract must explicitly declare prior research reuse or exclusion")
    _require(policy["policy"] == "allow", "permission_denied", "This investigation excludes prior research")
    excluded_runs = set(policy.get("excluded_run_ids", []))
    excluded_exports = set(policy.get("excluded_export_ids", []))
    excluded_artifacts = set(policy.get("excluded_artifact_ids", []))
    excluded_keys = {(item["namespace"], item["external_id"]) for item in policy.get("excluded_artifact_keys", [])}
    _require(manifest["run_id"] not in excluded_runs and manifest["export_id"] not in excluded_exports,
             "permission_denied", "Prior run or export is excluded by the current contract")
    tables = snapshot["tables"]
    family = _families(tables)
    selected_families = {family[aid] for aid in artifact_ids}
    family_ids = {aid for aid, canonical in family.items() if canonical in selected_families}
    for aid in family_ids:
        _require(aid not in excluded_artifacts, "permission_denied", "Selected prior artifact is excluded")
        _require(not any((key["namespace"], key["external_id"]) in excluded_keys for key in tables["artifact_keys"] if key["artifact_id"] == aid),
                 "permission_denied", "Selected original provider identity is excluded")
    for row in tables["prior_run_records"]:
        aids = {row["artifact_id"]} if row.get("artifact_id") else set()
        for kind, field in (("assertion", "subject_artifact_id"), ("inspection", "artifact_id")):
            if row.get(kind + "_id"):
                aids |= {r[field] for r in tables[kind + "s"] if r[kind + "_id"] == row[kind + "_id"]}
        locator_ids = set()
        if row.get("lead_id"):
            locator_ids |= {r["locator_id"] for r in tables["lead_origins"] if r["lead_id"] == row["lead_id"]}
        if row.get("claim_id"):
            locator_ids |= {r["locator_id"] for r in tables["claim_evidence"] if r["claim_id"] == row["claim_id"]}
        capture_ids = {r["capture_id"] for r in tables["locators"] if r["locator_id"] in locator_ids}
        aids |= {r["artifact_id"] for r in tables["captures"] if r["capture_id"] in capture_ids}
        selected_record = bool((records or {}).get(row["prior_record_kind"]) and row.get(row["prior_record_kind"] + "_id") in records[row["prior_record_kind"]])
        if not aids & family_ids and not selected_record:
            continue
        lineage = row["lineage_json"]
        for ancestor in lineage:
            _require(ancestor["run_id"] not in excluded_runs and ancestor["export_id"] not in excluded_exports
                     and ancestor.get("artifact_id") not in excluded_artifacts
                     and (ancestor.get("original_namespace"), ancestor.get("original_external_id")) not in excluded_keys,
                     "permission_denied", "Selected prior lineage contains excluded research")


def _selection(selection, snapshot, connection):
    _require(isinstance(selection, dict) and set(selection) <= {"policy", "artifact_ids", "record_ids", "question_map", "reason"},
             "invalid_packet", "Unexpected prior import selection fields")
    _require(selection.get("policy") in {"leads_only", "attested_evidence"} and isinstance(selection.get("reason"), str) and selection["reason"].strip(),
             "invalid_packet", "Prior import needs an explicit policy and reason")
    artifacts = selection.get("artifact_ids", [])
    records = selection.get("record_ids", {})
    _require(isinstance(artifacts, list) and isinstance(records, dict) and set(records) <= {"assertion", "inspection", "lead", "claim"},
             "invalid_packet", "Prior import selection must be typed ID arrays")
    values = [artifacts] + list(records.values())
    _require(all(isinstance(ids, list) and all(isinstance(v, str) and v for v in ids) and len(ids) == len(set(ids)) for ids in values)
             and 0 < sum(map(len, values)) <= MAX_IMPORT_RECORDS,
             "invalid_packet", "Prior import needs a nonempty bounded selection of distinct records")
    tables = snapshot["tables"]
    known = {row["artifact_id"] for row in tables["artifacts"]}
    _require(set(artifacts) <= known, "unknown_identity", "Selected prior artifact does not exist")
    for kind, ids in records.items():
        _require(set(ids) <= {row[kind + "_id"] for row in tables[kind + "s"]}, "unknown_identity", "Selected prior record does not exist")
    _require(selection["policy"] == "attested_evidence" or not any(records.get(k) for k in ("assertion", "inspection", "claim")),
             "permission_denied", "Leads-only imports cannot import earlier inspections or conclusions")
    mapping = selection.get("question_map", {})
    _require(isinstance(mapping, dict) and set(mapping.values()) <= {row[0] for row in connection.execute("SELECT question_id FROM questions")},
             "scope_mismatch", "Question map must name current investigation questions")
    return set(artifacts), records, mapping


def _plain(record):
    return {key: value for key, value in record.items() if key not in graph.INJECTED}


def _import_plan(root, directory, import_id, manifest, snapshot, digest, selection, connection):
    tables = snapshot["tables"]
    selected, records, questions = _selection(selection, snapshot, connection)
    contract, _ = _read(root, "contract.json")
    _reuse_policy(contract, manifest, snapshot, selected, records)
    prior_run, prefix = manifest["run_id"], "graph/imports/" + import_id + "/"
    rows, copied, maps, pending_rows = [], {}, {}, {}
    family = _families(tables)
    current_keys = {(r[0], r[1]): r[2] for r in connection.execute("SELECT namespace,external_id,artifact_id FROM artifact_keys")}

    def mapped(kind, old):
        if old is None:
            return None
        key = (kind, old)
        if key not in maps:
            maps[key] = _id("prior-" + kind, prior_run if kind in {"artifact", "actor"} else import_id, old)
        return maps[key]

    def add(table, record):
        identity = graph._identity(table, record)
        key = (table, graph._json(identity))
        existing = pending_rows.get(key) or graph._get(connection, table, identity)
        if existing is None:
            rows.append(_insert(table, **record))
            pending_rows[key] = record
        else:
            _require(all(existing.get(k) == v for k, v in record.items()), "batch_conflict", "Previously imported record has different content", table=table)

    def copy_file(export_relative, target):
        _require(any(item["relative_path"] == export_relative for item in manifest["files"]),
                 "integrity_error", "Prior evidence file is not bound by its manifest")
        data = _path(directory, export_relative).read_bytes()
        copied[target] = data
        return target, _sha(data)

    def map_question(question):
        if question is None:
            return None
        _require(question in questions, "scope_mismatch", "Selected historical question needs an explicit current-question mapping", question_id=question)
        return questions[question]

    artifact_rows = {r["artifact_id"]: r for r in tables["artifacts"]}
    original_keys = {aid: [r for r in tables["artifact_keys"] if family[r["artifact_id"]] == family[aid]] for aid in selected}
    root_artifact = _id("prior-export", prior_run, manifest["export_id"])
    add("artifacts", dict(artifact_id=root_artifact, title="Prior research export " + manifest["export_id"], artifact_type="research_export"))
    add("discoveries", dict(discovery_id=_id("prior-export-discovery", import_id), artifact_id=root_artifact,
                            supplied_seed_id="prior-export:" + manifest["export_id"], observed_title="Prior research export"))
    add("assertions", dict(assertion_id=_id("prior-trunk-role", import_id), kind="role", predicate="has_role", subject_artifact_id=root_artifact,
                           value_json="trunk", basis="metadata", temporal_scope_json={}, assessed_at=manifest["created_at"]))
    add("prior_run_imports", dict(import_id=import_id, prior_run_id=prior_run, prior_export_id=manifest["export_id"], manifest_sha256=digest,
                                 source_artifact_id=root_artifact, policy=selection["policy"],
                                 scope_json={"artifact_ids": sorted(selected), "record_ids": records}, limitations=HISTORICAL + " " + selection["reason"]))

    def lineage(kind, old, new, aid=None):
        keys = original_keys.get(aid, [])
        key = next((k for k in keys if not k["namespace"].startswith(("bundle-source:", "legacy-source:"))), keys[0] if keys else {})
        previous = []
        for prior in tables["prior_run_records"]:
            same = prior.get(kind + "_id") == old
            if kind == "artifact" and prior.get("artifact_id"):
                same = family[prior["artifact_id"]] == family[old]
            if prior["prior_record_kind"] == kind and same:
                previous.extend(prior["lineage_json"])
        this = {"run_id": prior_run, "export_id": manifest["export_id"], "manifest_sha256": digest}
        if aid:
            this["artifact_id"] = aid
        if key:
            this.update(original_namespace=key["namespace"], original_external_id=key["external_id"])
        unique = {_bytes(item): item for item in previous + [this]}
        limitations = HISTORICAL
        if kind == "artifact":
            outcomes = [_plain(row) for row in tables["source_outcomes"] if family[row["artifact_id"]] == family[old]]
            if outcomes:
                limitations += " Historical source outcomes (not current access or disposition): " + json.dumps(outcomes, ensure_ascii=False, sort_keys=True)
        add("prior_run_records", dict(record_id=_id("prior-map", import_id, kind, old), import_id=import_id,
                                     prior_record_kind=kind, prior_record_id=old, **{kind + "_id": new},
                                     original_namespace=key.get("namespace"), original_external_id=key.get("external_id"),
                                     lineage_json=list(unique.values()), limitations=limitations))

    for aid in sorted(selected):
        keys = original_keys[aid]
        candidates = {ingestion._canonical(connection, current_keys[(k["namespace"], k["external_id"])]) for k in keys if (k["namespace"], k["external_id"]) in current_keys}
        _require(len(candidates) <= 1, "scope_mismatch", "Prior keys identify conflicting current artifacts; reconcile first")
        new = next(iter(candidates), mapped("artifact", family[aid]))
        maps[("artifact", aid)] = new
        if not candidates:
            original = artifact_rows[family[aid]]
            add("artifacts", dict(artifact_id=new, title=original["title"], artifact_type=original["artifact_type"], published_at=original["published_at"]))
        for key in keys:
            pair = (key["namespace"], key["external_id"])
            if pair not in current_keys:
                add("artifact_keys", dict(namespace=pair[0], external_id=pair[1], artifact_id=new, basis="Verified prior export original identity"))
                current_keys[pair] = new
        add("discoveries", dict(discovery_id=_id("prior-discovery", import_id, aid), artifact_id=new,
                                prior_import_id=import_id, observed_title=artifact_rows[aid]["title"]))
        lineage("artifact", aid, new, aid)
    current_versions = {(row["artifact_id"], row["provider_revision"]): row["version_id"] for row in _all(connection, "artifact_versions") if row["provider_revision"]}
    for row in tables["artifact_versions"]:
        if row["artifact_id"] not in selected:
            continue
        value = _plain(row)
        value.update(version_id=mapped("version", row["version_id"]), artifact_id=mapped("artifact", row["artifact_id"]))
        if value["provider_revision"]:
            identity = value["artifact_id"], value["provider_revision"]
            known = current_versions.get(identity)
            if known:
                maps[("version", row["version_id"])] = known
                continue
            current_versions[identity] = value["version_id"]
        add("artifact_versions", value)
    captures = {r["capture_id"]: r for r in tables["captures"] if r["artifact_id"] in selected}
    operations = {r["operation_id"]: r for r in tables["operation_links"]}
    for op_id in sorted({r["operation_id"] for r in captures.values()}):
        original = operations[op_id]
        source_prefix = _prior_operation_prefix(tables, original)
        for name in ("request.json", "receipt.json"):
            copy_file("evidence/" + source_prefix + name, prefix + "operations/" + mapped("operation", op_id) + "/" + name)
        value = _plain(original)
        value.update(operation_id=mapped("operation", op_id), assignment_id=None, budget_event_id=None,
                     verification_state="legacy_attested", return_id=None)
        add("operation_links", value)
    for old, row in captures.items():
        content = prefix + "captures/" + mapped("capture", old) + "/content"
        copy_file("evidence/" + row["relative_path"], content)
        receipt_relative = (str(Path(row["relative_path"]).parent / "receipt.json") if row["relative_path"].startswith("graph/imports/")
                            else "operations/" + row["operation_id"] + "/receipt.json")
        copy_file("evidence/" + receipt_relative, str(Path(content).parent / "receipt.json"))
        value = _plain(row)
        value.update(capture_id=mapped("capture", old), artifact_id=mapped("artifact", row["artifact_id"]), operation_id=mapped("operation", row["operation_id"]),
                     version_id=mapped("version", row["version_id"]), relative_path=content, extraction_status="legacy_attested", limitations=HISTORICAL + " Original extraction status: " + row["extraction_status"] + ". " + row["limitations"])
        add("captures", value)
    locator_rows = {r["locator_id"]: r for r in tables["locators"] if r["capture_id"] in captures}
    for old, row in locator_rows.items():
        value = _plain(row)
        value.update(locator_id=mapped("locator", old), capture_id=mapped("capture", row["capture_id"]))
        add("locators", value)
    selected_inspections = set(records.get("inspection", []))
    selected_assertions = set(records.get("assertion", []))
    selected_claims = set(records.get("claim", []))
    selected_leads = set(records.get("lead", []))
    selected_leads |= {r["lead_id"] for r in tables["lead_origins"] if r["locator_id"] in locator_rows}
    for evidence in tables["assertion_evidence"]:
        if evidence["assertion_id"] in selected_assertions and evidence["inspection_id"]:
            selected_inspections.add(evidence["inspection_id"])
    scans = {r["scan_id"]: r for r in tables["reference_scans"] if r["capture_id"] in captures}
    inspection_rows = {r["inspection_id"]: r for r in tables["inspections"] if r["inspection_id"] in selected_inspections}
    _require(all(r["artifact_id"] in selected for r in inspection_rows.values()), "scope_mismatch", "Inspection evidence requires explicitly selected artifacts")
    returned_ids = {r["return_id"] for r in scans.values() if r["return_id"]} | {r["return_id"] for r in inspection_rows.values()}
    returned_ids |= {r["return_id"] for r in tables["assertions"] if r["assertion_id"] in selected_assertions and r["return_id"]}
    returned_ids |= {r["return_id"] for r in tables["lead_origins"] if r["lead_id"] in selected_leads and r["return_id"] and (r["locator_id"] is None or r["locator_id"] in locator_rows)}
    returned_rows = {r["return_id"]: r for r in tables["reader_returns"] if r["return_id"] in returned_ids}
    actors = {r["actor_id"]: r for r in tables["actors"]}
    coordinator = connection.execute("SELECT actor_id FROM actors WHERE role='coordinator' AND parent_actor_id IS NULL").fetchone()[0]
    for actor_id in sorted({r["actor_id"] for r in returned_rows.values()}):
        original = actors[actor_id]
        add("actors", dict(actor_id=mapped("actor", actor_id), parent_actor_id=coordinator, role="reader",
                           declared_runtime_identity="Prior run " + prior_run + " actor " + actor_id + "; " + (original["declared_runtime_identity"] or "attested label")))
    for old, row in returned_rows.items():
        relative, sha = copy_file("evidence/" + row["file_path"], prefix + "returns/" + mapped("return", old) + ".json")
        value = _plain(row)
        value.update(return_id=mapped("return", old), actor_id=mapped("actor", row["actor_id"]), assignment_id=None,
                     file_path=relative, payload_sha256=sha, recorded_budget_event=None, verification_state="legacy_attested", limitations=HISTORICAL)
        add("reader_returns", value)
    for old, row in inspection_rows.items():
        value = _plain(row)
        value.update(inspection_id=mapped("inspection", old), artifact_id=mapped("artifact", row["artifact_id"]),
                     return_id=mapped("return", row["return_id"]), actor_id=mapped("actor", row["actor_id"]), provenance_basis="prior_run",
                     limitations=HISTORICAL + " Original inspection provenance: " + row["provenance_basis"] + ". " + row["limitations"])
        add("inspections", value)
        for inspected in tables["inspection_captures"]:
            if inspected["inspection_id"] == old:
                _require(inspected["capture_id"] in captures, "scope_mismatch", "Inspection capture is outside the bounded selection")
                add("inspection_captures", dict(inspection_id=value["inspection_id"], capture_id=mapped("capture", inspected["capture_id"]), inspected_extent_json=inspected["inspected_extent_json"]))
        lineage("inspection", old, value["inspection_id"], row["artifact_id"])
    for row in tables.get("inspection_withdrawals", []):
        if row["inspection_id"] not in inspection_rows:
            continue
        _require("inspection_withdrawals" in graph.storage_tables(connection), "unsupported_schema",
                 "Upgrade the destination graph before importing withdrawn inspection history")
        original = actors[row["actor_id"]]
        add("actors", dict(actor_id=mapped("actor", row["actor_id"]), parent_actor_id=coordinator, role="reader",
            declared_runtime_identity="Prior run " + prior_run + " actor " + row["actor_id"] + "; " + (original["declared_runtime_identity"] or "attested label")))
        relative, sha = copy_file("evidence/" + row["evidence_path"],
            prefix + "withdrawals/" + mapped("withdrawal", row["withdrawal_id"]) + Path(row["evidence_path"]).suffix)
        value = _plain(row)
        value.update(withdrawal_id=mapped("withdrawal", row["withdrawal_id"]),
            inspection_id=mapped("inspection", row["inspection_id"]), actor_id=mapped("actor", row["actor_id"]),
            evidence_path=relative, evidence_sha256=sha)
        add("inspection_withdrawals", value)
    for old, row in scans.items():
        value = _plain(row)
        value.update(scan_id=mapped("scan", old), capture_id=mapped("capture", row["capture_id"]), return_id=mapped("return", row["return_id"]), limitations=HISTORICAL + " " + row["limitations"])
        add("reference_scans", value)
    for row in tables["reference_occurrences"]:
        if row["scan_id"] not in scans:
            continue
        value = _plain(row)
        target = row["target_artifact_id"]
        value.update(reference_id=mapped("reference", row["reference_id"]), from_artifact_id=mapped("artifact", row["from_artifact_id"]),
                     scan_id=mapped("scan", row["scan_id"]), locator_id=mapped("locator", row["locator_id"]),
                     target_artifact_id=mapped("artifact", target) if target in selected else None)
        if target and target not in selected:
            value["label"] += " [prior target outside selection: " + target + "]"
        add("reference_occurrences", value)
    # Resolve only newly imported occurrences with selected endpoints. The full
    # older correction chain remains in the retained prior snapshot.
    replaced = {r["replaces_resolution_id"] for r in tables["reference_resolutions"]}
    imported_references = {r["reference_id"] for r in tables["reference_occurrences"] if r["scan_id"] in scans}
    for resolution in tables["reference_resolutions"]:
        if resolution["resolution_id"] in replaced or resolution["reference_id"] not in imported_references or resolution["target_artifact_id"] not in selected:
            continue
        actor_id = resolution["actor_id"]
        original = actors[actor_id]
        add("actors", dict(actor_id=mapped("actor", actor_id), parent_actor_id=coordinator, role="reader",
                           declared_runtime_identity="Prior run " + prior_run + " actor " + actor_id + "; " + (original["declared_runtime_identity"] or "attested label")))
        add("reference_resolutions", dict(resolution_id=mapped("resolution", resolution["resolution_id"]),
                                          reference_id=mapped("reference", resolution["reference_id"]), target_artifact_id=mapped("artifact", resolution["target_artifact_id"]),
                                          basis=HISTORICAL + " Original resolution " + resolution["resolution_id"] + ": " + resolution["basis"], actor_id=mapped("actor", actor_id)))
    for row in tables["assertions"]:
        if row["assertion_id"] not in selected_assertions:
            continue
        _require(row["subject_artifact_id"] in selected and (row["object_artifact_id"] is None or row["object_artifact_id"] in selected),
                 "scope_mismatch", "Scoped assertion endpoints require explicitly selected artifacts")
        for key in ("subject_locator_id", "object_locator_id"):
            _require(row[key] is None or row[key] in locator_rows, "scope_mismatch", "Scoped prior assertion locator is outside selected captures")
        if row["kind"] in {"role", "relationship"}:
            table, field, value = ("role_vocabulary", "role", row["value_json"]) if row["kind"] == "role" else ("relationship_vocabulary", "predicate", row["predicate"])
            vocabulary = next((r for r in tables[table] if r[field] == value), None)
            _require(vocabulary, "integrity_error", "Prior assertion vocabulary is missing")
            add(table, vocabulary)
        value = _plain(row)
        value.update(assertion_id=mapped("assertion", row["assertion_id"]), subject_artifact_id=mapped("artifact", row["subject_artifact_id"]),
                     object_artifact_id=mapped("artifact", row["object_artifact_id"]), subject_locator_id=mapped("locator", row["subject_locator_id"]),
                     object_locator_id=mapped("locator", row["object_locator_id"]), question_id=map_question(row["question_id"]),
                     return_id=mapped("return", row["return_id"]), basis="prior_run")
        add("assertions", value)
        for evidence in tables["assertion_evidence"]:
            if evidence["assertion_id"] != row["assertion_id"]:
                continue
            _require(evidence["locator_id"] in locator_rows, "scope_mismatch", "Assertion evidence is outside selected captures")
            entry = _plain(evidence)
            entry.update(evidence_id=mapped("assertion-evidence", evidence["evidence_id"]), assertion_id=value["assertion_id"],
                         locator_id=mapped("locator", evidence["locator_id"]), inspection_id=mapped("inspection", evidence["inspection_id"]),
                         rationale=HISTORICAL + " " + evidence["rationale"])
            add("assertion_evidence", entry)
        lineage("assertion", row["assertion_id"], value["assertion_id"], row["subject_artifact_id"])
    for row in tables["leads"]:
        if row["lead_id"] not in selected_leads:
            continue
        origins = [o for o in tables["lead_origins"] if o["lead_id"] == row["lead_id"] and (o["locator_id"] is None or o["locator_id"] in locator_rows)]
        _require(origins, "scope_mismatch", "Selected prior lead has no origin inside the selected scope")
        value = _plain(row)
        value.update(lead_id=mapped("lead", row["lead_id"]), disposition="pending", reason=None,
                     significance=row["significance"] + " Historical disposition: " + row["disposition"] + ". " + HISTORICAL)
        add("leads", value)
        for origin in origins:
            add("lead_origins", dict(origin_id=mapped("lead-origin", origin["origin_id"]), lead_id=value["lead_id"],
                                     return_id=mapped("return", origin["return_id"]), returned_lead_id=origin["returned_lead_id"], reference_id=mapped("reference", origin["reference_id"]) if origin["reference_id"] and any(r["reference_id"] == origin["reference_id"] and r["scan_id"] in scans for r in tables["reference_occurrences"]) else None,
                                     locator_id=mapped("locator", origin["locator_id"]), question_id=map_question(origin["question_id"]),
                                     original_value=origin["original_value"], origin_basis=HISTORICAL + " Original return/lead: " + str(origin["return_id"]) + "/" + str(origin["returned_lead_id"]) + "; " + origin["origin_basis"]))
        lineage("lead", row["lead_id"], value["lead_id"])
    for row in tables["claims"]:
        if row["claim_id"] not in selected_claims:
            continue
        value = _plain(row)
        value.update(claim_id=mapped("claim", row["claim_id"]), basis="prior_run", status="draft", reason=HISTORICAL + " Original status/basis: " + row["status"] + "/" + row["basis"])
        add("claims", value)
        for evidence in tables["claim_evidence"]:
            if evidence["claim_id"] != row["claim_id"] or evidence["claim_revision"] != row["revision"]:
                continue
            _require(evidence["locator_id"] in locator_rows, "scope_mismatch", "Prior claim evidence needs explicitly selected source artifacts")
            entry = _plain(evidence)
            entry.update(evidence_id=mapped("claim-evidence", evidence["evidence_id"]), claim_id=value["claim_id"], claim_revision=1,
                         locator_id=mapped("locator", evidence["locator_id"]), assertion_id=mapped("assertion", evidence["assertion_id"]) if evidence["assertion_id"] in selected_assertions else None,
                         rationale=HISTORICAL + " " + evidence["rationale"])
            add("claim_evidence", entry)
        for question in tables["claim_questions"]:
            if question["claim_id"] == row["claim_id"] and question["claim_revision"] == row["revision"]:
                add("claim_questions", dict(claim_id=value["claim_id"], claim_revision=1, question_id=map_question(question["question_id"]), relationship=question["relationship"]))
        lineage("claim", row["claim_id"], value["claim_id"])
    return rows, copied


def _proof_validator(root, connection, packet, mutation):
    imports = [m["record"] for m in packet["mutations"] if m.get("table") == "prior_run_imports" and m.get("op") == "insert"]
    _require(len(imports) == 1, "permission_denied", "Historical import requires one explicit provenance binding")
    import_id = imports[0]["import_id"]
    relative = "migration/proof.json" if packet["packet_kind"] == "migrate" else "graph/imports/" + import_id + "/proof.json"
    proof, _ = _read(root, relative)
    _require(proof["packet_sha256"] == _sha(_bytes({k: v for k, v in packet.items() if k in {"schema_version", "batch_id", "actor_id", "assignment_id", "packet_kind", "contract_sha256", "submitted_at", "mutations"}})),
             "integrity_error", "Historical packet differs from retained provenance proof")
    for item in proof["files"]:
        _require(_sha(_path(root, item["relative_path"]).read_bytes()) == item["sha256"], "integrity_error", "Historical import evidence changed")
    if packet["packet_kind"] == "import":
        manifest, digest = _read(root, "graph/imports/" + import_id + "/manifest.json")
        _require(digest == imports[0]["manifest_sha256"] and manifest["run_id"] == imports[0]["prior_run_id"]
                 and manifest["export_id"] == imports[0]["prior_export_id"], "integrity_error", "Historical import is not bound to its retained prior manifest")
    row = mutation.get("after", mutation.get("record", {}))
    table = mutation["table"]
    created = {(m["table"], graph._json(graph._identity(m["table"], m["record"]))) for m in packet["mutations"] if m["op"] == "insert"}

    def newly_created(target, identity):
        return (target, graph._json(graph._identity(target, identity))) in created

    _require(mutation["op"] == "insert" and table not in {"inspection_scopes", "assertion_decisions", "assignments", "work_items", "work_origins", "source_outcomes", "questions", "plan_items"},
             "permission_denied", "Historical import cannot schedule, close or accept current research")
    if table == "claims":
        _require(row["status"] == "draft" and row["basis"] in {"legacy_attested", "prior_run"}, "permission_denied", "Historical claims must remain unaccepted historical drafts")
    if table == "leads":
        _require(row["disposition"] == "pending", "permission_denied", "Historical leads cannot close current obligations")
    if table == "artifacts":
        _require(row.get("canonical_artifact_id") is None, "permission_denied", "Import cannot redirect current canonical identities")
    if table == "reference_resolutions":
        _require(newly_created("reference_occurrences", {"reference_id": row["reference_id"]}),
                 "permission_denied", "Import can resolve only its own newly imported references")
    bindings = {"captures": [("operation_links", "operation_id")],
                "locators": [("captures", "capture_id")],
                "inspections": [("reader_returns", "return_id")],
                 "inspection_captures": [("inspections", "inspection_id"), ("captures", "capture_id")],
                 "inspection_withdrawals": [("inspections", "inspection_id")],
                "reference_scans": [("captures", "capture_id"), ("reader_returns", "return_id")],
                "reference_occurrences": [("reference_scans", "scan_id"), ("locators", "locator_id")],
                "assertion_evidence": [("assertions", "assertion_id"), ("inspections", "inspection_id"), ("locators", "locator_id")],
                "claim_evidence": [("claims", "claim_id"), ("assertions", "assertion_id"), ("locators", "locator_id")],
                "lead_origins": [("leads", "lead_id"), ("reader_returns", "return_id"), ("reference_occurrences", "reference_id"), ("locators", "locator_id")]}
    for target, field in bindings.get(table, []):
        if row.get(field) is not None:
            _require(newly_created(target, {field: row[field]}), "permission_denied", "Historical evidence cannot extend a current-run evidence record", table=table, field=field)
    if table == "operation_links":
        _require(row["verification_state"] == "legacy_attested" and row["assignment_id"] is None and row["budget_event_id"] is None,
                 "permission_denied", "Imported operation is historical, not a current budget admission")
    if table == "reader_returns":
        _require(row["verification_state"] == "legacy_attested" and row["assignment_id"] is None, "permission_denied", "Imported return cannot be native")
    if table == "inspections":
        _require(row["provenance_basis"] in {"legacy_attested", "prior_run"}, "permission_denied", "Imported inspection cannot become a fresh read")
    if table == "inspection_withdrawals":
        _require(packet["packet_kind"] == "import"
                 and row["evidence_path"].startswith("graph/imports/" + import_id + "/withdrawals/"),
                 "permission_denied", "Imported withdrawal must retain its own historical basis")
    if table == "assertions":
        _require(row["basis"] in {"legacy_attested", "prior_run", "metadata"}, "permission_denied", "Imported interpretation cannot become a current inspected fact")
    if table == "captures" and packet["packet_kind"] == "import":
        _require(row["relative_path"].startswith("graph/imports/" + import_id + "/captures/") and row["extraction_status"] == "legacy_attested",
                 "permission_denied", "Imported capture path/provenance is not bound to its import")
        receipt = str(Path(row["relative_path"]).parent / "receipt.json")
        _require(_sha(_path(root, receipt).read_bytes()) == row["receipt_sha256"], "integrity_error", "Imported receipt is not the retained original")
        mapped = connection.execute("SELECT 1 FROM prior_run_records WHERE import_id=? AND artifact_id=?", (import_id, row["artifact_id"])).fetchone()
        _require(mapped, "integrity_error", "Imported capture lacks its original-artifact lineage mapping")
    return True


def evidence_validator(root, connection, packet, mutation):
    return ingestion._safe(_proof_validator, Path(root), connection, packet, mutation)


def _retain_proof(root, relative, packet, files):
    entries = []
    for path, data in sorted(files.items()):
        _save(_path(root, path, exists=False), data)
        entries.append({"relative_path": path, "sha256": _sha(data)})
    _save(_path(root, relative, exists=False), _bytes({"packet_sha256": _sha(_bytes(packet)), "files": entries}))


def _import_prior(root, export_directory, import_id, selection):
    root, directory = Path(root).resolve(), Path(export_directory).resolve()
    graph._id(import_id, filename=True)
    manifest, snapshot, digest = _verify_export(directory)
    prefix = "graph/imports/" + import_id + "/"
    with graph.writer(root):
        retained = _path(root, prefix + "packet.json", exists=False)
        if retained.exists():
            old_selection, _ = _read(root, prefix + "selection.json")
            _require(old_selection == {"selection": selection, "manifest_sha256": digest}, "batch_conflict", "Import ID already names a different bounded selection")
            contract, _ = _read(root, "contract.json")
            _reuse_policy(contract, manifest, snapshot, set(selection.get("artifact_ids", [])), selection.get("record_ids", {}))
        else:
            with graph.snapshot(root) as connection:
                rows, copies = _import_plan(root, directory, import_id, manifest, snapshot, digest, selection, connection)
                coordinator = connection.execute("SELECT actor_id FROM actors WHERE role='coordinator' AND parent_actor_id IS NULL").fetchone()[0]
                packet = dict(schema_version=1, batch_id=_id("prior-import", import_id), actor_id=coordinator, assignment_id=None,
                              packet_kind="import", contract_sha256=_sha(_path(root, "contract.json").read_bytes()),
                              submitted_at=graph._now(), mutations=rows)
                graph._packet(packet)
                graph._normal_mutations(packet)
                copies[prefix + "manifest.json"] = _path(directory, "manifest.json").read_bytes()
                copies[prefix + "graph.json"] = _path(directory, "graph.json").read_bytes()
                copies[prefix + "selection.json"] = _bytes({"selection": selection, "manifest_sha256": digest})
                _retain_proof(root, prefix + "proof.json", packet, copies)
                _save(retained, _bytes(packet))
        result = graph.ingest(root, retained, evidence_validator=evidence_validator)
        return dict(result, command="import-prior", import_id=import_id, prior_run_id=manifest["run_id"], prior_export_id=manifest["export_id"], policy=selection["policy"], limitations=[HISTORICAL])


def import_prior(root, export_directory, import_id, selection):
    return ingestion._safe(_import_prior, root, export_directory, import_id, selection)


def _tree(root):
    files = {}
    for path in sorted(root.rglob("*")):
        _require(not path.is_symlink(), "integrity_error", "Legacy bundle contains a symlink; choose a retained local copy")
        if path.is_file() and not path.name.endswith(".lock"):
            files[str(path.relative_to(root))] = path.read_bytes()
    return files


def _legacy_time(value, fallback):
    try:
        return graph._timestamp(value)
    except GraphError:
        return fallback


def _legacy_plan(root, ledger, manifest, actor_id, run_id, connection):
    rows, extra, pending = [], {}, []
    seen = set()
    stamp = graph._now()
    import_id = "legacy-migration"
    manifest_sha = _sha(_bytes(manifest))

    def add(table, record):
        key = (table, graph._json(graph._identity(table, record)))
        if key not in seen:
            rows.append(_insert(table, **record))
            seen.add(key)

    def actor(label):
        label = label or "unknown historical reader"
        key = _id("legacy-reader", label)
        add("actors", dict(actor_id=key, parent_actor_id=actor_id, role="reader", declared_runtime_identity=label))
        return key

    metadata, _ = _read(root, "bundle.json")
    plan_hash = metadata.get("plan_sha256")
    if plan_hash:
        try:
            plan, actual = _read(root, "plans/" + plan_hash + ".json")
            _, ledger_hash = _read(root, "plans/" + plan_hash + ".ledger.json")
            _require(actual == plan_hash and plan.get("_record", {}).get("contract_sha256") == metadata["contract_sha256"]
                     and plan["_record"].get("ledger_sha256") == ledger_hash, "integrity_error", "Historical saved plan binding is incomplete")
            add("plan_snapshots", dict(plan_sha256=plan_hash, contract_sha256=metadata["contract_sha256"], relative_path="plans/" + plan_hash + ".json"))
        except GraphError:
            pending.append({"plan_sha256": plan_hash, "reason": "Original plan files retained, but their current contract/ledger binding cannot be registered; no work inferred."})

    subject = _id("legacy-bundle", manifest_sha)
    add("artifacts", dict(artifact_id=subject, title="Historical investigation bundle", artifact_type="research_bundle"))
    add("discoveries", dict(discovery_id="legacy-bundle-discovery", artifact_id=subject, supplied_seed_id="legacy-bundle", observed_title="Historical investigation bundle"))
    add("prior_run_imports", dict(import_id=import_id, prior_run_id="legacy-" + manifest_sha[:24], prior_export_id="legacy-bundle",
                                 manifest_sha256=manifest_sha, source_artifact_id=subject, policy="legacy_attested",
                                 scope_json={"artifact_ids": [subject]}, limitations=HISTORICAL))
    add("assertions", dict(assertion_id="legacy-trunk-role", kind="role", predicate="has_role", subject_artifact_id=subject,
                           value_json="trunk", basis="legacy_attested", assessed_at=stamp, temporal_scope_json={}))
    operations = _operations(root)
    sources = {row["id"]: row for row in ledger.get("sources", [])}
    metadata = {}
    for spec, receipt, result in operations.values():
        for sid in (result or {}).get("source_ids", []):
            sources.setdefault(sid, {"id": sid})
        for candidate in (result or {}).get("sources", []):
            metadata.setdefault(candidate["source_id"], candidate)
        if spec.get("kind") == "read":
            sources.setdefault(spec["request"]["source_id"], {"id": spec["request"]["source_id"]})
    # Index files can survive an interrupted legacy request write.
    for path in sorted((root / "operations").glob("*/result.json")):
        result = graph._read_json(path.read_bytes())
        for sid in result.get("source_ids", []):
            sources.setdefault(sid, {"id": sid})
    artifacts, identities = {}, {}
    for sid, source in sorted(sources.items()):
        info = metadata.get(sid, {})
        stable = (info.get("namespace", "legacy-source:" + manifest_sha), info.get("external_id", sid))
        aid = identities.setdefault(stable, _id("legacy-source", *stable))
        artifacts[sid] = aid
        add("artifacts", dict(artifact_id=aid, title=source.get("title", info.get("title", "")), artifact_type=info.get("type", "unknown")))
        for namespace, external_id in (stable, ("bundle-source:" + run_id, sid)):
            add("artifact_keys", dict(namespace=namespace, external_id=external_id, artifact_id=aid, basis="Original historical source identity; metadata completeness is unknown"))
        add("discoveries", dict(discovery_id=_id("legacy-source-seed", sid), artifact_id=aid, prior_import_id=import_id,
                                observed_title=source.get("title", info.get("title", "")), observed_url=source.get("url", info.get("url"))))
        add("prior_run_records", dict(record_id=_id("legacy-source-map", sid), import_id=import_id, prior_record_kind="artifact", prior_record_id=sid,
                                     artifact_id=aid, original_namespace=stable[0], original_external_id=stable[1],
                                     lineage_json=[{"run_id": "legacy-" + manifest_sha[:24], "export_id": "legacy-bundle", "manifest_sha256": manifest_sha,
                                                    "artifact_id": sid, "original_namespace": stable[0], "original_external_id": stable[1]}], limitations=HISTORICAL))
        observations = []
        if source.get("finding"):
            observations.append(("finding", "states", {"text": source["finding"]}))
        roles = source.get("roles", [])
        if isinstance(roles, str):
            roles = [roles]
        for role in roles:
            if role.casefold() in {"content", "critical", "trunk"}:
                observations.append(("role", "has_role", role.casefold()))
            else:
                observations.append(("finding", "states", {"text": "Historical role label: " + role}))
        for field in ("relevance", "disposition", "readability", "contributor_basis"):
            if source.get(field):
                observations.append(("finding", "states", {"text": "Historical " + field + ": " + json.dumps(source[field], ensure_ascii=False)}))
        for index, (kind, predicate, value) in enumerate(observations):
            assertion_id = _id("legacy-assessment", sid, index)
            add("assertions", dict(assertion_id=assertion_id, kind=kind, predicate=predicate, subject_artifact_id=aid,
                                   value_json=value, basis="legacy_attested", assessed_at=stamp, temporal_scope_json={"note": "Original assessment date/scope may be unknown; migration time is not event time."}))
            add("prior_run_records", dict(record_id=_id("legacy-assessment-map", sid, index), import_id=import_id,
                                         prior_record_kind="assertion", prior_record_id=sid + ":assessment:" + str(index), assertion_id=assertion_id,
                                         lineage_json=[{"run_id": "legacy-" + manifest_sha[:24], "export_id": "legacy-bundle", "manifest_sha256": manifest_sha}], limitations=HISTORICAL))
    next(row["record"] for row in rows if row["table"] == "prior_run_imports")["scope_json"]["artifact_ids"] = sorted({subject, *artifacts.values()})
    captures = {}
    for op_id, (spec, receipt, result) in operations.items():
        base = "operations/" + op_id + "/"
        request_hash = _sha(_path(root, base + "request.json").read_bytes())
        receipt_hash = _sha(_path(root, base + "receipt.json").read_bytes()) if receipt else None
        intact = receipt is not None and set(receipt.get("files", {})) == {"request.json", "stdout", "stderr"}
        if intact:
            for name, expected in receipt["files"].items():
                path = root / base / name
                intact = intact and path.is_file() and _sha(path.read_bytes()) == expected
        if not intact:
            pending.append({"operation_id": op_id, "reason": "Missing or changed original request/receipt/output; no inspection inferred."})
        add("operation_links", dict(operation_id=op_id, assignment_id=None, budget_event_id=None, request_sha256=request_hash,
                                    receipt_sha256=receipt_hash, execution_state="succeeded" if intact and receipt["outcome"] == "ok" else "failed" if intact else "unknown",
                                    verification_state="legacy_attested"))
        for rank, sid in enumerate((result or {}).get("source_ids", [])):
            add("discoveries", dict(discovery_id=_id("legacy-operation-discovery", op_id, rank), artifact_id=artifacts[sid], operation_id=op_id,
                                    rank=rank, observed_title=sources[sid].get("title", "")))
        if intact and receipt.get("outcome") == "ok" and spec.get("kind") == "read":
            sid = spec["request"]["source_id"]
            cid = _id("legacy-capture", op_id)
            captures[op_id] = cid
            add("captures", dict(capture_id=cid, artifact_id=artifacts[sid], operation_id=op_id, version_id=None,
                                 format=spec["request"].get("format", "unknown"), relative_path=base + "stdout", sha256=receipt["files"]["stdout"],
                                 capture_extent_json={"body": {"attestation": "Original retained response", "limitations": "Captured extent and completeness were not verified during migration."}},
                                 extraction_status="legacy_attested", receipt_sha256=receipt_hash,
                                 captured_at=_legacy_time(receipt.get("finished"), stamp), limitations=HISTORICAL))
    for path in sorted((root / "operations").glob("*/result.json")):
        if path.parent.name in operations:
            continue
        result = graph._read_json(path.read_bytes())
        pending.append({"operation_id": path.parent.name, "reason": "Index retained without original request; discovery is a historical seed, not an operation admission."})
        for rank, sid in enumerate(result.get("source_ids", [])):
            add("discoveries", dict(discovery_id=_id("legacy-orphan-discovery", path.parent.name, rank), artifact_id=artifacts[sid],
                                    supplied_seed_id="legacy-index:" + path.parent.name, rank=rank, observed_title=sources[sid].get("title", "")))
    contract, _ = _read(root, "contract.json")
    question_ids = {q["id"] for q in contract["questions"]}

    def lead(original, sid, origin_key, return_id=None):
        value = original.get("value")
        if not isinstance(value, str) or not value:
            pending.append({"lead_id": str(original.get("id")), "reason": "Legacy lead lacks wording; raw ledger is retained."})
            return
        questions = [q for q in original.get("questions", []) if q in question_ids]
        unknown = [q for q in original.get("questions", []) if q not in question_ids]
        if unknown or not questions:
            pending.append({"lead_id": str(original.get("id")), "reason": "Legacy lead has unknown or absent question scope; original observation is retained without inventing authority.", "unknown_questions": unknown, "original": original})
        if not questions:
            # A lead origin requires a real contract question. Preserve this
            # unscoped observation as historical text until that is reconciled.
            assertion_id = _id("legacy-unscoped-lead", origin_key, original.get("id"))
            add("assertions", dict(assertion_id=assertion_id, kind="finding", predicate="states", subject_artifact_id=artifacts[sid],
                                   value_json={"text": "Historical lead with unresolved question scope: " + json.dumps(original, ensure_ascii=False, sort_keys=True)},
                                   basis="legacy_attested", assessed_at=stamp, temporal_scope_json={"note": "Original lead scope is unresolved; migration did not assign a question."}))
            add("prior_run_records", dict(record_id=_id("legacy-unscoped-lead-map", origin_key, original.get("id")), import_id=import_id,
                                         prior_record_kind="assertion", prior_record_id="unscoped-lead:" + origin_key + ":" + str(original.get("id")), assertion_id=assertion_id,
                                         lineage_json=[{"run_id": "legacy-" + manifest_sha[:24], "export_id": "legacy-bundle", "manifest_sha256": manifest_sha}], limitations=HISTORICAL))
            return
        lid = _id("legacy-lead", origin_key, original.get("id"))
        kind = {"linked-record": "linked_record", "question-gap": "observed_gap"}.get(original.get("kind"), original.get("kind", "observed_gap"))
        if kind not in {"vocabulary", "linked_record", "neighborhood", "observed_gap", "inferred_gap"}:
            kind = "observed_gap"
        add("leads", dict(lead_id=lid, kind=kind, original_value=value, normalized_key=" ".join(value.casefold().split()),
                          significance=original.get("reason") or HISTORICAL, disposition="pending"))
        locator = None
        read_id = original.get("read_id")
        if read_id in captures and operations[read_id][0]["request"]["source_id"] == sid:
            locator = _id("legacy-lead-locator", origin_key, original.get("id"))
            add("locators", dict(locator_id=locator, capture_id=captures[read_id], locator_kind="metadata",
                                 locator_json={"provider_metadata": {"original_locator": original.get("locator", "Not recorded"), "limitations": HISTORICAL}}))
        for q in questions:
            add("lead_origins", dict(origin_id=_id("legacy-lead-origin", origin_key, original.get("id"), q), lead_id=lid,
                                     return_id=return_id, returned_lead_id=original["id"] if return_id else None,
                                     locator_id=locator, question_id=q, original_value=value,
                                     origin_basis="Historical source " + sid + "; locator: " + original.get("locator", "Not recorded") + "; original disposition: " + original.get("status", "unknown")))
        add("prior_run_records", dict(record_id=_id("legacy-lead-map", origin_key, original.get("id")), import_id=import_id,
                                     prior_record_kind="lead", prior_record_id=origin_key + ":" + str(original.get("id")), lead_id=lid,
                                     lineage_json=[{"run_id": "legacy-" + manifest_sha[:24], "export_id": "legacy-bundle", "manifest_sha256": manifest_sha}], limitations=HISTORICAL))

    def inspection(return_id, raw, sid, relative, payload_sha, receipt_sha=None):
        actor_id_ = actor(raw.get("actor"))
        add("reader_returns", dict(return_id=return_id, actor_id=actor_id_, assignment_id=None, file_path=relative, payload_sha256=payload_sha,
                                   receipt_sha256=receipt_sha, recorded_budget_event=None, verification_state="legacy_attested", limitations=HISTORICAL))
        read_id = raw.get("read_id")
        if read_id in captures and sid in artifacts and operations[read_id][0]["request"]["source_id"] == sid and raw.get("coverage") and raw.get("inspection") in {"full", "partial"}:
            iid = _id("legacy-inspection", return_id)
            add("inspections", dict(inspection_id=iid, artifact_id=artifacts[sid], return_id=return_id, actor_id=actor_id_,
                                    inspection_extent=raw["coverage"], method="Historical explicit reader attestation", inspected_at=stamp,
                                    provenance_basis="legacy_attested", limitations=HISTORICAL + " Migration timestamp; exact inspection time unknown."))
            add("inspection_captures", dict(inspection_id=iid, capture_id=captures[read_id],
                                            inspected_extent_json={"body": {"attestation": raw["coverage"], "limitations": HISTORICAL}}))
            add("prior_run_records", dict(record_id=_id("legacy-inspection-map", return_id), import_id=import_id,
                                         prior_record_kind="inspection", prior_record_id=return_id, inspection_id=iid,
                                         lineage_json=[{"run_id": "legacy-" + manifest_sha[:24], "export_id": "legacy-bundle", "manifest_sha256": manifest_sha}], limitations=HISTORICAL))
        else:
            pending.append({"return_id": return_id, "reason": "Original return retained, but missing capture or explicit extent prevents even an attested inspection."})
        for entry in raw.get("leads", []):
            lead(dict(entry, read_id=read_id), sid, return_id, return_id)

    for path in sorted((root / "reader-returns").glob("*.json")):
        data = path.read_bytes()
        original = graph._read_json(data)
        raw = original.get("return", {})
        sid = original.get("source_id")
        rid = raw.get("id", path.stem)
        if sid not in artifacts:
            pending.append({"return_id": rid, "reason": "Unknown return source; original retained without a fabricated identity."})
            continue
        expected = original.get("receipt_sha256")
        if not isinstance(expected, str) or not graph.HASH.fullmatch(expected):
            expected = None
        inspection(rid, raw, sid, str(path.relative_to(root)), _sha(data), expected)
    for sid, source in sources.items():
        for index, declared in enumerate(source.get("inspections", [])):
            rid = _id("legacy-ledger-return", sid, index)
            raw = dict(declared, id=rid, inspection=declared.get("inspection", "partial"), leads=[])
            relative = "migration/ledger-attestations/" + rid + ".json"
            data = _bytes({"return": raw, "source_id": sid, "migration_ledger_attestation": True,
                           "original_ledger_sha256": manifest.get("ledger_sha256"), "limitations": HISTORICAL})
            extra[relative] = data
            inspection(rid, raw, sid, relative, _sha(data))
        for entry in source.get("leads", []):
            lead(entry, sid, "ledger:" + sid)
    for claim in ledger.get("claims", []):
        if not claim.get("id") or not claim.get("text"):
            pending.append({"claim": "unknown", "reason": "Incomplete historical claim remains in the original ledger."})
            continue
        cid = _id("legacy-claim", claim["id"])
        add("claims", dict(claim_id=cid, text=claim["text"], basis="legacy_attested", temporal_scope_json={}, comparison_scope_json={},
                           status="draft", reason=HISTORICAL + " Original basis: " + str(claim.get("basis", "unknown"))))
        for op_id in claim.get("reads", []):
            if op_id not in captures:
                continue
            lid = _id("legacy-claim-locator", claim["id"], op_id)
            add("locators", dict(locator_id=lid, capture_id=captures[op_id], locator_kind="metadata",
                                 locator_json={"provider_metadata": {"original_locator": claim.get("locator", "Unknown"), "limitations": HISTORICAL}}))
            add("claim_evidence", dict(evidence_id=_id("legacy-claim-evidence", claim["id"], op_id), claim_id=cid, claim_revision=1,
                                      locator_id=lid, stance="support", rationale="Historical claimed support; not freshly inspected during migration."))
        for q in ledger.get("questions", []):
            if q.get("id") in question_ids and claim["id"] in q.get("claims", []):
                add("claim_questions", dict(claim_id=cid, claim_revision=1, question_id=q["id"], relationship="partly_answers"))
        add("prior_run_records", dict(record_id=_id("legacy-claim-map", claim["id"]), import_id=import_id, prior_record_kind="claim", prior_record_id=claim["id"], claim_id=cid,
                                     lineage_json=[{"run_id": "legacy-" + manifest_sha[:24], "export_id": "legacy-bundle", "manifest_sha256": manifest_sha}], limitations=HISTORICAL))
    return rows, extra, pending


def _migrate_legacy(source_root, destination_root, run_id, actor_id, ledger_path):
    source, destination = Path(source_root).resolve(), Path(destination_root).resolve()
    _require(source.is_dir() and not (source / "graph").exists(), "invalid_packet", "Migration source must be an old bundle without a graph")
    _require(not destination.exists() and not destination.is_relative_to(source) and not source.is_relative_to(destination),
             "permission_denied", "Migration needs a new separate destination; source is read-only")
    graph._id(run_id)
    graph._id(actor_id)
    originals = _tree(source)
    _require("contract.json" in originals and "bundle.json" in originals, "pending_dependency", "Legacy bundle contract metadata is missing")
    ledger_file = Path(ledger_path).resolve() if ledger_path is not None else source / "ledger.json"
    ledger_data = ledger_file.read_bytes() if ledger_file.is_file() else _bytes({"sources": [], "claims": [], "questions": []})
    ledger = graph._read_json(ledger_data)
    manifest = dict(schema_version=1, kind="legacy-migration", ledger_sha256=_sha(ledger_data),
                    files=[_file_entry(p, data, "original") for p, data in sorted(originals.items())],
                    limitations=[HISTORICAL, "Missing old receipts, scopes, ancestry or reader events remain explicit limitations; migration does not establish their absence historically."])
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".legacy-migration-", dir=destination.parent))
    try:
        for path, data in originals.items():
            _save(temporary / path, data)
        _save(temporary / "migration/manifest.json", _bytes(manifest))
        _save(temporary / "migration/original-ledger.json", ledger_data)
        graph.initialize(temporary, actor_id, run_id)
        with graph.snapshot(temporary) as connection:
            rows, extra, pending = _legacy_plan(temporary, ledger, manifest, actor_id, run_id, connection)
            packet = dict(schema_version=1, batch_id="legacy-migration", actor_id=actor_id, assignment_id=None, packet_kind="migrate",
                          contract_sha256=_sha(originals["contract.json"]), submitted_at=graph._now(), mutations=rows)
        graph._packet(packet)
        graph._normal_mutations(packet)
        proof_files = dict(originals, **extra)
        proof_files["migration/manifest.json"] = _bytes(manifest)
        proof_files["migration/original-ledger.json"] = ledger_data
        _retain_proof(temporary, "migration/proof.json", packet, proof_files)
        _save(temporary / "migration/packet.json", _bytes(packet))
        receipt = graph.ingest(temporary, temporary / "migration/packet.json", evidence_validator=evidence_validator)
        _save(temporary / "migration/receipt.json", _bytes(dict(receipt, limitations=manifest["limitations"], pending=pending)))
        _require({p: _sha(data) for p, data in originals.items()} == {p: _sha(data) for p, data in _tree(source).items()},
                 "integrity_error", "Legacy source changed during migration; original was not rewritten")
        graph._sync_dir(temporary)
        os.rename(temporary, destination)
        graph._sync_dir(destination.parent)
        return dict(receipt, command="migrate-legacy", destination=str(destination), migration_manifest_sha256=_sha(_bytes(manifest)),
                    pending=pending, limitations=manifest["limitations"])
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def migrate_legacy(source_root, destination_root, *, run_id, actor_id, ledger_path=None):
    return ingestion._safe(_migrate_legacy, source_root, destination_root, run_id, actor_id, ledger_path)
