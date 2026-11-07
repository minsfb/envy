"""Graph-derived follow-up coverage and retained allocation/stop reviews.

No provider calls, alternative inventory, or inferred relevance. Graph facts
establish recorded work; coordinator bounds remain explicit attestations.
"""

from collections import defaultdict
from contextlib import nullcontext
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import research_graph as graph


OPEN = {"pending", "leased", "running", "blocked"}
BOUND_KINDS = {"out-of-scope", "duplicate", "diminishing-value", "budget-deferred",
               "depth-deferred", "access-blocked", "unreadable", "user-deferred"}
UNRESOLVED_KINDS = {"allocation-deferred", "discretionary-unresolved"}


def _modules():
    # Delay the import to keep the original recorder's module loading acyclic.
    import research_bundle as bundle
    work = bundle.graph_component("research_graph_work")
    return work, work.bundle, work.budget


def _require(condition, message, **details):
    if not condition:
        raise graph.GraphError("pending_dependency", message, **details)


def _sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


def _executable(work):
    return work["state"] in OPEN and not work.get("terminal_unavailable", False)


def _url(value):
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            return None
        # Fragments are navigation within a record, not distinct research assets.
        return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, parsed.query, ""))
    except ValueError:
        return None


def _items(root, conn, works):
    """Keep terms and every record reference, deduplicating targets per question."""
    _, bundle, _ = _modules()
    contract = bundle.contract_at(root)
    scope_basis = _sha({key: contract.get(key) for key in ("purpose", "scope", "questions", "providers")})
    questions = [r[0] for r in conn.execute("SELECT question_id FROM questions ORDER BY question_id")]
    # Index once per snapshot. References can outnumber work items by orders of
    # magnitude; avoid reference x work x origin scans and per-link SQL calls.
    canonical = dict(conn.execute("SELECT artifact_id,canonical_id FROM canonical_artifacts"))
    work_by_id = {w["work_id"]: w for w in works}
    work_by_lead, work_by_reference, work_by_artifact = defaultdict(set), defaultdict(set), defaultdict(set)
    for origin in conn.execute("SELECT work_id,lead_id,reference_id FROM work_origins"):
        question = work_by_id[origin["work_id"]]["question_id"]
        work_by_lead[origin["lead_id"], question].add(origin["work_id"])
        work_by_reference[origin["reference_id"], question].add(origin["work_id"])
    for candidate in works:
        identity = canonical.get(candidate["artifact_id"])
        if identity:
            work_by_artifact[identity, candidate["question_id"]].add(candidate["work_id"])
    inspections, return_scopes, own_urls, resolutions = (defaultdict(set) for _ in range(4))
    for row in conn.execute("""SELECT c.canonical_id,s.question_id,i.inspection_id FROM inspections i
            JOIN inspection_scopes s USING(inspection_id) JOIN canonical_artifacts c USING(artifact_id)
            JOIN reader_returns r USING(return_id)
            WHERE s.coverage='sufficient' AND i.provenance_basis='native' AND r.verification_state='verified'
              AND NOT EXISTS(SELECT 1 FROM inspection_withdrawals x WHERE x.inspection_id=i.inspection_id)"""):
        inspections[row[0], row[1]].add(row[2])
    for row in conn.execute("SELECT i.return_id,s.question_id FROM inspections i JOIN inspection_scopes s USING(inspection_id)"):
        return_scopes[row[0]].add(row[1])
    for row in conn.execute("SELECT artifact_id,observed_url FROM discoveries"):
        own_urls[row[0]].add(_url(row[1] or ""))
    for row in conn.execute("""SELECT reference_id,target_artifact_id FROM reference_resolutions r
            WHERE NOT EXISTS(SELECT 1 FROM reference_resolutions n WHERE n.replaces_resolution_id=r.resolution_id)"""):
        resolutions[row[0]].add(row[1])
    lead_origins = defaultdict(list)
    for row in conn.execute("""SELECT o.*,w.generation FROM lead_origins o
            LEFT JOIN reader_returns r ON r.return_id=o.return_id
            LEFT JOIN assignments a ON a.assignment_id=r.assignment_id
            LEFT JOIN work_items w ON w.work_id=a.work_id ORDER BY o.origin_id"""):
        lead_origins[row["lead_id"]].append(dict(row))
    items = []
    for lead in conn.execute("SELECT * FROM leads ORDER BY lead_id"):
        source_origins = lead_origins[lead["lead_id"]]
        scopes = sorted({o["question_id"] for o in source_origins if o["question_id"]}) or questions
        for question in scopes:
            scoped = [o for o in source_origins if o["question_id"] in (None, question)]
            related = [work_by_id[identity] for identity in sorted(work_by_lead[lead["lead_id"], question])]
            generations = [o["generation"] + 1 for o in scoped if o["generation"] is not None]
            items.append({"key": "lead:" + lead["lead_id"] + ":" + question,
                          "kind": lead["kind"], "label": lead["original_value"], "question": question,
                          "generation": min(generations) if generations else 1,
                          "origins": [o["origin_id"] for o in scoped], "work_ids": [w["work_id"] for w in related],
                          "graph_disposition": lead["disposition"], "graph_reason": lead["reason"],
                          "inspection_ids": [], "surface": None})
    grouped = {}
    refs = conn.execute("""SELECT x.*, s.return_id, w.generation FROM reference_occurrences x
        JOIN reference_scans s USING(scan_id)
        LEFT JOIN reader_returns r ON r.return_id=s.return_id
        LEFT JOIN assignments a ON a.assignment_id=r.assignment_id
        LEFT JOIN work_items w ON w.work_id=a.work_id ORDER BY x.reference_id""").fetchall()
    for ref in refs:
        target_url = _url(ref["original_value"])
        # Keep opaque IDs, relative record links and unresolved title mentions.
        # Only a source's own navigation anchors are not new research assets.
        if ref["kind"] == "link" and ref["original_value"].strip().startswith("#"):
            continue
        if target_url and target_url in own_urls[ref["from_artifact_id"]]:
            continue
        targets = resolutions[ref["reference_id"]]
        target = next(iter(targets)) if len(targets) == 1 else ref["target_artifact_id"]
        canonical_target = canonical.get(target)
        if canonical_target and canonical_target == canonical.get(ref["from_artifact_id"]):
            continue
        target_key = canonical_target or target_url or ref["original_value"].strip()
        scopes = sorted(return_scopes[ref["return_id"]]) or questions
        for question in scopes:
            key = "reference:" + _sha(target_key)[:24] + ":" + question
            item = grouped.setdefault(key, {"key": key, "kind": "reference", "label": ref["label"] or ref["original_value"],
                "question": question, "generation": (ref["generation"] + 1) if ref["generation"] is not None else 1,
                "origins": [], "reference_ids": [], "values": [], "urls": [], "work_ids": [], "inspection_ids": [],
                "surface": urlsplit(target_url).hostname if target_url else None, "graph_disposition": None, "graph_reason": None})
            item["generation"] = min(item["generation"], (ref["generation"] + 1) if ref["generation"] is not None else 1)
            item["origins"].append(ref["reference_id"])
            item["reference_ids"].append(ref["reference_id"])
            item["values"].append(ref["original_value"])
            if target_url:
                item["urls"].append(target_url)
            item["inspection_ids"].extend(inspections[canonical_target, question])
            item["work_ids"].extend(work_by_artifact[canonical_target, question] | work_by_reference[ref["reference_id"], question])
    items.extend(grouped.values())
    # One coverage obligation per known surface, not one compulsory read per
    # initial search hit. References above still retain their individual targets.
    known_hosts = {host for policy in contract["providers"].values() for host in policy.get("reference_hosts", [])}
    surfaces = {}
    for row in conn.execute("""SELECT d.*, w.question_id, w.generation FROM discoveries d
            LEFT JOIN operation_links o ON o.operation_id=d.operation_id
            LEFT JOIN assignments a ON a.assignment_id=o.assignment_id
            LEFT JOIN work_items w ON w.work_id=a.work_id ORDER BY d.discovery_id"""):
        url = _url(row["observed_url"] or "")
        host = urlsplit(url).hostname if url else None
        if host not in known_hosts:
            continue
        canonical_target = canonical.get(row["artifact_id"])
        for question in [row["question_id"]] if row["question_id"] else questions:
            key = "surface:" + host + ":" + question
            item = surfaces.setdefault(key, {"key": key, "kind": "source_surface", "label": "Candidate records on " + host,
                "question": question, "generation": row["generation"] or 0, "origins": [], "work_ids": [],
                "inspection_ids": [], "candidate_artifact_ids": [], "surface": host,
                "graph_disposition": None, "graph_reason": None})
            item["generation"] = min(item["generation"], row["generation"] or 0)
            item["origins"].append(row["discovery_id"])
            item["candidate_artifact_ids"].append(canonical_target)
            item["inspection_ids"].extend(inspections[canonical_target, question])
            item["work_ids"].extend(identity for identity in work_by_artifact[canonical_target, question]
                                    if work_by_id[identity]["kind"] == "read")
    items.extend(surfaces.values())
    for item in items:
        for field in ("origins", "reference_ids", "values", "urls", "work_ids", "inspection_ids", "candidate_artifact_ids"):
            if field in item:
                item[field] = sorted(set(item[field]))
        related = [work_by_id[identity] for identity in item["work_ids"]]
        completed = [w for w in related if w["verified_complete"]
                     and (item["kind"] not in {"reference", "linked_record", "source_surface"} or w["kind"] == "read")]
        item["status"] = ("inspected" if item["inspection_ids"] else "completed" if completed
                          else "scheduled" if any(_executable(w) for w in related) else "unassigned")
        # Work disposition is deliberately excluded: a blanket graph deferral is
        # visible here until an item-specific typed review is retained.
        item["basis_sha256"] = _sha({"scope": scope_basis, **{k: item[k] for k in (
            "key", "label", "question", "origins", "generation", "graph_disposition", "graph_reason")}})
    return sorted(items, key=lambda item: (item["generation"], item["question"], item["key"]))


