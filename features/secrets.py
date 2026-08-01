#!/usr/bin/env python3
"""Restore one local secrets file exclusively from SOPS+age sources."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator


FORMATS = {"binary", "dotenv", "ini", "json", "yaml"}
MAX_SECRET_BYTES = 16 * 1024 * 1024
SSH_DESTINATION = re.compile(
    r"^(?:[A-Za-z0-9_.-]+@)?(?:[A-Za-z0-9_.-]+|\[[0-9A-Fa-f:.%]+\])$"
)
REMOTE_PATH_COMPONENT = re.compile(r"^[A-Za-z0-9._-]+$")


class SecretError(RuntimeError):
    pass


@dataclass(frozen=True)
class EncryptedSource:
    path: Path
    input_type: str
    output_type: str


@dataclass(frozen=True)
class Configuration:
    target: Path
    sources: tuple[EncryptedSource, ...]
    identity_file: Path


@dataclass(frozen=True)
class KeyTransferOptions:
    destination: str
    remote_path: str | None
    port: int | None
    force: bool
    assume_yes: bool
    restore: bool


@dataclass(frozen=True)
class SshTransport:
    ssh: str
    scp: str
    destination: str
    port: int | None
    control_path: Path | None

    def shared_options(self) -> list[str]:
        if self.control_path is None:
            return []
        return [
            "-o",
            "ControlMaster=auto",
            "-o",
            "ControlPersist=300",
            "-o",
            f"ControlPath={self.control_path}",
        ]

    def run(self, script: str, *, capture: bool) -> subprocess.CompletedProcess[bytes]:
        command = [self.ssh, *self.shared_options()]
        if self.port is not None:
            command.extend(["-p", str(self.port)])
        command.extend([self.destination, "sh -s"])
        try:
            return subprocess.run(
                command,
                check=False,
                input=script.encode(),
                stdout=subprocess.PIPE if capture else None,
                timeout=300,
            )
        except subprocess.TimeoutExpired as error:
            raise SecretError(
                f"SSH operation timed out for {self.destination}"
            ) from error

    def push(
        self, source: Path, remote_path: str
    ) -> subprocess.CompletedProcess[bytes]:
        command = [self.scp, *self.shared_options()]
        if self.port is not None:
            command.extend(["-P", str(self.port)])
        command.extend([str(source), f"{self.destination}:{remote_path}"])
        try:
            return subprocess.run(command, check=False, timeout=300)
        except subprocess.TimeoutExpired as error:
            raise SecretError(
                f"SCP identity transfer timed out for {self.destination}"
            ) from error

    def pull(
        self, remote_path: str, destination: Path
    ) -> subprocess.CompletedProcess[bytes]:
        command = [self.scp, *self.shared_options()]
        if self.port is not None:
            command.extend(["-P", str(self.port)])
        command.extend([f"{self.destination}:{remote_path}", str(destination)])
        try:
            return subprocess.run(command, check=False, timeout=300)
        except subprocess.TimeoutExpired as error:
            raise SecretError(
                f"SCP identity transfer timed out for {self.destination}"
            ) from error

    def close(self) -> None:
        if self.control_path is None:
            return
        command = [self.ssh, "-o", f"ControlPath={self.control_path}"]
        if self.port is not None:
            command.extend(["-p", str(self.port)])
        command.extend(["-O", "exit", self.destination])
        try:
            subprocess.run(
                command,
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass


def expanded_path(raw: str) -> Path:
    return Path(os.path.expandvars(raw)).expanduser()


def configuration() -> Configuration:
    manifest_path = os.environ.get("ENVY_MANIFEST")
    if not manifest_path:
        raise SecretError("ENVY_MANIFEST is unavailable")
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    value: Any = manifest.get("settings", {}).get("secrets", {})
    if not isinstance(value, dict):
        raise SecretError("settings.secrets must be an object")
    unknown = set(value) - {"target", "sources", "age"}
    if unknown:
        raise SecretError(
            "settings.secrets contains unsupported fields: "
            + ", ".join(sorted(unknown))
        )

    target_raw = value.get("target", "~/.secrets")
    age = value.get("age", {})
    sources_raw = value.get("sources", [])
    if not isinstance(target_raw, str) or not isinstance(age, dict):
        raise SecretError("secrets target and age settings are invalid")
    if set(age) - {"identity_file"}:
        raise SecretError("settings.secrets.age supports only identity_file")
    identity_raw = age.get("identity_file", "~/.config/sops/age/keys.txt")
    if not isinstance(identity_raw, str) or not identity_raw:
        raise SecretError("settings.secrets.age.identity_file must be a path")
    if not isinstance(sources_raw, list):
        raise SecretError("settings.secrets.sources must be an array")
    if not sources_raw:
        sources_raw = [
            {
                "type": "sops-age",
                "path": "~/.secrets.sops",
                "input_type": "binary",
                "output_type": "binary",
            }
        ]

    sources: list[EncryptedSource] = []
    for index, item in enumerate(sources_raw):
        if not isinstance(item, dict):
            raise SecretError(
                "plaintext secrets sources are unsupported; "
                f"settings.secrets.sources[{index}] must be a sops-age object"
            )
        unknown_source = set(item) - {"type", "path", "input_type", "output_type"}
        if unknown_source:
            raise SecretError(
                f"settings.secrets.sources[{index}] contains unsupported fields: "
                + ", ".join(sorted(unknown_source))
            )
        if item.get("type") != "sops-age":
            raise SecretError(
                f"settings.secrets.sources[{index}].type must be sops-age"
            )
        path_raw = item.get("path")
        input_type = item.get("input_type", "dotenv")
        output_type = item.get("output_type", input_type)
        if not isinstance(path_raw, str) or not path_raw:
            raise SecretError(f"settings.secrets.sources[{index}].path is required")
        if input_type not in FORMATS or output_type not in FORMATS:
            raise SecretError(
                f"settings.secrets.sources[{index}] has an unsupported format"
            )
        sources.append(
            EncryptedSource(expanded_path(path_raw), input_type, output_type)
        )

    return Configuration(
        target=expanded_path(target_raw),
        sources=tuple(sources),
        identity_file=expanded_path(identity_raw),
    )


def regular_file(path: Path, *, nonempty: bool) -> os.stat_result:
    try:
        value = path.lstat()
    except FileNotFoundError as error:
        raise SecretError(f"configured file is missing: {path}") from error
    if stat.S_ISLNK(value.st_mode) or not stat.S_ISREG(value.st_mode):
        raise SecretError(f"refusing non-regular file: {path}")
    if nonempty and value.st_size == 0:
        raise SecretError(f"configured file is empty: {path}")
    if value.st_size > MAX_SECRET_BYTES:
        raise SecretError(f"configured file is too large: {path}")
    return value


def read_regular_file(path: Path, *, nonempty: bool) -> bytes:
    regular_file(path, nonempty=nonempty)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise SecretError(f"could not safely open configured file: {path}") from error
    try:
        value = os.fstat(descriptor)
        if not stat.S_ISREG(value.st_mode):
            raise SecretError(f"refusing non-regular file: {path}")
        if nonempty and value.st_size == 0:
            raise SecretError(f"configured file is empty: {path}")
        if value.st_size > MAX_SECRET_BYTES:
            raise SecretError(f"configured file is too large: {path}")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            contents = handle.read(MAX_SECRET_BYTES + 1)
        if len(contents) > MAX_SECRET_BYTES:
            raise SecretError(f"configured file is too large: {path}")
        return contents
    finally:
        os.close(descriptor)


def validate_identity(path: Path) -> None:
    value = regular_file(path, nonempty=True)
    if os.name != "nt" and stat.S_IMODE(value.st_mode) & 0o077:
        raise SecretError(f"age identity file permissions are too open: {path}")


def ensure_identity(identity_file: Path, age_keygen: str) -> bool:
    """Generate a software age identity on first convergence."""
    if identity_file.exists() or identity_file.is_symlink():
        return False
    identity_file.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        os.chmod(identity_file.parent, 0o700)
    process = subprocess.run(
        [age_keygen, "-o", str(identity_file)],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=60,
    )
    if process.returncode != 0 or not identity_file.is_file():
        raise SecretError("age-keygen could not generate a new identity")
    if os.name != "nt":
        os.chmod(identity_file, 0o600)
    print(f"Generated a new age identity: {identity_file}")
    return True


def identity_recipient(identity_file: Path, age_keygen: str) -> str:
    process = subprocess.run(
        [age_keygen, "-y", str(identity_file)],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        timeout=60,
    )
    recipient = process.stdout.strip().splitlines()[-1] if process.stdout.strip() else ""
    if process.returncode != 0 or not recipient.startswith("age1"):
        raise SecretError(f"could not derive the age recipient from {identity_file}")
    return recipient


def encrypted_source_contents(path: Path) -> bytes:
    contents = read_regular_file(path, nonempty=True)
    lowered = contents.lower()
    if b"ENC[" not in contents or b"sops" not in lowered or b"age" not in lowered:
        raise SecretError(f"configured source is not SOPS-encrypted with age: {path}")
    return contents


def validate_encrypted_source(path: Path) -> None:
    encrypted_source_contents(path)


def resolve_tool(command: str) -> str | None:
    resolved = shutil.which(command)
    if resolved:
        return resolved
    mise = shutil.which("mise")
    if not mise:
        return None
    try:
        process = subprocess.run(
            [mise, "which", command],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    candidate = process.stdout.strip()
    if (
        process.returncode != 0
        or not candidate
        or "\n" in candidate
        or not Path(candidate).is_file()
        or not os.access(candidate, os.X_OK)
    ):
        return None
    return candidate


def required_tools() -> dict[str, str]:
    result = {}
    missing = []
    for command in ("sops", "age", "age-keygen"):
        resolved = resolve_tool(command)
        if not resolved:
            missing.append(command)
        else:
            result[command] = resolved
    if missing:
        raise SecretError(
            "secrets feature is not initialized; missing managed tools: "
            f"{', '.join(missing)}. Run "
            "'envy upgrade --only chezmoi,mise --force' and retry"
        )
    return result


def ssh_tools() -> dict[str, str]:
    result = {}
    for command in ("ssh", "scp"):
        resolved = shutil.which(command)
        if not resolved:
            raise SecretError(f"{command} is required to transfer an age identity")
        result[command] = resolved
    return result


@contextmanager
def open_ssh_transport(
    tools: dict[str, str], destination: str, port: int | None
) -> Iterator[SshTransport]:
    temporary: tempfile.TemporaryDirectory[str] | None = None
    control_path = None
    if os.name != "nt":
        short_root = "/tmp" if Path("/tmp").is_dir() else None
        temporary = tempfile.TemporaryDirectory(prefix="envy-ssh-", dir=short_root)
        control_path = Path(temporary.name) / "control"
    transport = SshTransport(
        ssh=tools["ssh"],
        scp=tools["scp"],
        destination=destination,
        port=port,
        control_path=control_path,
    )
    try:
        yield transport
    finally:
        transport.close()
        if temporary is not None:
            temporary.cleanup()


def validate_native_age_identity(path: Path) -> None:
    age_keygen = resolve_tool("age-keygen")
    if not age_keygen:
        raise SecretError("age-keygen is required to validate the identity")
    try:
        process = subprocess.run(
            [age_keygen, "-y", str(path)],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired as error:
        raise SecretError("age identity validation timed out") from error
    recipients = [line for line in process.stdout.splitlines() if line]
    if (
        process.returncode != 0
        or not recipients
        or not all(recipient.startswith("age1") for recipient in recipients)
    ):
        raise SecretError(
            "configured identity is not a usable native age software identity"
        )


def validate_destination(destination: str) -> None:
    if len(destination) > 255 or not SSH_DESTINATION.fullmatch(destination):
        raise SecretError(
            "SSH destination must be a host, SSH alias, or user@host "
            "(use ~/.ssh/config for advanced routing)"
        )


def remote_identity_path(identity_file: Path, override: str | None) -> str:
    if override:
        raw = override[2:] if override.startswith("~/") else override
        if override.startswith("/") or (
            override.startswith("~") and not override.startswith("~/")
        ):
            raise SecretError(
                "--remote-path must be relative to the remote home directory"
            )
    else:
        local_home = Path(os.path.expanduser("~"))
        try:
            raw = identity_file.relative_to(local_home).as_posix()
        except ValueError as error:
            raise SecretError(
                "configured identity is outside the local home directory; "
                "provide --remote-path"
            ) from error

    parts = raw.split("/")
    if (
        not raw
        or raw.startswith("/")
        or any(
            not part or part in {".", ".."} or not REMOTE_PATH_COMPONENT.fullmatch(part)
            for part in parts
        )
    ):
        raise SecretError(
            "remote identity path must be a simple home-relative path without '..'"
        )
    return "/".join(parts)


REMOTE_HASH_FUNCTION = r"""
hash_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  elif command -v openssl >/dev/null 2>&1; then
    openssl dgst -sha256 "$1" | awk '{print $NF}'
  else
    return 1
  fi
}
"""


def marker(output: bytes, prefix: str) -> list[str] | None:
    for line in output.decode(errors="replace").splitlines():
        fields = line.strip().split()
        if fields[:1] == [prefix]:
            return fields[1:]
    return None


def inspect_remote_identity(
    transport: SshTransport, remote_path: str
) -> tuple[str, str | None]:
    script = f"""set -eu
