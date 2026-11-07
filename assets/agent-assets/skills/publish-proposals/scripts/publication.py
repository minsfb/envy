#!/usr/bin/env python3
"""Offline proposal planning and source-qualified, fixed GitHub publication.

GitHub construction follows local input/store/exact-authority preflight. Source
admission is separate from permission to publish; legacy v1 stays unavailable.
"""

import argparse
import contextlib
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile

try:
    import fcntl
except ImportError:
    fcntl = None


MAX_INPUT = 1024 * 1024
MAX_DETAIL = 64 * 1024
MAX_BODY = 128 * 1024
MAX_PLAN = 4 * 1024 * 1024
MAX_RECEIPT = 8 * 1024 * 1024
MAX_PROPOSALS = 100
MAX_HISTORY = 1024
MAX_AUTHORITIES = 64
MAX_DEPTH = 16
MAX_NODES = 50000
PROVIDER = {"name": "offline-fixture", "version": 1}
FILES = {"proposals.json", "plan.json", "receipt.json", "lock"}
STATES = {"planned", "attempting", "created-unverified", "verified", "uncertain", "blocked"}
EVENTS = {"intent", "result", "verified", "uncertain", "blocked", "reconciled"}
GITHUB_BODY = 60 * 1024
GITHUB_POLICY = {
    "discovery": "best-effort-single-attempt",
    "consistency": "not-guaranteed",
    "coordination": "one-canonical-store-and-coordinated-context",
    "reconciliation": "required-artifact-not-authorship-or-global-uniqueness",
    "permission": "observed-role-not-a-create-permission-guarantee",
    "body_bytes": GITHUB_BODY, "page_nodes": 100, "pages": 10,
    "discovery_bytes": 32 * 1024 * 1024, "call_seconds": 30,
    "term_seconds": 5, "reap_seconds": 5, "invocation_seconds": 900,
    "stdout_bytes": 8 * 1024 * 1024, "stderr_bytes": 64 * 1024,
    "calls": 2048, "output_bytes": 256 * 1024 * 1024,
}
GITHUB_HTTPS_PROFILE = "fixed-https-graphql-v1"
GITHUB_HTTPS_POLICY = {
    "request": "fixed-https-post-v1", "redirects": "refused", "retries": "none",
    "credentials": "fixed-gh-auth-token-v2", "credential_bytes": 4096,
    "worker_input_bytes": 1056768, "proxy": "https-env-subset-v1",
    "tls": "linux-runtime-default-verified", "worker": "isolated-no-site-v1",
    "config": "gh-v1-no-unix-socket",
}
MULTI_ROLES = {
    "project.create": "project", "details.create": "details", "issue.create": "issue",
    "tracker.create": "tracker", "details.comment.create": "details",
    "issue.comment.create": "issue", "tracker.dependencies.add": "tracker",
}
MULTI_OPERATIONS = 121
MULTI_BODY = 64 * 1024
MULTI_POLICY = {
    "discovery": "best-effort-single-attempt", "consistency": "not-guaranteed",
    "writes": "one-attempt-per-operation", "dependencies": "additive-only",
    "coordination": "one-canonical-store-and-coordinated-context",
    "audiences": "equal-only", "proposals": 20, "operations": MULTI_OPERATIONS,
    "body_bytes": MULTI_BODY, "issue_body_bytes": GITHUB_BODY,
    "reference_bytes": 512, "history": MAX_HISTORY, "receipt_bytes": MAX_RECEIPT,
}


class Stop(Exception):
    """Only fixed diagnostic codes, never provider/input exception text."""


def need(condition, code):
    if not condition:
        raise Stop(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        need(key not in result, "duplicate_key")
        result[key] = value
    return result


def bounded(value, limit):
    """JSON data only; no hooks, numeric coercion, custom mappings or objects."""
    nodes = 0
    active = set()

    def visit(item, depth):
        nonlocal nodes
        nodes += 1
        need(nodes <= MAX_NODES and depth <= MAX_DEPTH, "data_bound")
        kind = type(item)
        need(kind in {dict, list, str, int, bool, type(None)}, "invalid_json_type")
        if kind is int:
            need(-(2 ** 53) < item < 2 ** 53, "integer_bound")
        if kind is str:
            need(len(item) <= limit and not any(0xD800 <= ord(c) <= 0xDFFF for c in item), "invalid_text")
        if kind in {dict, list}:
            need(id(item) not in active, "cyclic_data")
            active.add(id(item))
            if kind is dict:
                for key, child in item.items():
                    need(type(key) is str, "invalid_key")
                    visit(key, depth + 1)
                    visit(child, depth + 1)
            else:
                for child in item:
                    visit(child, depth + 1)
            active.remove(id(item))

    visit(value, 0)
    need(len(canonical(value)) + 1 <= limit, "data_too_large")
    return value


def parse(data, limit=MAX_INPUT):
    need(type(data) is bytes and len(data) <= limit, "data_too_large")
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=no_duplicates,
                           parse_float=lambda _: (_ for _ in ()).throw(Stop("floating_point_unsupported")),
                           parse_constant=lambda _: (_ for _ in ()).throw(Stop("invalid_json_number")))
        return bounded(value, limit)
    except (ValueError, UnicodeError, RecursionError):
        raise Stop("invalid_json") from None


def fields(value, names):
    need(type(value) is dict and set(value) == set(names.split()), "invalid_fields")


def text(value, limit=4096, empty=False):
    need(type(value) is str and (empty or bool(value)), "invalid_text")
    need(len(value.encode("utf-8")) <= limit and not any(ord(c) < 32 and c not in "\n\t" for c in value), "invalid_text")


def token(value, pattern=r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}"):
    need(type(value) is str and re.fullmatch(pattern, value) is not None, "invalid_identifier")


def opaque(value):
    token(value, r"[0-9a-f]{32}")


def sha(value):
    token(value, r"[0-9a-f]{64}")


def sequence(value, limit=100):
    need(type(value) is list and len(value) <= limit, "invalid_list")


def number(value):
    token(value, r"[1-9][0-9]{0,19}")


def destination_check(value):
    fields(value, "host account account_id owner repository repository_id audience")
    text(value["host"], 253)
    token(value["host"], r"[a-z0-9]+(?:[.-][a-z0-9]+)*\.[a-z]{2,63}")
    for name in ("account", "owner", "repository"):
        token(value[name])
        need(value[name] not in {".", ".."}, "invalid_identifier")
    number(value["account_id"])
    number(value["repository_id"])
    audience = value["audience"]
    fields(audience, "kind realm id")
    need(audience == {"kind": "internet", "realm": "internet", "id": "public"}
         or audience == {"kind": "repository", "realm": value["host"], "id": value["repository_id"]},
         "unsupported_audience")


