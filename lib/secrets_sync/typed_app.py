"""Typed interactive inventory and honest multi-destination transactions."""
from contextlib import ExitStack
import os
from pathlib import Path

from .crypto import Crypto, MAX_CIPHER, MAX_PLAIN
from .errors import Cancelled, CommittedError, ProviderUnavailable, SyncError
from .files import SecureFile, locks
from .github import Backend, ORPHAN_WARNING, Saved
from .inventory import Inventory, Ref
from .ssh import Backend as SSHBackend
from .git_credentials import Backend as GitBackend
from .ssh_format import Material, validate_native
from .model import apply_map, classify, parse
from .terminal import Terminal, signals
from .ui import counts


def discover_provider(policy, factory, kind, terminal):
    """Missing optional tooling blocks that store, never invents empty logins."""
    if policy is None or not policy.accounts:
        return None, {}
    try:
        bound = policy.bind()
    except ProviderUnavailable as error:
        name = "saved-gh" if kind == "github" else "native Git"
        reason = f"{name} provider unavailable: {error}"
        terminal.emit(reason + ". Other providers remain independent.\n")
        return None, {Ref(kind, host=host, account=account): Saved("blocked", reason=reason)
                      for host, account in policy.accounts}
    backend = factory(bound)
    return backend, backend.discover()


def transaction(config, operation):
    resources = {"files": [], "backends": []}
    try:
        return _transaction(config, operation, resources)
    except CommittedError:
        raise
    except BaseException:
        if any(target.committed for target in resources["files"]) or any(b.mutation_started for b in resources["backends"]):
            raise CommittedError("destination write started; completion uncertain; compare again before retrying") from None
        raise