target="$HOME/{remote_path}"
if [ -L "$target" ] || {{ [ -e "$target" ] && [ ! -f "$target" ]; }}; then
  printf 'ENVY_IDENTITY invalid\\n'
  exit 18
fi
{REMOTE_HASH_FUNCTION}
if [ -f "$target" ]; then
  digest="$(hash_file "$target")" || exit 19
  printf 'ENVY_IDENTITY present %s\\n' "$digest"
else
  printf 'ENVY_IDENTITY absent\\n'
fi
"""
    process = transport.run(script, capture=True)
    result = marker(process.stdout, "ENVY_IDENTITY")
    if result == ["invalid"] or process.returncode == 18:
        raise SecretError(
            "remote identity path is a symlink or non-regular file: "
            f"{transport.destination}:~/{remote_path}"
        )
    if process.returncode == 19:
        raise SecretError(
            "remote host requires sha256sum, shasum, or openssl to verify the transfer"
        )
    if process.returncode != 0:
        raise SecretError(
            f"SSH identity check failed for {transport.destination} "
            f"(exit {process.returncode})"
        )
    if result == ["absent"]:
        return "absent", None
    if (
        result
        and len(result) == 2
        and result[0] == "present"
        and re.fullmatch(r"[0-9A-Fa-f]{64}", result[1])
    ):
        return "present", result[1].lower()
    raise SecretError(
        f"could not verify the remote identity state on {transport.destination}"
    )


def confirm_private_key_transfer(prompt: str) -> bool:
    try:
        with open("/dev/tty", "r+", encoding="utf-8") as terminal:
            terminal.write(prompt)
            terminal.flush()
            reply = terminal.readline().strip().lower()
    except OSError as error:
        raise SecretError("non-interactive key transfer requires --yes") from error
    return reply in {"y", "yes"}


def cleanup_remote_temporary(transport: SshTransport, remote_temporary: str) -> None:
    script = f'rm -f "$HOME/{remote_temporary}"\n'
    try:
        transport.run(script, capture=True)
    except SecretError:
        pass


def secure_existing_remote_identity(
    transport: SshTransport,
    remote_path: str,
    expected_digest: str,
) -> None:
    script = f"""set -eu