def _search_chain(operation, indexed, budget, limit):
    """Resolve a terminal page to one unchanged query's original first page."""
    chain, seen = [], set()
    while operation and operation not in seen and len(chain) < limit:
        seen.add(operation)
        original = indexed(operation)
        if original is None:
            return []
        _, record, result = original
        spec = record["spec"]
        if spec["kind"] != "search":
            return []
        if not chain and (result.get("pagination") != "complete" or result.get("next_cursor") is not None):
            return []
        chain.append((operation, result))
        if spec["request"].get("cursor") is None:
            return chain
        parent = spec.get("research", {}).get("parent_operation")
        previous = indexed(parent) if parent else None
        if previous is None or not budget.is_pagination(previous[1]["spec"], spec, previous[2]):
            return []
        operation = parent
    return []


def _cycles(root, works, conn, rounds):
    _, _, budget = _modules()
    with budget.locked(Path(root)) as state:
        attempts = [dict(e) for e in state["events"] if e["type"] == "admit"]
    originals = {}

    def indexed(operation):
        if operation not in originals:
            try:
                originals[operation] = budget._indexed_admission(Path(root), operation, attempts)
            except (ValueError, OSError):
                originals[operation] = None
        return originals[operation]

    links = {r["operation_id"]: r["work_id"] for r in conn.execute(
        "SELECT o.operation_id,a.work_id FROM operation_links o JOIN assignments a USING(assignment_id)")}
    parents = {}
    for row in conn.execute("SELECT work_id,parent_operation_id FROM work_origins WHERE parent_operation_id IS NOT NULL"):
        parents.setdefault(row[0], set()).add(links.get(row[1]))
    cycles = {"followup-" + str(i): [] for i in range(1, rounds + 1)}
    for candidate in works:
        if not candidate["verified_complete"] or candidate["generation"] < 1:
            continue
        bucket = "followup-" + str(candidate["generation"])
        if bucket not in cycles or candidate["activity_purpose"] == "coverage-review":
            continue
        if candidate["kind"] == "search":
            operations = [operation for operation, identity in links.items() if identity == candidate["work_id"]]
            page_works, empty = set(), False
            for operation in operations:
                chain = _search_chain(operation, indexed, budget, min(len(attempts), 1024))
                if not chain:
                    continue
                page_works.update(links[page] for page, _ in chain if page in links)
                empty |= all(result.get("source_ids") == [] and not result.get("sources") for _, result in chain)
            children = [w for w in works if w["kind"] == "read" and w["verified_complete"]
                        and w["question_id"] == candidate["question_id"] and w["generation"] == candidate["generation"]
                        and page_works.intersection(parents.get(w["work_id"], set()))]
            if children or empty:
                cycles[bucket].append({"entry_work_id": candidate["work_id"], "question": candidate["question_id"],
                    "branch": candidate["branch"], "read_work_ids": [w["work_id"] for w in children],
                    "outcome": "searched-and-inspected" if children else "empty-search"})
        elif candidate["kind"] == "read":
            # A directly linked inspection is also a full child-record cycle.
            from_reads = [w for w in works if w["work_id"] in parents.get(candidate["work_id"], set()) and w["kind"] == "read"]
            if from_reads:
                cycles[bucket].append({"entry_work_id": candidate["work_id"], "question": candidate["question_id"],
                    "branch": candidate["branch"], "read_work_ids": [candidate["work_id"]], "outcome": "linked-record-inspected"})
    return cycles


