#!/usr/bin/env python3
"""Offline, commit-bound review records. Never launches reviewers or publishes."""

import argparse
import contextlib
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import uuid

try:
    import fcntl
except ImportError:
    fcntl = None


MAX_FILE = 8 * 1024 * 1024
MAX_LEDGER = 32 * 1024 * 1024
ROUTING = {"GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
           "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE"}
BASES = {"harness_metadata", "operator_attestation", "self_report", "requested_only", "unknown"}
TERMINAL = {"pass", "changes-required", "failed", "cancelled", "interrupted"}
PIN_LIMIT = "Gitlink pins only: nested objects, content, dirty state, compatibility, and code review are not established."


class Stop(Exception):
    """Static, non-source-bearing diagnostic code."""


def need(condition, code):
    if not condition:
        raise Stop(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def fields(value, names):
    need(type(value) is dict and set(value) == set(names.split()), "invalid_schema")


def string(value, limit=4096, empty=False):
    need(type(value) is str and len(value) <= limit and (empty or bool(value)), "invalid_string")
    need(not any(ord(c) < 32 and c not in "\n\t" for c in value), "invalid_string")
    # Defense in depth only. An operator must audit all inputs before capture.
    need(not re.search(r"-----BEGIN (?:[A-Z ]* )?PRIVATE KEY-----|\b(?:gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,})", value), "sensitive_input")
    need(not re.search(r"https?://[^\s/]*@", value), "sensitive_input")
    return value


def identifier(value):
    string(value, 128)
    need(re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]*", value), "invalid_identifier")
    return value


def hash_value(value):
    need(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value), "invalid_digest")


def oid(value):
    need(type(value) is str and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value), "invalid_commit")


def sequence(value, limit=100):
    need(type(value) is list and len(value) <= limit, "invalid_list")
    return value


def unique(items):
    need(len(items) == len(set(items)), "duplicate_identity")


def no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        need(key not in result, "duplicate_json_key")
        result[key] = value
    return result


def parse(data):
    return json.loads(data, object_pairs_hook=no_duplicates,
                      parse_constant=lambda _: (_ for _ in ()).throw(Stop("invalid_json_number")))


def path_checked(value, existing=True):
    path = Path(value).absolute()
    # Normalize dot segments before checking every component; never follow links.
    path = Path(os.path.normpath(path))
    for part in reversed((path, *path.parents)):
        need(not part.is_symlink(), "symlink_path")
    need(not existing or path.exists(), "missing_path")
    string(str(path))
    return path


def open_readonly(path, directory=False):
    flags = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW
    if directory:
        flags |= os.O_DIRECTORY
    try:
        return os.open(path, flags)
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise Stop("symlink_path") from None
        if error.errno == errno.ENOTDIR:
            raise Stop("invalid_directory") from None
        raise


@contextlib.contextmanager
def open_regular(path, limit=MAX_FILE, private=False):
    path = path_checked(path)
    need(stat.S_ISREG(path.lstat().st_mode), "invalid_file")
    # A replacement after lstat must not block before descriptor validation.
    with os.fdopen(open_readonly(path), "rb") as stream:
        info = os.fstat(stream.fileno())
        need(stat.S_ISREG(info.st_mode), "invalid_file")
        need(info.st_size <= limit, "input_too_large")
        if private:
            need(info.st_uid == os.getuid() and info.st_mode & 0o077 == 0, "unsafe_permissions")
        yield stream


def read_bytes(path, limit=MAX_FILE, private=False):
    with open_regular(path, limit, private) as stream:
        data = stream.read(limit + 1)
    need(len(data) <= limit, "input_too_large")
    return data


def read_json(path, private=False):
    return parse(read_bytes(path, MAX_LEDGER, private))


def fsync_directory(path):
    path = path_checked(path)
    need(stat.S_ISDIR(path.lstat().st_mode), "invalid_directory")
    descriptor = open_readonly(path, directory=True)
    try:
        need(stat.S_ISDIR(os.fstat(descriptor).st_mode), "invalid_directory")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_new(path, value):
    path = path_checked(path, existing=False)
    data = canonical(value) + b"\n"
    need(len(data) <= MAX_LEDGER, "ledger_too_large")
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    fsync_directory(path.parent)


def checkpoint(store, ledger):
    data = canonical(ledger) + b"\n"
    need(len(data) <= MAX_LEDGER, "ledger_too_large")
    staged = None
    try:
        with tempfile.NamedTemporaryFile(dir=store, prefix=".checkpoint-", delete=False) as stream:
            staged = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        path_checked(store / "ledger.json")
        os.replace(staged, store / "ledger.json")
        fsync_directory(store)
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)  # Only this invocation's staging file.


