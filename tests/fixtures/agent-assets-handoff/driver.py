"""Test-owned fault injection into real installer I/O; never installed or imported by it."""
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys


source, fault = Path(sys.argv.pop(1)), sys.argv.pop(1)
root = Path(os.environ["TEST_AGENT_ASSETS_ROOT"]).resolve(strict=True)
ownership = Path(os.environ["ENVY_AGENT_ASSETS_OWNERSHIP"])
if not ownership.is_relative_to(root) or not Path(os.environ["HOME"]).is_relative_to(root):
    raise SystemExit("driver requires explicitly owned fixture paths")
spec = importlib.util.spec_from_file_location("handoff_candidate", source)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

original_json = module.atomic_json
original_replace = module.os.replace
counts = {"workflow": 0, "site-adapter": 0}
handoff_response_witness = {"lost_link": 0, "lost_checkpoint": 0}

if fault.startswith("ancestor-"):
    cases = {"ancestor-root-0777": (0, 0o777), "ancestor-root-0775": (0, 0o775),
             "ancestor-root-0755": (0, 0o755), "ancestor-root-1777": (0, 0o1777),
             "ancestor-user-1777": (os.geteuid(), 0o1777)}
    if fault not in cases or os.geteuid() == 0:
        raise SystemExit("ancestor model requires its explicit nonroot fixture case")
    ancestor = root / "modeled-ancestor"
    if ownership.parent != ancestor / "private" or ancestor.resolve(strict=True) != ancestor:
        raise SystemExit("ancestor model requires its exact existing fixture anchor")
    original_stat, original_fstat = os.stat, os.fstat
    real = original_stat(ancestor, follow_symlinks=False)
    if not stat.S_ISDIR(real.st_mode) or real.st_uid != os.geteuid() or stat.S_IMODE(real.st_mode) != 0o700:
        raise SystemExit("ancestor model requires a real private owned directory")
    fields = ("st_mode", "st_uid", "st_gid", "st_dev", "st_ino", "st_nlink", "st_size",
              "st_mtime_ns", "st_ctime_ns")
    ancestor_before = {name: getattr(real, name) for name in fields}
    modeled_uid, modeled_mode = cases[fault]
    ancestor_witness = {"uid": modeled_uid, "mode": oct(modeled_mode), "stat": 0, "fstat": 0}

    class AncestorMetadata:
        def __init__(self, info):
            self.info = info
            self.st_uid = modeled_uid
            self.st_mode = stat.S_IFMT(info.st_mode) | modeled_mode

        def __getattr__(self, name):
            return getattr(self.info, name)

    def model_ancestor(info, kind):
        if (info.st_dev, info.st_ino) == (real.st_dev, real.st_ino):
            ancestor_witness[kind] += 1
            return AncestorMetadata(info)
        return info

    def ancestor_stat(*args, **kwargs):
        return model_ancestor(original_stat(*args, **kwargs), "stat")

    def ancestor_fstat(*args, **kwargs):
        return model_ancestor(original_fstat(*args, **kwargs), "fstat")

    module.os.stat, module.os.fstat = ancestor_stat, ancestor_fstat


ordinary_witness = {"checkpoint": 0, "rename": 0, "unlink": 0, "prepared_bytes": []}