def proposal_check(value):
    bounded(value, MAX_INPUT)
    fields(value, "version type batch_id destination proposals")
    need(type(value["version"]) is int and value["version"] == 1 and value["type"] == "ProposalSet", "unsupported_version")
    opaque(value["batch_id"])
    destination_check(value["destination"])
    sequence(value["proposals"], MAX_PROPOSALS)
    need(bool(value["proposals"]), "empty_selection")
    ids = []
    for proposal in value["proposals"]:
        fields(proposal, "id title problem evidence scope detail steps acceptance non_goals dependencies sensitivity")
        opaque(proposal["id"])
        ids.append(proposal["id"])
        text(proposal["title"], 256)
        need("\n" not in proposal["title"] and "\t" not in proposal["title"], "invalid_title")
        for name in ("problem", "evidence", "scope"):
            text(proposal[name])
        text(proposal["detail"], MAX_DETAIL)
        for name in ("steps", "acceptance", "non_goals"):
            sequence(proposal[name], 32)
            need(bool(proposal[name]), "empty_section")
            for item in proposal[name]:
                text(item, 2048)
        sequence(proposal["dependencies"], MAX_PROPOSALS - 1)
        for item in proposal["dependencies"]:
            opaque(item)
        need(len(set(proposal["dependencies"])) == len(proposal["dependencies"]), "duplicate_dependency")
        need(proposal["sensitivity"] in {"public", "repository"}, "unsupported_sensitivity")
        need(proposal["sensitivity"] == "public" or value["destination"]["audience"]["kind"] == "repository", "audience_mismatch")
    need(len(set(ids)) == len(ids), "duplicate_proposal")
    for proposal in value["proposals"]:
        need(set(proposal["dependencies"]).issubset(ids) and proposal["id"] not in proposal["dependencies"], "invalid_dependency")
    ordered(value)


def ordered(value):
    # Stable selection order among ready proposals; bounded <=100, no general DAG.
    pending = list(value["proposals"])
    result, done = [], set()
    while pending:
        ready = [p for p in pending if set(p["dependencies"]).issubset(done)]
        need(bool(ready), "dependency_cycle")
        for proposal in ready:
            result.append(proposal)
            done.add(proposal["id"])
            pending.remove(proposal)
    return result


def body(proposal, marker, links):
    parts = []
    for name in ("problem", "evidence", "scope", "detail", "steps", "acceptance", "non_goals"):
        value = proposal[name]
        content = "\n".join("- " + item for item in value) if type(value) is list else value
        parts.append("## " + name.replace("_", " ").title() + "\n\n" + content)
    parts.append("## Dependencies\n\n" + ("\n".join("- " + link for link in links) if links else "None."))
    parts.append("Tracking only; filing does not authorize implementation.\n<!-- " + marker + " -->")
    rendered = "\n\n".join(parts) + "\n"
    text(rendered, MAX_BODY)
    need(len(canonical(rendered)) <= MAX_BODY, "body_too_large")
    return rendered


def source_hash():
    # This trusted source identity is binding, not authentication or a sandbox.
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def make_plan(proposals, prepare_authority):
    proposal_check(proposals)
    text(prepare_authority, 512)
    snapshot = json.loads(canonical(proposals))
    operations = []
    for proposal in ordered(snapshot):
        marker = "proposal-v1:" + snapshot["batch_id"] + ":" + proposal["id"]
        # Validate a conservative maximum-size rendering before creating a store.
        body(proposal, marker, ["https://" + "x" * 512] * len(proposal["dependencies"]))
        operations.append({"id": "create-" + proposal["id"], "kind": "issue.create",
                           "proposal_id": proposal["id"], "marker": marker,
                           "body": body(proposal, marker, ["<issue-url:create-" + item + ">" for item in proposal["dependencies"]]),
                           "requires": ["create-" + item for item in proposal["dependencies"]]})
    plan = {"version": 1, "type": "PublicationPlan", "source_sha256": source_hash(),
            "provider": dict(PROVIDER), "prepare_authority": prepare_authority,
            "proposals": snapshot, "operations": operations}
    plan["digest"] = digest(plan)
    bounded(plan, MAX_PLAN)
    return plan


def plan_check(plan):
    bounded(plan, MAX_PLAN)
    fields(plan, "version type source_sha256 provider prepare_authority proposals operations digest")
    need(type(plan["version"]) is int and plan["version"] in {1, 2, 3} and plan["type"] == "PublicationPlan", "unsupported_version")
    provider_check(plan["provider"], plan["version"])
    sha(plan["source_sha256"])
    sha(plan["digest"])
    need(plan["digest"] == digest({key: value for key, value in plan.items() if key != "digest"}), "plan_digest_mismatch")
    regenerated = (make_plan(plan["proposals"], plan["prepare_authority"]) if plan["version"] == 1
                   else make_multi_plan(plan["proposals"], plan["prepare_authority"], plan["provider"]) if plan["version"] == 3
                   else make_github_plan(plan["proposals"], plan["prepare_authority"], plan["provider"]))
    need(canonical(plan) == canonical(regenerated), "plan_or_source_changed")


def provider_check(identity, version=1):
    if version == 3:
        multi_binding_check(identity)
        return
    if version == 2:
        github_binding_check(identity)
        return
    bounded(identity, 1024)
    fields(identity, "name version")
    need(type(identity["version"]) is int and canonical(identity) == canonical(PROVIDER), "provider_unavailable")


def github_source_hash():
    # One fixed sibling, never a data-selected path or imported provider.
    return hashlib.sha256(Path(__file__).with_name("github_issues.py").read_bytes()).hexdigest()


def github_binding_check(identity):
    bounded(identity, 32768)
    https = type(identity) is dict and identity.get("version") == 2
    fields(identity, "name version profile source_sha256 client context policy" + (" https" if https else ""))
    need(identity["name"] == "github-issues" and type(identity["version"]) is int
         and ((identity["version"] == 1 and identity["profile"] == "unavailable-graphql-v1")
              or (identity["version"] == 2 and identity["profile"] == GITHUB_HTTPS_PROFILE)),
         "provider_unavailable")
    need(identity["source_sha256"] == github_source_hash(), "provider_source_changed")
    client_identity_check(identity["client"])
    if https:
        transport = identity["https"]
        fields(transport, "interpreter sources tls_version policy selection")
        client_identity_check(transport["interpreter"])
        fields(transport["sources"], "http_client ssl socket ssl_extension")
        for source in transport["sources"].values():
            fields(source, "path sha256")
            identity_path(source["path"])
            sha(source["sha256"])
        text(transport["tls_version"], 256)
        selection = transport["selection"]
        fields(selection, "kind entry source artifacts launcher cwd control")
        need(selection["kind"] == "fixed-code-v2", "invalid_client_identity")
        text(selection["entry"], 256)
        fields(selection["source"], "path sha256")
        identity_path(selection["source"]["path"])
        sha(selection["source"]["sha256"])
        need(type(selection["artifacts"]) is list and len(selection["artifacts"]) <= 8, "invalid_client_identity")
        for artifact in selection["artifacts"]:
            client_identity_check(artifact)
        client_identity_check(selection["launcher"])
        identity_path(selection["cwd"])
        need(selection["control"] in {"inherit", "telemetry-disabled"}, "invalid_client_identity")
        if selection["control"] == "inherit":
            need(selection["launcher"] == identity["client"], "invalid_client_identity")
        need(canonical(transport["policy"]) == canonical(GITHUB_HTTPS_POLICY), "unsupported_provider_policy")
    context = identity["context"]
    fields(context, "destination repository_node_id visibility issues_enabled archived permission")
    destination_check(context["destination"])
    token(context["repository_node_id"], r"[A-Za-z0-9_+=/-]{1,256}")
    need(context["visibility"] in {"PRIVATE", "PUBLIC"} and context["issues_enabled"] is True
         and context["archived"] is False and context["permission"] in {"ADMIN", "MAINTAIN", "WRITE"}, "unsupported_repository")
    if context["visibility"] == "PUBLIC":
        need(context["destination"]["audience"] == {"kind": "internet", "realm": "internet", "id": "public"}, "audience_mismatch")
    need(canonical(identity["policy"]) == canonical(GITHUB_POLICY), "unsupported_provider_policy")