def _reviews(root):
    _, bundle, budget = _modules()
    with budget.locked(Path(root)) as state:
        events = [dict(e) for e in state["events"] if e["type"] == "followup-checkpoint"]
    result = []
    for event in events:
        path = graph._path(Path(root).resolve(), event["path"])
        _require(bundle.digest(path) == event["checkpoint_sha256"], "Changed follow-up checkpoint")
        result.append(bundle.read(path))
    return result


def status(root, conn=None):
    work, bundle, budget = _modules()
    root = Path(root)
    contract = bundle.contract_at(root)
    configuration = budget.allocations.config(contract)
    _require(configuration, "Protected follow-ups are not enabled")
    with nullcontext(conn) if conn is not None else graph.snapshot(root) as connection:
        works = []
        for row in connection.execute("SELECT * FROM work_items ORDER BY work_id"):
            candidate = graph._decoded(row)
            verified = False
            if candidate["state"] == "completed":
                try:
                    work._completion(root, connection, candidate)
                    verified = True
                except (graph.GraphError, ValueError):
                    pass
            metadata = candidate["request_json"].get("provider_metadata", {})
            terminal = candidate["state"] == "blocked" and any(_terminal_evidence(root, {"work_ids": [candidate["work_id"]]}, category)
                                                                  for category in ("access-blocked", "unreadable"))
            works.append({**candidate, "verified_complete": verified,
                          "terminal_unavailable": terminal,
                          "allocation": metadata.get("research_allocation"),
                          "activity_purpose": metadata.get("activity_purpose", "research")})
        items = _items(root, connection, works)
        reviews = _reviews(root)
        decisions = {entry["key"]: entry for review in reviews for entry in review["decisions"]}
        for item in items:
            decision = decisions.get(item["key"])
            item["reviewed_bound"] = decision if decision and decision["basis_sha256"] == item["basis_sha256"] else None
        surfaces = []
        for host in sorted({i["surface"] for i in items if i["surface"]}):
            same = [i for i in items if i["surface"] == host]
            surfaces.append({"host": host, "providers": sorted(p for p, cfg in contract["providers"].items()
                if host in cfg.get("reference_hosts", [])), "coverage_items": len(same),
                "reference_targets": sum(i["kind"] == "reference" for i in same),
                "candidate_artifact_ids": sorted({a for i in same for a in i.get("candidate_artifact_ids", [])}),
                "inspected_items": sum(bool(i["inspection_ids"]) for i in same),
                "unassigned": [i["key"] for i in same if i["status"] == "unassigned" and not i["reviewed_bound"]]})
        metadata = bundle.read(root / "bundle.json")
        plan = bundle.plan_snapshot(root, metadata["plan_sha256"])[0] if metadata.get("plan_sha256") else {"items": []}
        origins = [dict(row) for row in connection.execute("SELECT * FROM work_origins ORDER BY origin_id")]
        origin_work = {row["origin_id"]: row["work_id"] for row in origins}
        origins_by_work = defaultdict(list)
        for origin in origins:
            origins_by_work[origin["work_id"]].append(origin)
        capture_operations = dict(connection.execute("SELECT capture_id,operation_id FROM captures"))
        result = {"status": "pass", "run_id": connection.execute("SELECT run_id FROM graph_metadata").fetchone()[0],
                  "graph_sequence": connection.execute("SELECT COALESCE(MAX(sequence),0) FROM graph_events").fetchone()[0],
                  "research_sequence": connection.execute("SELECT COALESCE(MAX(sequence),0) FROM graph_events WHERE entity_kind <> 'export_manifests'").fetchone()[0],
                  "contract_sha256": metadata["contract_sha256"], "plan_sha256": metadata.get("plan_sha256"),
                  "items": items, "surfaces": surfaces,
                  # Items first saved closed have no graph work row. Their
                  # authoritative saved-plan bounds still limit completeness.
                  "plan_bounds": [{k: item[k] for k in ("id", "status", "reason")}
                                  for item in plan["items"] if item["status"] in {"bounded", "dropped"}],
                  "work": [{**{k: w[k] for k in ("work_id", "question_id", "branch", "generation", "allocation", "kind", "state", "verified_complete", "terminal_unavailable", "terminal_reason", "priority", "artifact_id", "required_scope_key", "request_json")},
                            "origins": origins_by_work[w["work_id"]]} for w in works],
                  "plan_items": plan["items"],
                  "plan_bindings": [{"plan_item_id": item["id"], "question_id": question,
                                     "work_id": origin_work.get(work._identifier("origin", metadata.get("plan_sha256"), item["id"], question))}
                                    for item in plan["items"] for question in graph.plan_item_questions(plan, item)],
                  "active_assignment_ids": [row[0] for row in connection.execute(
                      "SELECT assignment_id FROM assignments WHERE state IN ('leased','running','reconciling') ORDER BY assignment_id")],
                  "cycles": _cycles(root, works, connection, configuration["minimum_followup_rounds"])}
        with budget.locked(root) as state:
            spending = [e for e in state["events"] if e["type"] not in {"followup-checkpoint", "allocation-release"}]
            result["spending_sha256"] = spending[-1]["sha256"] if spending else None
        result["evidence_sha256"] = _sha({k: result[k] for k in ("research_sequence", "contract_sha256", "plan_sha256", "spending_sha256")})
    result["budget"] = budget.status(root)
    result["remaining_capacity"] = {
        "global": {metric: result["budget"]["limits"][metric] - result["budget"][metric] for metric in budget.allocations.METRICS},
        "allocations": {key: value["remaining"] for key, value in result["budget"]["protected_followups"]["buckets"].items()}}
    result["partial_basis_sha256"] = _sha({"evidence": result["evidence_sha256"],
        "funding": result["budget"]["protected_followups"]})
    result["unresolved"] = _unresolved(root, result, configuration, capture_operations)
    result["limitations"] = "Recorded work and typed coordinator bounds; not proof of relevance, query recall, complete extraction or runtime telemetry."
    return result