def ordinary_checkpoint_fault(path, value, after, previous):
    record = value.get("ordinary_operation")
    prior = previous.get("ordinary_operation") if previous else None
    point = None
    if record:
        point = "prepared" if record["prepared"] is not None else ("progress" if record["cursor"] else "intent")
    elif prior:
        point = "complete"
    if point and fault.startswith("ordinary-"):
        if after:
            ordinary_witness["checkpoint"] += 1
            if point == "prepared":
                ordinary_witness["prepared_bytes"].append(ownership.stat().st_size)
        if fault == f"ordinary-{'after' if after else 'before'}-{point}":
            raise SystemExit(71) if after else OSError(f"synthetic ordinary {point} write failure")
        if after and point == "complete" and fault == "ordinary-lost-complete":
            raise OSError("synthetic ordinary completed response lost")
        if after and point == "prepared" and fault == "ordinary-wrong-cursor-stage":
            changed = json.loads(json.dumps(value))
            changed["ordinary_operation"]["cursor"] += 1
            module.validate_ownership(changed)
            original_json(ownership, changed)
        if after and point == "prepared" and fault in {"ordinary-extra-stage", "ordinary-replaced-stage", "ordinary-missing-stage"}:
            step = record["plan"]["steps"][record["cursor"]]
            stage = Path(step["target"]).with_name(step["stage"])
            if fault == "ordinary-extra-stage":
                extra = stage.with_name(f".{Path(step['target']).name}.agent-assets-ordinary-" + "f" * 24)
                if os.path.lexists(extra):
                    raise SystemExit("ordinary sibling witness collision")
                extra.symlink_to(root / "outside")
            else:
                displaced = root / "observed-ordinary-stage"
                if os.path.lexists(displaced):
                    raise SystemExit("ordinary stage witness collision")
                stage.rename(displaced)
                if fault == "ordinary-missing-stage":
                    return
                if step["kind"] == "link":
                    stage.symlink_to(step["after"])
                else:
                    stage.write_bytes(displaced.read_bytes())
                    stage.chmod(0o755)
        if after and point == "intent" and fault == "ordinary-future-drift":
            step = next(item for item in record["plan"]["steps"][1:] if item["kind"] == "link")
            target = Path(step["target"])
            if os.path.lexists(target):
                target.rename(root / "observed-ordinary-future")
            target.symlink_to(root / "outside")


def checkpoint(path, value):
    previous = json.loads(ownership.read_text()) if fault.startswith("ordinary-") and ownership.is_file() else None
    ordinary_checkpoint_fault(path, value, False, previous)
    record = value.get("handoff") or {}
    if fault == "journal-failure" and record.get("state") == "pending":
        raise OSError("synthetic pending checkpoint failure")
    if fault == "before-checkpoint" and record.get("state") == "complete":
        raise OSError("synthetic final checkpoint failure")
    original_json(path, value)
    ordinary_checkpoint_fault(path, value, True, previous)
    if fault == "lost-checkpoint-response" and record.get("state") == "complete":
        handoff_response_witness["lost_checkpoint"] += 1
        raise OSError("synthetic lost response after completed replacement")
    if fault == "after-pending" and record.get("state") == "pending":
        raise SystemExit(71)
    if fault == "after-complete" and record.get("state") == "complete":
        raise SystemExit(71)


def replace(source_path, target, **kwargs):
    name = Path(target).name
    ordinary = ".agent-assets-ordinary-" in Path(source_path).name
    if ordinary and fault == "ordinary-before-rename":
        raise OSError("synthetic ordinary pre-rename failure")
    if fault == "orphan" and name == "workflow":
        os._exit(72)  # Deliberately bypass cleanup at the owned staging boundary.
    result = original_replace(source_path, target, **kwargs)
    if ordinary:
        ordinary_witness["rename"] += 1
        if fault == "ordinary-after-rename":
            raise SystemExit(71)
        if fault in {"ordinary-identical-foreign", "ordinary-identical-foreign-success"}:
            record = json.loads(ownership.read_text())["ordinary_operation"]
            step = record["plan"]["steps"][record["cursor"]]
            destination = Path(step["target"])
            displaced = root / "observed-ordinary-replacement"
            if os.path.lexists(displaced):
                raise SystemExit("ordinary destination witness collision")
            destination.rename(displaced)
            destination.symlink_to(os.readlink(displaced))
            ordinary_witness["foreign_identity"] = module.entry_identity(destination.lstat())
            if fault == "ordinary-identical-foreign":
                raise OSError("synthetic response lost after independent identical replacement")
            return result
        if fault == "ordinary-lost-rename":
            raise OSError("synthetic ordinary rename response lost")
    if name in counts:
        counts[name] += 1
        if fault == "lost-link-response":
            handoff_response_witness["lost_link"] += 1
            raise OSError("synthetic lost response after symlink replacement")
        if fault == "unrelated-drift" and name == "site-adapter" and counts[name] == 1:
            (root / "unrelated-entry").write_text("synthetic concurrent drift\n")
        if fault == f"after-{name}-{counts[name]}":
            raise SystemExit(71)
    return result