def identity_path(value):
    text(value, 4096)
    need(Path(value).is_absolute() and ".." not in Path(value).parts, "invalid_client_identity")


def client_identity_check(identity):
    fields(identity, "executable resolved sha256 version")
    for key in ("executable", "resolved"):
        identity_path(identity[key])
    sha(identity["sha256"])
    text(identity["version"], 256)


def make_github_plan(proposals, prepare_authority, binding):
    """Pure v2 plan builder; the caller supplies a validated observation."""
    github_binding_check(binding)
    plan = make_plan(proposals, prepare_authority)
    need(binding["context"]["destination"] == plan["proposals"]["destination"], "destination_changed")
    github_body_check(plan["proposals"])
    plan.update(version=2, provider=json.loads(canonical(binding)))
    plan["digest"] = digest({key: value for key, value in plan.items() if key != "digest"})
    bounded(plan, MAX_PLAN)
    return plan


def github_body_check(proposals):
    for proposal in ordered(proposals):
        marker = "proposal-v1:" + proposals["batch_id"] + ":" + proposal["id"]
        text(body(proposal, marker, ["https://" + "x" * 512] * len(proposal["dependencies"])), GITHUB_BODY)


def multi_audience(value):
    fields(value, "kind realm id")
    token(value["realm"])
    token(value["id"])
    need(type(value["kind"]) is str and value["kind"] in {"author", "group", "internet"}, "unsupported_audience")
    if value["kind"] == "internet":
        need(value == {"kind": "internet", "realm": "internet", "id": "public"}, "unsupported_audience")


def multi_destination(value):
    fields(value, "realm account_id target_id audience")
    token(value["realm"], r"[a-z0-9]+(?:[.-][a-z0-9]+)*\.[a-z]{2,63}")
    text(value["realm"], 253)
    token(value["account_id"])
    token(value["target_id"])
    multi_audience(value["audience"])


def multi_binding_check(binding):
    bounded(binding, 16384)
    fields(binding, "name version source_sha256 context policy")
    need(binding["name"] == "multi-artifact" and type(binding["version"]) is int
         and binding["version"] == 1, "provider_unavailable")
    sha(binding["source_sha256"])
    need(canonical(binding["policy"]) == canonical(MULTI_POLICY), "unsupported_provider_policy")
    context = binding["context"]
    fields(context, "actor_id destinations existing_project capabilities")
    token(context["actor_id"])
    fields(context["destinations"], "project details issue tracker")
    audiences = []
    for destination in context["destinations"].values():
        multi_destination(destination)
        audience = destination["audience"]
        need(audience["kind"] != "author" or audience["id"] == context["actor_id"], "audience_identity_mismatch")
        audiences.append(canonical(audience))
    need(len(set(audiences)) == 1, "audience_mismatch")
    project = context["existing_project"]
    if project is not None:
        fields(project, "id owner_id audience")
        token(project["id"])
        need(project["owner_id"] == context["actor_id"]
             and canonical(project["audience"]) == audiences[0], "project_identity_mismatch")
    caps = context["capabilities"]
    fields(caps, "read find " + " ".join(MULTI_ROLES))
    need(all(type(value) is bool for value in caps.values()), "invalid_capability")
    need(caps["read"] and caps["find"], "capability_unavailable")


def multi_proposal_check(value, binding):
    bounded(value, MAX_INPUT)
    multi_binding_check(binding)
    fields(value, "version type batch_id audience project proposals")
    need(type(value["version"]) is int and value["version"] == 3
         and value["type"] == "ArtifactProposalSet", "unsupported_version")
    opaque(value["batch_id"])
    multi_audience(value["audience"])
    need(value["audience"] == binding["context"]["destinations"]["details"]["audience"], "audience_mismatch")
    project = value["project"]
    need(type(project) is dict, "invalid_project")
    if project.get("mode") == "existing":
        fields(project, "mode id")
        token(project["id"])
        observed = binding["context"]["existing_project"]
        need(observed is not None and project["id"] == observed["id"], "project_identity_mismatch")
    else:
        fields(project, "mode title")
        need(project["mode"] == "create" and binding["context"]["existing_project"] is None, "invalid_project")
        text(project["title"], 256)
        need("\n" not in project["title"] and "\t" not in project["title"], "invalid_title")
    sequence(value["proposals"], 20)
    need(bool(value["proposals"]), "empty_selection")
    ids = []
    for proposal in value["proposals"]:
        fields(proposal, "id title detail issue_body tracker_body dependencies")
        opaque(proposal["id"])
        ids.append(proposal["id"])
        text(proposal["title"], 256)
        need("\n" not in proposal["title"] and "\t" not in proposal["title"], "invalid_title")
        for name in ("detail", "issue_body", "tracker_body"):
            text(proposal[name], MULTI_BODY)
        sequence(proposal["dependencies"], 19)
        for item in proposal["dependencies"]:
            opaque(item)
        need(len(set(proposal["dependencies"])) == len(proposal["dependencies"]), "duplicate_dependency")
    need(len(ids) == len(set(ids)), "duplicate_proposal")
    for proposal in value["proposals"]:
        need(set(proposal["dependencies"]) <= set(ids) and proposal["id"] not in proposal["dependencies"], "invalid_dependency")
    return ordered(value)


def multi_references(value):
    if type(value) is dict:
        if set(value) == {"ref", "field"}:
            return [(value["ref"], value["field"])]
        return [ref for child in value.values() for ref in multi_references(child)]
    if type(value) is list:
        return [ref for child in value for ref in multi_references(child)]
    return []