def _unresolved(root, report, configuration, capture_operations):
    """Delivery acknowledgments never become bounds or scheduler dispositions."""
    _, _, budget = _modules()
    originals = {}
    assessed = set(report["budget"]["assessed_artifact_ids"])

    def options(item, allocation, planned=False):
        intent = item.get("request_json", {})
        sources = {intent.get("source_id")}
        retained = planned and bool(intent.get("capture_ids"))
        if retained:
            sources = set()
            for capture in intent["capture_ids"]:
                operation = capture_operations[capture]
                if operation not in originals:
                    originals[operation] = budget.operation(root, operation)[1]["spec"]["request"]["source_id"]
                sources.add(originals[operation])
        result = []
        for source in sorted(sources, key=lambda value: value or ""):
            required, possible = {"active_minutes", "agent_minutes"}, set()
            if not planned:
                # No saved request fixes whether another retrieval or assessment
                # is needed. Unknown counts cannot prove local exhaustion.
                possible = ({"search_attempts"} if item["kind"] in {"vocabulary", "neighborhood", "question_gap"}
                            else {"read_attempts", "assessed_artifacts"})
            elif item["kind"] == "search":
                required.add("search_attempts")
            else:
                if not retained:
                    required.add("read_attempts")
                if source not in assessed:
                    required.add("assessed_artifacts")
            option = {"work_id": item.get("work_id"), "allocation": allocation,
                      "assessment_source_id": source, "required_metrics": sorted(required),
                      "possible_metrics": sorted(possible)}
            result.append({"option_id": _sha(option), **option})
        return result

    work_options = {item["work_id"]: options(item, item["allocation"], planned=True) for item in report["work"]
                    if not item["verified_complete"] and not item["terminal_unavailable"]}
    work_by_id = {item["work_id"]: item for item in report["work"]}
    targets = [{**item, "discovery_allocation": ("baseline" if item["generation"] == 0 else
               "followup-" + str(item["generation"]) if item["generation"] <= configuration["minimum_followup_rounds"] else "extension")}
               for item in report["items"] if item["status"] in {"scheduled", "unassigned"} and not item["reviewed_bound"]]
    for target in targets:
        resolving = [work_by_id[identity] for identity in target["work_ids"]
                     if work_by_id[identity]["question_id"] == target["question"] and _executable(work_by_id[identity])
                     and (target["kind"] not in {"reference", "linked_record", "source_surface"} or work_by_id[identity]["kind"] == "read")]
        target["funding_options"] = [option for item in resolving for option in work_options[item["work_id"]]]
        if not resolving:
            target["funding_options"] = options(target, target["discovery_allocation"])
        allocations = {option["allocation"] for option in target["funding_options"]}
        target["allocation"] = next(iter(allocations)) if len(allocations) == 1 else None
    targets += [{**item, "key": "work:" + item["work_id"]} for item in report["work"]
                if not item["verified_complete"] and not item["terminal_unavailable"]]
    for target in targets:
        if "funding_options" not in target:
            target["funding_options"] = work_options[target["work_id"]]
        target["basis_sha256"] = _sha({"snapshot": report["partial_basis_sha256"], "target": target})
    return sorted(targets, key=lambda item: item["key"])