module.atomic_json = checkpoint
module.os.replace = replace
if fault in {"ordinary-before-unlink", "ordinary-after-unlink", "ordinary-lost-unlink"}:
    original_unlink = module.os.unlink

    def ordinary_unlink(path, *args, **kwargs):
        record = json.loads(ownership.read_text()).get("ordinary_operation")
        step = record["plan"]["steps"][record["cursor"]] if record and record["cursor"] < len(record["plan"]["steps"]) else None
        payload = step is not None and step["after"] is None and Path(path).name == Path(step["target"]).name
        if payload and fault == "ordinary-before-unlink":
            raise OSError("synthetic ordinary pre-unlink failure")
        result = original_unlink(path, *args, **kwargs)
        if payload:
            ordinary_witness["unlink"] += 1
            if fault == "ordinary-after-unlink":
                raise SystemExit(71)
            if fault == "ordinary-lost-unlink":
                raise OSError("synthetic ordinary unlink response lost")
        return result

    module.os.unlink = ordinary_unlink
if fault == "without-unix-locking":
    module.fcntl = None  # Capability branch only; do not impersonate native Windows.
if fault in {"launcher-replaced-file", "launcher-replaced-link"}:
    launcher = root / "home/.local/bin/agent-assets"
    original_info = launcher.lstat()
    if (not stat.S_ISREG(original_info.st_mode) or original_info.st_uid != os.geteuid()
            or original_info.st_nlink != 1 or launcher.read_text() != module.launcher_script("envy", source.resolve())):
        raise SystemExit("launcher witness requires the exact owned current launcher")
    displaced = root / "observed-launcher"
    if os.path.lexists(displaced):
        raise SystemExit("launcher witness displacement collision")
    witness = {"injections": 0, "reader": None}
    original_text = Path.read_text
    original_read = os.read
    read_bytes = 0

    def replace_observed_launcher(reader):
        if witness["injections"]:
            return
        if not module.same_inode(original_info, launcher.lstat()):
            raise SystemExit("launcher witness original identity changed")
        launcher.rename(displaced)
        if fault == "launcher-replaced-file":
            launcher.write_text("Foreign launcher: preserve.\n")
        else:
            launcher.symlink_to(root / "outside/sentinel")
        info = launcher.lstat()
        witness.update({"injections": 1, "reader": reader,
                        "entry": {name: getattr(info, name) for name in (
                            "st_mode", "st_uid", "st_gid", "st_dev", "st_ino", "st_nlink", "st_size",
                            "st_mtime_ns", "st_ctime_ns")}})

    def launcher_text(self, *args, **kwargs):
        content = original_text(self, *args, **kwargs)
        if self == launcher:
            replace_observed_launcher("path-read")
        return content

    def launcher_read(fd, count):
        global read_bytes
        content = original_read(fd, count)
        if module.same_inode(original_info, os.fstat(fd)):
            read_bytes += len(content)
            if read_bytes >= original_info.st_size:
                replace_observed_launcher("descriptor-read")
        return content

    Path.read_text = launcher_text
    module.os.read = launcher_read