def make_multi_plan(proposals, prepare_authority, binding):
    """Compile a fixed seven-role v3 plan; no provider construction or discovery."""
    selected = multi_proposal_check(proposals, binding)
    text(prepare_authority, 512)
    context, operations = binding["context"], []
    ref = lambda identifier, field="url": {"ref": identifier, "field": field}
    literal = lambda value: {"text": value}

    def add(identifier, kind, proposal_id, data):
        need(context["capabilities"][kind], "capability_unavailable")
        earlier = {op["id"]: op for op in operations}
        references = multi_references(data)
        need(all(identifier in earlier and field in {"id", "url"} for identifier, field in references), "invalid_reference")
        need(all(field != "url" or earlier[identifier]["kind"] in {"details.create", "issue.create", "tracker.create"}
                 for identifier, field in references), "invalid_reference")
        marker = "proposal-v3:" + proposals["batch_id"] + ":" + (proposal_id or "project") + ":" + kind
        if "body" in data:
            data["body"].append(literal("\n\nTracking only; no implementation authority.\n<!-- " + marker + " -->\n"))
            maximum = "".join(part["text"] if "text" in part else "x" * 512 for part in data["body"])
            text(maximum, GITHUB_BODY if kind.startswith("issue.") else MULTI_BODY)
            need(len(canonical(maximum)) <= (GITHUB_BODY if kind.startswith("issue.") else MULTI_BODY), "body_too_large")
        operations.append({"id": identifier, "kind": kind, "proposal_id": proposal_id, "marker": marker,
                           "destination": context["destinations"][MULTI_ROLES[kind]], "audience": proposals["audience"],
                           "requires": [name for name in earlier if name in {r[0] for r in references}], "data": data})

    project = proposals["project"]
    if project["mode"] == "create":
        project_id = "project-" + proposals["batch_id"]
        add(project_id, "project.create", None, {"title": project["title"], "owner_id": context["actor_id"]})
        project_id = ref(project_id, "id")
    else:
        project_id = project["id"]
    for proposal in selected:
        pid = proposal["id"]
        details, issue, tracker = "details-" + pid, "issue-" + pid, "tracker-" + pid
        add(details, "details.create", pid, {"title": proposal["title"], "body": [literal(proposal["detail"])], "owner_id": context["actor_id"]})
        add(issue, "issue.create", pid, {"title": proposal["title"], "body": [literal(proposal["issue_body"] + "\n\nDetails: "), ref(details)]})
        add(tracker, "tracker.create", pid, {"title": proposal["title"], "body": [literal(proposal["tracker_body"] + "\n\nIssue: "), ref(issue), literal("\nDetails: "), ref(details)],
                                             "owner_id": context["actor_id"], "project_id": project_id, "external_url": ref(issue)})
        add("details-link-" + pid, "details.comment.create", pid, {"parent_id": ref(details, "id"), "body": [literal("Issue: "), ref(issue), literal("\nTracker: "), ref(tracker)]})
        add("issue-link-" + pid, "issue.comment.create", pid, {"parent_id": ref(issue, "id"), "body": [literal("Details: "), ref(details), literal("\nTracker: "), ref(tracker)]})
        if proposal["dependencies"]:
            add("tracker-deps-" + pid, "tracker.dependencies.add", pid,
                {"target_id": ref(tracker, "id"), "dependencies": [ref("tracker-" + dep, "id") for dep in proposal["dependencies"]], "mode": "additive"})
    need(len(operations) <= MULTI_OPERATIONS, "operation_limit")
    plan = {"version": 3, "type": "PublicationPlan", "source_sha256": source_hash(), "provider": binding,
            "prepare_authority": prepare_authority, "proposals": proposals, "operations": operations}
    plan = json.loads(canonical(bounded(plan, MAX_PLAN)))
    plan["digest"] = digest(plan)
    bounded(plan, MAX_PLAN)
    multi_capacity(plan)
    return plan


def multi_request(plan, receipt, operation):
    rows = {row["id"]: row for row in receipt["operations"]}
    references = multi_references(operation["data"])
    need(set(operation["requires"]) == {name for name, field in references}, "invalid_reference")
    for required in operation["requires"]:
        need(rows[required]["state"] == "verified", "dependency_unverified")

    def resolve(value):
        if type(value) is dict:
            if set(value) == {"ref", "field"}:
                result = rows[value["ref"]]["artifact"][value["field"]]
                need(type(result) is str, "invalid_reference")
                return result
            return {key: resolve(child) for key, child in value.items()}
        if type(value) is list:
            return [resolve(child) for child in value]
        return value

    data = resolve(operation["data"])
    if "body" in data:
        data["body"] = "".join(part["text"] if type(part) is dict else part for part in data["body"])
    return json.loads(canonical({"operation_id": operation["id"], "kind": operation["kind"], "marker": operation["marker"],
                                "destination": operation["destination"], "audience": operation["audience"], "data": data}))


def multi_capacity(plan):
    # Reserve every result, all bounded history and authority rows before any
    # store/provider effect. URLs and opaque IDs use their maximum slot sizes.
    receipt = {"operations": []}
    reserved = MAX_HISTORY * 512 + MAX_AUTHORITIES * 1024 + 16384
    for operation in plan["operations"]:
        request = multi_request(plan, receipt, operation)
        realm = operation["destination"]["realm"]
        url = "https://" + realm + "/"
        url += "x" * (512 - len(url))
        artifact = {**request, "id": "x" * 64, "url": url}
        if operation["kind"] == "tracker.dependencies.add":
            artifact["data"]["dependencies"] = ["x" * 64] * 100
        reserved += len(canonical(artifact)) + 512
        receipt["operations"].append({"id": operation["id"], "state": "verified", "artifact": artifact})
    need(reserved <= MAX_RECEIPT, "receipt_capacity")


def multi_artifact_check(value, plan, request):
    bounded(value, MAX_BODY + 8192)
    fields(value, "id url operation_id kind marker destination audience data")
    token(value["id"])
    token(value["operation_id"], r"[a-z-]+-[0-9a-f]{32}")
    operation = next((op for op in plan["operations"] if op["id"] == value["operation_id"]), None)
    need(operation is not None, "artifact_identity_mismatch")
    for key in ("kind", "marker", "destination", "audience"):
        need(value[key] == operation[key], "artifact_identity_mismatch")
    kind, data = value["kind"], value["data"]
    fields(data, {"project.create": "title owner_id", "details.create": "title body owner_id",
                  "issue.create": "title body", "tracker.create": "title body owner_id project_id external_url",
                  "details.comment.create": "parent_id body", "issue.comment.create": "parent_id body",
                  "tracker.dependencies.add": "target_id dependencies mode"}[kind])
    for name in ("owner_id", "parent_id", "project_id", "target_id"):
        if name in data:
            token(data[name])
    if "title" in data:
        text(data["title"], 256)
        need("\n" not in data["title"] and "\t" not in data["title"], "invalid_title")
    if "body" in data:
        text(data["body"], GITHUB_BODY if kind.startswith("issue.") else MULTI_BODY)
        need("<!-- " + value["marker"] + " -->" in data["body"], "artifact_content_mismatch")
    if "external_url" in data:
        multi_url(data["external_url"], plan["provider"]["context"]["destinations"]["issue"]["realm"])
    if kind in {"details.create", "issue.create", "tracker.create"}:
        multi_url(value["url"], value["destination"]["realm"])
    else:
        need(value["url"] is None, "artifact_url_mismatch")
    if kind == "tracker.dependencies.add":
        need(data["mode"] == "additive" and value["id"] == data["target_id"], "artifact_identity_mismatch")
        sequence(data["dependencies"], 100)
        for item in data["dependencies"]:
            token(item)
        need(len(set(data["dependencies"])) == len(data["dependencies"])
             and data["target_id"] not in data["dependencies"], "invalid_dependency")
    if request is not None:
        for key in ("kind", "operation_id", "marker", "destination", "audience"):
            need(value[key] == request[key], "artifact_content_mismatch")
        if kind == "tracker.dependencies.add":
            need(set(request["data"]["dependencies"]) <= set(data["dependencies"])
                 and data["target_id"] == request["data"]["target_id"], "artifact_content_mismatch")
        else:
            need(canonical(data) == canonical(request["data"]), "artifact_content_mismatch")


