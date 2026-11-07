#!/usr/bin/env python3
"""Capture research operations and check declared evidence/artifact invariants.

Standard library only. This is a recordkeeper, not a command sandbox, provider
adapter, authorization mechanism, or proof of semantic correctness/recall.
"""

import argparse
from collections import Counter
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
import hashlib
import fcntl
from html.parser import HTMLParser
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile


CHECKPOINT_LEDGER = object()
GRAPH_QUERIES = {
    "artifact": ("artifact_id", "include"),
    "neighbors": ("artifact_id", "direction", "relation", "predicate", "question_id", "disposition"),
    "frontier": ("branch", "stage", "question_id", "role", "max_generation"),
    "leads": ("kind", "disposition", "question_id", "unresolved_only"),
    "coverage": ("question_id", "branch", "scope_key", "format_policy_sha256"),
    "evidence": ("claim_id", "revision"),
    "changes": ("after_sequence", "through_sequence"),
}
GRAPH_WORK_ACTIONS = {
    "register-actor": "register_actor", "claim": "claim", "start": "start_assignment",
    "renew": "renew_lease", "reconcile-assignment": "reconcile_assignment",
    "resume": "resume_assignment", "reconcile-operation": "reconcile_operation",
    "complete": "complete_work", "bound": "bound_work", "release": "release_assignment",
    "reopen": "reopen_work", "record-admission": "record_admission",
    "record-dispatch": "record_dispatch", "record-completion": "record_completion",
    "recover-retry": "recover_retry",
    "recover-admission": "recover_admission",
}
_budget_spec = importlib.util.spec_from_file_location("research_budget", Path(__file__).with_name("research_budget.py"))
budget = importlib.util.module_from_spec(_budget_spec)
_budget_spec.loader.exec_module(budget)


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    """Atomic replacement; a killed capture retains its started request."""
    path = Path(path)
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


def require(condition, message):
    if not condition:
        raise ValueError(message)


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def strings(value):
    return isinstance(value, list) and all(nonempty(item) for item in value)


def identity(value):
    require(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]+", value),
            "IDs must contain only letters, digits, underscores or hyphens")
    return value


def contract_at(root):
    return read(root / "contract.json")


def check_contract(contract):
    require(isinstance(contract, dict), "contract must be an object")
    require(contract.get("version") == 1, "contract version must be 1")
    for field in ("purpose", "scope", "requirements_source"):
        require(nonempty(contract.get(field)), "missing contract " + field)
    requirements = contract.get("requirements")
    require(isinstance(requirements, dict), "requirements must be an object")
    require(isinstance(requirements.get("candidate_table"), bool),
            "candidate_table must be explicit")
    require("research_plan" not in requirements or type(requirements["research_plan"]) is bool,
            "research_plan must be boolean")
    require("search_routes" not in requirements or type(requirements["search_routes"]) is bool,
            "search_routes must be boolean")
    require(not requirements.get("search_routes") or requirements.get("research_plan"),
            "search_routes requires research_plan")
    require("discovery_followups" not in requirements or type(requirements["discovery_followups"]) is bool,
            "discovery_followups must be boolean")
    require(not requirements.get("discovery_followups") or requirements.get("research_plan"),
            "discovery_followups requires research_plan")
    require("research_budget" not in requirements or type(requirements["research_budget"]) is bool,
            "research_budget must be boolean")
    if requirements.get("research_budget"):
        budget.policy(contract)
    allocation = budget.allocations.config(contract)
    require(not allocation or requirements.get("research_plan"), "protected_followups requires research_plan")
    maximum = requirements.get("brief_max_words")
    require(maximum is None or (type(maximum) is int and maximum > 0),
            "brief_max_words must be positive or null")
    require(nonempty(requirements.get("destination")), "destination is required")
    providers = contract.get("providers")
    require(isinstance(providers, dict) and providers, "providers are required")
    for provider, policy in providers.items():
        identity(provider)
        require(strings(policy.get("read_formats")) and policy["read_formats"],
                "provider needs verified read_formats")
        require(nonempty(policy.get("basis")), "provider needs read-policy basis")
        if "reference_hosts" in policy:
            require(strings(policy["reference_hosts"]) and all(
                re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host) for host in policy["reference_hosts"]),
                "reference_hosts must contain literal lowercase hostnames")
    questions = contract.get("questions")
    require(isinstance(questions, list) and questions, "questions are required")
    seen = set()
    for question in questions:
        key = identity(question["id"])
        require(key not in seen, "duplicate question ID")
        seen.add(key)
        require(nonempty(question.get("text")) and type(question.get("central")) is bool,
                "question needs text and explicit central flag")


def init(root, contract):
    check_contract(contract)
    root.mkdir(mode=0o700)  # Never adopt or reset an existing evidence directory.
    (root / "operations").mkdir(mode=0o700)
    (root / "contracts").mkdir(mode=0o700)
    write(root / "contract.json", contract)
    contract_hash = digest(root / "contract.json")
    shutil.copyfile(root / "contract.json", root / "contracts" / (contract_hash + ".json"))
    write(root / "bundle.json", {"version": 1, "created": now(),
                                 "contract_sha256": contract_hash})
    if contract["requirements"].get("research_budget"):
        budget.initialize(root)


@contextmanager
def current_writer(root):
    """Serialize contract/plan publication; use the graph lock first if present."""
    with graph_module().writer(root) if (root / "graph").exists() else nullcontext():
        with (root / ".research-current.lock").open("a+b") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)


def _resume_current(root):
    pointer = root / "current-publication.json"
    if not pointer.exists():
        return {"status": "pass", "pending": False}
    key = read(pointer).get("sha256")
    require(isinstance(key, str) and re.fullmatch(r"[0-9a-f]{64}", key), "invalid current publication identity")
    directory = root / "state-publications"
    retained = directory / (key + ".json")
    require(retained.is_file() and digest(retained) == key, "changed current publication intent")
    intent = read(retained)
    require(intent.get("version") == 1 and intent.get("file") in {"contract.json", "plan.json"}, "invalid publication intent")
    current = root / intent["file"]
    field = "contract_sha256" if intent["file"] == "contract.json" else "plan_sha256"
    target_hash = intent["after"][field]
    require(re.fullmatch(r"[0-9a-f]{64}", target_hash), "invalid publication snapshot")
    snapshot = root / ("contracts" if field == "contract_sha256" else "plans") / (target_hash + ".json")
    require(snapshot.is_file() and digest(snapshot) == target_hash, "changed publication snapshot")
    metadata = read(root / "bundle.json")
    require(metadata in (intent["before"], intent["after"]), "current metadata changed outside pending publication")
    actual = digest(current) if current.exists() else None
    require(actual in {intent["before"].get(field), target_hash}, "current file changed outside pending publication")
    if actual != target_hash:
        write(current, read(snapshot))
        require(digest(current) == target_hash, "published current bytes differ from snapshot")
    if metadata != intent["after"]:
        write(root / "bundle.json", intent["after"])
    # These follow-on recorders are idempotent. Keep the pending pointer until
    # they finish so a process interruption has one explicit continuation.
    registration = None
    if field == "contract_sha256":
        if read(snapshot)["requirements"].get("research_budget"):
            budget.initialize(root)
    else:
        registration = sync_graph_plan(root, target_hash)
    complete = directory / (key + ".complete.json")
    result = {"status": "pass", "pending": False, "publication_sha256": key, "file": intent["file"], "sha256": target_hash}
    if complete.exists():
        require(read(complete) == result, "changed publication completion")
    else:
        write(complete, result)
    pointer.unlink()
    return dict(result, graph=registration) if registration is not None else result


def recover_current(root):
    """Finish only retained current-pointer intent; never guess or discard edits."""
    with current_writer(root):
        return _resume_current(root)


def _publish_current(root, name, before, after):
    require(not (root / "current-publication.json").exists(), "resume pending current publication first")
    directory = root / "state-publications"
    directory.mkdir(mode=0o700, exist_ok=True)
    intent = {"version": 1, "file": name, "before": before, "after": after}
    key = hashlib.sha256((json.dumps(intent, ensure_ascii=False, indent=2) + "\n").encode()).hexdigest()
    target = directory / (key + ".json")
    if target.exists():
        require(digest(target) == key, "changed publication intent")
    else:
        write(target, intent)
    write(root / "current-publication.json", {"sha256": key})
    return _resume_current(root)


def amend(root, contract):
    check_contract(contract)
    with current_writer(root):
        _resume_current(root)
        return _amend(root, contract)


def _amend(root, contract):
    """Retain earlier requirements and receipts when a user changes the task."""
    check_contract(contract)
    metadata = read(root / "bundle.json")
    require(digest(root / "contract.json") == metadata["contract_sha256"], "changed contract")
    require(not contract_at(root)["requirements"].get("research_budget") or
            contract["requirements"].get("research_budget"), "retain enabled research budget accounting")
    if contract["requirements"].get("protected_followups") and not contract_at(root)["requirements"].get("protected_followups"):
        require(not any((root / "operations").iterdir()) and not any((root / "reader-returns").glob("*.json")),
                "enable protected follow-ups before execution; retain untimed historical work without reclassification")
    if (root / "research-budget.json").exists():
        with budget.locked(root) as state:
            budget.validate_amendment(contract_at(root), contract, state)
    contract_hash = hashlib.sha256((json.dumps(contract, ensure_ascii=False, indent=2) + "\n").encode()).hexdigest()
    write(root / "contracts" / (contract_hash + ".json"), contract)
    _publish_current(root, "contract.json", metadata, dict(metadata, contract_sha256=contract_hash, amended=now()))


def start(root, spec, transport):
    contract = contract_at(root)
    require(not contract["requirements"].get("protected_followups") or
            (root / "graph" / "research.sqlite").is_file(), "protected follow-ups require an initialized graph")
    require(digest(root / "contract.json") == read(root / "bundle.json")["contract_sha256"],
            "contract changed; use amend to retain requirement history")
    require(isinstance(spec, dict), "operation spec must be an object")
    key = identity(spec["id"])
    require(nonempty(spec.get("actor")), "operation needs actor")
    require(spec.get("provider") in contract["providers"], "unknown provider")
    request = spec.get("request")
    require(isinstance(request, dict), "operation needs exact request object")
    if spec.get("kind") == "search":
        require(nonempty(request.get("query")), "search needs exact query")
        require(all(field in request for field in ("filters", "cap", "cursor")),
                "search must record filters, cap and cursor (null if inapplicable)")
    else:
        require(spec.get("kind") == "read", "operation kind must be search or read")
        require(nonempty(request.get("source_id")) and nonempty(request.get("extent")),
                "read needs stable source_id and requested extent")
        formats = contract["providers"][spec["provider"]]["read_formats"]
        require(request.get("format") in formats, "read format not allowed by provider policy")
    if contract["requirements"].get("research_plan"):
        check_planned_operation(root, spec, contract, starting=True)
    if transport == "subprocess":
        require(strings(spec.get("command")) and spec["command"], "capture needs argv command")
        require(nonempty(spec.get("cwd")) and Path(spec["cwd"]).is_absolute(),
                "capture needs absolute cwd")
        timeout = spec.get("timeout", 120)
        require(type(timeout) in (int, float) and 0 < timeout <= 3600,
                "timeout must be in (0, 3600]")
    sync_graph_work(root, "prepare", spec=spec)
    request_context = {"started": now(), "transport": transport, "contract_sha256": digest(root / "contract.json")}
    def create():
        operation = root / "operations" / key
        operation.mkdir(mode=0o700)
        write(operation / "request.json", dict(request_context, spec=spec))
        return operation
    operation = budget.admit(root, spec, create, request_context=request_context) if contract["requirements"].get("research_budget") else create()
    sync_graph_work(root, "admission", spec=spec)
    return operation


