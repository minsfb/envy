#!/usr/bin/env python3
"""Own includes, not host startup files. Called under envy's lifecycle lock.

Prepare before replacing the old composed source; converge only after chezmoi
has installed the included files. The journal records hashes and ownership,
never copies of startup-file contents. Interrupted writes can be resumed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import tempfile

import paths

BEGIN = b"# >>> envy >>>\n"
END = b"# <<< envy <<<\n"
LEGACY = {".bashrc": "dot_bashrc.tmpl", ".zshrc": "dot_zshrc.tmpl",
          ".tmux.conf": "dot_tmux.conf.tmpl"}


class IntegrationError(RuntimeError):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_file(path):
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise IntegrationError(f"Expected a regular file: {path}")
    return path.read_bytes() if path.exists() else b""


def atomic_write(path, contents, mode=0o600):
    read_file(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as handle:
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def strip_block(contents, entry=None):
    if b"# >>> envy >>>" not in contents and b"# <<< envy <<<" not in contents:
        return contents
    if contents.count(BEGIN) != 1 or contents.count(END) != 1:
        raise IntegrationError("Malformed or duplicate envy startup markers")
    start, end = contents.index(BEGIN), contents.index(END) + len(END)
    if end <= start or (start and contents[start - 1:start] != b"\n"):
        raise IntegrationError("Malformed envy startup block")
    if entry is None or contents[start:end] != entry["block"].encode():
        raise IntegrationError("Unowned or edited envy startup block; inspect it before retrying")
    # Undo only the separator we inserted for a file lacking a final newline.
    if entry.get("separator") and start and contents[start - 1:start] == b"\n":
        start -= 1
    return contents[:start] + contents[end:]


class Integration:
    def __init__(self, home=None, state=None, config=None, source=None, defaults=None, zdotdir=None):
        self.home = (home or paths.home()).resolve()
        self.state = state or paths.state_dir()
        self.config = config or paths.xdg_config_home() / "envy"
        self.source = source or paths.data_dir() / "chezmoi-source"
        self.backup = self.state / "backups/chezmoi"
        self.ledger = self.state / "startup-integration.json"
        self.defaults = defaults or {}
        self.zdotdir = Path(zdotdir or os.environ.get("ZDOTDIR") or self.home)

    def load(self):
        raw = read_file(self.ledger)
        value = json.loads(raw) if raw else {"version": 1, "entries": {}, "pending": {}}
        if value.get("version") != 1 or not isinstance(value.get("entries"), dict) or not isinstance(value.get("pending"), dict):
            raise IntegrationError("Invalid startup integration ledger")
        return value

    def save(self, value):
        atomic_write(self.ledger, (json.dumps(value, indent=2, sort_keys=True) + "\n").encode())

    def target(self, relative):
        target = self.home / relative
        if not target.is_relative_to(self.home) or target.parent.resolve() != target.parent:
            raise IntegrationError(f"Startup path must have real directories inside HOME: {target}")
        if ".." in Path(relative).parts:
            raise IntegrationError("Invalid startup path")
        read_file(target)
        return target

    def blocks(self):
        profile = next((name for name in (".bash_profile", ".bash_login", ".profile")
                        if (self.home / name).exists() or (self.home / name).is_symlink()), ".bash_profile")
        try:
            zdir = self.zdotdir.relative_to(self.home)
        except ValueError:
            raise IntegrationError("ZDOTDIR must be inside HOME for managed startup integration") from None
        bash = shlex.quote(str(self.config / "bashrc"))
        zsh = shlex.quote(str(self.config / "zshrc"))
        tmux = str(self.config / "tmux.conf").replace("'", "'\"'\"'")
        bodies = {
            ".bashrc": f'_ENVY_HOST_BASHRC_SEEN=1\nif [ -r {bash} ]; then . {bash}; fi\n',
            profile: f'''if [ -n "${{BASH_VERSION:-}}" ]; then
    case $- in
        *i*)
            if [ "${{_ENVY_HOST_BASHRC_SEEN:-}}" != 1 ] && [ -r "$HOME/.bashrc" ]; then
                . "$HOME/.bashrc"
            fi
            _ENVY_LOGIN_READY=1
            if [ -r {bash} ]; then . {bash}; fi
            unset _ENVY_LOGIN_READY
            ;;
    esac
fi
''',
            str(zdir / ".zshrc"): f'if [ -r {zsh} ]; then . {zsh}; fi\n',
            str(zdir / ".zlogin"): f'''_ENVY_LOGIN_READY=1
if [ -r {zsh} ]; then . {zsh}; fi
unset _ENVY_LOGIN_READY
''',
            ".tmux.conf": f"source-file -q '{tmux}'\n",
        }
        return {name: (BEGIN + body.encode() + END).decode() for name, body in bodies.items()}

    def listed(self, name):
        raw = read_file(self.backup / name)
        return set(raw.decode().splitlines())

    def written_targets(self):
        if not self.source.exists():
            return set()
        result = subprocess.run(["chezmoi", "state", "get-bucket", "--bucket", "entryState",
                                 "--source", str(self.source)], capture_output=True)
        if result.returncode:
            return set()
        value = json.loads(result.stdout or b"{}")
        return set(value) if isinstance(value, dict) else set()

    def original(self, relative, contents, written):
        """Return a base path, or None to retain the current host contents."""
        if relative not in LEGACY:
            return None
        target = self.target(relative)
        original = self.backup / "files" / relative
        existed = relative in self.listed("existing.list")
        absent = relative in self.listed("absent.list")
        if existed and original.exists() and contents == read_file(original):
            return None  # A failed first apply left the host original intact.
        legacy_source = self.source / LEGACY[relative]
        if legacy_source.is_file():
            rendered = subprocess.run(["chezmoi", "cat", str(target), "--source", str(self.source)],
                                      capture_output=True)
            if rendered.returncode == 0 and contents == rendered.stdout:
                if existed and original.exists():
                    return str(original)
                if absent:
                    return ""  # Envy originally created the entire file.
                raise IntegrationError(f"Missing original-file ownership for {target}")
        if str(target) in written or b"managed by envy/chezmoi" in contents[:200]:
            raise IntegrationError(f"Customized legacy startup file: {target}; reconcile it with its recorded original before upgrading")
        return None

    def prepare(self):
        value = self.load()
        if value["pending"]:
            return  # Preserve the pre-composition migration decision on retry.
        blocks = self.blocks()
        if set(value["entries"]) - set(blocks):
            raise IntegrationError("Startup profile selection changed; remove recorded hooks before relocating it")
        written = self.written_targets() if not value["entries"] else set()
        plans = {}
        for relative, block in blocks.items():
            target = self.target(relative)
            contents = read_file(target)
            entry = value["entries"].get(relative)
            base = strip_block(contents, entry)
            base_path = None if entry else self.original(relative, contents, written)
            if base_path is not None:
                base = read_file(Path(base_path)) if base_path else b""
            created = entry["created"] if entry else not target.exists() or base_path == ""
            # Bundles may supply a host skeleton only when no original exists.
            if created and not base and relative in self.defaults:
                candidate = Path(self.defaults[relative])
                if candidate.is_file():
                    base_path = str(candidate)
                    base = read_file(candidate)
                    created = False  # Adopt this host base, retain it on removal.
            separator = bool(base and not base.endswith(b"\n"))
            desired = base + (b"\n" if separator else b"") + block.encode()
            plans[relative] = {"before": digest(contents), "after": digest(desired),
                               "base_path": base_path, "base_sha": digest(base),
                               "existed": target.exists(),
                               "mode": stat.S_IMODE(target.stat().st_mode) if target.exists() else 0o644,
                               "block": block, "separator": separator, "created": created}
        value["pending"] = plans
        self.save(value)

    def converge(self):
        value = self.load()
        if not value["pending"]:
            raise IntegrationError("Prepare startup integration before composing chezmoi sources")
        for name in ("bashrc", "zshrc", "tmux.conf"):
            if not (self.config / name).is_file():
                raise IntegrationError(f"Missing managed startup fragment: {self.config / name}")
        planned = []
        for relative, plan in value["pending"].items():
            target = self.target(relative)
            contents = read_file(target)
            if digest(contents) == plan["after"]:
                planned.append((target, contents, plan))
                continue
            if digest(contents) != plan["before"] or target.exists() != plan["existed"]:
                raise IntegrationError(f"Startup file changed during installation: {target}")
            base = strip_block(contents, value["entries"].get(relative))
            if plan["base_path"] is not None:
                base = read_file(Path(plan["base_path"])) if plan["base_path"] else b""
            if digest(base) != plan["base_sha"]:
                raise IntegrationError(f"Startup migration base changed: {target}")
            desired = base + (b"\n" if plan["separator"] else b"") + plan["block"].encode()
            planned.append((target, desired, plan))
        for target, contents, plan in planned:
            if read_file(target) != contents:
                atomic_write(target, contents, plan["mode"])
        # Retire whole-file ownership, including pending first-install backups.
        # Keep archival originals on disk; they are no longer removal targets.
        for name in ("existing.list", "absent.list", "created.list", "pending-absent.list"):
            ledger = self.backup / name
            if ledger.exists():
                rows = read_file(ledger).decode().splitlines(keepends=True)
                atomic_write(ledger, "".join(row for row in rows if row.rstrip("\n") not in LEGACY).encode())
        for relative, plan in value["pending"].items():
            value["entries"][relative] = {key: plan[key] for key in ("block", "separator", "created")}
        value["pending"] = {}
        self.save(value)

    def verify(self):
        value = self.load()
        if value["pending"]:
            raise IntegrationError("Startup integration is pending; rerun envy upgrade --only chezmoi")
        blocks = self.blocks()
        if set(blocks) != set(value["entries"]):
            raise IntegrationError("Startup includes are missing or profile selection changed")
        for relative, block in blocks.items():
            contents = read_file(self.target(relative))
            entry = value["entries"][relative]
            strip_block(contents, entry)
            if entry["block"] != block or not contents.endswith(block.encode()):
                raise IntegrationError(f"Startup include drift: {relative}")

    def remove(self):
        if not self.ledger.exists():
            return  # Older installations still use the provider's old ledger.
        value = self.load()
        if value["pending"]:
            raise IntegrationError("Finish the pending startup migration before removing chezmoi")
        plans = []
        for relative, entry in value["entries"].items():
            target = self.target(relative)
            contents = read_file(target)
            plans.append((target, strip_block(contents, entry), entry))
        for target, contents, entry in plans:
            if entry["created"] and not contents:
                target.unlink(missing_ok=True)
            elif target.exists() and read_file(target) != contents:
                atomic_write(target, contents, stat.S_IMODE(target.stat().st_mode))
        value["entries"] = {}
        self.save(value)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "converge", "verify", "remove"))
    args = parser.parse_args()
    manifest = os.environ.get("ENVY_MANIFEST")
    defaults = {}
    if manifest:
        defaults = json.loads(Path(manifest).read_text()).get("settings", {}).get("startup", {}).get("defaults", {})
    try:
        integration = Integration(defaults=defaults)
        if os.environ.get("ENVY_DRY_RUN") in {"1", "true"} and args.action != "verify":
            print(f"Would {args.action} startup includes")
            return
        getattr(integration, args.action)()
    except (IntegrationError, OSError, ValueError) as error:
        parser.exit(1, f"envy startup: {error}\n")


if __name__ == "__main__":
    main()