def multi_url(value, realm):
    text(value, 512)
    need(value.startswith("https://" + realm + "/")
         and not re.search(r"[\s\\#\x00-\x1f\x7f]", value), "artifact_url_mismatch")


def same_artifact(actual, expected, plan):
    if plan["version"] != 3 or actual["kind"] != "tracker.dependencies.add":
        return actual == expected
    # Unrelated edges may concurrently grow; never replace or discard them.
    return all(actual[key] == expected[key] for key in actual if key != "data") and all(
        actual["data"][key] == expected["data"][key] for key in actual["data"] if key != "dependencies")


def provider_read(adapter, request, artifact, plan):
    if plan["version"] == 3:
        identifier = artifact["id"] if artifact is not None else request["data"]["target_id"]
        return adapter.read(json.loads(canonical(request)), identifier)
    return adapter.read(json.loads(canonical(artifact)))


def artifact_check(value, plan, request=None):
    if plan["version"] == 3:
        return multi_artifact_check(value, plan, request)
    bounded(value, MAX_BODY + 8192)
    fields(value, "id number url marker title body destination")
    number(value["id"])
    number(value["number"])
    destination_check(value["destination"])
    need(value["destination"] == plan["proposals"]["destination"], "artifact_destination_mismatch")
    dest = value["destination"]
    need(value["url"] == "https://" + dest["host"] + "/" + dest["owner"] + "/" + dest["repository"] + "/issues/" + value["number"], "artifact_url_mismatch")
    text(value["title"], 256)
    text(value["body"], MAX_BODY)
    token(value["marker"], r"proposal-v1:[0-9a-f]{32}:[0-9a-f]{32}")
    if request is not None:
        for name in ("marker", "title", "body", "destination"):
            need(value[name] == request[name], "artifact_content_mismatch")


def initial_receipt(plan):
    receipt = {"version": plan["version"], "type": "PublicationReceipt", "plan_digest": plan["digest"],
            "authorities": [], "history": [], "operations": [
                {"id": op["id"], "state": "planned", "attempt": 0, "artifact": None}
                for op in plan["operations"]]}
    if plan["version"] in {2, 3}:
        receipt["policy"] = json.loads(canonical(plan["provider"]["policy"]))
    return receipt


def request_for(plan, receipt, operation):
    if plan["version"] == 3:
        return multi_request(plan, receipt, operation)
    proposal = next(p for p in plan["proposals"]["proposals"] if p["id"] == operation["proposal_id"])
    rows = {row["id"]: row for row in receipt["operations"]}
    links = []
    for required in operation["requires"]:
        need(rows[required]["state"] == "verified", "dependency_unverified")
        links.append(rows[required]["artifact"]["url"])
    rendered = body(proposal, operation["marker"], links)
    if plan["version"] == 2:
        text(rendered, GITHUB_BODY)
    return {"kind": "issue.create", "destination": json.loads(canonical(plan["proposals"]["destination"])),
            "marker": operation["marker"], "title": proposal["title"],
            "body": rendered}


def receipt_check(receipt, plan):
    bounded(receipt, MAX_RECEIPT)
    v2 = plan["version"] in {2, 3}
    fields(receipt, "version type plan_digest authorities operations history" + (" policy" if v2 else ""))
    need(type(receipt["version"]) is int and receipt["version"] == plan["version"]
         and receipt["type"] == "PublicationReceipt", "unsupported_version")
    if v2:
        need(canonical(receipt["policy"]) == canonical(plan["provider"]["policy"]), "receipt_policy_mismatch")
    need(receipt["plan_digest"] == plan["digest"], "receipt_plan_mismatch")
    sequence(receipt["authorities"], MAX_AUTHORITIES)
    for authority in receipt["authorities"]:
        fields(authority, "scope reference plan_digest")
        need(authority["scope"] in {"filing", "reconciliation"} and authority["plan_digest"] == plan["digest"], "invalid_authority")
        text(authority["reference"], 512)
    sequence(receipt["operations"], MULTI_OPERATIONS if plan["version"] == 3 else MAX_PROPOSALS)
    need([row.get("id") for row in receipt["operations"] if type(row) is dict] == [op["id"] for op in plan["operations"]], "invalid_operations")
    for row, op in zip(receipt["operations"], plan["operations"]):
        fields(row, "id state attempt artifact")
        need(type(row["state"]) is str and row["state"] in STATES, "invalid_state")
        need(type(row["attempt"]) is int and row["attempt"] in {0, 1}, "invalid_attempt")
        need((row["state"] == "planned") == (row["attempt"] == 0), "invalid_attempt")
        if row["artifact"] is not None:
            artifact_check(row["artifact"], plan, request_for(plan, receipt, op))
        need(row["state"] not in {"created-unverified", "verified"} or row["artifact"] is not None, "missing_artifact")
        need(row["state"] != "planned" or row["artifact"] is None, "unexpected_artifact")
    sequence(receipt["history"], MAX_HISTORY)
    ids = {op["id"] for op in plan["operations"]}
    replay = {op: "planned" for op in ids}
    artifact_ids = {op: None for op in ids}
    transitions = {"intent": ({"planned"}, "attempting"),
                   "result": ({"attempting"}, "created-unverified"),
                   "verified": ({"created-unverified"}, "verified"),
                   "uncertain": ({"attempting", "created-unverified", "uncertain"}, "uncertain"),
                   "reconciled": ({"attempting", "created-unverified", "uncertain"}, "verified"),
                   "blocked": ({"planned"}, "blocked")}
    if v2:
        transitions["preflight-paused"] = ({"planned"}, "planned")
    for index, event in enumerate(receipt["history"]):
        fields(event, "sequence operation event artifact_id" + (" reason" if v2 else ""))
        need(type(event["sequence"]) is int and event["sequence"] == index + 1 and event["operation"] in ids
             and event["event"] in transitions, "invalid_history")
        if v2:
            need(event["reason"] == ("preflight_unavailable" if event["event"] == "preflight-paused" else None), "invalid_history_reason")
        if event["artifact_id"] is not None:
            (token if plan["version"] == 3 else number)(event["artifact_id"])
        before, after = transitions[event["event"]]
        need(replay[event["operation"]] in before, "invalid_history_transition")
        if event["event"] in {"result", "verified", "reconciled"}:
            need(event["artifact_id"] is not None, "missing_history_artifact")
        if artifact_ids[event["operation"]] is not None:
            need(event["artifact_id"] == artifact_ids[event["operation"]], "history_artifact_changed")
        artifact_ids[event["operation"]] = event["artifact_id"]
        replay[event["operation"]] = after
    seen_ids, seen_urls = set(), set()
    for row in receipt["operations"]:
        need(row["state"] == replay[row["id"]], "history_state_mismatch")
        need((row["artifact"]["id"] if row["artifact"] else None) == artifact_ids[row["id"]], "history_mapping_mismatch")
        if row["artifact"]:
            artifact = row["artifact"]
            identity = ((canonical(artifact["destination"]), artifact["kind"], artifact["id"])
                        if plan["version"] == 3 else artifact["id"])
            url = artifact["url"]
            need(identity not in seen_ids and (url is None and plan["version"] == 3 or url not in seen_urls), "duplicate_artifact")
            seen_ids.add(identity)
            if url is not None:
                seen_urls.add(url)
    scopes = {entry["scope"] for entry in receipt["authorities"]}
    need(not receipt["history"] or "filing" in scopes, "missing_filing_authority")
    need(not any(e["event"] in {"uncertain", "reconciled"} for e in receipt["history"])
         or "reconciliation" in scopes, "missing_reconciliation_authority")