def complete(operation, outcome, provenance, returncode=None, failure=None):
    require(not (operation / "receipt.json").exists(), "operation already completed")
    files = {name: digest(operation / name) for name in ("request.json", "stdout", "stderr")}
    write(operation / "receipt.json", {"finished": now(), "outcome": outcome,
                                      "provenance": provenance, "returncode": returncode,
                                      "files": files, "failure": failure})
    sync_graph_work(operation.parent.parent, "completion", operation_id=operation.name)


def capture(root, spec):
    operation = start(root, spec, "subprocess")
    outcome, code = "error", None
    failure = None
    with (operation / "stdout").open("wb") as output, (operation / "stderr").open("wb") as error:
        try:
            # Record dispatch intent immediately before launch. A launch failure
            # still gets its own receipt; no graph lock spans the child process.
            sync_graph_work(root, "dispatch", operation_id=spec["id"])
            child = subprocess.Popen(spec["command"], cwd=spec["cwd"], stdout=output,
                                     stderr=error, start_new_session=os.name == "posix")
            try:
                code = child.wait(timeout=spec.get("timeout", 120))
            except (subprocess.TimeoutExpired, KeyboardInterrupt):
                # Kill the owned process group so wrapper grandchildren cannot
                # continue source work after a timeout. Deliberately detached
                # processes and remote/tool-import calls remain outside this boundary.
                if os.name == "posix":
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    child.kill()
                child.wait()
                raise
            outcome = "ok" if code == 0 else "error"
        except subprocess.TimeoutExpired:
            outcome = "timeout"
        except KeyboardInterrupt:
            outcome = "interrupted"
        except OSError as error:
            outcome = "launch-error"
            failure = {"type": type(error).__name__, "errno": error.errno}
    complete(operation, outcome, "captured-subprocess", code, failure)
    return 0 if outcome == "ok" else 1


def import_output(root, key, output, error, outcome):
    operation = root / "operations" / identity(key)
    require(read(operation / "request.json")["transport"] == "tool-import",
            "finish applies only to tool-import operations")
    require(not (operation / "receipt.json").exists(), "operation already completed")
    shutil.copyfile(output, operation / "stdout")
    if error:
        shutil.copyfile(error, operation / "stderr")
    else:
        (operation / "stderr").write_bytes(b"")
    complete(operation, outcome, "agent-attested-tool-output")


def prepare_index_result(root, key, result):
    """Prepare the exact immutable index envelope without retaining it."""
    operation = root / "operations" / identity(key)
    require((operation / "receipt.json").is_file(), "complete operation before indexing")
    require(not (operation / "result.json").exists(),
            "result already retained; use graph sync, or graph omit-index-version for unsupported optional versions")
    require(strings(result.get("source_ids")), "result needs source_ids, including empty results")
    require(len(set(result["source_ids"])) == len(result["source_ids"]), "duplicate result IDs")
    require(result.get("pagination") in ("complete", "capped", "more", "unknown", "not-applicable"),
            "result needs observed pagination state")
    require("next_cursor" in result and nonempty(result.get("note")),
            "result needs next_cursor and extraction/coverage note")
    return dict(result, indexed=now(), provenance="agent-extracted",
                receipt_sha256=digest(operation / "receipt.json"))


def index_result(root, key, result):
    """Preflight the exact index before retention, under the shared writer."""
    root = Path(root)
    if (root / "graph").exists():
        with graph_module().writer(root):
            prepared = prepare_index_result(root, key, result)
            graph_component("research_graph_ingest").validate_prepared_index(root, key, prepared)
            write(root / "operations" / key / "result.json", prepared)
            return sync_graph_evidence(root, operation_id=key)
    prepared = prepare_index_result(root, key, result)
    write(root / "operations" / key / "result.json", prepared)
    return sync_graph_evidence(root, operation_id=key)


def record_reader_return(root, returned):
    """Preflight graph returns, then retain and normalize under one writer."""
    if (Path(root) / "graph").exists():
        with graph_module().writer(root):
            graph_component("research_graph_ingest").validate_reader_return(root, returned)
            budget.reader_return(root, returned)
            return sync_graph_evidence(root, return_id=returned["id"])
    budget.reader_return(root, returned)
    return sync_graph_evidence(root, return_id=returned["id"])


def sync_graph_work(root, event, *, spec=None, operation_id=None):
    if not (Path(root) / "graph").exists():
        return None
    work = graph_component("research_graph_work")
    if event == "prepare":
        return work.prepare_operation(root, spec)
    if event == "admission":
        return work.record_admission(root, spec)
    if event == "dispatch":
        return work.record_dispatch(root, operation_id)
    if event == "completion":
        return work.record_completion(root, operation_id)
    raise work.GraphError("invalid_packet", "Unknown graph execution event")


def sync_graph_plan(root, plan_sha256):
    if not (Path(root) / "graph").exists():
        return None
    return graph_component("research_graph_work").register_plan(root, plan_sha256)


def sync_graph_evidence(root, *, operation_id=None, return_id=None):
    if not (Path(root) / "graph").exists():
        return None
    ingest = graph_component("research_graph_ingest")
    if (operation_id is None) == (return_id is None):
        raise ingest.GraphError("invalid_packet", "Specify exactly one original to synchronize")
    if operation_id is not None:
        return ingest.index_operation(root, operation_id)
    return ingest.ingest_reader_return(root, return_id)


def sync_graph(root):
    """Try each original once, retaining unresolved dependencies for later work."""
    ingest = graph_component("research_graph_ingest")
    originals = ingest.ingestion_status(root)
    attempted = []
    for item in originals["items"]:
        if item["state"] == "complete":
            continue
        kind, key = item["kind"], item["original_id"]
        try:
            result = sync_graph_evidence(root, **{("operation_id" if kind == "operation" else "return_id"): key})
            attempted.append({"kind": kind, "original_id": key, "state": result["state"]})
        except ingest.GraphError as error:
            attempted.append({"kind": kind, "original_id": key, "state": "pending",
                              "error": error.response("sync")["error"]})
    result = ingest.ingestion_status(root)
    result.update(command="sync", attempted=attempted)
    if not result["complete"]:
        result.update(status="fail", error={"code": "pending_dependency", "retryable": False,
            "message": "Original evidence remains pending; inspect each dependency before retrying"})
    return result


def unique(items, label):
    require(isinstance(items, list), label + " must be a list")
    result = {}
    for item in items:
        require(isinstance(item, dict), label + " entry must be an object")
        key = item["id"]
        require(nonempty(key) and key not in result, "missing/duplicate " + label + " ID")
        result[key] = item
    return result


def plan_request(item, contract, current_policy=True):
    """Check concrete intended requests before any command can run."""
    require(nonempty(item.get("actor")), "plan item needs assigned actor")
    require(item.get("provider") in contract["providers"], "unknown plan provider")
    request = item.get("request")
    require(isinstance(request, dict), "plan item needs request object")
    if item.get("kind") == "search":
        require(nonempty(request.get("query")), "planned search needs exact query")
        require(isinstance(request.get("filters"), dict), "search filters must be an object")
        require("cap" in request and (request["cap"] is None or
                (type(request["cap"]) is int and request["cap"] > 0)),
                "search cap must be null or a positive integer")
        require("cursor" in request and (request["cursor"] is None or nonempty(request["cursor"])),
                "search cursor must be null or a nonempty string")
    else:
        require(item.get("kind") == "read", "plan item kind must be search or read")
        require(nonempty(request.get("source_id")) and nonempty(request.get("extent")),
                "planned read needs source_id and extent")
        require(nonempty(request.get("format")), "planned read needs format")
        require(not current_policy or
                request["format"] in contract["providers"][item["provider"]]["read_formats"],
                "planned read format not allowed by provider policy")


def plan_snapshot(root, key=None):
    """Load a retained, hashed plan and its automatically saved ledger checkpoint."""
    metadata = read(root / "bundle.json")
    key = key or metadata.get("plan_sha256")
    require(isinstance(key, str) and re.fullmatch(r"[0-9a-f]{64}", key),
            "missing or invalid research plan")
    path = root / "plans" / (key + ".json")
    require(path.is_file() and digest(path) == key, "unknown or changed plan snapshot")
    if key == metadata.get("plan_sha256"):
        require((root / "plan.json").is_file() and digest(root / "plan.json") == key,
                "changed current plan; use plan to retain history")
    plan = read(path)
    record = plan["_record"]
    previous = root / "contracts" / (record["contract_sha256"] + ".json")
    require(previous.is_file() and digest(previous) == record["contract_sha256"],
            "plan used an unknown or changed contract")
    ledger_path = root / "plans" / (key + ".ledger.json")
    require(ledger_path.is_file() and digest(ledger_path) == record["ledger_sha256"],
            "missing or changed plan ledger checkpoint")
    return plan, read(ledger_path), key


def plan_history(root):
    history, seen = [], set()
    key = read(root / "bundle.json").get("plan_sha256")
    require(key, "missing research plan")
    while key:
        require(key not in seen, "cyclic plan history")
        seen.add(key)
        plan, ledger, key = plan_snapshot(root, key)
        history.append((plan, ledger, key))
        key = plan["_record"]["parent_sha256"]
    return history


def check_planned_operation(root, spec, contract, starting=False, started=None):
    plan_request(spec, contract)
    history = plan_history(root)
    snapshots = {key: plan for plan, _, key in history}
    require(spec.get("plan_sha256") in snapshots, "operation needs a retained prior plan snapshot")
    assigned = unique(snapshots[spec["plan_sha256"]]["items"], "plan item")
    require(spec.get("plan_item") in assigned, "unknown operation plan_item")
    item = assigned[spec["plan_item"]]
    require(item["status"] in ("queued", "active"), "operation snapshot assignment was closed")
    for field in ("actor", "provider", "kind", "request"):
        require(spec.get(field) == item[field], "operation differs from planned " + field)
    if contract["requirements"].get("protected_followups"):
        annotation = item.get("graph", {})
        require(spec.get("research", {}).get("allocation") == annotation.get("allocation")
                and annotation.get("allocation") is not None,
                "operation differs from planned research allocation")
        require(spec.get("research", {}).get("purpose", "research") == annotation.get("activity_purpose", "research"),
                "operation differs from planned activity purpose")
    if contract["requirements"].get("discovery_followups") and "read_id" in item.get("origin", {}):
        origin = item["origin"]
        checkpoint = next(ledger for _, ledger, key in history if key == spec["plan_sha256"])
        source = unique(checkpoint["sources"], "source")[origin["source_id"]]
        require(origin["read_id"] in {entry["read_id"] for entry in source["inspections"]},
                "source-derived work needs inspection in its prior plan snapshot")
    if contract["requirements"].get("search_routes") and item["kind"] == "search":
        for field in ("search_branch", "input_kind"):
            require(spec.get(field) == item.get(field), "operation differs from planned " + field)
        route = search_route_for_item(snapshots[spec["plan_sha256"]], item)
        invocation = route["invocation"]
        if "command_prefix" in invocation:
            prefix = invocation["command_prefix"]
            require(spec.get("command", [])[:len(prefix)] == prefix,
                    "operation command does not match search route")
        else:
            require(spec.get("tool") == invocation["tool"], "operation tool does not match search route")
    if starting:
        current = unique(history[0][0]["items"], "plan item")
        require(spec["plan_item"] in current and
                current[spec["plan_item"]]["status"] in ("queued", "active"),
                "current plan item is closed; reopen or create follow-up before executing")
        if contract["requirements"].get("search_routes") and item["kind"] == "search":
            branch = unique(history[0][0]["search_branches"], "search branch")[item["search_branch"]]
            require(branch["status"] == "selected" and
                    search_route_for_item(history[0][0], item)["availability"] == "available",
                    "current search branch or route is unavailable")
    if started:
        require(snapshots[spec["plan_sha256"]]["_record"]["saved"] <= started,
                "operation plan snapshot did not precede execution")