target="$HOME/{remote_path}"
[ -f "$target" ] && [ ! -L "$target" ] || exit 18
chmod 600 "$target"
{REMOTE_HASH_FUNCTION}
digest="$(hash_file "$target")" || exit 19
[ "$digest" = "{expected_digest}" ] || exit 20
printf 'ENVY_IDENTITY secured %s\\n' "$digest"
"""
    process = transport.run(script, capture=True)
    result = marker(process.stdout, "ENVY_IDENTITY")
    if process.returncode != 0 or result != ["secured", expected_digest]:
        raise SecretError(
            f"could not secure the existing identity on {transport.destination}"
        )


def install_remote_identity(
    transport: SshTransport,
    options: KeyTransferOptions,
    config: Configuration,
    remote_path: str,
    expected_digest: str,
) -> None:
    parent = remote_path.rsplit("/", 1)[0] if "/" in remote_path else "."
    name = remote_path.rsplit("/", 1)[-1]
    token = uuid.uuid4().hex
    remote_temporary = (
        f"{parent}/.{name}.envy-{token}"
        if parent != "."
        else f".{name}.envy-{token}"
    )
    prepare = f"""set -eu
target="$HOME/{remote_path}"
temporary="$HOME/{remote_temporary}"
parent="$HOME/{parent}"
umask 077
mkdir -p -m 700 "$parent"
if [ -L "$target" ] || {{ [ -e "$target" ] && [ ! -f "$target" ]; }}; then exit 18; fi
if [ -e "$target" ] && [ "{1 if options.force else 0}" != 1 ]; then exit 17; fi
[ ! -e "$temporary" ] && [ ! -L "$temporary" ] || exit 16
"""
    process = transport.run(prepare, capture=True)
    if process.returncode != 0:
        raise SecretError(
            f"could not prepare the remote identity path on {options.destination} "
            f"(exit {process.returncode})"
        )

    try:
        copied = transport.push(config.identity_file, remote_temporary)
        if copied.returncode != 0:
            raise SecretError(
                f"SCP identity transfer failed for {options.destination} "
                f"(exit {copied.returncode})"
            )

        finalize = f"""set -eu