if fault in {"ordinary-prepare-initial-limit", "ordinary-prepare-prepared-limit", "ordinary-prepare-prepared-over",
             "ordinary-prepare-pending-overflow", "ordinary-prepare-cursor-envelope"}:
    arguments = module.parse_args()
    manager = module.AgentAssets(arguments)
    if manager.ownership["version"] != 2 or manager.ordinary_record():
        raise SystemExit("ordinary capacity fixture requires completed v2 ownership")
    base = dict(manager.ownership)
    base["manifests"] = ["/p"]  # Legal unused historical provenance, not a production padding field.
    plan = manager.make_ordinary_plan(base, "converge")
    replacements = [index for index, step in enumerate(plan["steps"]) if step["after"] is not None]
    if not replacements:
        raise SystemExit("ordinary capacity fixture requires a replacement")
    size = lambda value: len((json.dumps(value, indent=2, sort_keys=True) + "\n").encode())
    initial = module.operation_checkpoint(base, plan)
    prepared = module.operation_checkpoint(base, plan, replacements[-1], "0" * 64)
    selected = initial if fault in {"ordinary-prepare-initial-limit", "ordinary-prepare-pending-overflow"} else prepared
    padding = module.CONTROL_LIMIT - size(selected)
    if fault == "ordinary-prepare-prepared-over":
        padding += 1
    if padding <= 0:
        raise SystemExit("ordinary capacity fixture has no positive padding")
    base["manifests"] = ["/p" + "p" * padding]
    plan = manager.make_ordinary_plan(base, "converge")
    initial = module.operation_checkpoint(base, plan)
    prepared = module.operation_checkpoint(base, plan, replacements[-1], "0" * 64)
    complete = module.operation_complete(base, plan)
    for value in (base, initial, prepared, complete):
        module.validate_ownership(value)
    sizes = {"limit": module.CONTROL_LIMIT, "base": size(base), "initial": size(initial),
             "prepared": size(prepared), "complete": size(complete), "last_replacement": replacements[-1]}
    if fault == "ordinary-prepare-cursor-envelope":
        if replacements[-1] < 10:
            raise SystemExit("cursor fixture must cross a decimal-width boundary")
        _, _, maximum = module.admit_operation(base, plan)
        if maximum != module.CONTROL_LIMIT:
            raise SystemExit("cursor envelope was not admitted at the exact limit")
        sizes["admitted_maximum"] = maximum
    else:
        original_json(ownership, initial if fault == "ordinary-prepare-pending-overflow" else base)
        actual = module.AgentAssets(arguments)
        if actual.ownership != (initial if fault == "ordinary-prepare-pending-overflow" else base):
            raise SystemExit("ordinary capacity fixture did not survive actual bounded read")
    print(json.dumps({"ordinary_capacity": sizes}), flush=True)
    raise SystemExit(0)
if fault == "selection-read-witness":
    selection_witness = {name: 0 for name in (
        "configured_manifests", "compose_manifests", "_load_selection", "_load_assets",
        "link_report", "destination_metadata_issues", "audit_roots_report", "plugin_report")}
    for name in selection_witness:
        owner = module if name in {"configured_manifests", "compose_manifests"} else module.AgentAssets

        def forbidden_selection(*args, _name=name, **kwargs):
            selection_witness[_name] += 1
            raise module.AssetError("synthetic selection inspection forbidden")

        setattr(owner, name, forbidden_selection)
if fault == "staging-read-witness":
    watched = []
    for harness in (".claude", ".agents"):
        for name in ("workflow", "site-adapter"):
            path = root / "home" / harness / "skills" / f".{name}.agent-assets-read-witness"
            if os.path.lexists(path):
                if not stat.S_ISLNK(path.lstat().st_mode) or os.readlink(path) != str(root / "outside"):
                    raise SystemExit("staging witness requires its exact synthetic outside link")
                watched.append(path)
    if len(watched) != 1:
        raise SystemExit("staging witness requires exactly one owned fixture entry")
    watched.append(root / "outside")
    witness = {"following_stat_attempts": 0, "content_open_attempts": 0,
               "scanners": {name: 0 for name in (
                   "link_report", "destination_metadata_issues", "audit_roots_report", "plugin_report")}}
    original_path_stat = Path.stat
    original_path_open = Path.open

    def watched_path(path):
        # Lexical classification must not itself resolve or follow the witness.
        return any(path == entry or path.is_relative_to(entry) for entry in watched)

    def witness_stat(self, *args, **kwargs):
        if kwargs.get("follow_symlinks", True) and watched_path(self):
            witness["following_stat_attempts"] += 1
        return original_path_stat(self, *args, **kwargs)

    def witness_open(self, *args, **kwargs):
        mode = args[0] if args else kwargs.get("mode", "r")
        if watched_path(self) and ("r" in mode or "+" in mode):
            witness["content_open_attempts"] += 1
        return original_path_open(self, *args, **kwargs)

    # Python 3.9 pathlib caches OS accessors; patch the actual Path boundaries.
    Path.stat = witness_stat
    Path.open = witness_open
    for scanner in witness["scanners"]:
        original_scanner = getattr(module.AgentAssets, scanner)

        def witness_scan(self, *args, _name=scanner, _original=original_scanner, **kwargs):
            witness["scanners"][_name] += 1
            return _original(self, *args, **kwargs)

        setattr(module.AgentAssets, scanner, witness_scan)
