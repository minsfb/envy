#!/usr/bin/env python3
"""Plan, approve, and verify one non-force Git branch publication."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit


class Stop(Exception):
    pass


def require(condition, message):
    if not condition:
        raise Stop(message)


def git(repo, *args, allowed=(0,), data=None):
    try:
        process = subprocess.Popen(
            ["git", "-C", str(repo), *args], text=True,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=(os.name == "posix"),
        )
        try:
            output, _ = process.communicate(data, timeout=120)
        except BaseException:
            # Do not leave our hook/transport subprocesses publishing after the
            # caller has been told to reconcile an uncertain result.
            try:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
            except ProcessLookupError:
                pass
            process.communicate()
            raise
    except (OSError, subprocess.TimeoutExpired):
        raise Stop("Git could not complete; reconcile any attempted publication before retrying.")
    # Git/credential helpers can put credentials in diagnostics. Never relay them.
    require(process.returncode in allowed, "Git operation failed (exit %d); inspect privately; do not repair or retry automatically." % process.returncode)
    return output.rstrip("\n")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def write_new(path, value):
    with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def replace_receipt(path, value):
    # Owned receipt only; callers must have created/validated it first.
    staged = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=Path(path).absolute().parent, delete=False) as stream:
            staged = stream.name
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staged, path)
    finally:
        if staged is not None:
            # Remove only the staging file allocated by this invocation. An
            # interruption never makes a stale receipt imply approval.
            Path(staged).unlink(missing_ok=True)


def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        raise Stop("Cannot read valid publication JSON.")


def root(repo):
    return str(Path(git(repo, "rev-parse", "--show-toplevel")).resolve())


def require_plain_push(repo):
    configured = git(repo, "config", "--name-only", "--get-regexp", r"^push\.pushoption$", allowed=(0, 1))
    require(not configured, "Configured push options are unsupported; they can request actions outside the approved publication.")


def source_oid(repo, source):
    require(source and not source.startswith("-") and not any(c.isspace() or ord(c) < 32 for c in source), "Supply an explicit source revision without whitespace/options.")
    oid = git(repo, "rev-parse", "--verify", "--end-of-options", source + "^{commit}")
    require(re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", oid), "Source must resolve to one commit.")
    return oid


def endpoint(repo, remote):
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", remote), "Use a configured remote name, not a URL.")
    mirror = git(repo, "config", "--bool", "--get-all", "remote." + remote + ".mirror", allowed=(0, 1))
    require("true" not in mirror.splitlines(), "Mirror remotes are not supported.")
    urls = git(repo, "remote", "get-url", "--push", "--all", remote).splitlines()
    require(len(urls) == 1, "Exactly one effective push URL is required.")
    url = urls[0]
    require(url and not url.startswith("-") and not any(c.isspace() or ord(c) < 32 for c in url), "Unsupported push endpoint; do not include credentials or whitespace.")
    # get-url expands insteadOf/pushInsteadOf. Reject a second rewrite when the
    # resulting URL is passed to Git explicitly; custom helpers are unsupported.
    rewrites = git(repo, "config", "--null", "--get-regexp", r"^url\..*\.(insteadof|pushinsteadof)$", allowed=(0, 1))
    prefixes = [entry.partition("\n")[2] for entry in rewrites.split("\0") if entry]
    require(not any(url.startswith(prefix) for prefix in prefixes), "Effective push endpoint would be rewritten again; resolve the ambiguity before planning.")
    if "://" in url:
        parsed = urlsplit(url)
        require(parsed.scheme in ("https", "http", "ssh", "file"), "Custom Git transports/helpers are not supported.")
        require(not parsed.query and not parsed.fragment and parsed.password is None, "Credential-bearing or query/fragment URLs are not supported; use a credential helper.")
        require(parsed.scheme == "ssh" or not parsed.username, "URL userinfo is not supported; use a credential helper.")
        require(parsed.scheme == "file" or parsed.hostname, "Push endpoint is missing a host.")
    elif ":" in url:
        require("@" not in url.partition(":")[2], "Credential-ambiguous scp-like endpoints are not supported; use a credential helper or SSH agent.")
        require(re.fullmatch(r"(?:[A-Za-z0-9._-]+@)?[A-Za-z0-9._-]+:[^:].*", url), "Custom Git transports/helpers are not supported.")
    else:
        url = str((Path(root(repo)) / url).resolve())
    require(not any(url.startswith(prefix) for prefix in prefixes), "Canonical push endpoint would be rewritten; resolve the ambiguity before planning.")
    return url


def remote_state(repo, url, destination):
    output = git(repo, "ls-remote", "--symref", "--", url, "HEAD", destination)
    old, default, symbolic = None, None, None
    for line in output.splitlines():
        left, sep, ref = line.partition("\t")
        require(sep, "Unexpected remote advertisement.")
        if ref == "HEAD" and left.startswith("ref: "):
            default = left[5:]
        elif ref == destination and left.startswith("ref: "):
            symbolic = left[5:]
        elif ref == destination:
            require(re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", left) and old is None, "Unexpected destination advertisement.")
            old = left
    return {"oid": old, "default_ref": default, "symbolic_target": symbolic}


def validate_local(repo, plan):
    require(root(repo) == plan["repository"], "Wrong repository/worktree for this plan.")
    require(str(Path(git(repo, "rev-parse", "--absolute-git-dir")).resolve()) == plan["git_directory"], "Repository identity changed.")
    require(source_oid(repo, plan["source"]) == plan["candidate"], "Source candidate moved; obtain a new plan/review.")
    require(endpoint(repo, plan["remote"]) == plan["endpoint"], "Effective push endpoint changed; obtain a new plan/authorization.")
    require_plain_push(repo)


def load_plan(path):
    envelope = read_json(path)
    plan = envelope["plan"]
    require(plan["version"] == 1 and digest(plan) == envelope["digest"], "Publication plan digest/version mismatch.")
    require(plan["intent"] in ("feature", "protected"), "Unknown publication intent.")
    require(plan["server_contract"] == "ordinary-refs", "A known ordinary-ref server contract is required; unknown servers are unsupported.")
    return plan, envelope["digest"]


def make_plan(args):
    repo = root(args.repo)
    require_plain_push(repo)
    require(args.destination.startswith("refs/heads/"), "Destination must be a full refs/heads/... ref.")
    git(repo, "check-ref-format", args.destination)
    for ref in args.protected_ref:
        require(ref.startswith("refs/heads/"), "Protected refs must be full branch refs.")
        git(repo, "check-ref-format", ref)
    candidate = source_oid(repo, args.source)
    url = endpoint(repo, args.remote)
    before = remote_state(repo, url, args.destination)
    require(before["symbolic_target"] is None, "Symbolic destination is unsupported; do not publish through an alias.")
    protected = sorted(set(args.protected_ref + ["refs/heads/main", "refs/heads/master", "refs/heads/trunk", "refs/heads/default"] + ([before["default_ref"]] if before["default_ref"] else [])))
    require(args.intent == "protected" or args.destination not in protected, "Default/protected destination requires protected-branch authorization, not feature publication.")
    if before["oid"]:
        git(repo, "merge-base", "--is-ancestor", before["oid"], candidate)
    plan = {
        "version": 1, "repository": repo,
        "git_directory": str(Path(git(repo, "rev-parse", "--absolute-git-dir")).resolve()),
        "source": args.source, "candidate": candidate, "remote": args.remote,
        "endpoint": url, "destination": args.destination, "before": before,
        "intent": args.intent, "protected_refs": protected,
        "server_contract": args.server_contract,
    }
    envelope = {"plan": plan, "digest": digest(plan)}
    write_new(args.output, envelope)
    return envelope


def hook(args):
    context = read_json(args.context)
    plan = context["plan"]
    outcome = "guard_rejected"
    try:
        updates = sys.stdin.read()
        rows = [row.split() for row in updates.splitlines()]
        expected_old = plan["before"]["oid"] or "0" * len(plan["candidate"])
        require(len(rows) == 1 and len(rows[0]) == 4, "Push must advertise exactly one update.")
        _, candidate, destination, old = rows[0]
        require((candidate, destination, old) == (plan["candidate"], plan["destination"], expected_old), "Advertised publication changed; stop and reconcile.")
        existing = context["existing_hook"]
        if existing and os.access(existing, os.X_OK):
            # Preserve rejection semantics and stdin, but suppress potentially
            # secret output. No repository hook is changed.
            result = subprocess.run([existing, plan["remote"], plan["endpoint"]], input=updates, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if result.returncode:
                outcome = "existing_hook_rejected"
                raise Stop("Existing pre-push hook rejected publication; inspect privately.")
        validate_local(plan["repository"], plan)
        require(remote_state(plan["repository"], plan["endpoint"], plan["destination"]) == plan["before"], "Remote destination/default/alias changed during pre-push; stop and reconcile.")
        outcome = "accepted"
    finally:
        # The verdict must survive parent interruption and temporary-hook
        # cleanup. Unknown/pending is not permission to infer success later.
        receipt = read_json(context["receipt_path"])
        require(receipt["plan_digest"] == digest(plan), "Hook receipt belongs to a different plan.")
        receipt["pre_push"] = outcome
        replace_receipt(context["receipt_path"], receipt)
    return None


def verify(args, plan=None, receipt=None):
    if plan is None:
        plan, plan_digest = load_plan(args.plan)
        receipt = read_json(args.receipt)
        require(receipt["plan_digest"] == plan_digest, "Receipt belongs to a different plan.")
    require(root(args.repo) == plan["repository"], "Wrong repository/worktree for verification.")
    require(endpoint(args.repo, plan["remote"]) == plan["endpoint"], "Remote endpoint changed; reconcile the originally planned endpoint privately.")
    observed = remote_state(args.repo, plan["endpoint"], plan["destination"])
    rejected = receipt.get("pre_push") in ("guard_rejected", "existing_hook_rejected")
    approved = receipt.get("pre_push") == "accepted" or (receipt.get("pre_push") == "not_required" and plan["before"]["oid"] == plan["candidate"])
    if observed["symbolic_target"] is not None:
        status = "unsupported_server_state"
    elif rejected:
        status = "rejected"
    else:
        status = "verified" if approved and observed["oid"] == plan["candidate"] else "needs_reconciliation"
    receipt.update({"observed": observed, "status": status})
    replace_receipt(args.receipt, receipt)
    require(observed["symbolic_target"] is None, "Observed a symbolic destination: the server contract was violated; inspect all potentially affected refs. No rollback was attempted.")
    require(not rejected, "Pre-push guard/hook rejected publication; inspect the receipt and reconcile, even if another actor published the candidate.")
    require(approved, "Pre-push approval is unknown/interrupted; a matching remote SHA alone cannot verify this publication.")
    require(receipt["status"] == "verified", "Destination does not match candidate; no retry/rollback was attempted. Reconcile the receipt.")
    return receipt


def apply(args):
    plan, plan_digest = load_plan(args.plan)
    require(args.approve == plan_digest and args.authorize == plan["intent"], "Approval digest/authority does not match this exact publication plan.")
    validate_local(args.repo, plan)
    require(remote_state(args.repo, plan["endpoint"], plan["destination"]) == plan["before"], "Remote destination/default changed; obtain a new plan and approval.")
    if plan["before"]["oid"]:
        git(args.repo, "merge-base", "--is-ancestor", plan["before"]["oid"], plan["candidate"])
    receipt = {"plan_digest": plan_digest, "candidate": plan["candidate"], "destination": plan["destination"], "status": "attempting", "pre_push": "not_required" if plan["before"]["oid"] == plan["candidate"] else "pending", "server_contract": plan["server_contract"]}
    write_new(args.receipt, receipt)
    push_result = "failed_or_interrupted"
    try:
        if plan["before"]["oid"] != plan["candidate"]:
            existing = Path(git(plan["repository"], "rev-parse", "--git-path", "hooks/pre-push"))
            if not existing.is_absolute():
                existing = Path(plan["repository"]) / existing
            with tempfile.TemporaryDirectory(prefix="git-publish-") as directory:
                context = Path(directory) / "context.json"
                write_new(context, {"plan": plan, "existing_hook": str(existing.resolve()), "receipt_path": str(Path(args.receipt).resolve())})
                wrapper = Path(directory) / "pre-push"
                wrapper.write_text("#!/bin/sh\nexec " + " ".join(shlex.quote(arg) for arg in [sys.executable, str(Path(__file__).resolve()), "_pre_push", "--context", str(context)]) + ' "$@"\n')
                wrapper.chmod(0o700)
                git(args.repo, "-c", "core.hooksPath=" + directory, "-c", "push.autoSetupRemote=false", "push", "--porcelain", "--no-force", "--no-follow-tags", "--recurse-submodules=no", "--", plan["endpoint"], plan["candidate"] + ":" + plan["destination"])
        push_result = "completed"
    except Stop:
        pass
    finally:
        # Reload the hook's durable verdict, including on KeyboardInterrupt.
        # Never overwrite it with the parent's earlier pending in-memory copy.
        receipt = read_json(args.receipt)
        receipt["push_result"] = push_result
        replace_receipt(args.receipt, receipt)
    # A failed/uncertain push may actually have landed. Read back, never retry.
    return verify(args, plan, receipt)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan", help="Read-only Git preflight; write a new local plan")
    plan.add_argument("--repo", required=True)
    plan.add_argument("--source", required=True)
    plan.add_argument("--remote", required=True)
    plan.add_argument("--destination", required=True)
    plan.add_argument("--intent", required=True, choices=("feature", "protected"))
    plan.add_argument("--server-contract", required=True, choices=("ordinary-refs",), help="Acknowledge a known server contract forbidding destination aliases/concurrent alias changes; this flag does not enforce server behavior")
    plan.add_argument("--protected-ref", action="append", default=[])
    plan.add_argument("--output", required=True)
    for name in ("apply", "verify"):
        command = commands.add_parser(name)
        command.add_argument("--repo", required=True)
        command.add_argument("--plan", required=True)
        command.add_argument("--receipt", required=True)
        if name == "apply":
            command.add_argument("--approve", required=True)
            command.add_argument("--authorize", required=True, choices=("feature", "protected"))
    hidden = commands.add_parser("_pre_push", help=argparse.SUPPRESS)
    hidden.add_argument("--context", required=True)
    hidden.add_argument("remote_args", nargs=2)
    args = parser.parse_args()
    try:
        if args.command != "_pre_push":
            require(not any(os.environ.get(key) for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE")), "Unset repository-routing Git environment overrides before publication.")
        result = {"plan": make_plan, "apply": apply, "verify": verify, "_pre_push": hook}[args.command](args)
        if result is not None:
            print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (Stop, OSError, ValueError, KeyError, TypeError):
        # Do not echo arbitrary malformed plan/config values or helper stderr.
        error = sys.exc_info()[1]
        print(str(error) if isinstance(error, Stop) else "Publication stopped: invalid input or local file operation failed.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Publication interrupted; reconcile the receipt before any retry.", file=sys.stderr)
        return 130


def interrupted(_signal, _frame):
    raise KeyboardInterrupt


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, interrupted)
    sys.exit(main())