def cancelled_operation(operation_id, record, graph_context):
    """Only a verified frozen cancellation can account for absent output."""
    return (record[1] is None and record[2] is None and
            operation_id in (graph_context or {}).get("cancelled_operations", {}))


def plan_operations(root):
    """Planning may proceed while independent captures remain in progress."""
    result = {}
    admissions = {}
    if (root / "research-budget.json").exists():
        with budget.locked(root) as state:
            admissions = {event["operation"]: event for event in state["events"] if event["type"] == "admit"}
    identities = {path.name for path in (root / "operations").iterdir()} | set(admissions)
    for operation_id in sorted(identities):
        path = root / "operations" / operation_id
        if (path / "request.json").is_file():
            request = read(path / "request.json")
        else:
            require(operation_id in admissions, "operation has no retained request or admission intent: " + operation_id)
            request = budget.admission_request(admissions[operation_id])
            require(not path.exists() or not any(path.iterdir()), "output exists without its original request")
        require(request["spec"]["id"] == path.name, "operation identity mismatch")
        historical = root / "contracts" / (request["contract_sha256"] + ".json")
        require(historical.is_file() and digest(historical) == request["contract_sha256"],
                "operation used an unknown or changed contract")
        if read(historical)["requirements"].get("research_plan"):
            check_planned_operation(root, request["spec"], read(historical), started=request["started"])
        receipt, indexed = None, None
        if (path / "receipt.json").exists():
            receipt = read(path / "receipt.json")
            require(set(receipt["files"]) == {"request.json", "stdout", "stderr"}, "incomplete receipt")
            for name, expected in receipt["files"].items():
                require(digest(path / name) == expected, "changed operation output: " + path.name)
        if (path / "result.json").exists():
            require(receipt is not None, "indexed operation lacks receipt")
            indexed = read(path / "result.json")
            require(indexed["receipt_sha256"] == digest(path / "receipt.json"), "changed receipt")
            require(strings(indexed.get("source_ids")), "invalid result IDs")
        result[path.name] = (request["spec"], receipt, indexed)
    return result


def search_route_for_item(plan, item):
    branches = unique(plan.get("search_branches"), "search branch")
    require(item.get("search_branch") in branches, "search item needs known search_branch")
    return unique(plan.get("search_routes"), "search route")[branches[item["search_branch"]]["route"]]


def check_search_routes(plan, contract, coverage, items, phase, partial_items=()):
    """Bind selected coverage paths to supported queries; this does not judge recall."""
    routes = unique(plan.get("search_routes"), "search route")
    branches = unique(plan.get("search_branches"), "search branch")
    require(routes and branches, "search_routes needs a route catalog and coverage branches")
    modes = {}
    for key, route in routes.items():
        identity(key)
        require(route.get("provider") in contract["providers"], "route needs known provider")
        for field in ("family", "basis", "checked_at", "limitations"):
            require(nonempty(route.get(field)), "route needs " + field)
        for field in ("artifact_types", "locations"):
            require(strings(route.get(field)) and route[field], "route needs " + field)
        require(route.get("availability") in ("available", "unverified", "unavailable"),
                "invalid route availability")
        invocation = route.get("invocation")
        require(invocation is None or (isinstance(invocation, dict) and (
            (set(invocation) == {"command_prefix"} and strings(invocation["command_prefix"]) and
             invocation["command_prefix"]) or
            (set(invocation) == {"tool"} and nonempty(invocation["tool"])))),
            "route invocation needs command_prefix or tool, or null when unknown")
        require(route["availability"] != "available" or invocation is not None,
                "available route needs verified invocation")
        modes[key] = unique(route.get("modes"), "query mode")
        require(modes[key], "route needs query modes")
        for mode in modes[key].values():
            require(strings(mode.get("input_kinds")) and mode["input_kinds"] and
                    set(mode["input_kinds"]) <= {"topic", "term", "title", "person"},
                    "query mode needs supported input_kinds")
            require(strings(mode.get("filter_fields")), "query mode needs explicit filter_fields")
    mapped = set()
    mapped_work = set()
    for key, branch in branches.items():
        identity(key)
        require(strings(branch.get("coverage")) and branch["coverage"] and
                set(branch["coverage"]) <= set(coverage), "branch needs known coverage")
        mapped.update(branch["coverage"])
        require(branch.get("route") in routes, "branch needs known route")
        route = routes[branch["route"]]
        require(branch.get("mode") in modes[branch["route"]], "branch needs supported query mode")
        require(branch.get("artifact_type") in route["artifact_types"] and
                branch.get("location") in route["locations"], "branch outside route artifact/location scope")
        for entry_id in branch["coverage"]:
            require(branch.get("work_type") in coverage[entry_id]["work_types"] and
                    route["provider"] in coverage[entry_id]["providers"],
                    "branch outside declared work type/provider coverage: " + key + " -> " + entry_id)
            mapped_work.add((entry_id, branch["work_type"]))
        require(branch.get("status") in ("selected", "bounded", "omitted") and nonempty(branch.get("reason")),
                "branch needs selection status and rationale")
        assigned = [item for item in items.values() if item.get("search_branch") == key]
        if branch["status"] == "selected":
            require(route["availability"] == "available", "selected branch needs available route")
            require(assigned, "selected branch lacks concrete searches: " + key)
            if phase == "synthesis":
                require(not any(item["status"] in ("queued", "active") and item["id"] not in partial_items for item in assigned),
                        "selected branch has pending searches: " + key)
                require(any(item["status"] == "done" or item["id"] in partial_items for item in assigned),
                        "selected branch has no executed search; bound or omit it: " + key)
        else:
            require(not any(item["status"] in ("queued", "active") for item in assigned),
                    "closed branch has pending searches: " + key)
    require(mapped == set(coverage), "coverage entry lacks search branch or explicit omission")
    missing_work = {(key, work_type) for key, entry in coverage.items()
                    for work_type in entry["work_types"]} - mapped_work
    require(not missing_work, "declared work type lacks search branch or explicit omission: " +
            ", ".join(key + "/" + work_type for key, work_type in sorted(missing_work)))
    for item in items.values():
        if item.get("kind") != "search":
            require("search_branch" not in item and "input_kind" not in item,
                    "read items do not satisfy search branches")
            continue
        route = search_route_for_item(plan, item)
        branch = branches[item["search_branch"]]
        mode = modes[branch["route"]][branch["mode"]]
        require(item["provider"] == route["provider"] and
                set(item["coverage"]) <= set(branch["coverage"]),
                "search item differs from branch coverage/provider: " + item["id"] + " -> " + branch["id"])
        require(item.get("input_kind") in mode["input_kinds"], "input kind unsupported by query mode")
        require(set(item["request"]["filters"]) <= set(mode["filter_fields"]),
                "search uses unsupported filter fields")


def check_discovery_followups(plan, contract, sources, items, operations, inspected, phase, graph_context=None):
    """Account for read-derived leads and stopping decisions, not their adequacy."""
    require(nonempty(plan.get("wave_strategy")), "discovery_followups needs wave_strategy")
    questions = {question["id"] for question in contract["questions"]}
    graph_leads = {(row["source_id"], row["lead_id"]): row
                   for row in (graph_context or {}).get("lead_bindings", [])}
    for source_id, source in sources.items():
        if source["analysis"]["status"] == "complete":
            require(nonempty(source.get("expansion_review")),
                    "analyzed source needs expansion_review: " + source_id)
        for lead in source["leads"]:
            label = source_id + "/" + lead["id"]
            require(lead.get("kind") in ("vocabulary", "linked-record", "neighborhood", "question-gap")
                    and nonempty(lead.get("value")), "lead needs kind and value: " + label)
            require(strings(lead.get("questions")) and lead["questions"] and
                    set(lead["questions"]) <= questions, "lead needs relevant questions: " + label)
            if graph_context is not None:
                binding = graph_leads.get((source_id, lead["id"]))
                require(binding is not None and binding["questions"] == lead["questions"],
                        "graph lead needs verified question obligations: " + label)
                if phase == "synthesis" and binding["graph_obligation_id"] not in (graph_context.get("partial_delivery") or {}).get("obligation_ids", []):
                    require(binding["obligation_state"] == "terminal" and
                            binding["disposition"] in ("resolved", "bounded", "excluded", "deferred"),
                            "unresolved graph discovery lead: " + label)
                    require(all(scope["status"] in ("resolved", "bounded") for scope in binding["question_scopes"]),
                            "graph lead has unfinished question scope: " + label)
                continue
            require(lead.get("status") in ("pending", "addressed", "bounded"),
                    "lead needs explicit status: " + label)
            if lead["status"] == "addressed":
                item = items.get(lead.get("plan_item"))
                require(item is not None and item["status"] == "done",
                        "addressed lead needs executed work: " + label)
                if item["kind"] == "read":
                    require(any(key in inspected and record[0].get("plan_item") == item["id"]
                                for key, record in operations.items()),
                            "addressed read lead needs actual inspection: " + label)
            if phase == "synthesis":
                require(lead["status"] != "pending", "unresolved discovery lead: " + label)
    checkpoints = plan.get("checkpoints")
    unique(checkpoints, "coverage checkpoint")
    for checkpoint in checkpoints:
        label = identity(checkpoint["id"])
        for field, known in (("operations", set(operations)), ("inspected_reads", inspected),
                             ("questions", questions), ("next_items", set(items))):
            values = checkpoint.get(field)
            require(strings(values) and len(values) == len(set(values)) and set(values) <= known,
                    "checkpoint needs known distinct " + field + ": " + label)
        require(checkpoint["operations"] and checkpoint["questions"],
                "checkpoint needs recorded work and questions: " + label)
        require(all((operations[key][1] is not None and operations[key][2] is not None)
                    or cancelled_operation(key, operations[key], graph_context)
                    for key in checkpoint["operations"]), "checkpoint includes unfinished operations: " + label)
        require(checkpoint.get("decision") in ("continue", "synthesize", "bounded") and
                nonempty(checkpoint.get("reason")) and nonempty(checkpoint.get("limits")),
                "checkpoint needs decision, rationale and limits: " + label)
        require(checkpoint["decision"] != "continue" or checkpoint["next_items"],
                "continue checkpoint needs concrete next work: " + label)
    if phase == "synthesis":
        require(checkpoints and checkpoints[-1]["decision"] in ("synthesize", "bounded"),
                "synthesis needs final coverage checkpoint")
        final = checkpoints[-1]
        require(set(final["operations"]) == set(operations) and set(final["inspected_reads"]) == inspected,
                "final coverage checkpoint is stale; review current operations and inspections")
        require(set(final["questions"]) == questions, "final checkpoint must cover every question")
        partial_items = ((graph_context or {}).get("partial_delivery") or {}).get("pending_plan_item_ids", [])
        require(all(items[key]["status"] not in ("queued", "active") or key in partial_items for key in final["next_items"]),
                "final checkpoint still assigns pending work")
        require(not (graph_context or {}).get("partial_delivery") or final["decision"] == "bounded",
                "partial delivery needs an explicitly bounded final coverage checkpoint")
        require(set(partial_items) <= set(final["next_items"]),
                "partial coverage checkpoint must retain every pending plan item")