def _transaction(config, operation, resources):
    from .app import check_index
    with ExitStack() as stack:
        terminal = Terminal()
        stack.callback(terminal.close)
        stack.enter_context(signals())
        local = SecureFile(config.local, MAX_PLAIN)
        stack.callback(local.close)
        store = SecureFile(config.store, MAX_CIPHER)
        stack.callback(store.close)
        resources["files"].extend((local, store))
        paths = [config.local, config.store]
        paths.extend(provider.directory for provider in (config.github, config.ssh) if provider is not None)
        if config.git and config.git.backend == "osxkeychain":
            # Stable context locks across repositories, independent of gh locks.
            paths.extend("git-https:" + host + "/" + account for host, account in config.git.accounts)
        stack.enter_context(locks(paths, config.lock_dir))
        before_local, before_store = local.snapshot(), store.snapshot()
        if operation != "export" and before_store.signature is None:
            raise SyncError("repository secrets missing; first export from an initialized host")
        check_index(config)
        crypto = Crypto()
        terminal.emit("Do not edit inputs, run Git operations, or change authentication/key files during sync.\n")
        identity = terminal.identity()
        recipient = crypto.recipient(identity)
        repository = (crypto.decrypt_inventory(before_store.content, identity, recipient)
                      if before_store.signature is not None else Inventory({}))
        document = parse(before_local.content, config.blocked)
        backend, saved = discover_provider(config.github, Backend, "github", terminal)
        ssh_backend = SSHBackend(config.ssh, config.lock_dir) if config.ssh else None
        git_backend, git_saved = discover_provider(
            config.git if config.git and config.git.backend == "osxkeychain" else None,
            GitBackend, "git", terminal)
        resources["backends"] = [b for b in (backend, ssh_backend, git_backend) if b]
        saved.update(git_saved)
        if config.git and config.git.backend == "gh":
            for host, account in config.git.accounts:
                shared = Ref("github", host=host, account=account)
                terminal.emit(f"Git HTTPS {host}/{account}: " +
                              (f"shared saved-gh record ({shared.label}); no duplicate Git entry.\n" if shared in saved else
                               "saved-gh provider not configured for this context; Git store not inspected.\n"))
        if ssh_backend:
            saved.update(ssh_backend.discover())
            for name, reason in sorted(ssh_backend.issues.items()):
                terminal.emit(f"Unavailable SSH key {name}: {reason}.\n")
        local_values = {Ref("environment", name=name): value for name, value in document.values.items()}
        local_values.update({ref: value.token for ref, value in saved.items() if value.state == "available"})
        local_inventory = Inventory(local_values, 3 if any(ref.kind in {"git", "ssh"} for ref in saved) else 2 if saved else 1)
        source, destination = ((local_inventory, repository) if operation == "export" else (repository, local_inventory))
        blocked = repository.blocked(config.github.accounts if config.github else (), config.blocked,
                                     github_enabled=config.github is not None,
                                     git_accounts=config.git.accounts if git_backend else (),
                                     ssh_names=config.ssh.filenames if config.ssh else ())
        blocked.update(Ref("environment", name=name) for name in document.blocked)
        blocked.update(ref for ref, value in saved.items() if value.state == "blocked")
        candidates = {Ref("environment", name=name) for name in document.assignments}
        candidates.update(ref for ref, value in saved.items() if value.state == "blocked")
        states = classify(source.entries, destination.entries, blocked, candidates)
        refs = {ref.label: ref for ref in states}
        rows = {ref.label: state for ref, state in states.items()}
        reasons = {}
        for ref in blocked:
            if ref.kind == "environment":
                reasons[ref.label] = document.block_reasons.get(ref.name, {"blocked environment name"})
            elif ref.kind in {"github", "git"}:
                reasons[ref.label] = {saved[ref].reason if ref in saved else "account is not configured for sync"}
            else:
                reasons[ref.label] = {"credential provider is not configured for sync"}
        permitted, destinations = {}, {}
        for label, ref in refs.items():
            permitted[label] = {"add", "overwrite", "remove"}
            if operation != "export" and ref.kind in {"github", "git", "ssh"}:
                permitted[label].discard("remove")
            if operation == "setup":
                permitted[label] &= {"add"}
                if ref.kind == "environment" and before_local.signature is not None:
                    rows[label] = "blocked"
                    reasons[label] = {"local file exists; use merge"}
            destinations[label] = (str(config.store) if operation == "export" else str(config.local)
                                   if ref.kind == "environment" else
                                   f"{config.github.directory} ({ref.host}/{ref.account})" if ref.kind == "github" and config.github else
                                   f"macOS Git Keychain (HTTPS {ref.host}/{ref.account}; empty path)" if ref.kind == "git" and git_backend else
                                   ssh_backend.destination(ref, source.entries.get(ref, destination.entries.get(ref)))
                                   if ref.kind == "ssh" and ssh_backend else
                                   "unavailable: no active credential provider")
        if ssh_backend and operation != "export":
            for ref, value in source.entries.items():
                if ref.kind == "ssh":
                    rows[ref.label] = ssh_backend.compare(ref, value)
                    if rows[ref.label] == "blocked":
                        reasons[ref.label] = {"SSH target unavailable, unconfigured, or would mismatch an existing private key; choose name ID BASENAME"}
        local_description = str(config.local)
        if config.github:
            local_description += f"; saved gh logins in {config.github.directory}"
        if config.ssh:
            local_description += f"; SSH file keys in {config.ssh.directory}"
        if git_backend:
            local_description += "; configured native Git Keychain contexts"
        source_path, target_path = ((local_description, str(config.store)) if operation == "export"
                                    else (str(config.store), local_description))
        actions = None
        def rename(label, name):
            ref = refs[label]
            if not ssh_backend or operation == "export" or ref.kind != "ssh" or ref not in source.entries:
                raise SyncError("only incoming SSH keys can choose a target filename")
            ssh_backend.rename(ref, source.entries[ref], name)
            rows[label] = ssh_backend.compare(ref, source.entries[ref])
            destinations[label] = ssh_backend.destination(ref, source.entries[ref])
            if rows[label] != "blocked":
                blocked.discard(ref)
                reasons.pop(label, None)

        while True:
            actions = terminal.selection(rows, operation, source_path, target_path, reasons, actions,
                                         destinations=destinations, permitted=permitted,
                                         rename=rename if ssh_backend and operation != "export" else None)
            if not actions:
                terminal.emit("No changes.\n")
                return 0
            typed_actions = {refs[label]: action for label, action in actions.items()}
            # SSH import comparisons are path-aware; a different fingerprint at
            # the same filename is an overwrite, not an inventory addition.
            inventory_actions = {ref: action for ref, action in typed_actions.items()
                                 if operation == "export" or ref.kind != "ssh"}
            candidate_inventory = destination.reconcile(source, inventory_actions, blocked=blocked, identity=identity)
            gh_actions = {ref: action for ref, action in typed_actions.items() if ref.kind == "github"}
            git_actions = {ref: action for ref, action in typed_actions.items() if ref.kind == "git"}
            ssh_actions = {ref: action for ref, action in typed_actions.items() if ref.kind == "ssh"}
            warnings = []
            if operation != "export" and (gh_actions or git_actions or ssh_actions):
                warnings.append("Native credential updates are not atomic; interruption can leave partial changes.")
                warnings.append("Commit order: GitHub accounts, native Git credentials, SSH file pairs, then the environment file.")
                if any(saved[ref].state == "absent" for ref in gh_actions):
                    warnings.append(ORPHAN_WARNING)
            for ref, action in sorted(ssh_actions.items()):
                if operation == "export":
                    if action != "remove":
                        material = Material.decode(source.entries[ref])
                        if material.private is not None:
                            warnings.append(f"PRIVATE SSH key {ref.name}; "
                                            + ("passphrase protection retained" if material.protected else "UNPROTECTED")
                                            + ". Encrypted exports persist in Git history if published.")
                else:
                    warnings.extend(ssh_backend.warnings(ref, source.entries[ref], action))
            if terminal.confirm(operation, target_path, recipient, actions, destinations=destinations, warnings=warnings):
                break

        outcomes = {label: "not attempted" for label in actions}
        current = []

        def recheck():
            local.recheck(before_local)
            store.recheck(before_store)
            with config.manifest.open("rb") as handle:
                if handle.read(len(config.manifest_bytes) + 1) != config.manifest_bytes:
                    raise SyncError("configuration changed during sync")
            check_index(config)
            if backend:
                backend.recheck()
            if ssh_backend:
                ssh_backend.recheck()
            if git_backend:
                git_backend.recheck()

        def report():
            terminal.emit("\nOUTCOMES\n")
            for label in sorted(outcomes):
                terminal.emit(f"  {outcomes[label]}\t{label}\n")

        try:
            recheck()
            # All selected private keys are natively verified after confirmation
            # and before any write. Passphrases remain ephemeral stdin/TTY input.
            for ref, action in sorted(ssh_actions.items()):
                if action == "remove":
                    continue
                current = [ref.label]
                material = Material.decode(source.entries[ref])
                passphrase = ""
                if material.protected:
                    terminal.emit(f"Validate protected SSH key {ref.name}; passphrase is not saved.\n")
                    passphrase = terminal.hidden("SSH passphrase (hidden): ", encoding="utf-8")
                try:
                    if operation == "export":
                        validate_native(material, passphrase, staging_parent=config.lock_dir)
                    else:
                        ssh_backend.preflight(ref, source.entries[ref], action, passphrase=passphrase)
                finally:
                    passphrase = ""
            if operation == "export":
                candidate = crypto.encrypt_inventory(candidate_inventory, identity, recipient)
                current = list(actions)
                store.install(candidate, before_store, recheck)
                for label in current:
                    outcomes[label] = "applied"
            else:
                env_actions = {ref.name: action for ref, action in typed_actions.items() if ref.kind == "environment"}
                env_source = {ref.name: value for ref, value in source.entries.items() if ref.kind == "environment"}
                candidate = None
                if env_actions:
                    candidate = document.render(env_source, env_actions)
                    if parse(candidate, config.blocked).values != apply_map(env_source, document.values, env_actions):
                        raise SyncError("local candidate validation failed")
                # Complete every preflight before any destination mutation.
                for ref, action in sorted(gh_actions.items()):
                    current = [ref.label]
                    backend.preflight(ref, source.entries[ref], action)
                for ref, action in sorted(git_actions.items()):
                    current = [ref.label]
                    git_backend.preflight(ref, source.entries[ref], action)
                recheck()
                for ref, action in sorted(gh_actions.items()):
                    current = [ref.label]
                    recheck()
                    backend.commit(ref, source.entries[ref], action)
                    outcomes[ref.label] = "applied"
                for ref, action in sorted(git_actions.items()):
                    current = [ref.label]
                    recheck()
                    git_backend.commit(ref, source.entries[ref], action)
                    outcomes[ref.label] = "applied"
                for ref, action in sorted(ssh_actions.items()):
                    current = [ref.label]
                    recheck()
                    ssh_backend.commit(ref, source.entries[ref], action)
                    outcomes[ref.label] = "applied"
                if candidate is not None:
                    current = [Ref("environment", name=name).label for name in env_actions]
                    local.install(candidate, before_local, recheck)
                    for label in current:
                        outcomes[label] = "applied"
        except BaseException as error:
            uncertain = (isinstance(error, CommittedError) or local.committed or store.committed
                         or any(b.mutation_started and b.mutation_ref.label in current for b in resources["backends"]))
            for label in current:
                if outcomes[label] != "applied":
                    outcomes[label] = "uncertain" if uncertain else "failed"
            if not current:
                outcomes[next(iter(actions))] = "failed"
            try:
                report()
            except BaseException:
                # A closed terminal or another handled signal cannot turn a
                # partial native commit into a pre-commit failure exit status.
                if uncertain or "applied" in outcomes.values():
                    raise CommittedError("partial or uncertain completion; outcome report interrupted") from None
                raise SyncError("selected changes refused before commit; outcome report unavailable") from None
            if uncertain or "applied" in outcomes.values():
                raise CommittedError("partial or uncertain completion; start a fresh comparison before retrying") from None
            if isinstance(error, Cancelled):
                raise
            raise SyncError("selected changes refused before commit; no destination changed") from None
        try:
            report()
            terminal.emit(f"Changes: {counts(actions)}.\n")
            if operation == "export":
                terminal.emit("Local secrets and saved logins unchanged. Git commit/push not performed.\n")
            else:
                terminal.emit("Repository ciphertext unchanged.\n")
                if local.committed:
                    terminal.emit(f"Updated {config.local}; permissions 0600.\n")
                    if str(config.local) == os.environ.get("ENVY_SECRETS_FILE", str(Path.home() / ".secrets")):
                        terminal.emit("Start a fresh shell to load this local secrets file.\n")
        except BaseException:
            raise CommittedError("selected destinations committed; completion report interrupted") from None
        return 0