def _partial_review(root, request, report):
    """Validate explicit gaps, without asserting that research is settled."""
    work, bundle, _ = _modules()
    _require(request.get("partial_basis_sha256") == report["partial_basis_sha256"],
             "Partial stop review is stale; use the current graph, plan and allocation status")
    _require(not request.get("release") and not request.get("no_opportunity") and not request.get("decisions"),
             "Partial stop cannot release reserves, declare no opportunity or add resolved bounds")
    _require(not report["budget"]["active_actors"] and not report["active_assignment_ids"],
             "Close research clocks and settle active assignments before a partial stop")
    _require(not any(w["state"] == "completed" and not w["verified_complete"] for w in report["work"]),
             "Partial stop cannot excuse unverified completed work")
    _require(not any(item["unresolved_operation_ids"] for item in work.recovery_status(root)["items"]),
             "Partial stop needs original worker operations reconciled")
    _require(bundle.graph_component("research_graph_ingest").ingestion_status(root)["complete"],
             "Partial stop needs every original operation and reader return synchronized")
    targets = {item["key"]: item for item in report["unresolved"]}
    entries = request.get("unresolved")
    _require(isinstance(entries, list) and entries, "Partial stop needs explicit unresolved decisions")
    seen = set()
    for entry in entries:
        _require(isinstance(entry, dict), "Invalid unresolved decision")
        fields = {"key", "basis_sha256", "disposition", "reason", "priority"}
        fields |= ({"blocked_options"} if "blocked_options" in entry else {"allocation", "metric"}) if entry.get("disposition") == "allocation-deferred" else {"capacity_remaining"}
        _require(set(entry) == fields and entry.get("disposition") in UNRESOLVED_KINDS,
                 "Unresolved decision needs a typed partial-stop disposition")
        key = entry["key"]
        _require(key in targets and key not in seen and entry["basis_sha256"] == targets[key]["basis_sha256"],
                 "Unresolved decision needs a unique current item")
        _require(bundle.nonempty(entry["reason"]) and entry["priority"] in {"high", "normal", "low"},
                 "Unresolved decision needs an item-specific reason and priority")
        target = targets[key]
        options = {option["option_id"]: option for option in target["funding_options"]}
        spending = report["budget"]
        buckets = spending["protected_followups"]["buckets"]
        _require(options and all(option["allocation"] in buckets for option in options.values()),
                 "Unresolved work needs its original protected allocation")
        available = lambda value, metric: value > 0 if metric.endswith("minutes") else value >= 1
        if entry["disposition"] == "allocation-deferred":
            blocked = entry.get("blocked_options")
            if "blocked_options" not in entry:
                _require(len(options) == 1, "Multiple resolving options need individual exhaustion proofs")
                blocked = [{"option_id": next(iter(options)), "allocation": entry["allocation"], "metric": entry["metric"]}]
            _require(isinstance(blocked, list) and len(blocked) == len(options),
                     "Allocation deferral must account for every resolving option")
            checked = set()
            for proof in blocked:
                _require(isinstance(proof, dict) and set(proof) == {"option_id", "allocation", "metric"}
                         and proof["option_id"] in options and proof["option_id"] not in checked,
                         "Allocation deferral needs unique current resolving options")
                option, metric = options[proof["option_id"]], proof["metric"]
                _require(proof["allocation"] == option["allocation"] and metric in option["required_metrics"],
                         "Allocation deferral must name the item's actual funding and relevant metric")
                _require(not available(buckets[option["allocation"]]["remaining"][metric], metric) and
                         available(spending["limits"][metric] - spending[metric], metric),
                         "Allocation deferral requires an exhausted local allowance with global capacity remaining")
                checked.add(proof["option_id"])
        else:
            _require(entry["capacity_remaining"] is True and any(all(available(buckets[option["allocation"]]["remaining"][m], m) and
                     available(spending["limits"][m] - spending[m], m) for m in option["required_metrics"] + option["possible_metrics"])
                     for option in options.values()),
                     "Discretionary unresolved work must retain usable capacity; record actual allocation deferral otherwise")
        seen.add(key)
    _require(seen == set(targets), "Partial stop must retain every unresolved frontier and work item",
             missing_keys=sorted(set(targets) - seen))
    return request