if fault in {"scaffold-first-appearance", "scaffold-suffix-appearance", "scaffold-anchor-replaced"}:
    original_converge = module.AgentAssets.converge

    def appear_after_preliminary_observation(self):
        anchor = root / "anchor"
        if fault == "scaffold-anchor-replaced":
            displaced = root / "displaced-anchor"
            anchor.rename(displaced)
            anchor.mkdir(mode=0o755)
            injected = [anchor, displaced]
        else:
            first = anchor / "owned-state"
            first.mkdir(mode=0o755)
            injected = [first]
            if fault == "scaffold-suffix-appearance":
                child = first / "child"
                child.mkdir(mode=0o755)
                injected.append(child)
        observations = {}
        for path in injected:
            info = path.lstat()
            observations[str(path)] = {name: getattr(info, name) for name in (
                "st_mode", "st_uid", "st_gid", "st_dev", "st_ino", "st_nlink", "st_size",
                "st_mtime_ns", "st_ctime_ns")}
        print(json.dumps({"injected_scaffolding": observations}), file=sys.stderr)
        return original_converge(self)

    module.AgentAssets.converge = appear_after_preliminary_observation
if fault == "collision":
    module.os.urandom = lambda _length: b"\x00" * 16
    original_symlink = module.os.symlink
    collision_injected = False

    def collide_after_preflight(source_path, target, **kwargs):
        global collision_injected
        if target == ".site-adapter.agent-assets-" + "00" * 16 and not collision_injected:
            parent = kwargs.get("dir_fd")
            expected_parent = root / "home/.claude/skills"
            anchored = os.fstat(parent)
            visible = expected_parent.lstat()
            record = json.loads(ownership.read_text()).get("handoff") or {}
            if (record.get("state") != "pending" or not stat.S_ISDIR(visible.st_mode)
                    or (anchored.st_dev, anchored.st_ino) != (visible.st_dev, visible.st_ino)):
                raise SystemExit("collision requires the pending fixture adapter boundary")
            original_symlink(str(root / "outside"), target, **kwargs)
            collision_injected = True
        return original_symlink(source_path, target, **kwargs)

    module.os.symlink = collide_after_preflight
if fault == "scaffold-race":
    original = module.AgentAssets._converge_locked

    def changed_preflight(self):
        target = root / "home/.claude/skills/common-tool"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("synthetic racing writer\n", encoding="utf-8")
        return original(self)

    module.AgentAssets._converge_locked = changed_preflight
if fault == "busy":
    with module.regular_descriptor(ownership.with_name(ownership.name + ".lock"), lock=True, write=True) as fd:
        module.fcntl.flock(fd, module.fcntl.LOCK_EX | module.fcntl.LOCK_NB)
        result = subprocess.run([sys.executable, "-B", str(source), *sys.argv[1:]], timeout=10)
        raise SystemExit(result.returncode)
if fault == "scaffold-appearance":
    original_mkdir = module.os.mkdir

    def appearing_directory(path, mode=0o777, *, dir_fd=None):
        if path == "child" and dir_fd is not None:
            original_mkdir(path, 0o755, dir_fd=dir_fd)
        return original_mkdir(path, mode, dir_fd=dir_fd)

    module.os.mkdir = appearing_directory