def checked_path(value):
    path = Path(value)
    need(".." not in path.parts, "parent_traversal")
    path = Path(os.path.abspath(path))
    for part in reversed((path, *path.parents)):
        need(not part.is_symlink(), "symlink_path")
        if part != path:
            need(part.is_dir(), "invalid_parent")
    return path


def private_directory(path):
    info = path.lstat()
    need(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and info.st_mode & 0o077 == 0, "unsafe_directory")


@contextlib.contextmanager
def regular(path, limit):
    path = checked_path(path)
    info = path.lstat()
    need(stat.S_ISREG(info.st_mode), "invalid_file")
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW), "rb") as stream:
        info = os.fstat(stream.fileno())
        need(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
             and info.st_mode & 0o077 == 0 and info.st_nlink == 1, "unsafe_file")
        need(info.st_size <= limit, "data_too_large")
        yield stream


def read_json(path, limit):
    with regular(path, limit) as stream:
        return parse(stream.read(limit + 1), limit)


def fsync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_new(path, value, limit):
    data = canonical(bounded(value, limit)) + b"\n"
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "wb") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    fsync_directory(path.parent)


def checkpoint(store, receipt, plan):
    receipt_check(receipt, plan)
    staged = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=".checkpoint-", dir=store)
        staged = Path(name)
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(canonical(receipt) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        with regular(store / "receipt.json", MAX_RECEIPT):
            pass
        os.replace(staged, store / "receipt.json")
        fsync_directory(store)
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)  # Only this invocation's exclusive file.


def unix_only():
    need(os.name == "posix" and fcntl is not None and hasattr(os, "O_NOFOLLOW"), "unsupported_platform")


