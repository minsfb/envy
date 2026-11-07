"""Admission limits for declared research work, not a process sandbox.

Activity windows are actor-attested research time, including reasoning between
calls. The helper observes command timeouts, not model runtime. All callers must
use admission and close activity windows when they stop research. Delivery does
not use this module's admission path.
"""

from contextlib import contextmanager
import fcntl
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import tempfile
import time


DEFAULTS = {"search_attempts": 250, "read_attempts": 400,
            "assessed_artifacts": 200, "active_minutes": 120,
            "agent_minutes": 300, "followup_generations": 3,
            "attempts_per_request": 3, "request_timeout_seconds": 240}
STAGES = ("baseline", "priority", "deep")
TERMINAL = ("access-blocked", "unreadable")
_allocation_spec = importlib.util.spec_from_file_location(
    "research_allocation", Path(__file__).with_name("research_allocation.py"))
allocations = importlib.util.module_from_spec(_allocation_spec)
_allocation_spec.loader.exec_module(allocations)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


def write(path, value):
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def policy(contract):
    overrides = contract.get("research_budget", {})
    require(isinstance(overrides, dict) and set(overrides) <= set(DEFAULTS),
            "unknown research budget fields")
    defaults = dict(DEFAULTS)
    if allocations.config(contract):
        defaults.update(active_minutes=330, agent_minutes=600)
    result = dict(defaults, **overrides)
    for key, value in result.items():
        require(type(value) in (int, float) and math.isfinite(value) and value > 0,
                "research budget limits must be positive finite numbers")
        if key not in ("active_minutes", "agent_minutes", "request_timeout_seconds"):
            require(type(value) is int, "research count limits must be integers")
    configuration = allocations.config(contract)
    if configuration:
        require(result["followup_generations"] >= configuration["minimum_followup_rounds"],
                "followup generation cap must permit the protected minimum rounds")
    return result


def initialize(root):
    """Resume storage initialization without replacing an existing journal."""
    if (root / "research-budget.json").exists():
        with locked(root):
            pass
    else:
        write(root / "research-budget.json", {"version": 1, "events": []})
    (root / "reader-returns").mkdir(mode=0o700, exist_ok=True)