@contextlib.contextmanager
def locked(store, writing=False):
    need(fcntl is not None and os.name == "posix", "unsupported_platform")
    store = path_checked(store)
    info = store.stat()
    need(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and info.st_mode & 0o077 == 0, "unsafe_permissions")
    lock = store / "lock"
    with open_regular(lock, 0, private=True) as stream:
        try:
            fcntl.flock(stream, (fcntl.LOCK_EX if writing else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Stop("store_busy")
        ledger = read_json(store / "ledger.json", private=True)
        validate_ledger(ledger)
        yield store, ledger


def git(repo, operation, *values):
    """Only fixed read-only builtins. Git and inherited security context are trusted."""
    need(not ROUTING.intersection(os.environ), "git_routing_override")
    executable = shutil.which("git")
    need(executable, "git_unavailable")
    commands = {
        "root": ["rev-parse", "--show-toplevel"],
        "directory": ["rev-parse", "--absolute-git-dir"],
        "partial": ["config", "--local", "--name-only", "--get-regexp", r"^(remote\..*\.promisor|extensions\.partialclone)$"],
        "head": ["rev-parse", "--verify", "HEAD^{commit}"],
        "head_id": ["rev-parse", "--verify", "HEAD"],
        "commit": ["rev-parse", "--verify", "--end-of-options", values[0] + "^{commit}"] if values else [],
        "tree": ["rev-parse", "--verify", "--end-of-options", values[0] + "^{tree}"] if values else [],
        "entries": ["ls-tree", "-r", "-z", "--full-tree", *values],
        "index": ["ls-files", "--stage", "-z"],
        "diff": ["diff", "--no-ext-diff", "--no-textconv", "--no-renames", "--no-color", "--no-relative", "--binary", "--full-index", "--unified=3", "--diff-algorithm=myers", "--no-indent-heuristic", "--src-prefix=a/", "--dst-prefix=b/", "--ignore-submodules=none", "--submodule=short", *values, "--"],
    }
    need(operation in commands, "unsupported_git_operation")
    command = [str(Path(executable).absolute()), "--no-pager", "--no-optional-locks", "--no-replace-objects", "-C", str(repo), "-c", "core.fsmonitor=false", *commands[operation]]
    result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, timeout=30,
                            env={**os.environ, "GIT_NO_LAZY_FETCH": "1", "GIT_TERMINAL_PROMPT": "0"})
    need(result.returncode == 0 or (operation == "partial" and result.returncode == 1), "git_read_failed")
    need(len(result.stdout) <= MAX_FILE, "git_output_too_large")
    return result.stdout


def repository(repo):
    repo = path_checked(repo)
    need(git(repo, "root").decode().strip() == str(repo), "wrong_repository")
    directory = str(Path(git(repo, "directory").decode().strip()).resolve())
    need(not git(repo, "partial"), "partial_clone_unsupported")
    return {"worktree": str(repo), "git_directory": directory}


def entries(repo, commit):
    result = {}
    for row in git(repo, "entries", commit).split(b"\0"):
        if not row:
            continue
        header, name = row.split(b"\t", 1)
        mode, kind, value = header.decode().split()
        need((kind == "blob" and mode in {"100644", "100755", "120000"}) or (kind == "commit" and mode == "160000"), "unsupported_tree_entry")
        relative = name.decode("utf-8")
        need(relative and not relative.startswith("/") and all(p not in ("", ".", "..", ".git") for p in relative.split("/")), "unsafe_tree_path")
        result[relative] = (mode, value)
    return result


def clean_candidate(repo, head, gitlink_scope):
    # Do not call status/diff against the worktree: clean filters can execute.
    # Exact blob bytes/modes and index entries define the shared parent check.
    need(git(repo, "head").decode().strip() == head, "candidate_moved")
    expected = entries(repo, head)
    pins = {name: entry for name, entry in expected.items() if entry[0] == "160000"}
    need(not pins or gitlink_scope == "pins-only", "gitlink_scope_required")
    index = {}
    for row in git(repo, "index").split(b"\0"):
        if row:
            header, name = row.split(b"\t", 1)
            mode, value, stage = header.decode().split()
            need(stage == "0", "dirty_candidate")
            index[name.decode("utf-8")] = (mode, value)
    need(index == expected, "dirty_candidate")
    actual = dict(pins)
    for name, (_, pin) in pins.items():
        path = path_checked(repo / name, existing=False)
        need(not path.exists() or path.is_dir(), "invalid_gitlink_checkout")
        if (path / ".git").exists():
            need(git(path, "root").decode().strip() == str(path), "wrong_gitlink_checkout")
            # Read the ref ID, not HEAD^{commit}: pin-only review does not need
            # or acquire nested objects, including in a partial nested clone.
            need(git(path, "head_id").decode().strip() == pin, "gitlink_head_mismatch")
    def unreadable(_error):
        raise Stop("worktree_unreadable")

    for base, dirs, files in os.walk(repo, followlinks=False, onerror=unreadable):
        if Path(base) == repo:
            dirs[:] = [name for name in dirs if name != ".git"]
            files = [name for name in files if name != ".git"]
        for name in dirs[:]:
            path = Path(base) / name
            if path.relative_to(repo).as_posix() in pins:
                dirs.remove(name)  # Explicit pins-only scope, never nested content.
            elif path.is_symlink():
                dirs.remove(name)
                files.append(name)
        for name in files:
            path = Path(base) / name
            relative = path.relative_to(repo).as_posix()
            need(relative in expected, "dirty_candidate")
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                data = os.fsencode(os.readlink(path))
                mode = "120000"
            else:
                data = read_bytes(path)
                mode = "100755" if info.st_mode & 0o100 else "100644"
            blob = b"blob " + str(len(data)).encode() + b"\0" + data
            algorithm = hashlib.sha1 if len(head) == 40 else hashlib.sha256
            actual[relative] = (mode, algorithm(blob).hexdigest())
    need(actual == expected, "dirty_candidate")
    need(git(repo, "head").decode().strip() == head, "candidate_moved")


def policy_check(policy, version=1):
    sequence(policy, 32)
    need(policy, "missing_reviewers")
    for slot in policy:
        names = "id required adapter requested_model identity_basis"
        fields(slot, names + (" model_selection identity_requirement researcher_model_overrides" if version in {2, 3} else ""))
        identifier(slot["id"])
        identifier(slot["adapter"])
        need(type(slot["required"]) is bool, "invalid_policy")
        if version in {2, 3}:
            string(slot["requested_model"], 256)
            selection = slot["model_selection"]
            fields(selection, "family basis source source_sha256")
            identifier(selection["family"])
            string(selection["basis"])
            evidence_reference(selection)
            need(slot["identity_requirement"] in {"launch-selection", "served-model"}, "invalid_identity_requirement")
            for model in sequence(slot["researcher_model_overrides"], 32):
                string(model, 256)
            unique(slot["researcher_model_overrides"])
        elif slot["requested_model"] is not None:
            identifier(slot["requested_model"])
        need(slot["identity_basis"] in {"harness_metadata", "operator_attestation"}, "invalid_policy")
    unique([s["id"] for s in policy])
    need(any(s["required"] for s in policy), "missing_required_reviewer")


def artifact_check(artifact):
    fields(artifact, "name text sha256")
    identifier(artifact["name"])
    string(artifact["text"], MAX_FILE, empty=True)
    hash_value(artifact["sha256"])
    need(sha(artifact["text"].encode()) == artifact["sha256"], "artifact_changed")


def evidence_reference(value):
    """A supplied provenance reference, not authenticated external evidence."""
    string(value["source"])
    hash_value(value["source_sha256"])


def team_policy_check(policy):
    fields(policy, "checklist severity_scale max_researchers accepted_risk" +
           (" release_policy" if type(policy) is dict and "release_policy" in policy else ""))
    if "release_policy" in policy:
        need(policy["release_policy"] == "blocking-only", "invalid_release_policy")
    need(type(policy["max_researchers"]) is int and 0 <= policy["max_researchers"] <= 128, "invalid_researcher_limit")
    need(type(policy["accepted_risk"]) is bool, "invalid_team_policy")
    need(sequence(policy["severity_scale"], 32), "missing_severity_scale")
    for severity in policy["severity_scale"]:
        identifier(severity)
    unique(policy["severity_scale"])
    need(sequence(policy["checklist"], 100), "missing_checklist")
    for item in policy["checklist"]:
        fields(item, "id description paths lead_required")
        identifier(item["id"])
        need(not item["id"].startswith("file-"), "reserved_obligation_id")
        string(item["description"])
        need(type(item["lead_required"]) is bool, "invalid_checklist")
        for path in sequence(item["paths"], 1000):
            string(path)
        unique(item["paths"])
    unique([item["id"] for item in policy["checklist"]])


def coverage_requirements(trees, base, head, policy):
    changed = {path for path in trees[base].keys() | trees[head].keys()
               if trees[base].get(path) != trees[head].get(path)}
    support = {path for item in policy["checklist"] for path in item["paths"]}
    need(support <= trees[base].keys() | trees[head].keys(), "unknown_checklist_path")
    result = [{"id": "file-" + sha(path.encode())[:24],
               "kind": "changed-file" if path in changed else "supporting-file", "path": path,
               "base": list(trees[base][path]) if path in trees[base] else None,
               "head": list(trees[head][path]) if path in trees[head] else None}
              for path in sorted(changed | support)]
    result += [{"kind": "checklist", **item} for item in policy["checklist"]]
    unique([item["id"] for item in result])
    return result


def requirements_check(bundle):
    rows = sequence(bundle["coverage_requirements"], 10000)
    for row in rows:
        fields(row, "id kind description paths lead_required"
               if type(row) is dict and row.get("kind") == "checklist"
               else "id kind path base head")
        identifier(row["id"])
        if row["kind"] != "checklist":
            need(row["kind"] in {"changed-file", "supporting-file"}, "invalid_requirement")
            string(row["path"])
            need(row["id"] == "file-" + sha(row["path"].encode())[:24], "invalid_requirement")
            need(row["base"] is not None or row["head"] is not None, "invalid_requirement")
            for entry in (row["base"], row["head"]):
                if entry is not None:
                    need(type(entry) is list and len(entry) == 2, "invalid_requirement")
                    need(entry[0] in {"100644", "100755", "120000", "160000"}, "invalid_requirement")
                    oid(entry[1])
            need((row["base"] != row["head"]) == (row["kind"] == "changed-file"), "invalid_requirement")
    unique([row["id"] for row in rows])
    need([r for r in rows if r["kind"] == "checklist"] ==
         [{"kind": "checklist", **item} for item in bundle["team_review"]["checklist"]], "checklist_changed")


def bundle_check(bundle):
    need(type(bundle) is dict and type(bundle.get("version")) is int and bundle["version"] in {1, 2, 3}, "unsupported_version")
    version = bundle["version"]
    fields(bundle, "version repository base head tree scope questions dependency_changes gitlink_scope gitlinks scope_limits reviewers artifacts diff" +
           (" team_review coverage_requirements" if version in {2, 3} else ""))
    fields(bundle["repository"], "worktree git_directory")
    for value in bundle["repository"].values():
        string(value)
    for name in ("base", "head", "tree"):
        oid(bundle[name])
    string(bundle["scope"])
    string(bundle["dependency_changes"])
    need(bundle["gitlink_scope"] in {"none", "pins-only"}, "invalid_gitlink_scope")
    for link in sequence(bundle["gitlinks"]):
        fields(link, "path base head")
        string(link["path"])
        need(link["base"] is not None or link["head"] is not None, "invalid_gitlink")
        for name in ("base", "head"):
            if link[name] is not None:
                oid(link[name])
    unique([link["path"] for link in bundle["gitlinks"]])
    need(not bundle["gitlinks"] or bundle["gitlink_scope"] == "pins-only", "gitlink_scope_required")
    need(bundle["scope_limits"] == ([PIN_LIMIT] if bundle["gitlinks"] else []), "invalid_scope_limits")
    need(sequence(bundle["questions"], 50), "missing_questions")
    for question in bundle["questions"]:
        string(question)
    policy_check(bundle["reviewers"], version)
    for artifact in sequence(bundle["artifacts"]):
        artifact_check(artifact)
    unique([a["name"] for a in bundle["artifacts"]])
    artifact_check(bundle["diff"])
    if version in {2, 3}:
        team_policy_check(bundle["team_review"])
        requirements_check(bundle)


def prepare_bundle(repo, base, head, input_path):
    oid(base)
    oid(head)
    identity = repository(repo)
    repo = Path(identity["worktree"])
    trees = {}
    for value in (base, head):
        need(git(repo, "commit", value).decode().strip() == value, "invalid_commit")
        trees[value] = entries(repo, value)
    spec = read_json(input_path)
    version = spec.get("version", 1) if type(spec) is dict else 1
    need(type(version) is int and version in {1, 2, 3}, "unsupported_version")
    fields(spec, "scope questions dependency_changes gitlink_scope reviewers artifacts" +
           (" version team_review" if version in {2, 3} else ""))
    if version in {2, 3}:
        team_policy_check(spec["team_review"])
    need(spec["gitlink_scope"] in {"none", "pins-only"}, "invalid_gitlink_scope")
    names = sorted({name for tree in trees.values() for name, entry in tree.items() if entry[0] == "160000"})
    pins = [{"path": name, **{key: trees[revision].get(name, (None, None))[1]
             if trees[revision].get(name, (None, None))[0] == "160000" else None
             for key, revision in (("base", base), ("head", head))}} for name in names]
    need(not pins or spec["gitlink_scope"] == "pins-only", "gitlink_scope_required")
    clean_candidate(repo, head, spec["gitlink_scope"])
    artifacts = []
    for item in sequence(spec["artifacts"]):
        fields(item, "name path")
        identifier(item["name"])
        string(item["path"])
        path = Path(item["path"])
        need(not path.is_absolute() and ".." not in path.parts, "unsafe_artifact_path")
        text = read_bytes(path_checked(input_path).parent / path).decode("utf-8")
        artifacts.append({"name": item["name"], "text": text, "sha256": sha(text.encode())})
    difference = git(repo, "diff", base, head).decode("utf-8")
    bundle = {"version": version, "repository": identity, "base": base, "head": head,
              "tree": git(repo, "tree", head).decode().strip(),
              **{key: spec[key] for key in ("scope", "questions", "dependency_changes", "gitlink_scope", "reviewers")},
              "gitlinks": pins, "scope_limits": [PIN_LIMIT] if pins else [],
              "artifacts": artifacts, "diff": {"name": "candidate.diff", "text": difference, "sha256": sha(difference.encode())}}
    if version in {2, 3}:
        bundle.update(team_review=spec["team_review"],
                      coverage_requirements=coverage_requirements(trees, base, head, spec["team_review"]))
    bundle_check(bundle)
    clean_candidate(repo, head, spec["gitlink_scope"])
    return bundle


def result_check(result, attempt, candidate):
    if candidate["bundle"]["version"] in {2, 3}:
        return team_result_check(result, attempt, candidate)
    fields(result, "version candidate attempt request_digest slot adapter run_id verdict identity findings executed_checks supplied_evidence limitations provenance")
    need(type(result["version"]) is int and result["version"] == 1, "unsupported_version")
    need(result["candidate"] == candidate["digest"] and result["attempt"] == attempt["id"] and result["request_digest"] == digest(attempt["request"]) and result["slot"] == attempt["request"]["slot"] and result["adapter"] == attempt["request"]["adapter"], "result_binding_mismatch")
    need(result["verdict"] in TERMINAL, "invalid_verdict")
    if result["run_id"] is not None:
        identifier(result["run_id"])
    need(result["verdict"] != "pass" or result["run_id"] is not None, "missing_completed_run")
    identity = result["identity"]
    fields(identity, "basis reviewer model evidence")
    need(identity["basis"] in BASES, "invalid_identity")
    for name in ("reviewer", "model"):
        if identity[name] is not None:
            identifier(identity[name])
    string(identity["evidence"], empty=True)
    for finding in sequence(result["findings"]):
        fields(finding, "id severity summary")
        identifier(finding["id"])
        need(finding["severity"] in {"blocking", "nit"}, "invalid_finding")
        string(finding["summary"])
    unique([f["id"] for f in result["findings"]])
    for check in sequence(result["executed_checks"]):
        fields(check, "command cwd mode revision outcome exit_code writes evidence provenance")
        for name in ("command", "cwd", "revision", "writes", "evidence"):
            string(check[name])
        need(check["mode"] in {"isolated", "integration", "live"}, "invalid_check")
        need(check["outcome"] in {"pass", "fail", "skipped", "interrupted", "unknown"}, "invalid_check")
        need(check["exit_code"] is None or type(check["exit_code"]) is int, "invalid_check")
        need(check["provenance"] in {"reviewer_reported", "adapter_observed"}, "invalid_check")
    names = {a["name"] for a in candidate["bundle"]["artifacts"]}
    for name in sequence(result["supplied_evidence"]):
        need(name in names, "unknown_supplied_evidence")
    for limit in sequence(result["limitations"]):
        string(limit)
    provenance = result["provenance"]
    fields(provenance, "importer source source_sha256")
    string(provenance["importer"])
    string(provenance["source"])
    hash_value(provenance["source_sha256"])


def team_identity_check(identity, launch):
    fields(identity, "basis reviewer model evidence")
    need(identity["basis"] in BASES, "invalid_identity")
    for name in ("reviewer", "model"):
        if identity[name] is not None:
            string(identity[name], 256)
    string(identity["evidence"], empty=True)
    if launch is not None:
        fields(launch, "model source source_sha256")
        string(launch["model"], 256)
        evidence_reference(launch)


def team_identity_valid(identity, launch, policy):
    return bool(launch and identity["basis"] == policy["identity_basis"] and
                identity["reviewer"] and identity["evidence"] and
                (identity["model"] is None or identity["model"] == launch["model"]) and
                (policy["identity_requirement"] != "served-model" or identity["model"] is not None))


def classification_check(finding, scale, nonblocking_unknown_confidence=False):
    need(finding["kind"] in {"defect", "suggestion"}, "invalid_finding")
    need(finding["severity"] in scale or
         (finding["kind"] == "suggestion" and finding["severity"] is None), "invalid_severity")
    need(finding["confidence"] in {"high", "medium", "low"} or
         (nonblocking_unknown_confidence and finding["confidence"] is None and
          finding["recommendation"] in {"follow-up", "optional"}), "invalid_confidence")
    need(finding["recommendation"] in {"block", "address-before-merge", "follow-up", "optional"}, "invalid_recommendation")
    string(finding["rationale"])


def actionable(finding):
    return finding["kind"] == "defect" or finding["recommendation"] != "optional"


def team_complete(result, policy):
    actors = [(result["identity"], result["launch"])] + [(r["identity"], r["launch"]) for r in result["researchers"]]
    return bool(result["run_id"] and all(team_identity_valid(identity, launch, policy) for identity, launch in actors)
                and all(row["state"] == "reviewed" for row in result["coverage"]))


def team_result_check(result, attempt, candidate):
    fields(result, "version candidate attempt request_digest slot adapter run_id verdict identity findings executed_checks supplied_evidence limitations provenance launch researchers coverage")
    need(type(result["version"]) is int and result["version"] in {2, 3} and
         result["version"] == candidate["bundle"]["version"], "unsupported_version")
    need(result["candidate"] == candidate["digest"] and result["attempt"] == attempt["id"] and
         result["request_digest"] == digest(attempt["request"]) and result["slot"] == attempt["request"]["slot"] and
         result["adapter"] == attempt["request"]["adapter"], "result_binding_mismatch")
    need(result["verdict"] in TERMINAL, "invalid_verdict")
    if result["run_id"] is not None:
        string(result["run_id"], 512)  # Actual native task paths may contain '/'.
    # Validated attempt-slot membership and the binding above guarantee a match.
    policy = next(p for p in candidate["bundle"]["reviewers"] if p["id"] == result["slot"])
    team_identity_check(result["identity"], result["launch"])
    if result["launch"] is not None:
        need(result["launch"]["model"] == policy["requested_model"], "launch_model_mismatch")
    provenance = result["provenance"]
    fields(provenance, "importer source source_sha256")
    string(provenance["importer"])
    evidence_reference(provenance)
    owners = {"lead": {"complete": result["run_id"] is not None and team_identity_valid(result["identity"], result["launch"], policy),
                       "report": provenance}}
    researchers = sequence(result["researchers"], candidate["bundle"]["team_review"]["max_researchers"])
    for child in researchers:
        fields(child, "id adapter run_id parent_run_id status identity launch report")
        identifier(child["id"])
        need(child["id"] != "lead" and child["id"] not in owners, "invalid_researcher_id")
        identifier(child["adapter"])
        string(child["run_id"], 512)
        string(child["parent_run_id"], 512)
        need(child["parent_run_id"] == result["run_id"], "invalid_researcher_parent")
        need(child["status"] in {"complete", "failed", "cancelled", "interrupted"}, "invalid_researcher_status")
        team_identity_check(child["identity"], child["launch"])
        if child["launch"] is not None:
            need(child["launch"]["model"] in [policy["requested_model"]] + policy["researcher_model_overrides"], "researcher_model_mismatch")
        if child["report"] is not None:
            fields(child["report"], "source source_sha256")
            evidence_reference(child["report"])
        need(child["status"] != "complete" or child["report"] is not None, "missing_researcher_report")
        owners[child["id"]] = {"complete": child["status"] == "complete" and team_identity_valid(child["identity"], child["launch"], policy),
                               "report": child["report"]}
    unique([(result["adapter"], result["run_id"])] + [(r["adapter"], r["run_id"]) for r in researchers])
    requirements = {row["id"]: row for row in candidate["bundle"]["coverage_requirements"]}
    coverage = sequence(result["coverage"], 10000)
    for row in coverage:
        fields(row, "obligation state owners evidence")
        need(row["obligation"] in requirements, "unknown_obligation")
        need(row["state"] in {"assigned", "reviewed", "unresolved"}, "invalid_coverage_state")
        for owner in sequence(row["owners"], 129):
            need(owner in owners, "unknown_coverage_owner")
        unique(row["owners"])
        evidence_owners = []
        for evidence in sequence(row["evidence"], 129):
            fields(evidence, "owner source source_sha256")
            evidence_reference(evidence)
            need(evidence["owner"] in row["owners"], "invalid_coverage_evidence_owner")
            report_ref = owners[evidence["owner"]]["report"]
            need(report_ref is not None and all(evidence[key] == report_ref[key] for key in ("source", "source_sha256")), "coverage_report_mismatch")
            evidence_owners.append(evidence["owner"])
        unique(evidence_owners)
        if row["state"] == "reviewed":
            need(row["owners"] and set(evidence_owners) == set(row["owners"]) and
                 all(owners[owner]["complete"] for owner in row["owners"]), "unsubstantiated_coverage")
            requirement = requirements[row["obligation"]]
            need(requirement["kind"] != "checklist" or not requirement["lead_required"] or "lead" in row["owners"], "lead_assessment_required")
    unique([row["obligation"] for row in coverage])
    need({row["obligation"] for row in coverage} == set(requirements), "missing_coverage")
    scale = candidate["bundle"]["team_review"]["severity_scale"]
    nonblocking_unknown_confidence = candidate["bundle"]["team_review"].get("release_policy") == "blocking-only"
    for finding in sequence(result["findings"]):
        fields(finding, "id kind severity confidence recommendation summary rationale evidence assumptions requested_outcome assessments classification_reason")
        identifier(finding["id"])
        classification_check(finding, scale, nonblocking_unknown_confidence)
        for key in ("summary", "requested_outcome"):
            string(finding[key])
        string(finding["classification_reason"], empty=True)
        for key in ("evidence", "assumptions"):
            for value in sequence(finding[key]):
                string(value)
        need(finding["kind"] != "defect" or finding["evidence"], "missing_defect_evidence")
        references = []
        for assessment in sequence(finding["assessments"], 128):
            fields(assessment, "researcher finding kind severity confidence recommendation rationale")
            need(assessment["researcher"] in owners and assessment["researcher"] != "lead" and
                 owners[assessment["researcher"]]["report"] is not None, "unknown_researcher_assessment")
            identifier(assessment["finding"])
            classification_check(assessment, scale, nonblocking_unknown_confidence)
            references.append((assessment["researcher"], assessment["finding"]))
            if any(assessment[key] != finding[key] for key in ("kind", "severity", "confidence", "recommendation")):
                need(finding["classification_reason"], "missing_classification_reason")
        unique(references)
    unique([f["id"] for f in result["findings"]])
    for check in sequence(result["executed_checks"]):
        fields(check, "command cwd mode revision outcome exit_code writes evidence provenance")
        for name in ("command", "cwd", "revision", "writes", "evidence"):
            string(check[name])
        need(check["mode"] in {"isolated", "integration", "live"}, "invalid_check")
        need(check["outcome"] in {"pass", "fail", "skipped", "interrupted", "unknown"}, "invalid_check")
        need(check["exit_code"] is None or type(check["exit_code"]) is int, "invalid_check")
        need(check["provenance"] in {"reviewer_reported", "adapter_observed"}, "invalid_check")
    names = {a["name"] for a in candidate["bundle"]["artifacts"]}
    for name in sequence(result["supplied_evidence"]):
        need(name in names, "unknown_supplied_evidence")
    for limit in sequence(result["limitations"]):
        string(limit)
    if result["verdict"] == "pass":
        need(team_complete(result, policy), "incomplete_team_pass")
        need(not any(actionable(f) for f in result["findings"]), "actionable_findings_require_changes")


def disposition_check(record, candidate, authorized):
    fields(record, "version candidate id actor authority decisions provenance")
    need(type(record["version"]) is int and record["version"] in {2, 3} and
         record["version"] == candidate["bundle"]["version"] and record["candidate"] == candidate["digest"], "disposition_binding_mismatch")
    identifier(record["id"])
    string(record["actor"])
    string(record["authority"])
    fields(record["provenance"], "importer source source_sha256")
    string(record["provenance"]["importer"])
    evidence_reference(record["provenance"])
    findings = {(a["id"], f["id"]) for a in candidate["attempts"] if a["result"] for f in a["result"]["findings"]}
    need(type(authorized) is bool, "invalid_authorization")
    blocking_only = candidate["bundle"]["team_review"].get("release_policy") == "blocking-only"
    need(sequence(record["decisions"]), "missing_decisions")
    for decision in record["decisions"]:
        fields(decision, "attempt finding decision rationale" +
               (" follow_up" if type(decision) is dict and decision.get("decision") == "nonblocking" else ""))
        need((decision["attempt"], decision["finding"]) in findings, "unknown_disposition_finding")
        need(decision["decision"] in {"request-fix", "dispute", "defer", "accept-risk"} or
             (blocking_only and decision["decision"] in {"nonblocking", "false-positive"}), "invalid_decision")
        string(decision["rationale"])
        if decision["decision"] in {"nonblocking", "false-positive"}:
            need(decision["rationale"].strip(), "missing_release_rationale")
        if decision["decision"] == "nonblocking":
            follow_up = decision["follow_up"]
            fields(follow_up, "issue source source_sha256")
            string(follow_up["issue"])
            evidence_reference(follow_up)
            need(follow_up["issue"].strip() and follow_up["source"].strip(), "invalid_follow_up")
        if decision["decision"] == "accept-risk":
            need(candidate["bundle"]["team_review"]["accepted_risk"] and authorized, "accepted_risk_not_authorized")
    unique([(d["attempt"], d["finding"]) for d in record["decisions"]])


def continuation_authorization_check(authorization):
    fields(authorization, "actor authority limits source source_sha256")
    for key in ("actor", "authority", "limits"):
        string(authorization[key])
    evidence_reference(authorization)


def continuation_origin(previous, policy, identifier_value):
    need(previous is not None and previous["id"] == identifier_value, "invalid_continuation")
    result = previous["result"]
    need(result is not None and result["verdict"] == "interrupted" and result["run_id"] is not None and
         team_identity_valid(result["identity"], result["launch"], policy), "invalid_continuation")
    return result


def continuation_check(continuation, previous, policy):
    fields(continuation, "attempt result_digest authorization")
    identifier(continuation["attempt"])
    result = continuation_origin(previous, policy, continuation["attempt"])
    hash_value(continuation["result_digest"])
    need(continuation["result_digest"] == previous["result_digest"], "invalid_continuation")
    continuation_authorization_check(continuation["authorization"])
    return result


def continuation_result_check(result, previous):
    """Validate a linked observation; True permits only those already known actors."""
    if result["run_id"] is None:
        need(result["verdict"] in {"failed", "cancelled", "interrupted"} and result["launch"] is None and
             result["researchers"] == [] and result["identity"]["basis"] == "unknown" and
             result["identity"]["reviewer"] is None and result["identity"]["model"] is None and
             all(row["state"] != "reviewed" for row in result["coverage"]), "continuation_result_mismatch")
        return False  # No observed resumed runtime, no identity-reuse exception.
    need(result["adapter"] == previous["adapter"] and result["run_id"] == previous["run_id"] and
         result["researchers"] == previous["researchers"], "continuation_result_mismatch")
    before, after = previous["identity"], result["identity"]
    need(after["basis"] == before["basis"] and after["reviewer"] == before["reviewer"], "continuation_result_mismatch")
    need(before["model"] is None or after["model"] == before["model"], "continuation_result_mismatch")
    need(previous["launch"] is None or (result["launch"] is not None and
         result["launch"]["model"] == previous["launch"]["model"]), "continuation_result_mismatch")
    return True


def validate_ledger(ledger):
    fields(ledger, "version active candidates")
    need(type(ledger["version"]) is int and ledger["version"] in {1, 2, 3}, "unsupported_version")
    candidates = sequence(ledger["candidates"], 32)
    need(candidates, "missing_candidate")
    digests, attempts, runs = [], [], []
    identity = None
    for index, candidate in enumerate(candidates):
        need(type(candidate) is dict and "bundle" in candidate, "invalid_schema")
        bundle_check(candidate["bundle"])
        version = candidate["bundle"]["version"]
        fields(candidate, "digest bundle attempts supersedes reason" + (" dispositions" if version in {2, 3} else ""))
        need(version <= ledger["version"], "unsupported_version")
        if index == 0:
            identity = candidate["bundle"]["repository"]
        need(candidate["bundle"]["repository"] == identity, "wrong_repository")
        need(digest(candidate["bundle"]) == candidate["digest"], "candidate_digest_mismatch")
        need(candidate["supersedes"] == (candidates[index - 1]["digest"] if index else None), "invalid_history")
        string(candidate["reason"])
        digests.append(candidate["digest"])
        slots = {s["id"]: s for s in candidate["bundle"]["reviewers"]}
        latest = {}
        for attempt in sequence(candidate["attempts"], 128):
            fields(attempt, "id request retry reason result result_digest")
            identifier(attempt["id"])
            request = attempt["request"]
            fields(request, "version candidate slot attempt adapter requested_model" + (" continuation" if version == 3 else ""))
            need(type(request["version"]) is int and request["version"] == version and request["candidate"] == candidate["digest"] and request["attempt"] == attempt["id"] and request["slot"] in slots, "invalid_attempt")
            slot = slots[request["slot"]]
            need(request["adapter"] == slot["adapter"] and request["requested_model"] == slot["requested_model"], "invalid_attempt")
            previous = latest.get(slot["id"])
            continuation = request["continuation"] if version == 3 else None
            predecessor_result = None
            if continuation is not None:
                need(attempt["retry"] is None, "invalid_continuation")
                predecessor_result = continuation_check(continuation, previous, slot)
            else:
                need(attempt["retry"] == (previous["id"] if previous else None), "invalid_retry")
                if previous:
                    need(previous["result"] is not None and previous["result"]["verdict"] in {"failed", "cancelled", "interrupted"}, "invalid_retry")
            string(attempt["reason"])
            result = attempt["result"]
            if result is None:
                need(attempt["result_digest"] is None, "invalid_result_digest")
            else:
                result_check(result, attempt, candidate)
                need(digest(result) == attempt["result_digest"], "result_digest_mismatch")
                repeated_actors = predecessor_result is not None and continuation_result_check(result, predecessor_result)
                if not repeated_actors:
                    if result["run_id"] is not None:
                        runs.append((result["adapter"], result["run_id"]))
                    if version in {2, 3}:
                        runs.extend((r["adapter"], r["run_id"]) for r in result["researchers"])
            latest[slot["id"]] = attempt
            attempts.append(attempt["id"])
        if version in {2, 3}:
            records = sequence(candidate["dispositions"], 512)
            for entry in records:
                fields(entry, "record record_digest accepted_risk_authorized")
                disposition_check(entry["record"], candidate, entry["accepted_risk_authorized"])
                need(digest(entry["record"]) == entry["record_digest"], "disposition_digest_mismatch")
            unique([entry["record"]["id"] for entry in records])
    unique(digests)
    unique(attempts)
    unique(runs)
    need(ledger["version"] == max(c["bundle"]["version"] for c in candidates), "unsupported_version")
    need(ledger["active"] == digests[-1], "invalid_active_candidate")


def current(ledger):
    return ledger["candidates"][-1]


def check_source(candidate):
    identity = candidate["bundle"]["repository"]
    need(repository(identity["worktree"]) == identity, "wrong_repository")
    clean_candidate(Path(identity["worktree"]), candidate["bundle"]["head"], candidate["bundle"]["gitlink_scope"])
    bundle = candidate["bundle"]
    if bundle["version"] in {2, 3}:
        trees = {value: entries(Path(identity["worktree"]), value) for value in (bundle["base"], bundle["head"])}
        need(coverage_requirements(trees, bundle["base"], bundle["head"], bundle["team_review"]) == bundle["coverage_requirements"], "coverage_requirements_changed")


def report(ledger, candidate=None):
    candidate = candidate or current(ledger)
    if candidate["bundle"]["version"] in {2, 3}:
        return team_report(ledger, candidate)
    blockers, slots = [], []
    for policy in candidate["bundle"]["reviewers"]:
        attempts = [a for a in candidate["attempts"] if a["request"]["slot"] == policy["id"]]
        last = attempts[-1] if attempts else None
        result = last["result"] if last else None
        state = result["verdict"] if result else ("in-flight" if last else "not-started")
        if policy["required"] and state != "pass":
            blockers.append({"slot": policy["id"], "code": "required_" + state})
        if result and result["verdict"] == "pass":
            observed = result["identity"]
            valid = observed["basis"] == policy["identity_basis"] and observed["reviewer"] and observed["evidence"]
            valid = valid and (policy["requested_model"] is None or observed["model"] == policy["requested_model"])
            if policy["required"] and not valid:
                blockers.append({"slot": policy["id"], "code": "identity_unverified"})
        slots.append({"id": policy["id"], "state": state, "attempt": last["id"] if last else None})
    findings = any(a["result"] and (a["result"]["verdict"] == "changes-required" or any(f["severity"] == "blocking" for f in a["result"]["findings"])) for a in candidate["attempts"])
    if findings:
        blockers.append({"slot": None, "code": "blocking_findings"})
    if candidate["digest"] != ledger["active"]:
        state = "superseded"
    else:
        try:
            check_source(candidate)
        except Stop as error:
            blockers.append({"slot": None, "code": str(error)})
        state = "changes-required" if findings else "ready" if not blockers else "reviewing" if candidate["attempts"] else "prepared"
    return {"candidate": candidate["digest"], "head": candidate["bundle"]["head"], "state": state, "blockers": blockers, "slots": slots, "gitlinks": candidate["bundle"]["gitlinks"], "scope_limits": candidate["bundle"]["scope_limits"], "publication_authority": False}


def release_report(ledger, candidate, blockers, slots):
    """Separate opt-in gate; original results and clean readiness stay unchanged.

    Dispositions assert an operator's evidence-backed determination, not an
    authenticated classification or proof that an Issue was actually filed.
    Essential QA and current-owner delta evidence still require operator review.
    """
    decisions = {(row["attempt"], row["finding"]): row
                 for entry in candidate["dispositions"] for row in entry["record"]["decisions"]}
    resolved = {key for key, row in decisions.items()
                if row["decision"] in {"nonblocking", "false-positive"}}
    attempts = {attempt["id"]: attempt for attempt in candidate["attempts"]}
    # A findings-only rejection can be resolved explicitly, but an unexplained
    # rejection cannot borrow a resolution from an earlier attempt.
    resolved_verdicts = set()
    for slot in slots:
        attempt = attempts.get(slot["attempt"])
        result = attempt["result"] if attempt else None
        if result and result["findings"] and all(
                (attempt["id"], finding["id"]) in resolved for finding in result["findings"]):
            resolved_verdicts.add(slot["id"])
    release_blockers = [dict(blocker) for blocker in blockers
                        if not (blocker["code"] == "unresolved_review_verdict" and
                                blocker["slot"] in resolved_verdicts)]
    if candidate["digest"] != ledger["active"]:
        release_blockers.append({"slot": None, "code": "superseded_candidate"})
    follow_ups = []
    for attempt in candidate["attempts"]:
        if not attempt["result"]:
            continue
        for finding in attempt["result"]["findings"]:
            key = (attempt["id"], finding["id"])
            decision = decisions.get(key)
            if key in resolved:
                if decision["decision"] == "nonblocking":
                    follow_ups.append({"attempt": key[0], "finding": key[1], **decision["follow_up"]})
                continue
            blocking = (finding["recommendation"] in {"block", "address-before-merge"} or
                        (decision is not None and decision["decision"] == "request-fix"))
            release_blockers.append({"slot": attempt["request"]["slot"], "attempt": key[0],
                                     "finding": key[1], "code": "unresolved_blocking_finding" if blocking
                                     else "finding_disposition_required"})
    ready = not release_blockers
    return {"release_ready": ready, "release_blockers": release_blockers,
            "release_state": "ready-with-followups" if ready and follow_ups else "ready" if ready else "not-ready",
            "follow_ups": follow_ups}


def team_report(ledger, candidate):
    blockers, slots, actionable_findings = [], [], []
    for policy in candidate["bundle"]["reviewers"]:
        attempts = [a for a in candidate["attempts"] if a["request"]["slot"] == policy["id"]]
        last = attempts[-1] if attempts else None
        result = last["result"] if last else None
        state = result["verdict"] if result else ("in-flight" if last else "not-started")
        complete = bool(result and team_complete(result, policy))
        # Participating optional teams cannot hide an incomplete/contradictory result.
        if policy["required"] or last:
            if state not in {"pass", "changes-required"}:
                prefix = "required_" if policy["required"] else "participating_"
                blockers.append({"slot": policy["id"], "code": prefix + state})
            if result and not complete:
                blockers.append({"slot": policy["id"], "code": "team_incomplete"})
            if state == "changes-required" and not any(actionable(f) for f in result["findings"]):
                blockers.append({"slot": policy["id"], "code": "unresolved_review_verdict"})
        slot_summary = {"id": policy["id"], "state": state, "attempt": last["id"] if last else None,
                        "complete": complete}
        if candidate["bundle"]["version"] == 3:
            continuation = last["request"]["continuation"] if last else None
            slot_summary["continuation_of"] = continuation["attempt"] if continuation else None
        slots.append(slot_summary)
        for attempt in attempts:
            if attempt["result"]:
                actionable_findings.extend((attempt["id"], f["id"]) for f in attempt["result"]["findings"] if actionable(f))
    decisions = {}
    for entry in candidate["dispositions"]:
        for decision in entry["record"]["decisions"]:
            decisions[(decision["attempt"], decision["finding"])] = decision["decision"]
    active = candidate["digest"] == ledger["active"]
    if active:
        try:
            check_source(candidate)
        except Stop as error:
            blockers.append({"slot": None, "code": str(error)})
    clean = active and not blockers and not actionable_findings
    accepted = active and not blockers and bool(actionable_findings) and all(decisions.get(key) == "accept-risk" for key in actionable_findings)
    rejected = bool(actionable_findings) or any(slot["state"] == "changes-required" for slot in slots)
    state = "superseded" if not active else "ready" if clean else "accepted-with-findings" if accepted else "changes-required" if rejected else "reviewing" if candidate["attempts"] else "prepared"
    release = (release_report(ledger, candidate, blockers, slots)
               if candidate["bundle"]["team_review"].get("release_policy") == "blocking-only" else {})
    if actionable_findings:
        # Informational after readiness/acceptance: recorded findings can be accepted,
        # but never count as a clean review or grant publication authority.
        blockers.append({"slot": None, "code": "recorded_findings"})
    return {"candidate": candidate["digest"], "head": candidate["bundle"]["head"], "state": state,
            "clean_review_ready": clean, "accepted_with_findings": accepted,
            "blockers": blockers, "slots": slots, "gitlinks": candidate["bundle"]["gitlinks"],
            "scope_limits": candidate["bundle"]["scope_limits"], "publication_authority": False,
            "coordinator_authority_authenticated": False,
            "dispositions": [{"id": entry["record"]["id"], "sha256": entry["record_digest"]} for entry in candidate["dispositions"]],
            **release}


def outside_repo(path, repo):
    path = path_checked(path, existing=False)
    need(path != repo and repo not in path.parents, "output_inside_repository")
    return path


def export_request(path, candidate, attempt):
    repo = Path(candidate["bundle"]["repository"]["worktree"])
    path = outside_repo(path, repo)
    exported = {"request": attempt["request"], "request_digest": digest(attempt["request"]), "bundle": candidate["bundle"]}
    if candidate["bundle"]["version"] == 3:
        history, seen = [], {attempt["id"]}
        by_id = {entry["id"]: entry for entry in candidate["attempts"]}
        continuation = attempt["request"]["continuation"]
        while continuation is not None:
            prior = by_id.get(continuation["attempt"])
            need(prior is not None and prior["id"] not in seen and
                 prior["request"]["slot"] == attempt["request"]["slot"] and
                 prior["result_digest"] == continuation["result_digest"], "invalid_continuation")
            seen.add(prior["id"])
            history.append(prior)
            continuation = prior["request"]["continuation"]
        exported["continuation_history"] = list(reversed(history))
    write_new(path, exported)


def execute(args):
    if args.command in {"prepare", "supersede"}:
        need(args.sanitized, "sanitization_acknowledgment_required")
        bundle = prepare_bundle(args.repo, args.base, args.head, args.inputs)
        candidate = {"digest": digest(bundle), "bundle": bundle, "attempts": [], "supersedes": None, "reason": "initial candidate"}
        if bundle["version"] in {2, 3}:
            candidate["dispositions"] = []
        store = outside_repo(args.store, Path(bundle["repository"]["worktree"]))
        if args.command == "prepare":
            need(fcntl is not None, "unsupported_platform")
            ledger = {"version": bundle["version"], "active": candidate["digest"], "candidates": [candidate]}
            validate_ledger(ledger)
            need(len(canonical(ledger)) + 1 <= MAX_LEDGER, "ledger_too_large")
            store.mkdir(mode=0o700)  # Exclusive; partial creation is not a valid store.
            with os.fdopen(os.open(store / "lock", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb"):
                pass
            write_new(store / "ledger.json", ledger)
            return report(ledger)
        with locked(store, writing=True) as (store, ledger):
            need(args.expect == ledger["active"], "stale_candidate")
            string(args.reason)
            need(bundle["repository"] == current(ledger)["bundle"]["repository"], "wrong_repository")
            need(bundle["version"] >= ledger["version"], "version_downgrade")
            candidate.update(supersedes=ledger["active"], reason=args.reason)
            ledger["candidates"].append(candidate)
            ledger["active"] = candidate["digest"]
            ledger["version"] = max(ledger["version"], bundle["version"])
            validate_ledger(ledger)
            checkpoint(store, ledger)
            return report(ledger)
    with locked(args.store, writing=args.command in {"start", "record", "disposition"}) as (store, ledger):
        candidate = current(ledger)
        if args.command in {"status", "resume"}:
            if args.candidate:
                candidate = next((c for c in ledger["candidates"] if c["digest"] == args.candidate), None)
                need(candidate is not None, "unknown_candidate")
            if args.output:
                need(args.command == "resume" and args.attempt, "attempt_required")
                attempt = next((a for a in candidate["attempts"] if a["id"] == args.attempt), None)
                need(attempt is not None, "unknown_attempt")
                need(candidate["digest"] == ledger["active"], "stale_candidate")
                check_source(candidate)
                export_request(args.output, candidate, attempt)
            return report(ledger, candidate)
        need(args.expect == ledger["active"], "stale_candidate")
        check_source(candidate)
        if args.command == "start":
            policy = next((s for s in candidate["bundle"]["reviewers"] if s["id"] == args.slot), None)
            need(policy is not None, "unknown_slot")
            previous = [a for a in candidate["attempts"] if a["request"]["slot"] == args.slot]
            continue_from = getattr(args, "continue_from", None)
            authorization_path = getattr(args, "authorization", None)
            need(continue_from is not None or authorization_path is None, "continuation_not_requested")
            continuation = None
            if continue_from is not None:
                need(candidate["bundle"]["version"] == 3, "continuation_requires_v3")
                need(args.retry is None, "invalid_continuation")
                need(authorization_path is not None, "continuation_authorization_required")
                need(getattr(args, "sanitized", False), "sanitization_acknowledgment_required")
                prior = previous[-1] if previous else None
                continuation_origin(prior, policy, continue_from)
                authorization = read_json(authorization_path)
                continuation_authorization_check(authorization)
                continuation = {"attempt": prior["id"], "result_digest": prior["result_digest"],
                                "authorization": authorization}
            else:
                need(args.retry == (previous[-1]["id"] if previous else None), "explicit_retry_required")
            string(args.reason)
            identifier_value = uuid.uuid4().hex
            request = {"version": candidate["bundle"]["version"], "candidate": candidate["digest"], "slot": args.slot, "attempt": identifier_value, "adapter": policy["adapter"], "requested_model": policy["requested_model"]}
            if candidate["bundle"]["version"] == 3:
                request["continuation"] = continuation
            attempt = {"id": identifier_value, "request": request, "retry": args.retry, "reason": args.reason, "result": None, "result_digest": None}
            candidate["attempts"].append(attempt)
            validate_ledger(ledger)
            checkpoint(store, ledger)  # Durable before handing anything to a reviewer.
            export_request(args.output, candidate, attempt)
            summary = report(ledger)
            summary.update(attempt=identifier_value, request_digest=digest(request))
            return summary
        need(args.sanitized, "sanitization_acknowledgment_required")
        if args.command == "disposition":
            need(candidate["bundle"]["version"] in {2, 3}, "team_review_required")
            record = read_json(args.disposition)
            disposition_check(record, candidate, args.authorize_accepted_risk)
            candidate["dispositions"].append({"record": record, "record_digest": digest(record),
                                               "accepted_risk_authorized": args.authorize_accepted_risk})
            validate_ledger(ledger)
            checkpoint(store, ledger)
            return report(ledger)
        result = read_json(args.result)
        attempt = next((a for a in candidate["attempts"] if a["id"] == result.get("attempt")), None) if type(result) is dict else None
        need(attempt is not None, "unknown_attempt")
        need(attempt["result"] is None, "result_already_recorded")
        result_check(result, attempt, candidate)
        attempt["result"] = result
        attempt["result_digest"] = digest(result)
        validate_ledger(ledger)
        checkpoint(store, ledger)
        return report(ledger)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "supersede", "start", "record", "status", "resume", "disposition"):
        command = commands.add_parser(name)
        command.add_argument("--store", required=True)
        if name in {"prepare", "supersede"}:
            for option in ("repo", "base", "head", "inputs"):
                command.add_argument("--" + option, required=True)
        if name in {"prepare", "supersede", "start", "record", "disposition"}:
            command.add_argument("--sanitized", action="store_true")
        if name in {"supersede", "start", "record", "disposition"}:
            command.add_argument("--expect", required=True)
        if name in {"supersede", "start"}:
            command.add_argument("--reason", required=True)
        if name == "start":
            command.add_argument("--slot", required=True)
            attempt_kind = command.add_mutually_exclusive_group()
            attempt_kind.add_argument("--retry")
            attempt_kind.add_argument("--continue-from")
            command.add_argument("--authorization")
            command.add_argument("--output", required=True)
        if name == "record":
            command.add_argument("--result", required=True)
        if name == "disposition":
            command.add_argument("--disposition", required=True)
            command.add_argument("--authorize-accepted-risk", action="store_true")
        if name in {"status", "resume"}:
            command.add_argument("--candidate")
            command.add_argument("--attempt")
            command.add_argument("--output")
            gate = command.add_mutually_exclusive_group()
            gate.add_argument("--require-ready", action="store_true")
            gate.add_argument("--require-release-ready", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = execute(args)
        if getattr(args, "require_release_ready", False) and "release_ready" not in result:
            result.update(release_ready=False, release_state="not-enabled", follow_ups=[],
                          release_blockers=[{"slot": None, "code": "release_policy_not_enabled"}])
        print(json.dumps(result, sort_keys=True))
        if getattr(args, "require_release_ready", False):
            return 0 if result["release_ready"] else 2
        return 2 if getattr(args, "require_ready", False) and result["state"] != "ready" else 0
    except Stop as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 1
    except (OSError, ValueError, TypeError, KeyError, AttributeError, subprocess.SubprocessError, RecursionError):
        print('{"error":"invalid_input_or_local_operation"}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('{"error":"interrupted_reconcile_store"}', file=sys.stderr)
        return 130


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    sys.exit(main())