target="$HOME/{remote_path}"
temporary="$HOME/{remote_temporary}"
[ -f "$temporary" ] && [ ! -L "$temporary" ] || exit 16
chmod 600 "$temporary"
{REMOTE_HASH_FUNCTION}
digest="$(hash_file "$temporary")" || exit 19
[ "$digest" = "{expected_digest}" ] || exit 20
if [ -L "$target" ] || {{ [ -e "$target" ] && [ ! -f "$target" ]; }}; then exit 18; fi
if [ -e "$target" ]; then
  [ "{1 if options.force else 0}" = 1 ] || exit 17
  mv -f "$temporary" "$target"
else
  ln "$temporary" "$target"
  rm -f "$temporary"
fi
chmod 600 "$target"
printf 'ENVY_IDENTITY installed %s\\n' "$digest"
"""
        process = transport.run(finalize, capture=True)
        result = marker(process.stdout, "ENVY_IDENTITY")
        if process.returncode != 0 or result != ["installed", expected_digest]:
            raise SecretError(
                f"remote identity installation failed on {options.destination} "
                f"(exit {process.returncode})"
            )
    finally:
        cleanup_remote_temporary(transport, remote_temporary)


def restore_remote_secrets(transport: SshTransport) -> None:
    script = """set -eu
if [ -x "$HOME/.local/bin/envy" ]; then
  cli="$HOME/.local/bin/envy"