def backed_discovery(discovery, source, operations, graph_context):
    """Graph pointers require a separately authenticated, unchanged export."""
    return (discovery.startswith("seed:") or
            (graph_context is not None and source.get("graph_artifact_id") and
             discovery == "graph:" + source["graph_artifact_id"]) or
            (discovery in operations and operations[discovery][2] is not None and
             source["id"] in operations[discovery][2]["source_ids"]))


def retained_read_done(item, coverage, graph_context):
    """Use verified frozen graph reading, never a caller-supplied done flag."""
    if item["kind"] != "read" or not item.get("graph", {}).get("capture_ids") or graph_context is None:
        return False
    immutable = ("id", "actor", "provider", "kind", "request", "coverage", "origin", "search_branch", "input_kind")
    annotations = dict(item["graph"])
    annotations["capture_ids"] = sorted(annotations["capture_ids"])
    required = set(annotations.get("questions", [q for key in item["coverage"] for q in coverage[key]["questions"]]))
    completed = {record["question_id"] for record in graph_context.get("retained_plan_completions", [])
                 if all(record["item"].get(key) == item.get(key) for key in immutable)
                 and record["item"].get("graph") == annotations}
    return bool(required) and required <= completed


def checked_plan(plan, contract, ledger, operations, phase="research", graph_context=None):
    require(isinstance(plan, dict) and type(plan.get("version")) is int and plan["version"] == 1,
            "plan version must be 1")
    require(isinstance(ledger, dict), "ledger must be an object")
    require("_graph" not in ledger or graph_context is not None,
            "generated graph ledger needs verified export binding")
    require(nonempty(plan.get("owner")), "plan needs coordinating owner")
    coverage = unique(plan.get("coverage"), "coverage")
    require(coverage, "plan needs explicit research coverage")
    questions = {item["id"] for item in contract["questions"]}
    covered = set()
    for entry in coverage.values():
        identity(entry["id"])
        require(strings(entry.get("questions")) and entry["questions"] and
                set(entry["questions"]) <= questions, "coverage needs known questions")
        covered.update(entry["questions"])
        for field in ("terms", "providers", "people", "periods", "work_types"):
            require(strings(entry.get(field)), "coverage needs explicit " + field)
        require(entry["providers"] and set(entry["providers"]) <= set(contract["providers"]),
                "coverage needs known providers")
        require(nonempty(entry.get("reason")), "coverage needs rationale and bounds")
    require(covered == questions, "plan question coverage mismatch")
    items = unique(plan.get("items"), "plan item")
    partial_items = ((graph_context or {}).get("partial_delivery") or {}).get("pending_plan_item_ids", [])
    require(items, "plan needs concrete work items")
    if contract["requirements"].get("search_routes"):
        # Check ordinary requests first so malformed input still gives a diagnostic.
        for item in items.values():
            plan_request(item, contract, current_policy=item.get("status") in ("queued", "active"))
            require(strings(item.get("coverage")) and item["coverage"], "item needs coverage")
        check_search_routes(plan, contract, coverage, items, phase, partial_items)
    sources = unique(ledger.get("sources"), "source")
    for item in items.values():
        identity(item["id"])
        plan_request(item, contract, current_policy=item.get("status") in ("queued", "active"))
        require(strings(item.get("coverage")) and item["coverage"] and
                set(item["coverage"]) <= set(coverage), "item needs known coverage entries")
        require(item.get("priority") in ("high", "normal", "low"), "invalid work priority")
        require(item.get("status") in ("queued", "active", "done", "bounded", "dropped"),
                "invalid work status")
        require(nonempty(item.get("reason")), "work item needs status/priority rationale")
        if item["kind"] == "read":
            require(item["request"]["source_id"] in sources, "planned read needs inventoried source")
        if "origin" in item:
            origin = item["origin"]
            require(isinstance(origin, dict) and origin.get("source_id") in sources and
                    nonempty(origin.get("locator")), "follow-up needs known source and locator")
            if "read_id" in origin:
                record = operations.get(origin["read_id"])
                require(record is not None and record[0]["kind"] == "read" and
                        record[0]["request"]["source_id"] == origin["source_id"] and
                        record[1] is not None and record[1]["outcome"] == "ok" and record[2] is not None,
                        "follow-up origin needs successful indexed source read")
        matching = [value for value in operations.values() if value[0].get("plan_item") == item["id"] and
                    value[0].get("plan_sha256") and all(value[0].get(field) == item[field]
                    for field in ("actor", "provider", "kind", "request"))]
        if item["status"] == "done":
            if item.get("graph", {}).get("capture_ids"):
                require(retained_read_done(item, coverage, graph_context),
                        "done retained read lacks current matching graph inspection: " + item["id"])
            else:
                require(any(value[1] is not None and value[1]["outcome"] == "ok" and value[2] is not None
                            for value in matching), "done item lacks successful indexed execution: " + item["id"])
        if phase == "synthesis":
            require(not (item["priority"] == "high" and item["status"] in ("queued", "active") and item["id"] not in partial_items),
                    "pending high-priority research: " + item["id"])
    for spec, _, result in operations.values():
        if result:
            for source_id in result["source_ids"]:
                require(source_id in sources, "result missing from inventory: " + source_id)
                require(spec["id"] in sources[source_id].get("discovered_by", []),
                        "discovery provenance missing: " + source_id)
        if spec["kind"] == "read":
            require(spec["request"]["source_id"] in sources, "read source missing from inventory")
    inspected, analyzed = set(), set()
    for source_id, source in sources.items():
        for field in ("title", "url", "coverage", "finding", "contributor_basis"):
            require(nonempty(source.get(field)), "source missing " + field + ": " + source_id)
        require(strings(source.get("discovered_by")) and source["discovered_by"],
                "source needs discovery basis")
        for discovery in source["discovered_by"]:
            require(backed_discovery(discovery, source, operations, graph_context),
                    "unbacked discovery: " + source_id)
        require(strings(source.get("reads")), "source needs reads, empty if unread")
        for read_id in source["reads"]:
            record = operations.get(read_id)
            require(record is not None and record[1] is not None and record[1]["outcome"] == "ok" and
                    record[2] is not None and record[0]["kind"] == "read" and
                    record[0]["request"]["source_id"] == source_id,
                    "source read needs successful indexed matching operation")
            require(record[0]["request"]["format"] in
                    contract["providers"][record[0]["provider"]]["read_formats"], "unapproved read format")
        inspections = source.get("inspections")
        require(isinstance(inspections, list), "source needs explicit inspections")
        for inspection in inspections:
            require(isinstance(inspection, dict) and nonempty(inspection.get("actor")) and
                    nonempty(inspection.get("coverage")), "inspection needs actual actor and coverage")
            read_id = inspection.get("read_id")
            record = operations.get(read_id)
            require(read_id in source["reads"] and record is not None and record[1] is not None and
                    record[1]["outcome"] == "ok" and record[2] is not None and record[0]["kind"] == "read" and
                    record[0]["request"]["source_id"] == source_id,
                    "inspection needs successful indexed source read")
            inspected.add(read_id)
        analysis, disposition = source.get("analysis"), source.get("disposition")
        require(isinstance(analysis, dict) and analysis.get("status") in ("pending", "complete", "excluded")
                and nonempty(analysis.get("reason")), "source needs analysis state and reason")
        require(isinstance(disposition, dict) and disposition.get("status") in
                ("pending", "used", "context", "excluded", "blocked") and nonempty(disposition.get("reason")),
                "source needs disposition and reason")
        require(analysis["status"] != "complete" or inspections, "analysis requires inspected source")
        if analysis["status"] == "complete":
            analyzed.update(inspection["read_id"] for inspection in inspections)
        require(disposition["status"] not in ("used", "context") or analysis["status"] == "complete",
                "used/context source needs completed analysis")
        if phase == "synthesis" and graph_context is None:
            require(disposition["status"] != "pending", "source disposition still pending: " + source_id)
        leads = unique(source.get("leads"), "source lead")
        for lead in leads.values():
            require(nonempty(lead.get("locator")) and nonempty(lead.get("reason")),
                    "source lead needs locator and reason")
            require(graph_context is not None or "target_source_id" in lead or "plan_item" in lead or
                    (contract["requirements"].get("discovery_followups") and lead.get("status") == "bounded"),
                    "source lead needs an explicit destination or recorded bound")
            if "target_source_id" in lead:
                require(lead["target_source_id"] in sources, "lead target missing from inventory")
            if "plan_item" in lead:
                require(lead["plan_item"] in items, "lead references unknown plan item")
                origin = items[lead["plan_item"]].get("origin", {})
                require(origin.get("source_id") == source_id and origin.get("locator") == lead["locator"],
                        "lead/follow-up origin mismatch")
    for claim in unique(ledger.get("claims", []), "claim").values():
        require(strings(claim.get("reads")) and set(claim["reads"]) <= inspected,
                "claim needs inspected supporting reads")
        require(set(claim["reads"]) <= analyzed, "claim needs completed source analysis")
    if contract["requirements"].get("discovery_followups"):
        check_discovery_followups(plan, contract, sources, items, operations, inspected, phase, graph_context)
    return items, sources


def graph_ledger_context(root, ledger, *, historical=False, bootstrap=False):
    """Authenticate a generated view; only the first seed inventory is hand-written.

    Historical saved-plan rendering/registration may use its exact old snapshot.
    New planning and final validation need a fresh research snapshot. Export-only
    registration events do not make the represented research stale.
    """
    if not (Path(root) / "graph").exists():
        require("_graph" not in ledger, "graph ledger requires its original graph bundle")
        return None
    if "_graph" in ledger:
        result = graph_component("research_graph_exports").validate_ledger(root, ledger)
        require(historical or not result["stale"], "graph ledger is stale; export the current graph")
        for key, proof in result.get("cancelled_operations", {}).items():
            directory = Path(root) / "operations" / key
            # A historical view can coexist with a later real receipt. It cannot
            # excuse changed request bytes or be used as a new completion claim.
            require(digest(directory / "request.json") == proof["request_sha256"],
                    "cancelled operation request changed: " + key)
            require(historical or {p.name for p in directory.iterdir()} == {"request.json"},
                    "cancelled operation has other retained evidence to reconcile: " + key)
        return result
    if historical:
        # The original hash-bound pre-graph plan remains historical evidence.
        return None
    if bootstrap and not read(Path(root) / "bundle.json").get("plan_sha256"):
        sources = ledger.get("sources", [])
        require(not ledger.get("claims") and not ledger.get("semantic_review") and
                not plan_operations(root) and all(
                    not source.get("reads") and not source.get("inspections") and not source.get("leads") and
                    source.get("analysis", {}).get("status") == "pending" and
                    source.get("disposition", {}).get("status") == "pending" and
                    strings(source.get("discovered_by")) and source["discovered_by"] and
                    all(value.startswith("seed:") for value in source["discovered_by"])
                    for source in sources), "graph bootstrap permits only pending supplied seeds")
        return None
    raise ValueError("graph mode requires a registered generated ledger; export the graph")


