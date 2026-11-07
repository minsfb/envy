"""Public command/lifecycle integration. No secret input from argv or env."""
from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass
import os
from pathlib import Path
import stat
import sys

from .crypto import Crypto, MAX_CIPHER, MAX_PLAIN, NAME, envelope, strict_json, run_child
from .errors import Cancelled, CommittedError, ProviderUnavailable, SyncError, UsageError
from .files import SecureFile, locks
from .model import parse, classify, apply_map
from .terminal import Terminal, signals
from .ui import completion_view
from .github_config import GitHubPolicy, parse_github
from .ssh_config import SSHConfig, parse_ssh
from .git_config import GitPolicy, parse_git

HELP = "usage: envy secrets {setup|merge|export} [--help] (interactive-sync mode)"


def settings():
    raw = os.environ.get("ENVY_MANIFEST")
    if not raw:
        raise UsageError("ENVY_MANIFEST is unavailable")
    path = Path(raw)
    with path.open("rb") as handle:
        content = handle.read(2 * 1024 * 1024 + 1)
    if len(content) > 2 * 1024 * 1024:
        raise UsageError("manifest exceeds size limit")
    document = strict_json(content)
    if not isinstance(document, dict) or not isinstance(document.get("settings", {}), dict):
        raise UsageError("invalid manifest settings")
    value = document.get("settings", {}).get("secrets", {})
    if not isinstance(value, dict):
        raise UsageError("invalid secrets settings")
    return value, path, content


def configured_mode():
    value, _, _ = settings()
    mode = value.get("mode", "recovery")
    if mode not in {"recovery", "interactive-sync"}:
        raise UsageError("unsupported secrets mode")
    return mode


def clean_path(raw):
    if not isinstance(raw, str) or not raw or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in raw):
        raise UsageError("invalid configured path")
    path = Path(raw).expanduser()
    if not path.is_absolute() or ".." in path.parts:
        raise UsageError("configured path must be absolute without traversal")
    return path


@dataclass(repr=False)
class Config:
    local: Path
    root: Path
    store: Path
    blocked: set[str]
    manifest: Path
    manifest_bytes: bytes
    lock_dir: Path
    github: GitHubPolicy | None = None
    ssh: SSHConfig | None = None
    git: GitPolicy | None = None


def configuration():
    value, path, content = settings()
    if set(value) - {"mode", "target", "sync", "age", "sources"}:
        raise UsageError("unsupported secrets settings")
    sync = value.get("sync")
    if not isinstance(sync, dict) or set(sync) - {"repository_root", "ciphertext", "blocked_names", "github", "ssh", "git"}:
        raise UsageError("invalid sync configuration")
    root = clean_path(sync.get("repository_root")).resolve(strict=True)
    if not root.is_dir() or not (root / ".git").exists():
        raise UsageError("sync repository must be an existing Git checkout")
    relative = sync.get("ciphertext")
    if (not isinstance(relative, str) or not relative or Path(relative).is_absolute()
            or any(part in {"..", ".git"} for part in Path(relative).parts)
            or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in relative)):
        raise UsageError("ciphertext must be a repository-relative path without traversal")
    store = root / relative
    local = clean_path(value.get("target", "~/.secrets"))
    if local.is_relative_to(root) or local == store:
        raise UsageError("plaintext target must be outside the repository")
    # Compare declared protected paths without opening any of them.
    protected = []
    age = value.get("age", {})
    if isinstance(age, dict) and isinstance(age.get("identity_file"), str):
        protected.append(Path(age["identity_file"]).expanduser())
    for source in value.get("sources", []) if isinstance(value.get("sources", []), list) else []:
        if isinstance(source, dict) and isinstance(source.get("path"), str):
            protected.append(Path(source["path"]).expanduser())
    if local in protected or store in protected:
        raise UsageError("sync target overlaps recovery material")
    blocked = sync.get("blocked_names", [])
    if (not isinstance(blocked, list) or any(not isinstance(name, str) or not NAME.fullmatch(name) for name in blocked)
            or len(blocked) != len(set(blocked))):
        raise UsageError("invalid blocked entry names")
    state = clean_path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
    lock_dir = state / "envy/secrets-sync"
    if lock_dir.is_relative_to(root):
        raise UsageError("sync state must be outside repository")
    github = (parse_github(sync["github"], repository=root, local=local, protected=protected)
              if "github" in sync else None)
    if github:
        blocked = set(blocked) | github.blocked_names
    ssh = (parse_ssh(sync["ssh"], repository=root, local=local,
                     protected=(*protected, *([github.directory] if github else []), lock_dir))
           if "ssh" in sync else None)
    git = parse_git(sync["git"]) if "git" in sync else None
    return Config(local, root, store, set(blocked), path, content, lock_dir, github, ssh, git)


def check_index(config):
    env = {"PATH": "/usr/bin:/bin", "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_OPTIONAL_LOCKS": "0", "LANG": "C"}
    result = run_child(["/usr/bin/git", "-c", "core.fsmonitor=false", "ls-files",
                        "--unmerged", "--", str(config.store.relative_to(config.root))],
                       b"", environment=env, cwd=config.root, limit=65536)
    if result:
        raise SyncError("ciphertext has an unresolved Git conflict")


def transaction(config, operation):
    if any(provider is not None for provider in (config.github, config.ssh, config.git)):
        from .typed_app import transaction as typed_transaction
        return typed_transaction(config, operation)
    targets = []
    try:
        return _transaction(config, operation, targets)
    except BaseException:
        if any(target.committed for target in targets):
            raise CommittedError("target committed; completion/durability uncertain; do not blindly retry") from None
        raise