elif command -v envy >/dev/null 2>&1; then
  cli="$(command -v envy)"
else
  echo 'envy is not installed on the remote host' >&2
  exit 24
fi
"$cli" upgrade --only secrets --force
"$cli" doctor secrets --verbose
"""
    process = transport.run(script, capture=False)
    if process.returncode != 0:
        raise SecretError(
            "identity was installed, but remote secrets restore failed on "
            f"{transport.destination} (exit {process.returncode})"
        )


def push_key(
    options: KeyTransferOptions,
    confirmation: Callable[[str], bool] = confirm_private_key_transfer,
) -> int:
    validate_destination(options.destination)
    config = configuration()
    required_tools()
    validate_identity(config.identity_file)
    validate_native_age_identity(config.identity_file)
    remote_path = remote_identity_path(config.identity_file, options.remote_path)
    tools = ssh_tools()
    digest = hashlib.sha256(
        read_regular_file(config.identity_file, nonempty=True)
    ).hexdigest()
    with open_ssh_transport(tools, options.destination, options.port) as transport:
        return push_key_with_transport(
            transport, options, config, remote_path, digest, confirmation
        )


def push_key_with_transport(
    transport: SshTransport,
    options: KeyTransferOptions,
    config: Configuration,
    remote_path: str,
    digest: str,
    confirmation: Callable[[str], bool],
) -> int:
    print(
        f"Inspecting remote identity: {options.destination}:~/{remote_path} "
        "(nothing will be copied before confirmation)"
    )
    state, remote_digest = inspect_remote_identity(transport, remote_path)

    copied = False
    if state == "present" and remote_digest == digest:
        secure_existing_remote_identity(transport, remote_path, digest)
        print(
            f"Remote age identity is already current: "
            f"{options.destination}:~/{remote_path}"
        )
    else:
        if state == "present" and not options.force:
            raise SecretError(
                "remote identity differs; rerun with --force to authorize replacement"
            )
        print(
            "WARNING: This copies a private age identity over SSH.\n"
            "Anyone who obtains it can decrypt any SOPS document addressed to its "
            "recipient.\n"
            "Only continue for a trusted account and host; the remote file will be "
            "mode 0600.",
            file=sys.stderr,
        )
        if state == "present":
            print(
                "WARNING: A different remote identity will be replaced.",
                file=sys.stderr,
            )
        print(f"  Local:       {config.identity_file}", file=sys.stderr)
        print(f"  Destination: {options.destination}:~/{remote_path}", file=sys.stderr)
        if not options.assume_yes and not confirmation(
            "Copy private age identity? [y/N]: "
        ):
            print("Canceled; no key was copied.")
            return 0
        install_remote_identity(transport, options, config, remote_path, digest)
        copied = True
        print(f"Pushed age identity securely: {options.destination}:~/{remote_path}")

    if options.restore:
        restore_remote_secrets(transport)
        print(f"Remote secrets restored and verified: {options.destination}")
    elif copied:
        print(
            "Encrypted sources are not copied. Sync them separately, then run "
            "'envy upgrade --only secrets --force' on the remote host."
        )
    return 0


def local_identity_state(path: Path) -> tuple[str, str | None]:
    if not path.exists() and not path.is_symlink():
        return "absent", None
    contents = read_regular_file(path, nonempty=False)
    return "present", hashlib.sha256(contents).hexdigest()


def install_local_identity(
    transport: SshTransport,
    options: KeyTransferOptions,
    target: Path,
    remote_path: str,
    expected_digest: str,
) -> None:
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_raw = tempfile.mkstemp(
        prefix=f".{target.name}.pull-", dir=target.parent
    )
    temporary = Path(temporary_raw)
    try:
        if os.name != "nt":
            os.fchmod(descriptor, 0o600)
        os.close(descriptor)
        descriptor = -1
        copied = transport.pull(remote_path, temporary)
        if copied.returncode != 0:
            raise SecretError(
                f"SCP identity transfer failed from {options.destination} "
                f"(exit {copied.returncode})"
            )
        protect_target(temporary)
        contents = read_regular_file(temporary, nonempty=True)
        if hashlib.sha256(contents).hexdigest() != expected_digest:
            raise SecretError(
                f"pulled identity digest did not match {options.destination}"
            )
        validate_native_age_identity(temporary)

        if target.exists() or target.is_symlink():
            regular_file(target, nonempty=False)
            if not options.force:
                raise SecretError(
                    "local identity appeared during transfer; rerun with --force "
                    "to authorize replacement"
                )
        atomic_install(target, contents)
        del contents
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary.exists() or temporary.is_symlink():
            temporary.unlink()


def restore_local_secrets() -> None:
    root = Path(os.environ.get("ENVY_ROOT", Path(__file__).resolve().parent.parent))
    cli = root / "bin" / "envy"
    if not cli.is_file() or not os.access(cli, os.X_OK):
        raise SecretError(f"envy executable is unavailable: {cli}")
    for arguments in (
        ["upgrade", "--only", "secrets", "--force"],
        ["doctor", "secrets", "--verbose"],
    ):
        try:
            process = subprocess.run(
                [str(cli), *arguments],
                check=False,
                timeout=600,
            )
        except subprocess.TimeoutExpired as error:
            raise SecretError("local secrets restore timed out") from error
        if process.returncode != 0:
            raise SecretError(
                "identity was installed, but local secrets restore failed "
                f"(exit {process.returncode})"
            )


def pull_key(
    options: KeyTransferOptions,
    confirmation: Callable[[str], bool] = confirm_private_key_transfer,
) -> int:
    validate_destination(options.destination)
    config = configuration()
    required_tools()
    remote_path = remote_identity_path(config.identity_file, options.remote_path)
    local_state, local_digest = local_identity_state(config.identity_file)
    tools = ssh_tools()
    with open_ssh_transport(tools, options.destination, options.port) as transport:
        return pull_key_with_transport(
            transport,
            options,
            config,
            remote_path,
            local_state,
            local_digest,
            confirmation,
        )


def pull_key_with_transport(
    transport: SshTransport,
    options: KeyTransferOptions,
    config: Configuration,
    remote_path: str,
    local_state: str,
    local_digest: str | None,
    confirmation: Callable[[str], bool],
) -> int:
    print(
        f"Inspecting remote identity: {options.destination}:~/{remote_path} "
        "(nothing will be copied before confirmation)"
    )
    remote_state, remote_digest = inspect_remote_identity(transport, remote_path)
    if remote_state != "present" or remote_digest is None:
        raise SecretError(
            f"remote age identity is missing: {options.destination}:~/{remote_path}"
        )
    if remote_digest == hashlib.sha256(b"").hexdigest():
        raise SecretError(
            f"remote age identity is empty: {options.destination}:~/{remote_path}"
        )

    copied = False
    if local_state == "present" and local_digest == remote_digest:
        protect_target(config.identity_file)
        validate_identity(config.identity_file)
        validate_native_age_identity(config.identity_file)
        secure_existing_remote_identity(transport, remote_path, remote_digest)
        print(f"Local age identity is already current: {config.identity_file}")
    else:
        if local_state == "present" and not options.force:
            raise SecretError(
                "local identity differs; rerun with --force to authorize replacement"
            )
        print(
            "WARNING: This copies a private age identity from a remote SSH host.\n"
            "Anyone who obtains it can decrypt any SOPS document addressed to its "
            "recipient.\n"
            "Only continue if both this machine and the remote account are trusted; "
            "the local file will be mode 0600.",
            file=sys.stderr,
        )
        if local_state == "present":
            print(
                "WARNING: A different local identity will be replaced.",
                file=sys.stderr,
            )
        print(f"  Source:      {options.destination}:~/{remote_path}", file=sys.stderr)
        print(f"  Destination: {config.identity_file}", file=sys.stderr)
        if not options.assume_yes and not confirmation(
            "Copy private age identity? [y/N]: "
        ):
            print("Canceled; no key was copied.")
            return 0
        secure_existing_remote_identity(transport, remote_path, remote_digest)
        install_local_identity(
            transport,
            options,
            config.identity_file,
            remote_path,
            remote_digest,
        )
        copied = True
        print(f"Pulled age identity securely: {config.identity_file}")

    if options.restore:
        restore_local_secrets()
        print("Local secrets restored and verified")
    elif copied:
        print(
            "Encrypted sources are not copied. Sync them separately, then run "
            "'envy upgrade --only secrets --force' locally."
        )
    return 0


def port_number(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as error:
        raise argparse.ArgumentTypeError("port must be an integer") from error
    if not 1 <= value <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return value


def parse_key_transfer(argv: list[str], action: str) -> KeyTransferOptions:
    if action == "push-key":
        description = "Safely copy the configured age identity to a trusted SSH host."
        restore_help = "run remote envy secrets upgrade and doctor after transfer"
    else:
        description = "Safely copy the configured age identity from a trusted SSH host."
        restore_help = "run local envy secrets upgrade and doctor after transfer"
    parser = argparse.ArgumentParser(
        prog=f"envy secrets {action}",
        description=description,
    )
    parser.add_argument("destination", help="SSH host, alias, or user@host")
    parser.add_argument(
        "--remote-path",
        help="remote home-relative identity path (defaults to the local relative path)",
    )
    parser.add_argument("--port", type=port_number, help="SSH port")
    parser.add_argument(
        "--force", action="store_true", help="replace a different regular identity file"
    )
    parser.add_argument(
        "--yes", "-y", dest="assume_yes", action="store_true", help="skip confirmation"
    )
    parser.add_argument(
        "--restore",
        action="store_true",
        help=restore_help,
    )
    args = parser.parse_args(argv)
    return KeyTransferOptions(
        destination=args.destination,
        remote_path=args.remote_path,
        port=args.port,
        force=args.force,
        assume_yes=args.assume_yes,
        restore=args.restore,
    )


def decrypt_source(source: EncryptedSource, identity_file: Path, sops: str) -> bytes:
    encrypted = encrypted_source_contents(source.path)
    environment = os.environ.copy()
    environment.pop("SOPS_AGE_KEY", None)
    environment.pop("SOPS_AGE_KEY_CMD", None)
    environment["SOPS_AGE_KEY_FILE"] = str(identity_file)
    descriptor, snapshot = tempfile.mkstemp(prefix="envy-secrets.")
    try:
        if os.name != "nt":
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encrypted)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            process = subprocess.run(
                [
                    sops,
                    "--decrypt",
                    "--input-type",
                    source.input_type,
                    "--output-type",
                    source.output_type,
                    snapshot,
                ],
                check=False,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=180,
            )
        except subprocess.TimeoutExpired as error:
            raise SecretError(f"SOPS decryption timed out: {source.path}") from error
    finally:
        if os.path.exists(snapshot):
            os.unlink(snapshot)
    if process.returncode != 0:
        raise SecretError(
            f"SOPS could not decrypt {source.path} (exit {process.returncode}); "
            "diagnostic output was suppressed because it may contain secret data"
        )
    if not process.stdout:
        raise SecretError(f"SOPS decrypted an empty source: {source.path}")
    if len(process.stdout) > MAX_SECRET_BYTES:
        raise SecretError(f"SOPS decrypted source is too large: {source.path}")
    return process.stdout


def protect_target(path: Path) -> None:
    if os.name == "nt":
        return
    os.chmod(path, 0o600)


def atomic_install(target: Path, contents: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        regular_file(target, nonempty=False)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{target.name}.", dir=target.parent
    )
    try:
        if os.name != "nt":
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
        protect_target(Path(temporary))
        os.replace(temporary, target)
        protect_target(target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def usable_sources(config: Configuration) -> tuple[EncryptedSource, ...]:
    valid = []
    failures = []
    for source in config.sources:
        try:
            validate_encrypted_source(source.path)
            valid.append(source)
        except SecretError as error:
            failures.append(str(error))
    if not valid:
        detail = "; ".join(failures) or "no encrypted sources configured"
        raise SecretError(f"no usable SOPS-encrypted source was found: {detail}")
    return tuple(valid)


def encrypt_target_to_source(
    config: Configuration, source: EncryptedSource, sops: str, age_keygen: str
) -> None:
    recipient = identity_recipient(config.identity_file, age_keygen)
    plaintext = read_regular_file(config.target, nonempty=True)
    environment = os.environ.copy()
    environment.pop("SOPS_AGE_KEY", None)
    environment.pop("SOPS_AGE_KEY_CMD", None)
    environment["SOPS_AGE_KEY_FILE"] = str(config.identity_file)
    descriptor, snapshot = tempfile.mkstemp(prefix="envy-secrets.")
    try:
        if os.name != "nt":
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(plaintext)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            process = subprocess.run(
                [
                    sops,
                    "--encrypt",
                    "--age",
                    recipient,
                    "--input-type",
                    source.input_type,
                    "--output-type",
                    source.output_type,
                    snapshot,
                ],
                check=False,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=180,
            )
        except subprocess.TimeoutExpired as error:
            raise SecretError(f"SOPS encryption timed out: {source.path}") from error
    finally:
        if os.path.exists(snapshot):
            os.unlink(snapshot)
    if process.returncode != 0 or not process.stdout:
        raise SecretError(
            f"SOPS could not encrypt the target to {source.path} "
            f"(exit {process.returncode}); diagnostic output was suppressed"
        )
    atomic_install(source.path, process.stdout)
    print(f"Encrypted the secrets target to a new source: {source.path}")


def converge() -> int:
    config = configuration()
    tools = required_tools()
    ensure_identity(config.identity_file, tools["age-keygen"])
    validate_identity(config.identity_file)
    if config.target.exists() or config.target.is_symlink():
        regular_file(config.target, nonempty=True)
        protect_target(config.target)
        try:
            usable_sources(config)
        except SecretError:
            # First convergence on the machine that owns the plaintext: create
            # the encrypted recovery source instead of demanding it exists. An
            # existing-but-invalid source file is never overwritten.
            if any(
                source.path.exists() or source.path.is_symlink()
                for source in config.sources
            ):
                raise
            encrypt_target_to_source(
                config, config.sources[0], tools["sops"], tools["age-keygen"]
            )
            return 0
        print(f"Secrets target is already present: {config.target}")
        return 0
    try:
        sources = usable_sources(config)
    except SecretError as error:
        raise SecretError(
            f"{error}. The identity is ready; create the plaintext target "
            f"{config.target} and re-run to encrypt it here, or transfer the "
            "ciphertext and identity from another machine (secrets pull-key)"
        ) from error
    failures = []
    for source in sources:
        try:
            contents = decrypt_source(source, config.identity_file, tools["sops"])
        except SecretError as error:
            failures.append(str(error))
            continue
        atomic_install(config.target, contents)
        print(f"Restored SOPS+age secrets target: {config.target}")
        return 0
    raise SecretError("; ".join(failures))


def verify(verbose: bool) -> int:
    config = configuration()
    tools = required_tools()
    validate_identity(config.identity_file)
    sources = usable_sources(config)
    value = regular_file(config.target, nonempty=True)
    if os.name != "nt":
        mode = stat.S_IMODE(value.st_mode)
        if mode != 0o600:
            raise SecretError(f"secrets target mode is {mode:o}; expected 600")
    if verbose:
        print(f"secrets: SOPS found at {tools['sops']}")
        print(f"secrets: age found at {tools['age']}")
        print(f"secrets: configured age identity is protected: {config.identity_file}")
        print(f"secrets: {len(sources)} encrypted source(s) are available")
        print(f"secrets: protected target is current: {config.target}")
    return 0


def doctor() -> int:
    config = configuration()
    tools = required_tools()
    validate_identity(config.identity_file)
    sources = usable_sources(config)
    value = regular_file(config.target, nonempty=True)
    target_contents = read_regular_file(config.target, nonempty=True)
    if os.name != "nt":
        mode = stat.S_IMODE(value.st_mode)
        if mode != 0o600:
            raise SecretError(f"secrets target mode is {mode:o}; expected 600")

    failures = []
    for source in sources:
        try:
            plaintext = decrypt_source(source, config.identity_file, tools["sops"])
        except SecretError as error:
            failures.append(str(error))
            continue
        if plaintext != target_contents:
            failures.append(
                f"decrypted recovery source does not match target: {source.path}"
            )
            continue
        print(f"secrets: SOPS found at {tools['sops']}")
        print(f"secrets: age found at {tools['age']}")
        print(f"secrets: configured age identity is usable: {config.identity_file}")
        print(f"secrets: decrypted recovery source successfully: {source.path}")
        print(f"secrets: protected target is current: {config.target}")
        del plaintext
        return 0
    raise SecretError("; ".join(failures))


def main() -> int:
    action = sys.argv[1] if len(sys.argv) > 1 else "converge"
    try:
        if action == "converge":
            return converge()
        if action == "verify":
            return verify(False)
        if action == "doctor":
            return doctor()
        if action == "remove":
            print(
                "Retaining the user-owned secrets target, identity, and encrypted sources"
            )
            return 0
        if action == "push-key":
            return push_key(parse_key_transfer(sys.argv[2:], action))
        if action == "pull-key":
            return pull_key(parse_key_transfer(sys.argv[2:], action))
        if action in {"help", "--help", "-h"}:
            print(
                "usage: envy secrets {push-key|pull-key} HOST "
                "[--remote-path PATH] [--port PORT] [--force] [--yes] [--restore]"
            )
            return 0
        print(
            "usage: secrets.py {converge|verify|doctor|remove|push-key|pull-key}",
            file=sys.stderr,
        )
        return 2
    except (OSError, SecretError, json.JSONDecodeError) as error:
        print(f"secrets: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