def admission_check(root, conn, assigned):
    _, bundle, budget = _modules()
    if not budget.allocations.config(bundle.contract_at(root)) or assigned["generation"] < 2:
        return
    if assigned["request_json"].get("provider_metadata", {}).get("activity_purpose") == "coverage-review":
        return
    report = status(root, conn)
    unresolved = [i["key"] for i in report["items"] if i["generation"] < assigned["generation"]
                  and i["status"] == "unassigned" and not i["reviewed_bound"]]
    _require(not unresolved, "Promote or explicitly review earlier follow-up leads before deeper research", frontier_keys=unresolved)


def _cap_reached(report):
    spending = report["budget"]
    return any(spending[metric] >= spending["limits"][metric] for metric in (
        "active_minutes", "agent_minutes", "search_attempts", "read_attempts", "assessed_artifacts"))


def _terminal_evidence(root, item, expected):
    work, _, budget = _modules()
    with graph.snapshot(root) as conn:
        operations = [r[0] for r in conn.execute("SELECT o.operation_id,a.work_id FROM operation_links o JOIN assignments a USING(assignment_id)")
                      if r[1] in item["work_ids"]]
        unreadable = {r[0] for r in conn.execute("""SELECT o.operation_id,w.work_id FROM current_source_outcomes o
            JOIN locators l ON l.locator_id=o.locator_id JOIN captures c ON c.capture_id=l.capture_id
            JOIN canonical_artifacts a ON a.artifact_id=o.artifact_id
            JOIN canonical_artifacts b ON b.artifact_id=c.artifact_id
            JOIN work_items w ON w.artifact_id=o.artifact_id AND w.question_id=o.question_id
                AND w.required_scope_key=o.required_scope_key
            WHERE o.readability='unreadable' AND c.operation_id=o.operation_id
                AND a.canonical_id=b.canonical_id""") if r[1] in item["work_ids"]} if expected == "unreadable" else set()
    with budget.locked(root) as state:
        latest = {e["source_id"]: e for e in state["events"] if e["type"] == "admit" and e["kind"] == "read"}
        for event in latest.values():
            if event["operation"] not in operations or any(e["type"] == "reopen" and e["source_id"] == event["source_id"]
                                                          and e["id"] > event["id"] for e in state["events"]):
                continue
            original = work._admission(root, event["operation"], require_receipt=True)
            if expected == "unreadable" and event["operation"] in unreadable:
                # A successful fetch can contain unintelligible material. This
                # requires a scoped source outcome and locator in actual bytes;
                # an unread title or a scheduling bound still cannot establish it.
                return True
            if original["receipt"]["outcome"] != "ok" and budget.classification(root, state, event) == expected:
                return True
    return False


def _release_preflight(report, bucket):
    spending = report["budget"]
    buckets = spending["protected_followups"]["buckets"]
    _require(bucket in buckets and bucket != "extension", "Unknown protected reserve")
    _require(not buckets[bucket]["released_at_checkpoint"], "Allocation reserve already released")
    _require(not any(value["allocation"] == bucket for value in spending["active_activity"].values()),
             "Close allocation activity before releasing its unused reserve")
    _require(all(value >= 0 for value in buckets[bucket]["remaining"].values()), "Cannot release an overspent reserve")


def _require_complete(report, decisions=()):
    _require(not decisions and not any(i["reviewed_bound"] for i in report["items"]),
             "Bounded frontier needs a discretionary stop, not complete coverage")
    pending = [w["work_id"] for w in report["work"] if not w["verified_complete"]]
    _require(not pending and not report["plan_bounds"],
             "Complete coverage still has unverified planned work; use a discretionary stop for bounded work",
             work_ids=pending, plan_item_ids=[item["id"] for item in report["plan_bounds"]])