def _transaction(config, operation, targets):
    with ExitStack() as stack:
        terminal = Terminal()
        stack.callback(terminal.close)
        stack.enter_context(signals())
        local = SecureFile(config.local, MAX_PLAIN)
        stack.callback(local.close)
        store = SecureFile(config.store, MAX_CIPHER)
        stack.callback(store.close)
        targets.extend((local, store))
        stack.enter_context(locks((config.local, config.store), config.lock_dir))
        before_local, before_store = local.snapshot(), store.snapshot()
        if operation == "setup" and before_local.signature is not None:
            raise SyncError("local secrets already exist; use merge")
        if operation != "setup" and before_local.signature is None:
            raise SyncError("local secrets missing; use setup")
        if operation != "export" and before_store.signature is None:
            raise SyncError("repository secrets missing; first export from an initialized host")
        check_index(config)
        crypto = Crypto()
        terminal.emit("Do not edit these inputs or run Git operations during sync.\n")
        identity = terminal.identity()
        recipient = crypto.recipient(identity)
        repository = (crypto.decrypt(before_store.content, identity, recipient)
                      if before_store.signature is not None else {})
        document = parse(before_local.content, config.blocked)
        source, destination = ((document.values, repository) if operation == "export"
                               else (repository, document.values))
        rows = classify(source, destination, document.blocked, document.assignments)
        target = store if operation == "export" else local
        source_path = local.path if operation == "export" else store.path
        actions = None
        while True:
            actions = terminal.selection(rows, operation, source_path, target.path, document.block_reasons, actions)
            if not actions:
                terminal.emit("No changes.\n")
                return 0
            for name, action in actions.items():
                if action in {"add", "overwrite"} and identity in source[name]:
                    raise SyncError("unlocking identity cannot be synchronized")
            if terminal.confirm(operation, target.path, recipient, actions):
                break

        def recheck():
            local.recheck(before_local)
            store.recheck(before_store)
            with config.manifest.open("rb") as handle:
                if handle.read(len(config.manifest_bytes) + 1) != config.manifest_bytes:
                    raise SyncError("configuration changed during sync")
            check_index(config)

        recheck()
        if operation == "export":
            candidate = crypto.encrypt(apply_map(source, destination, actions), identity, recipient)
        else:
            candidate = document.render(source, actions)
            parsed = parse(candidate, config.blocked)
            expected = apply_map(source, destination, actions)
            if parsed.values != expected:
                raise SyncError("local candidate validation failed")
        target.install(candidate, before_store if operation == "export" else before_local, recheck)
        try:
            shell_path = os.environ.get("ENVY_SECRETS_FILE", str(Path.home() / ".secrets"))
            terminal.emit(completion_view(operation, target.path, actions, shell_loaded=str(config.local) == shell_path))
        except BaseException:
            raise CommittedError("target committed; completion uncertain") from None
        return 0


def routine(config, action):
    if action == "remove":
        print("Retaining user-owned secrets, ciphertext, and recovery material")
        return 0
    Crypto()  # availability only; never executes a crypto tool here
    for name, policy in (("saved-gh", config.github), ("native Git", config.git)):
        if policy is None or not policy.accounts:
            continue
        if name == "native Git" and policy.backend == "gh":
            print("secrets: Git HTTPS delegates to the configured saved-gh provider; no separate Git store")
            continue
        try:
            policy.bind()  # public executable/CA metadata only, never a backend
        except ProviderUnavailable as error:
            print(f"secrets: {name} provider unavailable: {error}; other providers remain independent")
        else:
            print(f"secrets: {name} tooling present; native compatibility and saved credentials not checked")
    with ExitStack() as stack:
        local = SecureFile(config.local, MAX_PLAIN)
        stack.callback(local.close)
        store = SecureFile(config.store, MAX_CIPHER)
        stack.callback(store.close)
        local_snapshot, encrypted = local.snapshot(), store.snapshot()
        if encrypted.signature is not None:
            envelope(encrypted.content)
        if local_snapshot.signature is None:
            print("secrets: setup required; run envy secrets setup explicitly")
            if any(provider is not None for provider in (config.github, config.ssh, config.git)):
                print("secrets: provider policy checked; credentials are not inspected")
                return 0
            return 0 if action == "converge" else 1
        mode = stat.S_IMODE(local_snapshot.signature[2])
        if mode != 0o600:
            if action != "converge":
                raise SyncError("local secrets permissions must be 0600")
            fd = os.open(local.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=local.parent)
            try:
                if (os.fstat(fd).st_dev, os.fstat(fd).st_ino) != local_snapshot.signature[:2]:
                    raise SyncError("local secrets changed before permission repair")
                os.fchmod(fd, 0o600)
            finally:
                os.close(fd)
        print("secrets: local file protected; decryption and equality not checked")
        if encrypted.signature is None:
            print("secrets: repository export has not been performed")
        return 0


def dispatch(argv):
    action = argv[0] if argv else "converge"
    if action in {"help", "--help", "-h"} or argv[1:] in (["--help"], ["-h"]):
        print(HELP)
        return 0
    try:
        if len(argv) > 1 or action not in {"setup", "merge", "export", "converge", "verify", "doctor", "remove"}:
            raise UsageError("unsupported secrets action or arguments")
        config = configuration()
        if action in {"setup", "merge", "export"}:
            return transaction(config, action)
        return routine(config, action)
    except (KeyboardInterrupt, EOFError):
        print("secrets: cancelled; no target committed", file=sys.stderr)
        return 130
    except SyncError as error:
        print(f"secrets: {error}", file=sys.stderr)
        return error.code
    except Exception:
        print("secrets: operation failed; diagnostic details suppressed", file=sys.stderr)
        return 1