def retained_plan(previous, plan, old_ledger, ledger):
    """Status evolves; work identity, discoveries and recorded leads cannot vanish."""
    old_items, items = unique(previous["items"], "plan item"), unique(plan["items"], "plan item")
    require(set(old_items) <= set(items), "prior plan items vanished; retain bounded/dropped entries")
    require(set(unique(previous["coverage"], "coverage")) <= set(unique(plan["coverage"], "coverage")),
            "prior coverage entries vanished")
    for key, before in old_items.items():
        for field in ("actor", "provider", "kind", "request", "coverage", "origin", "search_branch", "input_kind"):
            require(before.get(field) == items[key].get(field), "work definition changed; create a new item: " + key)
    for collection, mutable in (("search_routes", {"availability", "basis", "checked_at", "limitations"}),
                                ("search_branches", {"status", "reason"})):
        before = unique(previous.get(collection, []), collection)
        after = unique(plan.get(collection, []), collection)
        require(set(before) <= set(after), "previous " + collection + " vanished")
        for key, value in before.items():
            require({k: v for k, v in value.items() if k not in mutable} ==
                    {k: v for k, v in after[key].items() if k not in mutable},
                    collection + " definition changed; add a new ID: " + key)
    checkpoints = previous.get("checkpoints", [])
    require(plan.get("checkpoints", [])[:len(checkpoints)] == checkpoints,
            "coverage checkpoints changed or vanished; append a new checkpoint")
    old_sources, sources = unique(old_ledger["sources"], "source"), unique(ledger["sources"], "source")
    require(set(old_sources) <= set(sources), "previous inventory source vanished")
    for key, source in old_sources.items():
        old_leads = unique(source.get("leads", []), "source lead")
        leads = unique(sources[key].get("leads", []), "source lead")
        require(set(old_leads) <= set(leads), "recorded source lead vanished: " + key)
        for lead_id, before in old_leads.items():
            for field in ("locator", "target_source_id", "plan_item", "kind", "value", "questions"):
                if field == "questions" and "_graph" in old_ledger and "_graph" in ledger:
                    require(set(before.get(field, [])) <= set(leads[lead_id].get(field, [])),
                            "recorded graph lead lost a question origin: " + key)
                    continue
                require(field not in before or leads[lead_id].get(field) == before[field],
                        "recorded source lead changed: " + key)


def save_plan(root, plan, ledger):
    # Hold the shared graph writer from preflight through registration.
    with current_writer(root):
        _resume_current(root)
        return _save_plan(root, plan, ledger)


def _save_plan(root, plan, ledger):
    contract = contract_at(root)
    metadata = read(root / "bundle.json")
    require(digest(root / "contract.json") == metadata["contract_sha256"], "changed contract")
    require(contract["requirements"].get("research_plan"), "contract does not request research_plan")
    operations = plan_operations(root)
    graph_context = graph_ledger_context(root, ledger, bootstrap=True)
    checked_plan(plan, contract, ledger, operations, graph_context=graph_context)
    if contract["requirements"].get("research_budget"):
        budget.validate(root, ledger, "research", return_corrections=(graph_context or {}).get("return_corrections"),
                        non_inspecting_returns=(graph_context or {}).get("non_inspecting_returns"))
    if metadata.get("plan_sha256"):
        previous, old_ledger, _ = plan_snapshot(root)
        retained_plan(previous, plan, old_ledger, ledger)
    if (Path(root) / "graph").exists():
        graph_component("research_graph_work").validate_plan(root, plan, ledger)
    plan = {key: value for key, value in plan.items() if key != "_record"}
    ledger_hash = hashlib.sha256((json.dumps(ledger, ensure_ascii=False, indent=2) + "\n").encode()).hexdigest()
    plan["_record"] = {"saved": now(), "contract_sha256": metadata["contract_sha256"],
                       "parent_sha256": metadata.get("plan_sha256"), "ledger_sha256": ledger_hash}
    directory = root / "plans"
    directory.mkdir(mode=0o700, exist_ok=True)
    plan_hash = hashlib.sha256((json.dumps(plan, ensure_ascii=False, indent=2) + "\n").encode()).hexdigest()
    write(directory / (plan_hash + ".ledger.json"), ledger)
    write(directory / (plan_hash + ".json"), plan)
    publication = _publish_current(root, "plan.json", metadata, dict(metadata, plan_sha256=plan_hash))
    registration = publication.get("graph")
    result = render_plan(root, ledger, operations)
    if registration is not None:
        result["graph"] = registration
    return result


def render_plan(root, ledger=CHECKPOINT_LEDGER, operations=None):
    plan, checkpoint, key = plan_snapshot(root)
    # The displayed snapshot retains its original questions and requirements,
    # including between a contract amendment and the first amended plan save.
    contract_hash = plan["_record"]["contract_sha256"]
    contract = read(root / "contracts" / (contract_hash + ".json"))
    ledger = checkpoint if ledger is CHECKPOINT_LEDGER else ledger
    # A save renders the operation view it validated; concurrent captures may
    # acquire new result IDs before their coordinator inventories those results.
    operations = plan_operations(root) if operations is None else operations
    graph_context = graph_ledger_context(root, ledger, historical=True)
    items, sources = checked_plan(plan, contract, ledger, operations, graph_context=graph_context)
    retained_plan(plan, plan, checkpoint, ledger)
    lines = ["# Living research plan", "", "Snapshot: `" + key + "`", "",
             "Contract: `" + contract_hash + "`", "",
             "Coordinator: " + plan["owner"], "",
             "Execution done does not establish complete coverage. Capture, inspection and analysis are separate.",
             "", "## Questions and coverage", ""]
    for question in contract["questions"]:
        lines.append("- " + question["id"] + ": " + question["text"])
    for entry in plan["coverage"]:
        lines.extend(["", "### " + entry["id"], "", entry["reason"], ""])
        for field in ("questions", "terms", "providers", "people", "periods", "work_types"):
            lines.append("- " + field + ": " + (", ".join(entry[field]) or "None declared; see rationale"))
    if contract["requirements"].get("discovery_followups"):
        lines.extend(["", "## Discovery waves and coverage checkpoints", "", plan["wave_strategy"], ""])
        for checkpoint in plan["checkpoints"]:
            lines.extend(["### " + checkpoint["id"] + " — " + checkpoint["decision"], "",
                          checkpoint["reason"], "", "Limits: " + checkpoint["limits"], ""])
            for field in ("questions", "operations", "inspected_reads", "next_items"):
                lines.append("- " + field + ": " + (", ".join(checkpoint[field]) or "none"))
        lines.extend(["", "### Search limits to review", ""])
        for operation_id, (spec, _, result) in operations.items():
            if spec["kind"] == "search" and result and result["pagination"] in ("more", "capped", "unknown"):
                lines.append("- " + operation_id + " — " + result["pagination"] + ": " + result["note"])
        lines.extend(["", "### Pending discovery leads", ""])
        for source_id, source in sources.items():
            for lead in source["leads"]:
                if lead["status"] == "pending":
                    lines.append("- " + source_id + "/" + lead["id"] + ": " + lead["value"] +
                                 "; questions=" + ", ".join(lead["questions"]))
    if contract["requirements"].get("search_routes"):
        lines.extend(["", "## Artifact locations and retrieval routes", ""])
        for route in plan["search_routes"]:
            lines.extend(["### " + route["id"], "", "- Provider/family: " + route["provider"] + " / " + route["family"],
                          "- Availability: " + route["availability"] + "; checked: " + route["checked_at"],
                          "- Basis: " + route["basis"], "- Limits: " + route["limitations"],
                          "- Invocation: `" + json.dumps(route["invocation"]) + "`",
                          "- Modes: `" + json.dumps(route["modes"]) + "`", ""])
        lines.extend(["## Selected and bounded search branches", ""])
        for branch in plan["search_branches"]:
            assigned = [item for item in items.values() if item.get("search_branch") == branch["id"]]
            lines.extend(["### " + branch["id"] + " — " + branch["status"], "",
                          branch["work_type"] + " → " + branch["artifact_type"] + " at " + branch["location"] +
                          " → " + branch["route"] + " / " + branch["mode"], "", branch["reason"], "",
                          "Work: " + (", ".join(item["id"] + " (" + item["status"] + ")" for item in assigned) or "none"), ""])
    lines.extend(["", "## Worklist and recorded search/read attempts", ""])
    for item in items.values():
        lines.extend(["### " + item["id"] + " — " + item["priority"] + " / " + item["status"], "",
                      item["reason"], "", "Assigned: " + item["actor"] + " / " + item["provider"], "",
                      "Planned request: `" + json.dumps(item["request"], ensure_ascii=False) + "`", ""])
        if "origin" in item:
            lines.extend(["Follow-up origin: `" + json.dumps(item["origin"], ensure_ascii=False) + "`", ""])
        if "search_branch" in item:
            lines.extend(["Search branch: " + item["search_branch"] + "; input kind: " + item["input_kind"], ""])
        for operation_id, (spec, receipt, result) in operations.items():
            if spec.get("plan_item") != item["id"]:
                continue
            cancelled = cancelled_operation(operation_id, (spec, receipt, result), graph_context)
            lines.extend(["- Operation [" + operation_id + "](operations/" + operation_id + "/request.json): " +
                          (receipt["outcome"] if receipt else "cancelled; no captured result" if cancelled else "in progress") + "; actor " + spec["actor"],
                          "  - Actual request: `" + json.dumps(spec["request"], ensure_ascii=False) + "`",
                          "  - Receipt: " + ("[retained](operations/" + operation_id + "/receipt.json)" if receipt else
                                             "absent; verified worker cancellation grants no executed research credit" if cancelled else "pending")])
            if result:
                lines.extend(["  - Pagination/coverage: " + result["pagination"] + "; next cursor: `" +
                              json.dumps(result["next_cursor"]) + "`",
                              "  - Returned source IDs: " + (", ".join(result["source_ids"]) or "none"),
                              "  - Observed limit/note: " + result["note"]])
    lines.extend(["", "## Canonical source inventory view", ""])
    for source_id, source in sources.items():
        lines.extend(["### " + source_id + ": " + source.get("title", source_id), "",
                      "Source: " + source.get("url", "Not yet recorded"), "",
                      "Discovered by: " + ", ".join(source["discovered_by"]), "",
                      "Coverage: " + source["coverage"], "", "Finding: " + source["finding"], "",
                      "Contributor basis: " + source["contributor_basis"], "",
                      "Captured read operations: " + (", ".join(source["reads"]) or "none"), ""])
        if not source["inspections"]:
            lines.append("Inspection: unread / no inspection attested.")
        for inspection in source["inspections"]:
            lines.append("- Inspected by " + inspection["actor"] + " via " + inspection["read_id"] +
                         ": " + inspection["coverage"])
        lines.extend(["", "Analysis: " + source["analysis"]["status"] + " — " + source["analysis"]["reason"], "",
                      "Disposition: " + source["disposition"]["status"] + " — " + source["disposition"]["reason"], ""])
        if "expansion_review" in source:
            lines.extend(["Expansion review: " + source["expansion_review"], ""])
        for lead in source["leads"]:
            lines.append("- Lead " + lead["id"] + " at " + lead["locator"] + ": " + lead["reason"] +
                         "; source=" + lead.get("target_source_id", "none") + "; work=" + lead.get("plan_item", "none"))
            if contract_at(root)["requirements"].get("discovery_followups"):
                lines.append("  - " + lead["kind"] + " / " + lead["status"] + ": " + lead["value"])
    lines.extend(["", "Labels and records do not authenticate actors or establish plan quality, semantic support, "
                  "exhaustive recall, or visual/document quality.", ""])
    (root / "plan.md").write_text("\n".join(lines), encoding="utf-8")
    return {"plan_sha256": key, "plan_view_sha256": digest(root / "plan.md"),
            "plan_view": str(root / "plan.md")}