def _publish(root, path, record):
    """Resume this exact publication without duplicate checkpoint/release events."""
    _, bundle, budget = _modules()
    digest = bundle.digest(path)
    with budget.locked(root) as state:
        existing = [e for e in state["events"] if e["type"] == "followup-checkpoint"
                    and e["checkpoint_id"] == record["id"]]
        _require(not existing or len(existing) == 1 and existing[0]["checkpoint_sha256"] == digest,
                 "Checkpoint identity differs from retained journal")
        if not existing:
            budget.append(root, state, "followup-checkpoint", checkpoint_id=record["id"],
                          path=str(path.relative_to(root)), checkpoint_sha256=digest)
        released = [e for e in state["events"] if e["type"] == "allocation-release"
                    and e["allocation"] == record.get("release")]
        _require(not released or len(released) == 1 and released[0]["checkpoint"] == {"id": record["id"], "sha256": digest},
                 "Reserve was released by a different checkpoint")
    if record.get("release") and not released:
        budget.release(root, record["release"], {"id": record["id"], "sha256": digest})
    return {"status": "pass", "id": record["id"], "sha256": digest,
            "evidence_sha256": record["snapshot"]["evidence_sha256"],
            "released": record.get("release"), "stop": record.get("stop")}


def checkpoint(root, request):
    work, bundle, budget = _modules()
    root = Path(root)
    _require(isinstance(request, dict) and set(request) <= {"id", "actor", "reason", "decisions", "release", "stop", "no_opportunity", "partial_basis_sha256", "unresolved"},
             "Unknown follow-up checkpoint fields")
    _require(request.get("stop") == "partial" or not ({"partial_basis_sha256", "unresolved"} & set(request)),
             "Unresolved decisions belong only to a partial stop")
    graph._id(request.get("id"), filename=True)
    _require(bundle.nonempty(request.get("reason")), "Checkpoint needs a source/coverage-specific rationale")
    with graph.writer(root):
        with graph.snapshot(root) as conn:
            actor = graph._get(conn, "actors", request.get("actor"))
            _require(actor and actor["role"] == "coordinator", "Follow-up checkpoint requires the coordinator")
            report = status(root, conn)
        directory = root / "followup-checkpoints"
        path = directory / (request["id"] + ".json")
        if path.exists():
            saved = bundle.read(path)
            proposed = dict(request, decisions=request.get("decisions", []))
            _require({k: v for k, v in saved.items() if k != "snapshot"} == proposed,
                     "Checkpoint identity already exists with different intent")
            if saved.get("stop") == "complete":
                _require_complete(report, saved.get("decisions", []))
            if saved.get("stop") == "partial":
                _partial_review(root, saved, report)
            release = saved.get("release")
            already = report["budget"]["protected_followups"]["buckets"].get(release, {}).get("released_at_checkpoint")
            if not already:
                _require(saved["snapshot"]["evidence_sha256"] == report["evidence_sha256"],
                         "Interrupted checkpoint is stale; append a new review")
                _require(saved["snapshot"]["cycles"] == report["cycles"],
                         "Interrupted checkpoint cycle proof changed; append a new review")
                if release:
                    _release_preflight(report, release)
            return dict(_publish(root, path, saved), idempotent=True)
        items = {i["key"]: i for i in report["items"]}
        decisions, seen = request.get("decisions", []), set()
        _require(isinstance(decisions, list), "Checkpoint decisions must be a list")
        for decision in decisions:
            _require(isinstance(decision, dict) and set(decision) == {"key", "basis_sha256", "disposition", "reason"}, "Invalid follow-up decision")
            key = decision["key"]
            _require(key in items and key not in seen and decision["basis_sha256"] == items[key]["basis_sha256"], "Decision needs a unique current frontier item")
            _require(decision["disposition"] in BOUND_KINDS and bundle.nonempty(decision["reason"]), "Decision needs a typed bound and item-specific reason")
            if decision["disposition"] == "budget-deferred":
                _require(_cap_reached(report), "Budget deferral requires a reached cap; record discretionary prioritization otherwise")
            # Access/legibility are not inferred from absent output. These bounds
            # require recorded terminal outcomes for linked source work.
            if decision["disposition"] in {"access-blocked", "unreadable"}:
                _require(_terminal_evidence(root, items[key], decision["disposition"]),
                         "Access/legibility bound needs an original failed read and matching classification; an unread link is not denial")
            seen.add(key)
        effective = {i["key"] for i in report["items"] if i["reviewed_bound"]} | seen
        release, stop = request.get("release"), request.get("stop")
        no_opportunity = request.get("no_opportunity", {})
        _require(isinstance(no_opportunity, dict) and set(no_opportunity) <= set(report["cycles"])
                 and all(bundle.nonempty(v) for v in no_opportunity.values()), "Invalid no-opportunity explanation")
        for bucket in no_opportunity:
            generation = int(bucket.split("-")[1])
            _require(report["work"] and not any(w["generation"] < generation and _executable(w) for w in report["work"]),
                     "Finish or explicitly bound earlier work before declaring no follow-up opportunity")
            _require(not any(i["generation"] <= generation and i["status"] in {"scheduled", "unassigned"}
                             and i["key"] not in effective for i in report["items"]),
                     "Unresolved earlier leads contradict no follow-up opportunity")
        if release:
            _release_preflight(report, release)
            pending = [w["work_id"] for w in report["work"] if w["allocation"] == release and _executable(w)]
            _require(not pending, "Complete or explicitly bound allocation work before releasing its reserve", work_ids=pending)
            _require(any(w["allocation"] == "baseline" for w in report["work"]), "Baseline coverage must be planned before releasing reserves")
            generation = 0 if release == "baseline" else int(release.split("-")[1])
            unresolved = [i["key"] for i in report["items"] if i["generation"] <= generation and i["status"] == "unassigned" and i["key"] not in effective]
            _require(not unresolved, "Unassigned leads prevent reserve release", frontier_keys=unresolved)
            if release != "baseline":
                _require(report["cycles"][release] or release in no_opportunity, "Release needs a verified follow-up cycle or an explicit no-opportunity explanation")
                _require(not (release in no_opportunity and any(i["generation"] == generation and i["status"] == "scheduled" for i in report["items"])),
                         "Scheduled follow-ups contradict no-opportunity")
        if stop:
            _require(stop in {"complete", "discretionary", "cap", "user", "partial"}, "Unknown research stop kind")
            _require(not report["budget"]["active_actors"], "Stop research activity before the final stop review")
            if stop == "cap":
                _require(_cap_reached(report), "No research cap was reached; record a discretionary stop with remaining allowance")
            if stop in {"complete", "discretionary"}:
                unresolved = [i["key"] for i in report["items"] if i["status"] == "unassigned" and i["key"] not in effective]
                _require(not unresolved, "Stop review needs individual dispositions for unworked leads", frontier_keys=unresolved)
                _require(not any(_executable(w) for w in report["work"]), "Stop review still has executable work; retain its explicit graph bounds")
                _require(all(report["cycles"][bucket] or bucket in no_opportunity for bucket in report["cycles"]),
                         "Stop review needs two meaningful cycles or explicit no-opportunity explanations")
            if stop == "complete":
                _require_complete(report, decisions)
            if stop == "partial":
                _partial_review(root, request, report)
        record = {**request, "decisions": decisions, "snapshot": report}
        directory.mkdir(mode=0o700, exist_ok=True)
        bundle.write(path, record)
        return _publish(root, path, record)