if fault == "control-fifo-race":
    original_open = module.os.open
    if os.rename not in os.supports_dir_fd:
        raise SystemExit("control-fifo-race requires descriptor-relative rename")
    parent_info = root.lstat()
    if (ownership.parent != root or ownership.parent.resolve(strict=True) != root
            or not stat.S_ISDIR(parent_info.st_mode) or parent_info.st_uid != os.geteuid()
            or parent_info.st_mode & 0o022):
        raise SystemExit("control-fifo-race requires its exact owned fixture parent")
    original_info = ownership.lstat()
    if (not stat.S_ISREG(original_info.st_mode) or original_info.st_uid != os.geteuid()
            or original_info.st_nlink != 1):
        raise SystemExit("control-fifo-race requires an owned regular control")
    staged_fifo = ownership.with_name("." + ownership.name + ".control-fifo-race")
    if os.path.lexists(staged_fifo):
        raise SystemExit("control-fifo-race staging collision")
    # macOS Python exposes mkfifo's keyword but does not implement its dir_fd.
    # Prepare the FIFO first; rename it at the original intercepted open boundary.
    os.mkfifo(staged_fifo, 0o600)
    staged_info = staged_fifo.lstat()
    parent_after = root.lstat()
    if (not stat.S_ISFIFO(staged_info.st_mode) or staged_info.st_uid != os.geteuid()
            or staged_info.st_nlink != 1 or stat.S_IMODE(staged_info.st_mode) != 0o600
            or (parent_after.st_dev, parent_after.st_ino) != (parent_info.st_dev, parent_info.st_ino)):
        raise SystemExit("control-fifo-race staging identity changed")
    injected = False

    def changed_control(path, flags, mode=0o777, *, dir_fd=None):
        global injected
        if path == ownership.name and dir_fd is not None and not flags & os.O_CREAT:
            anchored = os.fstat(dir_fd)
            visible_parent = root.lstat()
            current = os.stat(path, dir_fd=dir_fd, follow_symlinks=False)
            staged = os.stat(staged_fifo.name, dir_fd=dir_fd, follow_symlinks=False)
            if (injected or (anchored.st_dev, anchored.st_ino) != (parent_info.st_dev, parent_info.st_ino)
                    or (visible_parent.st_dev, visible_parent.st_ino) != (anchored.st_dev, anchored.st_ino)
                    or (current.st_dev, current.st_ino) != (original_info.st_dev, original_info.st_ino)
                    or not stat.S_ISREG(current.st_mode) or current.st_uid != os.geteuid()
                    or current.st_nlink != 1
                    or (staged.st_dev, staged.st_ino) != (staged_info.st_dev, staged_info.st_ino)
                    or not stat.S_ISFIFO(staged.st_mode) or staged.st_uid != os.geteuid()
                    or staged.st_nlink != 1 or stat.S_IMODE(staged.st_mode) != 0o600
                    or not flags & os.O_NONBLOCK or not flags & os.O_NOFOLLOW):
                raise SystemExit("control-fifo-race intercepted boundary changed")
            os.rename(staged_fifo.name, path, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
            injected = True
            fd = original_open(path, flags, mode, dir_fd=dir_fd)
            opened = os.fstat(fd)
            if (not stat.S_ISFIFO(opened.st_mode)
                    or (opened.st_dev, opened.st_ino) != (staged_info.st_dev, staged_info.st_ino)):
                os.close(fd)
                raise SystemExit("control-fifo-race did not open the injected FIFO")
            print(json.dumps({"control_fifo_race": {
                "target": ownership.name, "injections": 1, "open_returned_fifo": True,
                "nonblocking": True, "nofollow": True,
                "parent": {"st_dev": anchored.st_dev, "st_ino": anchored.st_ino},
                "fifo": {name: getattr(opened, name) for name in (
                    "st_mode", "st_uid", "st_gid", "st_dev", "st_ino", "st_nlink", "st_size",
                    "st_mtime_ns", "st_ctime_ns")}}}), file=sys.stderr, flush=True)
            return fd
        return original_open(path, flags, mode, dir_fd=dir_fd)

    module.os.open = changed_control
if fault in {"prepare-pending-at-limit", "prepare-complete-at-limit", "prepare-oversize-pending"}:
    if ownership != root / "owned.json" or module.CONTROL_LIMIT != 16 * 1024 * 1024:
        raise SystemExit("checkpoint boundary requires its fixed owned store and actual limit")
    arguments = module.parse_args()
    manager = module.AgentAssets(arguments)
    if manager.selected_source_errors() or manager.ownership["version"] != 1:
        raise SystemExit("checkpoint boundary requires a valid predecessor installation")
    prior = manager.ownership
    # Extra v1 fields are legal and retained in the plan; input whitespace is not.
    prior["checkpoint_size_padding"] = ""
    mode, plan = manager.transition_preflight()
    if mode != "legacy" or plan["prior_ownership"] is not prior:
        raise SystemExit("checkpoint boundary requires an actual admitted legacy plan")

    def prospective_checkpoints():
        record = {"version": 1, "state": "pending", "plan_sha256": module.digest_json(plan), "plan": plan}
        pending = {"version": 2, "links": dict(prior["links"]),
                   "manifests": prior.get("manifests", []),
                   "launcher": prior.get("launcher", str(manager.launcher)), "handoff": record}
        links = dict(prior["links"])
        links.update({item["target"]: item["new_source"] for item in plan["links"] + plan["adapter_links"]})
        complete = {"version": 2, "manifests": [str(path) for path in manager.manifest_paths],
                    "links": links, "launcher": str(manager.launcher),
                    "handoff": {**record, "state": "complete"}}
        return pending, complete

    def encoded_size(value):
        return len((json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8"))

    pending, complete = prospective_checkpoints()
    bound = complete if fault == "prepare-complete-at-limit" else pending
    padding = module.CONTROL_LIMIT - encoded_size(bound)
    if padding <= 0 or encoded_size(complete) <= encoded_size(pending):
        raise SystemExit("checkpoint boundary requires positive padding and completion growth")
    prior["checkpoint_size_padding"] = "x" * padding
    pending, complete = prospective_checkpoints()
    for value in (prior, pending, complete):
        module.validate_ownership(value)
    sizes = {"prior": encoded_size(prior), "pending": encoded_size(pending),
             "complete": encoded_size(complete), "limit": module.CONTROL_LIMIT}
    if not sizes["prior"] < sizes["pending"] <= sizes["limit"]:
        raise SystemExit("checkpoint boundary is not admissible")
    if fault == "prepare-complete-at-limit":
        if sizes["complete"] != sizes["limit"]:
            raise SystemExit("completion boundary is not exact")
    elif sizes["pending"] != sizes["limit"] or sizes["complete"] <= sizes["limit"]:
        raise SystemExit("pending boundary is not exact")
    existing_pending = fault == "prepare-oversize-pending"
    original_json(ownership, pending if existing_pending else prior)
    # Reopen through the real bounded reader and recompute the complete plan.
    actual = module.AgentAssets(arguments)
    actual_mode, actual_plan = actual.transition_preflight()
    if actual_mode != ("pending" if existing_pending else "legacy") or actual_plan != plan:
        raise SystemExit("checkpoint boundary did not survive actual admission")
    print(json.dumps({"checkpoint_boundary": {**sizes, "padding_bytes": padding,
                                             "admitted_mode": actual_mode}}), flush=True)
    raise SystemExit(0)
if fault == "selection-read-witness":
    try:
        result = module.main()
    finally:
        print(json.dumps({"selection_read_witness": selection_witness}), file=sys.stderr, flush=True)
    raise SystemExit(result)
if fault == "staging-read-witness":
    try:
        result = module.main()
    finally:
        print(json.dumps({"staging_read_witness": witness}), file=sys.stderr, flush=True)
    raise SystemExit(result)
if fault in {"launcher-replaced-file", "launcher-replaced-link"}:
    try:
        result = module.main()
    finally:
        print(json.dumps({"launcher_removal_witness": witness}), file=sys.stderr, flush=True)
    raise SystemExit(result)
if fault.startswith("ordinary-"):
    try:
        result = module.main()
    finally:
        print(json.dumps({"ordinary_witness": ordinary_witness}), file=sys.stderr, flush=True)
    raise SystemExit(result)
if fault in {"lost-link-response", "lost-checkpoint-response"}:
    try:
        result = module.main()
    finally:
        print(json.dumps({"handoff_response_witness": {**handoff_response_witness, "renames": counts}}),
              file=sys.stderr, flush=True)
    raise SystemExit(result)
if fault.startswith("ancestor-"):
    try:
        result = module.main()
    finally:
        actual = original_stat(ancestor, follow_symlinks=False)
        ancestor_witness["real_metadata_unchanged"] = {
            name: getattr(actual, name) for name in fields} == ancestor_before
        print(json.dumps({"ancestor_witness": ancestor_witness}), file=sys.stderr, flush=True)
    raise SystemExit(result)
raise SystemExit(module.main())