@contextmanager
def locked(root):
    with (root / ".research-budget.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = load(root / "research-budget.json")
        require(state.get("version") == 1 and isinstance(state.get("events"), list),
                "invalid research budget journal")
        previous = None
        for index, event in enumerate(state["events"]):
            record = {k: v for k, v in event.items() if k != "sha256"}
            require(event["id"] == index + 1 and event.get("previous") == previous and
                    event.get("sha256") == sha(record), "changed research budget journal")
            previous = event["sha256"]
        yield state


def append(root, state, event_type, **fields):
    events = state["events"]
    event = dict(fields, id=len(events) + 1, at=time.time(), type=event_type,
                 previous=events[-1]["sha256"] if events else None)
    event["sha256"] = sha(event)
    events.append(event)
    write(root / "research-budget.json", state)
    return event


def accounting(state, current=None):
    current = time.time() if current is None else current
    active, intervals = {}, []
    stage = "baseline"
    for event in state["events"]:
        if event["type"] == "activity-start":
            active[event["actor"]] = event["at"]
        elif event["type"] == "activity-stop":
            start = active.pop(event["actor"])
            intervals.append((start, event["at"]))
        elif event["type"] == "stage":
            stage = event["stage"]
    intervals.extend((start, current) for start in active.values())
    effort = sum(max(0, end - start) for start, end in intervals)
    merged = []
    for start, end in sorted(intervals):
        end = max(start, end)
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(end, merged[-1][1])
        else:
            merged.append([start, end])
    attempts = [event for event in state["events"] if event["type"] == "admit"]
    assessments = {event["source_id"] for event in state["events"] if event["type"] == "assess" or
                   (event["type"] == "admit" and event["kind"] == "read" and event["assess"])}
    return {"search_attempts": sum(event["kind"] == "search" for event in attempts),
            "read_attempts": sum(event["kind"] == "read" for event in attempts),
            "assessed_artifacts": len(assessments),
            "active_minutes": sum(end - start for start, end in merged) / 60,
            "agent_minutes": effort / 60, "active_actors": sorted(active), "stage": stage,
            "assessed_artifact_ids": sorted(assessments),
            **allocations.accounting(state, current)}


def enabled(root):
    contract = load(root / "contract.json")
    require(hashlib.sha256((root / "contract.json").read_bytes()).hexdigest() ==
            load(root / "bundle.json")["contract_sha256"], "changed contract; retain amendment history")
    require(contract["requirements"].get("research_budget"), "research budget is not enabled")
    return policy(contract)


def activity(root, actor, action, *, allocation=None, purpose=None, branch=None):
    limits = enabled(root)
    configuration = allocations.config(load(root / "contract.json"))
    require(nonempty(actor), "activity needs actor")
    require(action in ("start", "stop"), "activity action must be start or stop")
    with locked(root) as state:
        totals = accounting(state)
        running = actor in totals["active_actors"]
        require(running == (action == "stop"), "actor activity already " + ("started" if running else "stopped"))
        fields = {}
        if action == "start" and configuration:
            fields = allocations.activity_fields(configuration, allocation, purpose, branch)
            capacity = allocations.capacities(configuration, limits, state, totals)
            require(allocations.remaining_seconds(allocation, limits, totals, capacity) > 0,
                    "protected research time budget exhausted: " + allocation)
        elif action == "start":
            require(all(value is None for value in (allocation, purpose, branch)),
                    "classified activity requires protected_followups")
        append(root, state, "activity-" + action, actor=actor, **fields)
        return accounting(state)


def validate_amendment(old, new, state):
    """Preserve the journal across contract changes; never reclassify old work."""
    configuration = allocations.validate_amendment(old, new, state)
    limits = policy(new)
    if configuration:
        totals = accounting(state)
        require(all(totals[metric] <= limits[metric] for metric in allocations.METRICS),
                "amendment cannot lower research caps below charged history")
        capacity = allocations.capacities(configuration, limits, state, totals)
        require(not capacity["overruns"], "amendment must retain charged allocation capacity")


def release(root, bucket, checkpoint_reference):
    """Record a release already authorized by the bundle's graph checkpoint.

    This low-level API trusts the caller's graph verification; it is not exposed
    as an unverified CLI. The retained ID/hash binds the release to that proof.
    """
    limits = enabled(root)
    configuration = allocations.config(load(root / "contract.json"))
    require(configuration is not None, "allocation release requires protected_followups")
    require(isinstance(checkpoint_reference, dict) and set(checkpoint_reference) == {"id", "sha256"}
            and nonempty(checkpoint_reference.get("id"))
            and isinstance(checkpoint_reference.get("sha256"), str)
            and len(checkpoint_reference["sha256"]) == 64
            and all(c in "0123456789abcdef" for c in checkpoint_reference["sha256"]),
            "allocation release needs a verified checkpoint ID and SHA256")
    require(bucket in allocations.shares(configuration) and bucket != "extension",
            "release needs a protected allocation bucket")
    with locked(root) as state:
        totals = accounting(state)
        require(not any(e["type"] == "allocation-release" and e["allocation"] == bucket for e in state["events"]),
                "allocation reserve already released")
        require(not any(entry["allocation"] == bucket for entry in totals["active_activity"].values()),
                "close allocation activity before releasing its unused reserve")
        capacity = allocations.capacities(configuration, limits, state, totals)
        entry = capacity["buckets"][bucket]
        require(all(value >= 0 for value in entry["remaining"].values()),
                "cannot release an overspent allocation reserve")
        return append(root, state, "allocation-release", allocation=bucket,
                      checkpoint=checkpoint_reference, spent=entry["spent"], released=entry["guaranteed_remaining"])


def stage(root, value, reason):
    enabled(root)
    require(value in STAGES and nonempty(reason), "stage needs value and rationale")
    with locked(root) as state:
        require(STAGES.index(value) >= STAGES.index(accounting(state)["stage"]),
                "research stage milestones cannot move backwards")
        append(root, state, "stage", stage=value, reason=reason)


def operation(root, key):
    require(isinstance(key, str) and key and all(c.isalnum() or c in "_-" for c in key),
            "invalid operation ID")
    path = root / "operations" / key
    return path, load(path / "request.json")


def classification(root, state, admission):
    choices = [event for event in state["events"] if event["type"] == "classify" and
               event["operation"] == admission["operation"]]
    path, _ = operation(root, admission["operation"])
    if choices:
        choice = choices[-1]
        recovery = choice.get("cancelled_admission")
        if recovery:
            require(hashlib.sha256((path / "request.json").read_bytes()).hexdigest() == recovery["request_sha256"],
                    "recovered admission request changed")
            # A late real receipt takes precedence over an earlier local recovery.
            # Recovery never manufactures a provider outcome or hides success.
            if not (path / "receipt.json").exists():
                return choice["classification"]
        else:
            return choice["classification"]
    if not (path / "receipt.json").exists():
        return "in-progress"
    outcome = load(path / "receipt.json")["outcome"]
    return {"ok": "ok", "timeout": "transient"}.get(outcome, "unknown")


def classify(root, key, value, reason):
    enabled(root)
    require(value in TERMINAL + ("transient", "unknown") and nonempty(reason),
            "failure classification needs category and observed basis")
    path, _ = operation(root, key)
    require((path / "receipt.json").exists() and load(path / "receipt.json")["outcome"] != "ok",
            "classification requires completed unsuccessful operation")
    with locked(root) as state:
        require(any(e["type"] == "admit" and e["operation"] == key for e in state["events"]),
                "operation lacks research admission")
        append(root, state, "classify", operation=key, classification=value, reason=reason)


def reopen(root, source_id, reason):
    enabled(root)
    require(nonempty(source_id) and nonempty(reason), "reopen needs source and changed-access/capability basis")
    with locked(root) as state:
        append(root, state, "reopen", source_id=source_id, reason=reason)


def is_pagination(previous, current, result):
    """Exact continuation of one provider/query/route, not a semantic expansion."""
    return (previous["kind"] == current["kind"] == "search"
            and previous["provider"] == current["provider"]
            and all(previous.get(key) == current.get(key) for key in ("search_branch", "input_kind"))
            and all(previous["request"].get(key) == current["request"].get(key) for key in ("query", "filters", "cap"))
            and current["request"].get("cursor") is not None
            and current["request"]["cursor"] == result.get("next_cursor"))


def _indexed_admission(root, key, attempts):
    matching = [event for event in attempts if event["operation"] == key]
    require(len(matching) == 1, "pagination ancestry needs one original admission")
    event = matching[0]
    path, record = operation(root, key)
    spec = record["spec"]
    require(spec["id"] == key and spec.get("research_admission") == {"event": event["id"], "sha256": event["sha256"]}
            and event["fingerprint"] == sha({field: spec[field] for field in ("kind", "provider", "request")})
            and all(spec.get(field) == event.get(field) for field in ("kind", "actor"))
            and all(spec.get("research", {}).get(field) == event.get(field) for field in ("parent_operation", "stage", "branch")),
            "pagination ancestry differs from its original admission")
    if "allocation" in event:
        require(spec.get("research", {}).get("allocation") == event["allocation"]
                and spec.get("research", {}).get("purpose", "research") == event["purpose"],
                "pagination ancestry allocation differs from its original admission")
    require((path / "receipt.json").is_file() and (path / "result.json").is_file(),
            "pagination ancestry needs successful indexed originals")
    receipt, result = load(path / "receipt.json"), load(path / "result.json")
    require(receipt.get("outcome") == "ok" and set(receipt.get("files", {})) == {"request.json", "stdout", "stderr"}
            and all(not (path / name).is_symlink() and hashlib.sha256((path / name).read_bytes()).hexdigest() == digest
                    for name, digest in receipt["files"].items())
            and result.get("receipt_sha256") == hashlib.sha256((path / "receipt.json").read_bytes()).hexdigest(),
            "pagination ancestry needs unchanged successful receipt and index")
    return event, record, result


def validate_source_origin(root, spec, attempts, origin=None):
    """Read-only source provenance check shared by planning and admission.

    origin.read_id identifies the inspected source. parent_operation identifies
    the immediate operation. They may differ only along an exact, successful
    pagination chain rooted at that same read. Spending remains in admit().
    """
    if origin is None and spec.get("plan_item"):
        plan = load(root / "plans" / (spec["plan_sha256"] + ".json"))
        item = next(item for item in plan["items"] if item["id"] == spec["plan_item"])
        if "parent_operation" in item.get("graph", {}):
            require(spec["research"].get("parent_operation") == item["graph"]["parent_operation"],
                    "budget ancestry differs from planned immediate parent")
        origin = item.get("origin")
    if not origin or not origin.get("read_id"):
        return
    source_read = origin["read_id"]
    parent = spec["research"].get("parent_operation")
    require(parent is not None, "budget ancestry differs from planned read origin")
    source_event, source_record, _ = _indexed_admission(root, source_read, attempts)
    require(source_record["spec"]["kind"] == "read" and source_record["spec"]["request"].get("source_id") == origin.get("source_id"),
            "pagination ancestry differs from planned source read")
    current, visited = spec, set()
    while parent != source_read:
        require(parent not in visited and len(visited) < min(len(attempts), 1024),
                "pagination ancestry is cyclic or exceeds its traversal bound")
        visited.add(parent)
        event, record, result = _indexed_admission(root, parent, attempts)
        require(is_pagination(record["spec"], current, result),
                "budget ancestry differs from planned read origin: not an exact pagination continuation")
        require(event["generation"] == source_event["generation"] + 1,
                "pagination ancestry cannot lower or change source-derived generation")
        current, parent = record["spec"], event["parent_operation"]
        require(parent is not None, "pagination ancestry does not reach the planned source read")


def admission_request(event):
    """Reconstruct declared intent only; never a provider dispatch or receipt."""
    intent = event.get("request_intent")
    require(isinstance(intent, dict) and set(intent) == {"started", "transport", "contract_sha256", "spec"},
            "admission predates retained request intent; original evidence is required")
    request = json.loads(json.dumps(intent))
    spec = request["spec"]
    require(spec.get("id") == event["operation"] and "research_admission" not in spec
            and sha({key: spec[key] for key in ("kind", "provider", "request")}) == event["fingerprint"]
            and all(spec.get(key) == event.get(key) for key in ("actor", "kind", "timeout")),
            "admission request intent differs from reservation")
    if "allocation" in event:
        require(spec.get("research", {}).get("allocation") == event["allocation"] and
                spec.get("research", {}).get("purpose", "research") == event["purpose"],
                "admission request allocation differs from reservation")
    spec["research_admission"] = {"event": event["id"], "sha256": event["sha256"]}
    return request


def admit(root, spec, create, request_context=None):
    """Reserve attempts and assessment slots under one cross-process lock.

The callback persists the operation request before any process/tool dispatch.
An interrupted dispatch remains a spent reservation, never a refunded attempt.
"""
    limits = enabled(root)
    configuration = allocations.config(load(root / "contract.json"))
    info = spec.get("research")
    require(isinstance(info, dict) and info.get("stage") in STAGES and nonempty(info.get("branch"))
            and "parent_operation" in info, "operation needs research stage, branch and parent_operation")
    assess = info.get("assess", spec["kind"] == "read")
    require(type(assess) is bool and (not assess or spec["kind"] == "read"),
            "assessment admission must be boolean and only applies to reads")
    with locked(root) as state:
        totals = accounting(state)
        require(spec["actor"] in totals["active_actors"], "start actor research activity before admission")
        activity_fields = totals["active_activity"][spec["actor"]]
        capacity = None
        if configuration:
            allocations.activity_fields(configuration, info.get("allocation"),
                                        activity_fields["purpose"], info["branch"])
            require(info["allocation"] == activity_fields["allocation"] and info["branch"] == activity_fields["branch"],
                    "operation allocation and branch must match actor research activity")
            require(info.get("purpose", "research") == activity_fields["purpose"],
                    "operation purpose must match actor research activity")
            capacity = allocations.capacities(configuration, limits, state, totals)
        count = "search_attempts" if spec["kind"] == "search" else "read_attempts"
        require(totals[count] < limits[count], "research budget exhausted: " + count)
        if capacity:
            require(capacity["buckets"][info["allocation"]]["remaining"][count] >= 1,
                    "protected research allocation exhausted: " + info["allocation"] + "/" + count)
        remaining = min(limits["active_minutes"] - totals["active_minutes"],
                        (limits["agent_minutes"] - totals["agent_minutes"]) / len(totals["active_actors"])) * 60
        require(remaining > 0, "research time budget exhausted")
        if capacity:
            remaining = min(remaining, allocations.remaining_seconds(info["allocation"], limits, totals, capacity))
            require(remaining > 0, "protected research time budget exhausted: " + info["allocation"])
        attempts = [e for e in state["events"] if e["type"] == "admit"]
        require(not any(e["operation"] == spec["id"] for e in attempts), "operation already admitted")
        parent = info["parent_operation"]
        validate_source_origin(root, spec, attempts)
        if parent is None:
            require(info["stage"] == "baseline" and nonempty(info.get("root_basis")),
                    "baseline roots need explicit scope/coverage basis; preserve derived ancestry")
            generation = 0
        else:
            ancestors = [event for event in attempts if event["operation"] == parent]
            require(ancestors, "parent operation was not admitted")
            ancestor = ancestors[0]
            require(classification(root, state, ancestor) == "ok", "parent operation must have succeeded")
            path, record = operation(root, parent)
            require((path / "result.json").exists(), "parent operation must be indexed")
            same_source = (ancestor["kind"] == spec["kind"] == "read" and
                           record["spec"]["request"]["source_id"] == spec["request"]["source_id"])
            generation = ancestor["generation"] + (ancestor["kind"] == "read" and not same_source)
            if ancestor["kind"] == "search":
                if spec["kind"] == "read":
                    require(spec["request"]["source_id"] in load(path / "result.json")["source_ids"],
                            "search-derived read needs an indexed returned source")
                else:
                    pagination = is_pagination(record["spec"], spec, load(path / "result.json"))
                    if not pagination:
                        require(nonempty(info.get("derivation_basis")) and nonempty(info.get("origin_locator")) and
                                info.get("origin_source_id") in load(path / "result.json")["source_ids"],
                                "metadata-derived search needs returned source, locator and derivation basis")
                        generation += 1
        retry_of = info.get("retry_of")
        fingerprint = sha({key: spec[key] for key in ("kind", "provider", "request")})
        matches = [e for e in attempts if e["fingerprint"] == fingerprint]
        if matches:
            previous = matches[-1]
            refreshed = (info.get("refresh_of") == previous["operation"] and nonempty(info.get("refresh_basis")))
            reopened = any(e["type"] == "reopen" and e["source_id"] == spec["request"].get("source_id") and
                           e["id"] > previous["id"] for e in state["events"])
            if not refreshed and not reopened:
                require(retry_of == previous["operation"], "repeated request needs explicit retry_of or documented refresh")
                require(classification(root, state, previous) == "transient", "retry requires observed transient failure")
                epoch = max((e["id"] for e in matches if e.get("fresh_epoch")), default=0)
                require(sum(e["id"] >= epoch for e in matches) < limits["attempts_per_request"],
                        "retry attempts exhausted")
            require(parent == previous["parent_operation"], "retry cannot reset ancestry")
            generation = previous["generation"]
        else:
            refreshed, reopened = False, False
            require(retry_of is None, "retry_of does not match the exact prior request")
        require(generation <= limits["followup_generations"], "research generation budget exhausted")
        require(info["stage"] != "baseline" or generation == 0, "follow-up cannot be labelled baseline")
        require(info["stage"] != "priority" or generation <= 1, "deeper generation must use deep stage")
        if configuration:
            allocations.lineage(configuration, info["allocation"], activity_fields["purpose"], generation)
        source_id = spec["request"].get("source_id")
        if spec["kind"] == "read":
            require(not assess or source_id in totals["assessed_artifact_ids"] or
                    totals["assessed_artifacts"] < limits["assessed_artifacts"],
                    "research budget exhausted: assessed_artifacts")
            if capacity and assess and source_id not in totals["assessed_artifact_ids"]:
                require(capacity["buckets"][info["allocation"]]["remaining"]["assessed_artifacts"] >= 1,
                        "protected research allocation exhausted: " + info["allocation"] + "/assessed_artifacts")
            latest_reopen_event_id = max((e["id"] for e in state["events"] if e["type"] == "reopen" and
                                         e["source_id"] == source_id), default=0)
            require(not any(e["source_id"] == source_id and e["id"] > latest_reopen_event_id and
                            classification(root, state, e) in TERMINAL
                            for e in attempts if e["kind"] == "read"),
                    "source is terminal for this run; record changed access/capability before reopening")
        timeout = spec.get("timeout", 120)
        require(type(timeout) in (int, float) and math.isfinite(timeout) and timeout > 0,
                "operation timeout must be positive and finite")
        spec["timeout"] = min(timeout, limits["request_timeout_seconds"], remaining)
        reservation = {"operation": spec["id"], "kind": spec["kind"], "actor": spec["actor"],
                       "source_id": source_id, "stage": info["stage"], "branch": info["branch"],
                       "parent_operation": parent, "generation": generation, "fingerprint": fingerprint,
                       "timeout": spec["timeout"], "assess": assess, "fresh_epoch": refreshed or reopened}
        if configuration:
            reservation.update(allocation=info["allocation"], purpose=activity_fields["purpose"])
        if request_context is not None:
            require(set(request_context) == {"started", "transport", "contract_sha256"}, "invalid request context")
            reservation["request_intent"] = dict(request_context, spec=json.loads(json.dumps(
                {key: value for key, value in spec.items() if key != "research_admission"})))
        event = append(root, state, "admit", **reservation)
        spec["research_admission"] = {"event": event["id"], "sha256": event["sha256"]}
        return create()


def assess(root, read_id, actor, *, assignment_validator=None):
    """Admit assessment of already retrieved content, without inventing a fetch."""
    limits = enabled(root)
    configuration = allocations.config(load(root / "contract.json"))
    path, request = operation(root, read_id)
    require(request["spec"]["kind"] == "read" and (path / "receipt.json").is_file() and
            load(path / "receipt.json")["outcome"] == "ok", "assessment needs successful read")
    source_id = request["spec"]["request"]["source_id"]
    with locked(root) as state:
        totals = accounting(state)
        require(actor in totals["active_actors"], "assessment needs active research actor")
        require(totals["active_minutes"] < limits["active_minutes"] and
                totals["agent_minutes"] < limits["agent_minutes"], "research time budget exhausted")
        require(source_id in totals["assessed_artifact_ids"] or
                totals["assessed_artifacts"] < limits["assessed_artifacts"],
                "research budget exhausted: assessed_artifacts")
        fields = {}
        if configuration:
            fields = totals["active_activity"][actor]
            original = next((event for event in state["events"] if event["type"] == "admit"
                             and event["operation"] == read_id), None)
            require(original is not None, "assessment needs original protected admission")
            if (Path(root) / "graph" / "research.sqlite").is_file():
                require(callable(assignment_validator), "protected graph assessment needs its assignment validator")
                binding = assignment_validator(root, read_id, actor, fields)
                fields = dict(fields, graph_assignment_id=binding["assignment_id"])
                generation, branch = binding["generation"], binding["branch"]
            else:
                generation, branch = original["generation"], original["branch"]
            allocations.lineage(configuration, fields["allocation"], fields["purpose"], generation)
            require(fields["branch"] == branch, "assessment branch must match assigned read")
            capacity = allocations.capacities(configuration, limits, state, totals)
            require(allocations.remaining_seconds(fields["allocation"], limits, totals, capacity) > 0,
                    "protected research time budget exhausted: " + fields["allocation"])
            require(source_id in totals["assessed_artifact_ids"] or
                    capacity["buckets"][fields["allocation"]]["remaining"]["assessed_artifacts"] >= 1,
                    "protected research allocation exhausted: " + fields["allocation"] + "/assessed_artifacts")
        append(root, state, "assess", read_id=read_id, source_id=source_id, actor=actor, **fields)


def status(root):
    limits = enabled(root)
    configuration = allocations.config(load(root / "contract.json"))
    with locked(root) as state:
        result = accounting(state)
        attempts = [event for event in state["events"] if event["type"] == "admit"]
        result["by_stage"] = {stage: sum(e["stage"] == stage for e in attempts) for stage in STAGES}
        result["by_generation"] = {str(g): sum(e["generation"] == g for e in attempts)
                                   for g in sorted({e["generation"] for e in attempts})}
        result["by_branch"] = {branch: sum(e["branch"] == branch for e in attempts)
                               for branch in sorted({e["branch"] for e in attempts})}
        result["limits"] = limits
        result["accounting_basis"] = "Declared research activity windows; assessment slots reserved at first read admission."
        result["delivery_limited"] = False
        if configuration:
            result["protected_followups"] = allocations.capacities(configuration, limits, state, result)
        result["global_overruns"] = {metric: result[metric] - limits[metric] for metric in allocations.METRICS
                                     if result[metric] > limits[metric]}
        return result


def correction_binding(root, returned):
    """Check an append-only correction without granting graph acceptance.

Corrections retain the original reader, evidence, locators, assertions and all
disclosed references/leads. This narrow form changes scope coverage/basis, not
the underlying inspection or its time. G2 separately requires a rejected,
unaccepted predecessor and validates the corrected native declaration.
"""
    link = returned.get("corrects")
    if link is None:
        return None
    require(isinstance(link, dict) and set(link) == {"return_id", "original_sha256", "reason"}
            and nonempty(link.get("reason")), "correction needs exact original linkage and reason")
    key = link.get("return_id")
    require(isinstance(key, str) and key and all(c.isalnum() or c in "_-" for c in key)
            and key != returned.get("id"), "invalid corrected return identity")
    path = Path(root) / "reader-returns" / (key + ".json")
    require(path.is_file() and not path.is_symlink(), "corrected original is unavailable")
    require(hashlib.sha256(path.read_bytes()).hexdigest() == link["original_sha256"], "corrected original hash differs")
    original = load(path)
    previous = original["return"]
    require(previous["id"] == key and all(previous.get(f) == returned.get(f)
            for f in ("read_id", "actor", "graph_assignment_id")), "correction changes original reader or assignment")
    require(isinstance(previous.get("graph"), dict) and isinstance(returned.get("graph"), dict),
            "corrections require structured native returns")

    require({k: v for k, v in previous.items() if k not in {"id", "corrects", "coverage", "inspection", "graph", "leads"}} ==
            {k: v for k, v in returned.items() if k not in {"id", "corrects", "coverage", "inspection", "graph", "leads"}},
            "correction changes the original interpretation")
    require(returned.get("inspection") == previous.get("inspection") or
            (previous.get("inspection") == "full" and returned.get("inspection") == "partial"),
            "correction cannot upgrade inspection coverage")

    def retained(before, after, label, changing=(), additions=False):
        require(isinstance(before, list) and isinstance(after, list), "correction needs " + label)
        old = {x["id"]: x for x in before}
        new = {x["id"]: x for x in after}
        require(len(old) == len(before) and len(new) == len(after) and
                (set(old) <= set(new) if additions else set(old) == set(new)),
                "correction omits or duplicates " + label)
        for key, item in old.items():
            require({k: v for k, v in item.items() if k not in changing} ==
                    {k: v for k, v in new[key].items() if k not in changing},
                    "correction changes disclosed " + label)
        return old, new

    before, after = previous["graph"], returned["graph"]
    require(before.get("inspected_at") == after.get("inspected_at"), "correction cannot invent a new inspection time")
    retained(before.get("locators", []), after.get("locators", []), "locators")
    retained(before.get("assertions", []), after.get("assertions", []), "assertions")
    scans, corrected_scans = retained(before.get("scans", []), after.get("scans", []), "scans", {"references"})
    for key, scan in scans.items():
        retained(scan["references"], corrected_scans[key]["references"], "references", additions=True)
    inspections, corrected_inspections = retained(before.get("inspections", []), after.get("inspections", []), "inspections", {"scopes"})
    for key, item in inspections.items():
        identity = lambda s: (s["question_id"], s["required_scope_key"], s["format_policy_sha256"])
        old = {identity(s): s for s in item.get("scopes", [])}
        new = {identity(s): s for s in corrected_inspections[key].get("scopes", [])}
        require(set(old) == set(new) and len(new) == len(corrected_inspections[key].get("scopes", [])),
                "correction changes original question scope")
        ranks = {"partial": 0, "sufficient": 1}
        require(all(new[k].get("coverage") in ranks and old[k].get("coverage") in ranks and
                    ranks[new[k]["coverage"]] <= ranks[old[k]["coverage"]] for k in old),
                "correction cannot upgrade sufficient coverage")
    leads, corrected_leads = retained(previous.get("leads", []), returned.get("leads", []), "leads", {"questions"}, additions=True)
    for key, lead in leads.items():
        require(set(lead["questions"]) <= set(corrected_leads[key]["questions"]), "correction drops a lead question origin")
    return {"return_id": link["return_id"], "original_sha256": link["original_sha256"], "original": original}


def reader_return(root, returned):
    """Preserve the worker's structured extraction before coordinator triage."""
    enabled(root)
    require(isinstance(returned, dict), "reader return must be an object")
    key = returned.get("id")
    require(isinstance(key, str) and key and all(c.isalnum() or c in "_-" for c in key),
            "invalid reader return ID")
    for field in ("read_id", "actor", "coverage", "expansion_review"):
        require(nonempty(returned.get(field)), "reader return needs " + field)
    require(returned.get("inspection") in ("full", "partial") and
            returned.get("relevance") in ("relevant", "irrelevant", "uncertain"),
            "reader return needs explicit inspection extent and relevance")
    path, request = operation(root, returned["read_id"])
    require(request["spec"]["kind"] == "read" and (path / "receipt.json").is_file() and
            load(path / "receipt.json")["outcome"] == "ok" and (path / "result.json").is_file(),
            "reader return requires a successful indexed read")
    questions = {q["id"] for q in load(root / "contract.json")["questions"]}
    leads, seen = returned.get("leads"), set()
    require(isinstance(leads, list), "reader return needs explicit lead list, empty if none")
    for lead in leads:
        require(isinstance(lead, dict) and nonempty(lead.get("id")) and lead["id"] not in seen,
                "reader return needs distinct lead IDs")
        seen.add(lead["id"])
        require(lead.get("kind") in ("vocabulary", "linked-record", "neighborhood", "question-gap"),
                "invalid returned lead kind")
        for field in ("value", "locator", "reason"):
            require(nonempty(lead.get(field)), "returned lead needs " + field)
        require(isinstance(lead.get("questions"), list) and lead["questions"] and
                all(isinstance(q, str) for q in lead["questions"]) and set(lead["questions"]) <= questions,
                "returned lead needs known questions")
    with locked(root) as state:
        destination = root / "reader-returns" / (key + ".json")
        require(not destination.exists(), "reader return already recorded; use a new ID for additions")
        correction = correction_binding(root, returned)
        if correction:
            original = correction["original"]
            require(any(e["type"] == "reader-return" and e["id"] == original["event"] and
                        e["return_id"] == correction["return_id"] and
                        e["payload_sha256"] == sha({k: v for k, v in original.items() if k != "event"})
                        for e in state["events"]), "correction lacks the original return journal event")
            require(original["source_id"] == request["spec"]["request"]["source_id"] and
                    original["receipt_sha256"] == hashlib.sha256((path / "receipt.json").read_bytes()).hexdigest(),
                    "correction changes original source or receipt")
        require(any((e["type"] == "admit" and e["operation"] == returned["read_id"] and e["assess"]) or
                    (e["type"] == "assess" and e["read_id"] == returned["read_id"])
                    for e in state["events"]), "admit substantive assessment before inspection/reader return")
        payload = {"return": returned, "source_id": request["spec"]["request"]["source_id"],
                   "receipt_sha256": hashlib.sha256((path / "receipt.json").read_bytes()).hexdigest()}
        committed = [e for e in state["events"] if e["type"] == "reader-return" and e["return_id"] == key]
        require(len(committed) <= 1, "reader return journal event is ambiguous")
        if committed:
            # Publication may have stopped after append but before the file.
            # Exact resubmission preserves that event and its observation time.
            event = committed[0]
            require(event["payload_sha256"] == sha(payload), "reader return differs from committed publication intent")
        else:
            event = append(root, state, "reader-return", return_id=key, payload_sha256=sha(payload))
        write(destination, dict(payload, event=event["id"]))


def check_returns(root, state, ledger, phase, *, return_corrections=None, non_inspecting_returns=None):
    sources = {source["id"]: source for source in ledger["sources"]}
    returns = {}
    for event in state["events"]:
        if event["type"] != "reader-return":
            continue
        path = root / "reader-returns" / (event["return_id"] + ".json")
        payload = load(path)
        require(payload.get("event") == event["id"] and
                sha({k: v for k, v in payload.items() if k != "event"}) == event["payload_sha256"],
                "changed reader return")
        returned = payload["return"]
        operation_path, _ = operation(root, returned["read_id"])
        require(hashlib.sha256((operation_path / "receipt.json").read_bytes()).hexdigest() ==
                payload["receipt_sha256"], "reader return receipt changed")
        returns[returned["id"]] = payload
    mapped, inspected_returns = set(), set()
    for source_id, source in sources.items():
        for inspection in source.get("inspections", []):
            operation_path, request = operation(root, inspection["read_id"])
            historical = load(root / "contracts" / (request["contract_sha256"] + ".json"))
            if not historical["requirements"].get("research_budget"):
                continue
            returned = returns.get(inspection.get("return_id"))
            require(returned is not None and returned["source_id"] == source_id and
                    returned["return"]["read_id"] == inspection["read_id"] and
                    returned["return"]["actor"] == inspection["actor"],
                    "inspection needs matching immutable reader return")
            inspected_returns.add(returned["return"]["id"])
        for lead in source.get("leads", []):
            for reference in lead.get("return_refs", []):
                returned = returns.get(reference.get("return_id"))
                require(returned is not None and returned["source_id"] == source_id,
                        "lead references unknown/mismatched reader return")
                candidates = [entry for entry in returned["return"]["leads"] if entry["id"] == reference.get("lead_id")]
                require(candidates, "lead references unknown returned lead")
                original = candidates[0]
                require(all(lead.get(field) == original[field] for field in ("kind", "value", "locator")) and
                        set(original["questions"]) <= set(lead.get("questions", [])),
                        "ledger lead differs from raw reader return")
                mapped.add((returned["return"]["id"], original["id"]))
    corrections = {} if return_corrections is None else return_corrections
    require(isinstance(corrections, dict), "verified return corrections must be a mapping")
    resolved = {}
    for key, binding in corrections.items():
        require(isinstance(binding, dict) and binding.get("return_id") == key and key in returns,
                "unknown corrected return mapping")
        target = binding.get("correction_return_id")
        chain = binding.get("chain")
        require(isinstance(chain, list) and 2 <= len(chain) <= 32 and all(isinstance(x, str) for x in chain) and len(set(chain)) == len(chain)
                and chain[0] == key and chain[-1] == target and all(x in returns for x in chain),
                "invalid return correction chain")
        for old, new in zip(chain, chain[1:]):
            checked = correction_binding(root, returns[new]["return"])
            require(checked is not None and checked["return_id"] == old and returns[old]["event"] < returns[new]["event"],
                    "correction mapping differs from immutable linkage")
        original, corrected = returns[key], returns[target]
        require(binding.get("original_sha256") == hashlib.sha256((root / "reader-returns" / (key + ".json")).read_bytes()).hexdigest()
                and binding.get("correction_sha256") == hashlib.sha256((root / "reader-returns" / (target + ".json")).read_bytes()).hexdigest()
                and binding.get("source_id") == original["source_id"] == corrected["source_id"]
                and binding.get("receipt_sha256") == original["receipt_sha256"] == corrected["receipt_sha256"]
                and binding.get("read_id") == original["return"]["read_id"] == corrected["return"]["read_id"]
                and binding.get("actor_id") == original["return"]["actor"] == corrected["return"]["actor"]
                and binding.get("assignment_id") == original["return"].get("graph_assignment_id") == corrected["return"].get("graph_assignment_id"),
                "verified correction mapping has different original evidence")
        resolved[key] = target
    # The bundle supplies this authority only from a fully verified registered
    # graph export. Keep accounting separate from effective inspections; all
    # original leads below still require reconciliation.
    non_inspecting = {} if non_inspecting_returns is None else non_inspecting_returns
    require(isinstance(non_inspecting, dict), "verified non-inspecting returns must be a mapping")
    for key, binding in non_inspecting.items():
        require(isinstance(binding, dict) and binding.get("return_id") == key and key in returns,
                "unknown non-inspecting return mapping")
        original = returns[key]
        raw = original["return"]
        require(binding.get("original_sha256") == hashlib.sha256((root / "reader-returns" / (key + ".json")).read_bytes()).hexdigest()
                and binding.get("source_id") == original["source_id"]
                and binding.get("receipt_sha256") == original["receipt_sha256"]
                and binding.get("read_id") == raw["read_id"] and binding.get("actor_id") == raw["actor"]
                and binding.get("assignment_id") == raw.get("graph_assignment_id")
                and isinstance(raw.get("graph"), dict), "non-inspecting return has different original evidence")
        declared = raw["graph"].get("inspections", [])
        ids, withdrawals = binding.get("inspection_ids"), binding.get("withdrawals")
        require(isinstance(ids, list) and all(isinstance(i, str) for i in ids) and len(set(ids)) == len(ids)
                and isinstance(withdrawals, list) and all(isinstance(w, dict) for w in withdrawals),
                "invalid non-inspecting return proof")
        if binding.get("disposition") == "no_inspections":
            require(declared == [] and ids == [] and withdrawals == [],
                    "no-inspection accounting cannot hide declared inspections")
        else:
            require(binding.get("disposition") == "withdrawn" and isinstance(declared, list) and declared
                    and len(declared) == len(ids) == len(withdrawals)
                    and {w.get("inspection_id") for w in withdrawals} == set(ids),
                    "withdrawal accounting needs every original inspection")
            for withdrawal in withdrawals:
                relative = withdrawal.get("evidence_path")
                require(nonempty(relative), "withdrawal proof needs retained evidence")
                evidence = (root / relative).resolve()
                require(evidence.is_relative_to(root.resolve()) and evidence.is_file()
                        and hashlib.sha256(evidence.read_bytes()).hexdigest() == withdrawal.get("evidence_sha256"),
                        "withdrawal evidence is missing or changed")
        require(key not in inspected_returns, "non-inspecting return cannot supply effective inspection credit")
    missing = [(key, lead["id"]) for key, payload in returns.items() for lead in payload["return"]["leads"]
               if (key, lead["id"]) not in mapped and (resolved.get(key), lead["id"]) not in mapped]
    if phase == "synthesis":
        accounted = inspected_returns | set(non_inspecting)
        handled = accounted | {key for key, target in resolved.items() if target in accounted}
        require(set(returns) <= handled,
                "reader returns missing from ledger inspections or verified non-inspecting accounting: " + ", ".join(sorted(set(returns) - handled)))
        require(not missing, "returned leads missing from ledger: " + ", ".join(a + "/" + b for a, b in missing))
    return {"reader_returns": len(returns), "corrected_reader_returns": len(resolved),
            "withdrawn_reader_returns": sum(b["disposition"] == "withdrawn" for b in non_inspecting.values()),
            "uninspected_reader_returns": sum(b["disposition"] == "no_inspections" for b in non_inspecting.values()),
            "returned_leads_unreconciled": len(missing)}


def validate(root, ledger, phase, *, return_corrections=None, non_inspecting_returns=None):
    """Validate admission history without refusing post-budget delivery."""
    enabled(root)
    with locked(root) as state:
        attempts = {e["operation"]: e for e in state["events"] if e["type"] == "admit"}
        windows, attempt_windows = {}, {}
        for event in state["events"]:
            if event["type"] == "activity-start":
                windows[event["actor"]] = event
            elif event["type"] == "activity-stop":
                windows.pop(event["actor"])
            elif event["type"] == "admit":
                attempt_windows[event["operation"]] = windows.get(event["actor"])
        for path in (root / "operations").iterdir():
            record = load(path / "request.json")
            historical = load(root / "contracts" / (record["contract_sha256"] + ".json"))
            if not historical["requirements"].get("research_budget"):
                continue
            spec = record["spec"]
            event = attempts.get(spec["id"])
            require(event is not None and spec.get("research_admission") ==
                    {"event": event["id"], "sha256": event["sha256"]}, "operation lacks bound research admission")
            require(event["fingerprint"] == sha({key: spec[key] for key in ("kind", "provider", "request")}),
                    "research admission request changed")
            require(all(event[field] == spec["research"][field] for field in ("stage", "branch", "parent_operation")) and
                    event["assess"] == spec["research"].get("assess", spec["kind"] == "read") and
                    event["timeout"] == spec["timeout"], "research admission provenance changed")
            configuration = allocations.config(historical)
            if configuration:
                window = attempt_windows.get(spec["id"])
                require(window is not None and all(event.get(field) == window.get(field)
                        for field in ("allocation", "purpose", "branch"))
                        and spec["research"].get("allocation") == event.get("allocation")
                        and spec["research"].get("purpose", "research") == event.get("purpose"),
                        "protected admission differs from retained activity allocation")
                allocations.activity_fields(configuration, event.get("allocation"),
                                            event.get("purpose"), event.get("branch"))
                allocations.lineage(configuration, event["allocation"], event["purpose"], event["generation"])
        if phase == "synthesis":
            require(not accounting(state)["active_actors"], "close research activity before synthesis accounting")
        returned = check_returns(root, state, ledger, phase, return_corrections=return_corrections,
                                 non_inspecting_returns=non_inspecting_returns)
    return dict(status(root), **returned)