@contextlib.contextmanager
def locked(store, writing=False):
    unix_only()
    store = checked_path(store)
    private_directory(store)
    with regular(store / "lock", 0) as lock:
        try:
            fcntl.flock(lock, (fcntl.LOCK_EX if writing else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Stop("store_busy") from None
        names = set()
        for index, path in enumerate(store.iterdir()):
            need(index < len(FILES), "incomplete_or_extra_store_files")
            names.add(path.name)
        need(names == FILES, "incomplete_or_extra_store_files")
        proposals = read_json(store / "proposals.json", MAX_INPUT)
        plan = read_json(store / "plan.json", MAX_PLAN)
        plan_check(plan)
        need(canonical(proposals) == canonical(plan["proposals"]), "proposal_plan_mismatch")
        receipt = read_json(store / "receipt.json", MAX_RECEIPT)
        receipt_check(receipt, plan)
        yield store, plan, receipt


def new_store_path(store):
    store = checked_path(store)
    private_directory(store.parent)
    need(not store.exists(), "store_exists")
    need(not any((part / ".git").exists() or (part / ".git").is_symlink() for part in store.parents), "store_inside_repository")
    return store


def prepare(store, proposals, prepare_authority, *, binding=None):
    unix_only()
    # An explicit in-process binding only; no CLI option accepts it.
    plan = (make_multi_plan(proposals, prepare_authority, binding)
            if type(proposals) is dict and proposals.get("version") == 3 else
            make_plan(proposals, prepare_authority) if binding is None
            else make_github_plan(proposals, prepare_authority, binding))
    store = new_store_path(store)
    store.mkdir(mode=0o700)
    store.chmod(0o700)
    # Persist the parent entry before any complete store can be accepted.
    fsync_directory(store.parent)
    # Preserve incomplete owned initialization on failure; never reuse/repair it.
    with os.fdopen(os.open(store / "lock", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "wb") as lock:
        os.fchmod(lock.fileno(), 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock.flush()
        os.fsync(lock.fileno())
        write_new(store / "proposals.json", plan["proposals"], MAX_INPUT)
        write_new(store / "plan.json", plan, MAX_PLAN)
        write_new(store / "receipt.json", initial_receipt(plan), MAX_RECEIPT)
    return {"plan_digest": plan["digest"], "operations": len(plan["operations"]), "provider_available": False}


def summary(plan, receipt):
    counts = {name: sum(row["state"] == name for row in receipt["operations"]) for name in sorted(STATES)}
    result = {"plan_digest": plan["digest"], "complete": counts["verified"] == len(plan["operations"]),
            "counts": counts, "provider_available": False,
            "operations": [{"id": row["id"], "state": row["state"],
                            "artifact": None if row["artifact"] is None else {
                                name: row["artifact"][name] for name in (("id", "kind", "url") if plan["version"] == 3 else ("id", "number", "url"))}}
                           for row in receipt["operations"]]}
    if plan["version"] in {2, 3}:
        result["policy"] = json.loads(canonical(receipt["policy"]))
    return result


def status(store):
    with locked(store) as (_, plan, receipt):
        return summary(plan, receipt)


def authorize(receipt, plan, expected, reference, scope):
    need(expected == plan["digest"], "approval_mismatch")
    text(reference, 512)
    authority = {"scope": scope, "reference": reference, "plan_digest": expected}
    if authority not in receipt["authorities"]:
        need(len(receipt["authorities"]) < MAX_AUTHORITIES, "authority_limit")
        receipt["authorities"].append(authority)


def adapter_context(adapter, plan):
    # Fixed provider or trusted fixture, never an input-selected module/executable.
    need(adapter is not None, "provider_unavailable")
    provider_check(adapter.identity, plan["version"])
    if plan["version"] in {2, 3}:
        need(canonical(adapter.identity) == canonical(plan["provider"]), "provider_changed")
    observed = adapter.read_context()
    bounded(observed, 16384 if plan["version"] in {2, 3} else 8192)
    if plan["version"] in {2, 3}:
        need(canonical(observed) == canonical(plan["provider"]["context"]), "destination_changed")
        return
    destination_check(observed)
    need(observed == plan["proposals"]["destination"], "destination_changed")


def event(receipt, row, name):
    need(len(receipt["history"]) < MAX_HISTORY, "history_limit")
    receipt["history"].append({"sequence": len(receipt["history"]) + 1,
                               "operation": row["id"], "event": name,
                               "artifact_id": row["artifact"]["id"] if row["artifact"] else None})
    if receipt["version"] in {2, 3}:
        receipt["history"][-1]["reason"] = "preflight_unavailable" if name == "preflight-paused" else None


def discovery(adapter, request, plan, *, preflight=False):
    if plan["version"] == 3 and request["kind"] == "tracker.dependencies.add":
        need(preflight, "invalid_discovery_operation")
        # The target comes from a verified predecessor. An additive operation
        # is not a create: ambiguous results are reconciled by direct read.
        return []
    found = adapter.find(json.loads(canonical(request)))
    bounded(found, MAX_RECEIPT)
    if plan["version"] in {2, 3}:
        fields(found, "traversal_complete consistency matches")
        need(found["traversal_complete"] is True and found["consistency"] == "not-guaranteed", "discovery_incomplete")
    else:
        fields(found, "complete consistent matches")
        need(found["complete"] is True and found["consistent"] is True, "discovery_incomplete")
    sequence(found["matches"], 2)
    for artifact in found["matches"]:
        artifact_check(artifact, plan, None if preflight and plan["version"] in {2, 3} else request)
        if preflight and plan["version"] == 2:
            need(artifact["marker"] == request["marker"]
                 and "<!-- " + request["marker"] + " -->" in artifact["body"], "artifact_content_mismatch")
        if preflight and plan["version"] == 3:
            need(artifact["operation_id"] == request["operation_id"]
                 and artifact["marker"] == request["marker"], "artifact_identity_mismatch")
    need(len(found["matches"]) <= 1 or preflight and plan["version"] in {2, 3}, "duplicate_matches")
    return found["matches"]


def apply(store, expected, authority, adapter=None):
    """Closed publication engine; the caller owns provider construction.

    One attempt per operation. Never recreate any attempted/blocked operation.
    Exceptions after durable intent leave that intent intact for reconciliation.
    """
    with locked(store, writing=True) as (store, plan, receipt):
        authorize(receipt, plan, expected, authority, "filing")
        need(not any(row["state"] == "blocked" for row in receipt["operations"]), "blocked_plan")
        need(all(row["state"] in {"planned", "verified"} for row in receipt["operations"]), "reconciliation_required")
        need(len(receipt["history"]) + 4 * sum(row["state"] == "planned" for row in receipt["operations"]) <= MAX_HISTORY, "history_limit")
        need(adapter is not None, "provider_unavailable")
        for operation, row in zip(plan["operations"], receipt["operations"]):
            if row["state"] == "verified":
                continue
            need(len(canonical(receipt)) + MAX_BODY + 12288 <= MAX_RECEIPT, "receipt_capacity")
            request = request_for(plan, receipt, operation)
            conflict = False
            try:
                adapter_context(adapter, plan)
                conflict = bool(discovery(adapter, request, plan, preflight=plan["version"] in {2, 3}))
                need(not conflict, "adoption_required")
            except Exception:
                if plan["version"] in {2, 3} and not conflict:
                    event(receipt, row, "preflight-paused")
                    checkpoint(store, receipt, plan)
                    raise Stop("preflight_paused") from None
                row.update(state="blocked", attempt=1)
                event(receipt, row, "blocked")
                checkpoint(store, receipt, plan)
                raise Stop("preflight_blocked") from None
            row.update(state="attempting", attempt=1)
            event(receipt, row, "intent")
            checkpoint(store, receipt, plan)
            try:
                result = adapter.create(json.loads(canonical(request)))
                artifact_check(result, plan, request)
                row.update(state="created-unverified", artifact=json.loads(canonical(result)))
                event(receipt, row, "result")
                # Returned result and its exact ID mapping become durable together.
                checkpoint(store, receipt, plan)
                adapter_context(adapter, plan)
                actual = provider_read(adapter, request, row["artifact"], plan)
                artifact_check(actual, plan, request)
                need(same_artifact(actual, row["artifact"], plan), "artifact_changed")
                if plan["version"] == 3:
                    row["artifact"] = json.loads(canonical(actual))
                row["state"] = "verified"
                event(receipt, row, "verified")
                checkpoint(store, receipt, plan)
            except Exception:
                # Do not replace a potentially failed checkpoint with a second
                # write. The last durable intent/result is the recovery evidence.
                raise Stop("write_or_verification_uncertain") from None
        return summary(plan, receipt)


def reconcile(store, expected, authority, adapter=None):
    """Backend-read-only, receipt-writing reconciliation; never creates anything."""
    with locked(store, writing=True) as (store, plan, receipt):
        authorize(receipt, plan, expected, authority, "reconciliation")
        need(adapter is not None, "provider_unavailable")
        need(len(receipt["history"]) + len(plan["operations"]) <= MAX_HISTORY, "history_limit")
        for operation, row in zip(plan["operations"], receipt["operations"]):
            if row["state"] in {"planned", "verified", "blocked"}:
                continue
            unresolved = False
            try:
                request = request_for(plan, receipt, operation)
                adapter_context(adapter, plan)
                if row["artifact"] is not None or plan["version"] == 3 and operation["kind"] == "tracker.dependencies.add":
                    actual = provider_read(adapter, request, row["artifact"], plan)
                    artifact_check(actual, plan, request)
                    need(row["artifact"] is None or same_artifact(actual, row["artifact"], plan), "artifact_changed")
                else:
                    matches = discovery(adapter, request, plan)
                    need(len(matches) == 1, "absence_not_proven")
                    actual = matches[0]
                    if plan["version"] in {2, 3}:
                        confirmed = provider_read(adapter, request, actual, plan)
                        artifact_check(confirmed, plan, request)
                        need(confirmed == actual, "artifact_changed")
                        actual = confirmed
                # A match verifies required content, not authorship by our attempt.
                row.update(state="verified", artifact=json.loads(canonical(actual)))
                event(receipt, row, "reconciled")
            except Exception:
                row["state"] = "uncertain"
                event(receipt, row, "uncertain")
                unresolved = True
            checkpoint(store, receipt, plan)
            if unresolved:
                raise Stop("reconciliation_unresolved") from None
        return summary(plan, receipt)


class _PendingMultiAdapter:
    """Trusted in-process factory only; no plan field can select its source.

    The factory supplies one static absolute source Path and open(expected).
    Its qualified source closure and backend restrictions are the integration's
    responsibility. This core supplies neither a live factory nor a CLI route.
    """

    def __init__(self, factory, binding):
        self.factory = factory
        self.binding = json.loads(canonical(binding))
        self.instance = None

    def _get(self):
        path = checked_path(self.factory.source)
        before = path.lstat()
        need(stat.S_ISREG(before.st_mode) and before.st_uid in {0, os.getuid()}
             and not before.st_mode & 0o022 and before.st_size <= MAX_INPUT, "provider_source_changed")
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
            opened = os.fstat(stream.fileno())
            need((before.st_dev, before.st_ino, before.st_mode, before.st_size) ==
                 (opened.st_dev, opened.st_ino, opened.st_mode, opened.st_size), "provider_source_changed")
            raw = stream.read(MAX_INPUT + 1)
        after = path.lstat()
        need(all(getattr(before, key) == getattr(after, key) for key in
                 ("st_dev", "st_ino", "st_uid", "st_mode", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns"))
             and len(raw) <= MAX_INPUT
             and hashlib.sha256(raw).hexdigest() == self.binding["source_sha256"], "provider_source_changed")
        if self.instance is None:
            self.instance = self.factory.open(json.loads(canonical(self.binding)))
        return self.instance

    @property
    def identity(self):
        return self._get().identity

    def read_context(self):
        return self._get().read_context()

    def find(self, request):
        return self._get().find(request)

    def create(self, request):
        return self._get().create(request)

    def read(self, request, identifier):
        return self._get().read(request, identifier)


def multi_execute(store, expected, authority, factory, *, scope):
    """Static caller-owned integration; construction follows locked preflight."""
    need(type(scope) is str and scope in {"filing", "reconciliation"}, "invalid_authority")
    with locked(store) as (_, plan, receipt):
        need(plan["version"] == 3, "unsupported_version")
        authorize(receipt, plan, expected, authority, scope)
        adapter = _PendingMultiAdapter(factory, plan["provider"])
    return (apply if scope == "filing" else reconcile)(store, expected, authority, adapter)


def _github_provider():
    # One fixed sibling and its existing source admission resolver, not a
    # provider registry or a data-selected path. No credentials or API reads.
    path = Path(__file__).with_name("github_issues.py")
    try:
        spec = importlib.util.spec_from_file_location("envy_fixed_github", path)
        provider = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(provider)
        provider._resolve_https_client()
        return provider
    except Exception:
        raise Stop("github_compatibility_unavailable") from None


def _github_plan(provider, store, proposals, prepare_authority, confirmed_host):
    unix_only()
    preview = make_plan(proposals, prepare_authority)
    github_body_check(preview["proposals"])
    text(confirmed_host, 253)
    need(confirmed_host == preview["proposals"]["destination"]["host"], "host_confirmation_mismatch")
    store = new_store_path(store)
    # Only now can adapter construction obtain a credential or read a backend.
    try:
        adapter = provider.HttpsGitHubIssues(preview["proposals"]["destination"])
    except Exception:
        raise Stop("github_preparation_unavailable") from None
    github_binding_check(adapter.identity)
    need(adapter.identity["version"] == 2, "provider_unavailable")
    result = prepare(store, preview["proposals"], prepare_authority, binding=adapter.identity)
    result["provider_available"] = True  # Source-admitted path, not proof of create permission.
    return result


class _PendingGithubAdapter:
    """Construct once, inside the core's lock and after its full preflight.

    The initial read-lock snapshot is validated again by apply/reconcile before
    this object is used. Digest/source/state changes between locks cannot cause
    a backend read with stale authority. No caller data selects provider code.
    """

    def __init__(self, provider, binding):
        self.provider = provider
        self.binding = json.loads(canonical(binding))
        self.instance = None

    def _get(self):
        if self.instance is None:
            self.instance = self.provider.HttpsGitHubIssues(self.binding["context"]["destination"], expected=self.binding)
        return self.instance

    @property
    def identity(self):
        return self._get().identity

    def read_context(self):
        return self._get().read_context()

    def find(self, request):
        return self._get().find(request)

    def create(self, request):
        return self._get().create(request)

    def read(self, artifact):
        return self._get().read(artifact)


def _github_execute(provider, store, expected, authority, scope):
    with locked(store) as (_, plan, receipt):
        need(plan["version"] == 2 and plan["provider"]["version"] == 2
             and plan["provider"]["profile"] == GITHUB_HTTPS_PROFILE, "provider_unavailable")
        authorize(receipt, plan, expected, authority, scope)
        adapter = _PendingGithubAdapter(provider, plan["provider"])
    # These existing operations re-lock, validate exact authority/source/receipt,
    # and check state/capacity before accessing the lazy adapter's identity.
    action = apply if scope == "filing" else reconcile
    result = action(store, expected, authority, adapter)
    result["provider_available"] = True
    return result


def github_plan(*args, **kwargs):
    return _github_plan(_github_provider(), *args, **kwargs)


def github_apply(*args, **kwargs):
    return _github_execute(_github_provider(), *args, scope="filing", **kwargs)


def github_reconcile(*args, **kwargs):
    return _github_execute(_github_provider(), *args, scope="reconciliation", **kwargs)


def main(argv=None):
    # Reject unsupported source profiles before touching input/store paths.
    # Admission never skips local input, store or exact-authority preflight.
    raw_args = sys.argv[1:] if argv is None else argv
    if raw_args and raw_args[0] in {"github-plan", "github-apply", "github-reconcile"}:
        try:
            _github_provider()
        except Stop:
            print(json.dumps({"error": "github_compatibility_unavailable"}), file=sys.stderr)
            return 1
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="Read private ProposalSet; no store or provider")
    validate.add_argument("--input", type=Path, required=True)
    create = commands.add_parser("prepare", help="Create private offline plan/store after a prepare request")
    create.add_argument("--input", type=Path, required=True)
    create.add_argument("--store", type=Path, required=True)
    create.add_argument("--prepare-authority", required=True)
    inspect = commands.add_parser("status", help="Read existing private store; no reconciliation/provider")
    inspect.add_argument("--store", type=Path, required=True)
    github_prepare = commands.add_parser("github-plan", help="Source-gated fixed HTTPS preview")
    github_prepare.add_argument("--input", type=Path, required=True)
    github_prepare.add_argument("--store", type=Path, required=True)
    github_prepare.add_argument("--prepare-authority", required=True)
    github_prepare.add_argument("--confirm-host", required=True)
    for command, authority in (("github-apply", "filing"), ("github-reconcile", "reconciliation")):
        execute = commands.add_parser(command, help="Source-gated fixed HTTPS operation")
        execute.add_argument("--store", type=Path, required=True)
        execute.add_argument("--plan-digest", required=True)
        execute.add_argument("--" + authority + "-authority", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "status":
            result = status(args.store)
        elif args.command == "github-apply":
            result = github_apply(args.store, args.plan_digest, args.filing_authority)
        elif args.command == "github-reconcile":
            result = github_reconcile(args.store, args.plan_digest, args.reconciliation_authority)
        else:
            unix_only()
            proposals = read_json(args.input, MAX_INPUT)
            proposal_check(proposals)
            if args.command == "github-plan":
                result = github_plan(args.store, proposals, args.prepare_authority, args.confirm_host)
            else:
                result = ({"valid": True, "proposals": len(proposals["proposals"]), "provider_available": False}
                          if args.command == "validate" else prepare(args.store, proposals, args.prepare_authority))
        print(json.dumps(result, sort_keys=True))
        return 0
    except (Stop, OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError) as error:
        print(json.dumps({"error": str(error) if type(error) is Stop else "invalid_input_or_store"}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