def validate(root, ledger, phase, semantic_review=None):
    contract = contract_at(root)
    check_contract(contract)
    contract_hash = digest(root / "contract.json")
    metadata = read(root / "bundle.json")
    require(contract_hash == metadata["contract_sha256"], "changed contract")
    graph_context = graph_ledger_context(root, ledger)
    if phase == "synthesis":
        if contract["requirements"].get("protected_followups"):
            followups = graph_component("research_followups")
            stop_review = followups.validate_stop(root)
            if stop_review["stop"] == "partial":
                require(graph_context is not None, "Partial delivery requires a verified current graph export")
                graph_context["partial_delivery"] = followups.partial_delivery(stop_review, graph_context)
        # Planning can use the preceding generated ledger to register an amended
        # contract. Completion must use a plan and export made under that contract;
        # the semantic review is then bound to that exact export below.
        if contract["requirements"].get("research_plan"):
            active_plan, _, _ = plan_snapshot(root)
            require(active_plan["_record"]["contract_sha256"] == contract_hash,
                    "research plan uses an earlier contract; save an amended plan before synthesis")
        if graph_context is not None:
            require(graph_context["contract_sha256"] == contract_hash and
                    graph_context["plan_sha256"] == metadata.get("plan_sha256"),
                    "graph ledger uses an earlier contract or plan; export the current graph before synthesis")
            graph_questions = {q["question_id"]: q for q in graph_context["question_bindings"]}
            require(all(q["id"] in graph_questions and
                        (graph_questions[q["id"]]["text"], graph_questions[q["id"]]["central"]) ==
                        (q["text"], int(q["central"])) for q in contract["questions"]),
                    "graph questions differ from the current contract; synchronize amended questions before synthesis")
    operations = {}
    for operation in sorted((root / "operations").iterdir()):
        request = read(operation / "request.json")
        spec = request["spec"]
        require(spec["id"] == operation.name, "operation identity mismatch")
        previous = root / "contracts" / (request["contract_sha256"] + ".json")
        require(previous.is_file() and digest(previous) == request["contract_sha256"],
                "operation used an unknown or changed contract")
        if read(previous)["requirements"].get("research_plan"):
            check_planned_operation(root, spec, read(previous), started=request["started"])
        if cancelled_operation(operation.name, (spec, None, None), graph_context):
            operations[operation.name] = (spec, None, None)
            continue
        require((operation / "receipt.json").is_file() and (operation / "result.json").is_file(),
                "operation needs a receipt and index or verified request-only cancellation: " + operation.name)
        receipt = read(operation / "receipt.json")
        require(set(receipt["files"]) == {"request.json", "stdout", "stderr"}, "incomplete receipt")
        for name, expected in receipt["files"].items():
            require(digest(operation / name) == expected, "changed operation output: " + operation.name)
        result = read(operation / "result.json")
        require(result["receipt_sha256"] == digest(operation / "receipt.json"), "changed receipt")
        operations[operation.name] = (spec, receipt, result)
    require(operations, "no recorded operations")
    sources = unique(ledger.get("sources"), "source")
    for key, (spec, receipt, result) in operations.items():
        if result is None:
            continue
        require(strings(result.get("source_ids")), "invalid result IDs")
        for source_id in result["source_ids"]:
            require(source_id in sources, "result missing from inventory: " + source_id)
            require(key in sources[source_id]["discovered_by"], "discovery provenance missing: " + source_id)
        if spec["kind"] == "read":
            require(spec["request"]["source_id"] in sources, "read source missing from inventory")
    for source_id, source in sources.items():
        for field in ("title", "url", "coverage", "finding", "contributor_basis"):
            require(nonempty(source.get(field)), "source missing " + field + ": " + source_id)
        require(strings(source.get("discovered_by")) and source["discovered_by"], "source needs discovery basis")
        for discovery in source["discovered_by"]:
            require(backed_discovery(discovery, source, operations, graph_context),
                    "unbacked discovery: " + source_id)
        require(strings(source.get("reads")), "source needs reads, empty if unread")
        for key in source["reads"]:
            require(key in operations, "read receipt missing: " + key)
            spec, receipt, _ = operations[key]
            require(spec["kind"] == "read" and spec["request"]["source_id"] == source_id,
                    "read/source mismatch: " + key)
            require(receipt is not None and receipt["outcome"] == "ok", "failed or cancelled operation cannot establish reading: " + key)
            require(spec["request"]["format"] in contract["providers"][spec["provider"]]["read_formats"],
                    "unapproved read format: " + key)
    claims = unique(ledger.get("claims", []), "claim")
    successful_reads = {key for source in sources.values() for key in source["reads"]}
    for claim in claims.values():
        require(nonempty(claim.get("text")) and nonempty(claim.get("basis")), "claim needs text and basis")
        require(strings(claim.get("reads")) and claim["reads"], "material claim needs supporting reads")
        require(set(claim["reads"]) <= successful_reads, "claim uses unverified read")
        require(nonempty(claim.get("locator")), "claim needs locator and retained excerpt location")
    if contract["requirements"].get("research_plan"):
        history = plan_history(root)
        plan, checkpoint, _ = history[0]
        for previous, earlier in zip(history, history[1:]):
            retained_plan(earlier[0], previous[0], earlier[1], previous[1])
        retained_plan(plan, plan, checkpoint, ledger)
        checked_plan(plan, contract, ledger, operations, phase, graph_context)
    if phase == "synthesis":
        if graph_context is not None:
            stops = graph_context.get("stop_checks", {})
            require(stops.get("settled") is True or graph_context.get("partial_delivery"),
                    "graph research has unresolved work, source scopes, questions or follow-ups; inspect graph validate-ledger stop_checks")
            graph_module().validate(root)
            recovery = graph_component("research_graph_work").recovery_status(root)
            require(not any(item["unresolved_operation_ids"] for item in recovery["items"]),
                    "original worker operations still need reconciliation before synthesis")
            require(graph_component("research_graph_ingest").ingestion_status(root)["complete"],
                    "graph synthesis needs every original operation and reader return synchronized")
        answers = unique(ledger.get("questions"), "question")
        require(set(answers) == {item["id"] for item in contract["questions"]}, "question coverage mismatch")
        for question in contract["questions"]:
            answer = answers[question["id"]]
            require(answer.get("status") in ("answered", "bounded"), "unresolved question: " + question["id"])
            require(nonempty(answer.get("reason")), "question needs evidence/gap rationale")
            require(strings(answer.get("claims")) and set(answer["claims"]) <= set(claims), "unknown claims")
            if answer["status"] == "answered":
                require(answer["claims"], "answered question needs evidence")
            elif question["central"]:
                require(strings(answer.get("followups")) and answer["followups"] and
                        set(answer["followups"]) <= {key for key, (_, receipt, indexed) in operations.items()
                                                     if receipt is not None and indexed is not None},
                        "bounded central gap needs executed follow-ups")
        review = ledger.get("semantic_review", {})
        if graph_context is not None:
            review = checked_semantic_review(root, semantic_review, graph_context)
        require(nonempty(review.get("reviewer")) and nonempty(review.get("note")),
                "synthesis needs recorded semantic review, not a truth score")
    result = {"operations": len(operations), "sources": len(sources), "claims": len(claims),
            "executed_operations": sum(item[1] is not None for item in operations.values()),
            "cancelled_operations": sorted(key for key, record in operations.items()
                                           if cancelled_operation(key, record, graph_context)),
            "tool_imports": sum(item[1] is not None and item[1]["provenance"] == "agent-attested-tool-output"
                                for item in operations.values())}
    if contract["requirements"].get("research_budget"):
        result["research_budget"] = budget.validate(root, ledger, phase,
            return_corrections=(graph_context or {}).get("return_corrections"),
            non_inspecting_returns=(graph_context or {}).get("non_inspecting_returns"))
    if graph_context is not None:
        result["graph"] = graph_context
        if graph_context.get("partial_delivery"):
            result.update(status="partial", coverage_complete=False)
        if phase == "synthesis":
            result["semantic_review"] = review
    return result


def checked_semantic_review(root, path, graph_context):
    """Bind a separate reviewer attestation to an immutable generated inventory."""
    require(path is not None, "graph synthesis needs --semantic-review inside the bundle")
    path = Path(path)
    if not path.is_absolute():
        path = Path(root) / path
    require(path.is_file() and path.resolve().is_relative_to(Path(root).resolve()),
            "semantic review must be a retained file inside the bundle")
    review = read(path)
    require(isinstance(review, dict) and nonempty(review.get("reviewer")) and nonempty(review.get("note")),
            "semantic review needs an explicit reviewer and note")
    require(review.get("ledger_sha256") == graph_context["ledger_sha256"] and
            type(review.get("graph_sequence")) is int and
            review["graph_sequence"] == graph_context["represented_sequence"],
            "semantic review names a different generated ledger or graph sequence")
    return dict(review, path=str(path.resolve().relative_to(Path(root).resolve())),
                sha256=digest(path), basis="explicit reviewer attestation; identity and semantic adequacy are not authenticated")


def normalized(text):
    return " ".join(text.split())