def validate_stop(root):
    _, bundle, budget = _modules()
    if not budget.allocations.config(bundle.contract_at(root)):
        return
    reports = [r for r in _reviews(root) if r.get("stop")]
    _require(reports, "Protected research needs a final follow-up stop checkpoint")
    current = status(root)
    _require(not current["budget"]["active_actors"], "Research activity remains open")
    _require(reports[-1]["snapshot"]["evidence_sha256"] == current["evidence_sha256"],
             "Follow-up stop review is stale; review the current graph, contract and plan")
    _require(reports[-1]["snapshot"]["cycles"] == current["cycles"],
             "Follow-up cycle proof changed; review the current query and inspection evidence")
    if reports[-1]["stop"] == "complete":
        _require_complete(current, reports[-1].get("decisions", []))
    if reports[-1]["stop"] == "partial":
        _partial_review(root, reports[-1], current)
    return reports[-1]


def partial_delivery(review, context):
    """A narrowly scoped delivery allowance; leave structural stop_checks intact."""
    report = review["snapshot"]
    frontier = [item for item in report["unresolved"] if not item["key"].startswith("work:")]
    frontier_keys = {item["key"] for item in frontier}
    reference_scopes = {(reference, item["question"]) for item in frontier for reference in item.get("reference_ids", [])}
    work_ids = {item["work_id"] for item in report["unresolved"] if item["key"].startswith("work:")}
    obligations = set()
    for obligation in context["obligation_bindings"]:
        scopes = obligation["question_scopes"]
        if scopes and all(scope["status"] in {"resolved", "bounded"} or scope["status"] in {"unassigned", "pending"} and
                ((obligation["lead_id"] is not None and
                "lead:" + obligation["lead_id"] + ":" + scope["question_id"] in frontier_keys)
                or (obligation["reference_id"], scope["question_id"]) in reference_scopes) for scope in scopes):
            obligations.add(obligation["obligation_id"])
    stops = context["stop_checks"]
    allowed = {"pending_work_ids", "nonterminal_obligation_ids", "unresolved_obligation_scopes"}
    _require(not any(value for key, value in stops.items() if key not in allowed | {"settled"}),
             "Partial delivery still needs settled source scopes, questions, claims and original evidence")
    _require(set(stops["pending_work_ids"]) <= work_ids and
             set(stops["nonterminal_obligation_ids"]) <= obligations and
             all(scope["obligation_id"] in obligations for scope in stops["unresolved_obligation_scopes"]),
             "Partial delivery has unacknowledged graph work or question-specific obligations")
    pending_plan = {item["id"] for item in report["plan_items"] if item["status"] in {"queued", "active"}}
    _require(all(binding["work_id"] in work_ids for binding in report["plan_bindings"]
                 if binding["plan_item_id"] in pending_plan),
             "Partial delivery needs each pending plan item bound to acknowledged unresolved work")
    return {"status": "partial", "coverage_complete": False, "checkpoint_id": review["id"],
            "pending_plan_item_ids": sorted(pending_plan), "obligation_ids": sorted(obligations),
            "unresolved": review["unresolved"], "remaining_capacity": report["remaining_capacity"]}