class ArtifactHTML(HTMLParser):
    """Visible body text, href multiset and ordered table cells; no style equality."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text = []
        self.links = []
        self.anchors = []
        self.anchor = None
        self.tables = []
        self.table = None
        self.row = None
        self.cell = None
        self.ignored = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ("head", "style", "script"):
            self.ignored += 1
        if self.ignored:
            return
        if tag in ("p", "div", "br", "li", "tr", "td", "th", "h1", "h2", "h3", "section"):
            self.add(" ")
        if tag == "table":
            require(self.table is None, "nested tables require a provider-specific check")
            self.table = []
        if tag == "tr" and self.table is not None:
            require(self.row is None, "unclosed table row")
            self.row = []
        if tag in ("td", "th") and self.row is not None:
            require(self.cell is None, "unclosed table cell")
            self.cell = {"text": [], "links": [], "rowspan": attrs.get("rowspan", "1"),
                         "colspan": attrs.get("colspan", "1")}
        if tag == "a" and attrs.get("href"):
            require(self.anchor is None, "nested anchors require provider-specific repair")
            self.links.append(attrs["href"])
            self.anchor = {"href": attrs["href"], "start": len(self.text)}
            if self.cell is not None:
                self.cell["links"].append(attrs["href"])

    def handle_endtag(self, tag):
        if self.ignored:
            if tag in ("head", "style", "script"):
                self.ignored -= 1
            return
        if tag == "a" and self.anchor is not None:
            self.anchor["end"] = len(self.text)
            self.anchors.append(self.anchor)
            self.anchor = None
        if tag in ("td", "th") and self.cell is not None:
            self.cell["text"] = normalized("".join(self.cell["text"]))
            self.row.append(self.cell)
            self.cell = None
        if tag == "tr" and self.row is not None:
            self.table.append(self.row)
            self.row = None
        if tag == "table" and self.table is not None:
            require(self.row is None and self.cell is None, "unclosed table content")
            self.tables.append(self.table)
            self.table = None
        if tag in ("p", "div", "li", "td", "th", "h1", "h2", "h3", "section"):
            self.add(" ")

    def add(self, data):
        self.text.append(data)
        if self.cell is not None:
            self.cell["text"].append(data)

    def handle_data(self, data):
        if not self.ignored:
            self.add(data)


def inspect_html(paths):
    text, links, tables, sections = [], [], [], []
    for path in paths:
        parser = ArtifactHTML()
        parser.feed(Path(path).read_text(encoding="utf-8"))
        parser.close()
        require(parser.table is None and parser.anchor is None and not parser.ignored,
                "incomplete HTML export")
        body = normalized("".join(parser.text))
        # Keep citation placement as well as URL counts. Normalized prefix offsets
        # ignore formatting wrappers but detect a link moved to another assertion.
        anchors = [{"href": anchor["href"],
                    "offset": len(normalized("".join(parser.text[:anchor["start"]]))),
                    "text": normalized("".join(parser.text[anchor["start"]:anchor["end"]]))}
                   for anchor in parser.anchors]
        sections.append({"text": body, "anchors": anchors, "tables": parser.tables})
        text.append(body)
        links.extend(parser.links)
        tables.extend(parser.tables)
    return {"text": normalized(" ".join(text)), "links": Counter(links),
            "tables": tables, "sections": sections}


def publication(root, ledger, manifest, semantic_review=None):
    validation = validate(root, ledger, "synthesis", semantic_review)
    artifacts = unique(manifest["artifacts"], "artifact")
    require(set(artifacts) == {"brief", "companion"}, "publication expects brief and companion")
    observations, hashes = {}, {}
    for key, artifact in artifacts.items():
        require(nonempty(artifact.get("url")), "artifact URL required")
        for side in ("expected", "actual"):
            require(strings(artifact.get(side)) and artifact[side], "artifact needs exported HTML files")
        require(not set(map(str, map(Path.resolve, map(Path, artifact["expected"])))) &
                set(map(str, map(Path.resolve, map(Path, artifact["actual"])))),
                "expected and actual must be separate files")
        expected = inspect_html(artifact["expected"])
        actual = inspect_html(artifact["actual"])
        require(expected == actual, "artifact text, links or table cells differ: " + key)
        observations[key] = actual
        hashes[key] = {side: {path: digest(Path(path)) for path in artifact[side]}
                       for side in ("expected", "actual")}
    for key, other in (("brief", "companion"), ("companion", "brief")):
        require(artifacts[other]["url"] in observations[key]["links"], "missing mutual link: " + key)
    requirements = contract_at(root)["requirements"]
    maximum = requirements.get("brief_max_words")
    if maximum is not None:
        require(len(observations["brief"]["text"].split()) <= maximum, "brief exceeds requested word limit")
    candidate_check = None
    if requirements["candidate_table"]:
        tables = observations["companion"]["tables"]
        selected = manifest.get("candidate_tables")
        require(isinstance(selected, list) and selected and
                all(type(index) is int and 0 <= index < len(tables) for index in selected) and
                len(set(selected)) == len(selected), "select candidate_tables by companion table index")
        rows = [row for index in selected for row in tables[index]]
        source_ids = {source["id"] for source in ledger["sources"]}
        declared_rows = manifest.get("candidate_rows")
        row_ids = {key: key for key in source_ids}
        exclusion_reasons = {}
        if declared_rows is not None:
            require(isinstance(declared_rows, list) and len(declared_rows) == len(source_ids),
                    "candidate_rows must map every source exactly once")
            row_ids, markers = {}, set()
            for entry in declared_rows:
                require(isinstance(entry, dict), "candidate row mapping must be an object")
                source_id, marker = entry.get("source_id"), entry.get("row_id")
                require(isinstance(source_id, str) and source_id in source_ids and source_id not in row_ids,
                        "candidate_rows has an unknown or duplicate source")
                require(nonempty(marker) and marker == normalized(marker) and marker not in markers,
                        "candidate row IDs must be unique normalized text")
                row_ids[source_id] = marker
                markers.add(marker)
                if "navigation_exclusion_reason" in entry:
                    reason = entry["navigation_exclusion_reason"]
                    require(nonempty(reason) and reason == normalized(reason),
                            "navigation exclusion reason must be nonempty normalized text")
                    exclusion_reasons[source_id] = reason
        authorized_exclusions = validation.get("graph", {}).get("navigation_exclusions", {})
        contract_hash = digest(root / "contract.json")
        used_rows, url_normalizations, excluded_navigation = set(), [], []
        for source in ledger["sources"]:
            matches = [(index, row) for index, row in enumerate(rows)
                       if row_ids[source["id"]] in {cell["text"] for cell in row}]
            require(len(matches) == 1, "candidate needs exactly one table row: " + source["id"])
            index, row = matches[0]
            require(index not in used_rows, "candidate records must have distinct table rows")
            used_rows.add(index)
            navigation_url = source["url"].strip()
            row_links = [url for cell in row for url in cell["links"]]
            row_text = normalized(" ".join(cell["text"] for cell in row))
            if source["id"] in exclusion_reasons:
                proof = authorized_exclusions.get(source["id"], {})
                require(proof.get("contract_sha256") == contract_hash,
                        "navigation exclusion needs an explicit current frozen contract identity: " + source["id"])
                require(not row_links, "excluded candidate row must contain no hyperlinks: " + source["id"])
                require(exclusion_reasons[source["id"]] in row_text,
                        "candidate row missing navigation exclusion reason: " + source["id"])
                excluded_navigation.append({"source_id_sha256": hashlib.sha256(source["id"].encode()).hexdigest(),
                                            "reason": exclusion_reasons[source["id"]], **proof})
            else:
                require(navigation_url in row_links, "candidate row missing source link: " + source["id"])
            if source["id"] not in exclusion_reasons and navigation_url != source["url"]:
                url_normalizations.append({"source_id_sha256": hashlib.sha256(source["id"].encode()).hexdigest(),
                                           "navigation_url_sha256": hashlib.sha256(navigation_url.encode()).hexdigest(),
                                           "rule": "strip-surrounding-whitespace"})
            for field in ("title", "coverage", "finding", "contributor_basis"):
                require(normalized(source[field]) in row_text,
                        "candidate row missing " + field + ": " + source["id"])
        candidate_check = {"rows_checked": len(used_rows),
                           "row_mapping_sha256": hashlib.sha256(json.dumps(
                               row_ids, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                           "identity_basis": "declared row IDs" if declared_rows is not None else "raw source IDs",
                           "navigation_url_normalizations": url_normalizations,
                           "navigation_exclusions": excluded_navigation}
    result = {"artifacts": hashes, "scope": "HTML text, links, cells, declared requirements; semantic/layout review separate"}
    if "graph" in validation:
        result.update(graph=validation["graph"], semantic_review=validation["semantic_review"])
    if validation.get("status") == "partial":
        result.update(status="partial", coverage_complete=False)
    if candidate_check is not None:
        result["candidate_check"] = candidate_check
    if requirements.get("research_plan"):
        check = manifest.get("plan_review")
        require(isinstance(check, dict) and nonempty(check.get("reviewer")) and
                nonempty(check.get("note")) and nonempty(check.get("companion_locator")),
                "publication needs review of plan coverage/worklist placement in companion")
        view = render_plan(root, ledger)
        require(check.get("plan_view_sha256") == view["plan_view_sha256"],
                "publication plan review must bind the refreshed plan view")
        result.update(view)
    return result


def add_graph_commands(sub):
    """Keep graph storage behind the existing bundle entry point."""
    command = sub.add_parser("graph", help="Record and inspect the investigation graph")
    actions = command.add_subparsers(dest="graph_action", required=True)
    for name in ("export", "verify-export", "validate-ledger", "import", "migrate"):
        action = actions.add_parser(name)
        action.add_argument("root", type=Path)
        if name in ("export", "import"):
            action.add_argument("--id", required=True)
        if name == "validate-ledger":
            action.add_argument("--ledger", type=Path, required=True)
        if name == "import":
            action.add_argument("--directory", type=Path, required=True)
            action.add_argument("--selection", type=Path, required=True)
        if name == "migrate":
            action.add_argument("--destination", type=Path, required=True)
            action.add_argument("--run-id", required=True)
            action.add_argument("--actor", default="coordinator")
            action.add_argument("--ledger", type=Path)
    for name in ("init", "upgrade-schema", "ingest", "reconcile", "checkpoint", "recover", "validate", "sync", "ingestion-status", "check-return", "reject-return", "check-index", "omit-index-version",
                 "register-plan", "next-work", "work-status", "work", "followup-status", "followup-checkpoint"):
        action = actions.add_parser(name)
        action.add_argument("root", type=Path)
        if name == "init":
            action.add_argument("--actor", default="coordinator")
            action.add_argument("--run-id", required=True)
        if name in ("ingest", "reconcile", "check-return", "check-index", "omit-index-version"):
            action.add_argument("--file", type=Path, required=True)
        if name in ("check-index", "reject-return", "omit-index-version"):
            action.add_argument("--id", required=True)
        if name in ("reject-return", "omit-index-version"):
            action.add_argument("--sha256", required=True)
            action.add_argument("--reason", required=True)
        if name == "checkpoint":
            action.add_argument("--id", required=True)
        if name == "recover":
            action.add_argument("--batch-id")
            action.add_argument("--apply", action="store_true",
                                help="Explicitly ingest the selected pending packet; never dispatch a provider")
        if name == "sync":
            original = action.add_mutually_exclusive_group()
            original.add_argument("--operation-id")
            original.add_argument("--return-id")
        if name == "register-plan":
            action.add_argument("--sha256", required=True)
        if name == "next-work":
            action.add_argument("--limit", type=int, default=50)
            action.add_argument("--actor")
        if name in ("work", "followup-checkpoint"):
            action.add_argument("--file", type=Path, required=True)
    for name, filters in GRAPH_QUERIES.items():
        action = actions.add_parser(name, help="Read a bounded page of graph facts")
        action.add_argument("root", type=Path)
        action.add_argument("--limit", type=int, default=50)
        action.add_argument("--cursor")
        for field in filters:
            options = {}
            if field in ("max_generation", "revision", "after_sequence", "through_sequence"):
                options["type"] = int
            elif field == "unresolved_only":
                options.update(action="store_const", const=True)
            elif field == "include":
                options["nargs"] = "+"
            if field == {"artifact": "artifact_id", "neighbors": "artifact_id",
                         "coverage": "question_id", "evidence": "claim_id"}.get(name):
                options["required"] = True
            action.add_argument("--" + field.replace("_", "-"), **options)


def graph_module():
    return graph_component("research_graph")


def graph_component(name):
    """Load only controller-selected siblings, also under Python isolated mode."""
    path = Path(__file__).with_name(name + ".py")
    existing = sys.modules.get(name)
    if existing is not None and Path(existing.__file__).resolve() == path.resolve():
        return existing
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[spec.name]
        raise
    return module


def graph_evidence_validator(root, connection, packet, mutation):
    component = "research_graph_exports" if packet.get("packet_kind") in ("import", "migrate") else "research_graph_work"
    return graph_component(component).evidence_validator(root, connection, packet, mutation)


def graph_command(args):
    graph = graph_module()
    try:
        action = args.graph_action
        if action in GRAPH_QUERIES:
            filters = {field: getattr(args, field) for field in GRAPH_QUERIES[action]
                       if getattr(args, field) is not None}
            result = graph_component("research_graph_queries").query(
                args.root, action, filters, args.limit, args.cursor)
        elif action in ("followup-status", "followup-checkpoint"):
            followups = graph_component("research_followups")
            result = followups.status(args.root) if action == "followup-status" else followups.checkpoint(args.root, read(args.file))
        elif action in ("register-plan", "next-work", "work-status", "work"):
            work = graph_component("research_graph_work")
            if action == "register-plan":
                result = work.register_plan(args.root, args.sha256)
            elif action == "next-work":
                result = work.fair_frontier(args.root, args.limit, args.actor)
            elif action == "work-status":
                result = work.recovery_status(args.root)
            else:
                request = graph._read_json(args.file.read_bytes())
                if not isinstance(request, dict) or request.get("action") not in GRAPH_WORK_ACTIONS:
                    raise graph.GraphError("invalid_packet", "Unknown graph work action")
                requested = request.pop("action")
                result = getattr(work, GRAPH_WORK_ACTIONS[requested])(args.root, **request)
                result = dict(result, work_action=requested)
        elif action in ("export", "verify-export", "validate-ledger", "import", "migrate"):
            exports = graph_component("research_graph_exports")
            if action == "export":
                result = exports.export_snapshot(args.root, args.id)
            elif action == "verify-export":
                result = exports.verify_export(args.root)
            elif action == "validate-ledger":
                result = exports.validate_ledger(args.root, graph._read_json(args.ledger.read_bytes()))
            elif action == "import":
                result = exports.import_prior(args.root, args.directory, args.id,
                                              graph._read_json(args.selection.read_bytes()))
            else:
                result = exports.migrate_legacy(args.root, args.destination, run_id=args.run_id,
                                               actor_id=args.actor, ledger_path=args.ledger)
        elif action == "init":
            result = graph.initialize(args.root, args.actor, args.run_id)
        elif action == "upgrade-schema":
            result = graph.upgrade_schema(args.root)
        elif action in ("ingest", "reconcile"):
            if action == "reconcile":
                packet = read(args.file)
                if not isinstance(packet, dict) or packet.get("packet_kind") != "reconcile":
                    raise graph.GraphError("invalid_packet", "reconcile requires a reconciliation packet")
            # Pass the original file, preserving its exact bytes and duplicate-key checks.
            result = graph.ingest(args.root, args.file,
                                  evidence_validator=graph_evidence_validator)
        elif action == "checkpoint":
            result = graph.checkpoint(args.root, args.id)
        elif action == "recover":
            result = graph.recover(args.root, args.batch_id, args.apply,
                                   evidence_validator=graph_evidence_validator)
        elif action == "ingestion-status":
            result = graph_component("research_graph_ingest").ingestion_status(args.root)
        elif action == "check-return":
            result = graph_component("research_graph_ingest").validate_reader_return(
                args.root, graph._read_json(args.file.read_bytes()))
        elif action == "check-index":
            result = graph_component("research_graph_ingest").validate_index_result(
                args.root, args.id, graph._read_json(args.file.read_bytes()))
        elif action == "omit-index-version":
            result = graph_component("research_graph_ingest").omit_index_version(
                args.root, args.id, expected_original_sha256=args.sha256,
                omissions=graph._read_json(args.file.read_bytes()), reason=args.reason)
        elif action == "reject-return":
            result = graph_component("research_graph_ingest").reject_reader_return(
                args.root, args.id, expected_original_sha256=args.sha256, reason=args.reason)
        elif action == "sync":
            if args.operation_id or args.return_id:
                result = sync_graph_evidence(args.root, operation_id=args.operation_id, return_id=args.return_id)
                if result is None:
                    raise graph.GraphError("pending_dependency", "Initialize the graph before synchronizing evidence")
            else:
                result = sync_graph(args.root)
        else:
            result = graph.validate(args.root)
        print(json.dumps(result))
        return 0 if result["status"] == "pass" else 1
    except graph.GraphError as error:
        print(json.dumps(error.response(args.graph_action)))
        return 1
    except (ValueError, KeyError, TypeError, AttributeError, OSError):
        error = graph.GraphError("invalid_packet", "Graph command input is unavailable or malformed")
        print(json.dumps(error.response(args.graph_action)))
        return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    add_graph_commands(sub)
    for name in ("init", "amend", "recover-current", "plan", "plan-view", "begin", "capture", "finish", "index", "validate", "publication",
                 "budget-activity", "budget-stage", "budget-status", "budget-classify", "budget-reopen", "budget-assess",
                 "reader-return"):
        command = sub.add_parser(name)
        command.add_argument("root", type=Path)
        if name in ("init", "amend"):
            command.add_argument("--contract", required=True)
        if name == "plan":
            command.add_argument("--plan", required=True)
        if name in ("begin", "capture"):
            command.add_argument("--spec", required=True)
        if name in ("finish", "index"):
            command.add_argument("--id", required=True)
        if name == "finish":
            command.add_argument("--output", required=True)
            command.add_argument("--error")
            command.add_argument("--outcome", choices=("ok", "error", "timeout", "interrupted"), required=True)
        if name == "index":
            command.add_argument("--result", required=True)
        if name in ("validate", "publication", "plan"):
            command.add_argument("--ledger", required=True)
        if name in ("validate", "publication"):
            command.add_argument("--semantic-review", type=Path)
        if name == "plan-view":
            command.add_argument("--ledger")
        if name == "validate":
            command.add_argument("--phase", choices=("research", "synthesis"), default="research")
        if name == "publication":
            command.add_argument("--manifest", required=True)
        if name == "budget-activity":
            command.add_argument("--actor", required=True)
            command.add_argument("--action", dest="activity_action", choices=("start", "stop"), required=True)
            command.add_argument("--allocation")
            command.add_argument("--purpose")
            command.add_argument("--branch")
        if name == "budget-stage":
            command.add_argument("--stage", choices=budget.STAGES, required=True)
            command.add_argument("--reason", required=True)
        if name == "budget-classify":
            command.add_argument("--id", required=True)
            command.add_argument("--classification", choices=budget.TERMINAL + ("transient", "unknown"), required=True)
            command.add_argument("--reason", required=True)
        if name == "budget-reopen":
            command.add_argument("--source-id", required=True)
            command.add_argument("--reason", required=True)
        if name == "budget-assess":
            command.add_argument("--read-id", required=True)
            command.add_argument("--actor", required=True)
            command.add_argument("--assignment")
        if name == "reader-return":
            command.add_argument("--return", dest="returned", required=True)
    args = parser.parse_args()
    try:
        result, code = None, 0
        if args.action == "graph":
            return graph_command(args)
        if args.action == "init":
            init(args.root, read(args.contract))
        elif args.action == "amend":
            amend(args.root, read(args.contract))
        elif args.action == "recover-current":
            result = recover_current(args.root)
        elif args.action == "plan":
            result = save_plan(args.root, read(args.plan), read(args.ledger))
        elif args.action == "plan-view":
            result = render_plan(args.root, read(args.ledger)) if args.ledger else render_plan(args.root)
        elif args.action == "begin":
            spec = read(args.spec)
            operation = start(args.root, spec, "tool-import")
            if "research_admission" in spec:
                result = {"operation": str(operation), "effective_timeout": spec["timeout"],
                          "research_admission": spec["research_admission"]}
        elif args.action == "capture":
            code = capture(args.root, read(args.spec))
        elif args.action == "finish":
            import_output(args.root, args.id, args.output, args.error, args.outcome)
        elif args.action == "index":
            result = index_result(args.root, args.id, read(args.result))
        elif args.action == "validate":
            result = validate(args.root, read(args.ledger), args.phase, args.semantic_review)
        elif args.action == "publication":
            result = publication(args.root, read(args.ledger), read(args.manifest), args.semantic_review)
        elif args.action == "budget-activity":
            result = budget.activity(args.root, args.actor, args.activity_action,
                                     allocation=args.allocation, purpose=args.purpose, branch=args.branch)
        elif args.action == "budget-stage":
            result = budget.stage(args.root, args.stage, args.reason)
        elif args.action == "budget-status":
            result = budget.status(args.root)
        elif args.action == "budget-classify":
            budget.classify(args.root, args.id, args.classification, args.reason)
        elif args.action == "budget-reopen":
            budget.reopen(args.root, args.source_id, args.reason)
        elif args.action == "budget-assess":
            with current_writer(args.root):
                validator = lambda root, read_id, actor, activity: graph_component("research_graph_work").prepare_assessment(
                    root, read_id, actor, activity, args.assignment)
                budget.assess(args.root, args.read_id, args.actor, assignment_validator=validator)
        elif args.action == "reader-return":
            result = record_reader_return(args.root, read(args.returned))
        if args.action in ("validate", "publication"):
            result.update(contract_sha256=digest(args.root / "contract.json"),
                          ledger_sha256=digest(Path(args.ledger)),
                          checker_sha256=digest(Path(__file__)),
                          budget_checker_sha256=digest(Path(__file__).with_name("research_budget.py")))
            if contract_at(args.root)["requirements"].get("research_plan"):
                result["plan_sha256"] = read(args.root / "bundle.json")["plan_sha256"]
        print(json.dumps({"status": ("partial" if (result or {}).get("status") == "partial" else "pass") if code == 0 else "fail", "action": args.action,
                          "result": result}))
        return code
    except (ValueError, KeyError, TypeError, AttributeError, OSError) as error:
        # Deliberately omit arbitrary source/command/error bodies from diagnostics.
        print(json.dumps({"status": "fail", "action": args.action,
                          "error": str(error) if isinstance(error, ValueError) else type(error).__name__}))
        return 1
    except Exception as error:
        graph = sys.modules.get("research_graph")
        if graph is None or not isinstance(error, graph.GraphError):
            raise
        print(json.dumps(error.response(args.action)))
        return 1


if __name__ == "__main__":
    sys.exit(main())
