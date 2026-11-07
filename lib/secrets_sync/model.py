"""Bounded flat-shell lexer and selective, byte-preserving destination edits."""
from __future__ import annotations

from dataclasses import dataclass, field
import re

from .crypto import MAX_PLAIN, NAME, validate_entries
from .errors import SyncError

ASSIGNMENT = re.compile(r"[ \t]*(?:export[ \t]+)?([A-Za-z_][A-Za-z0-9_]*)=")
SAFE = re.compile(r"[A-Za-z0-9_./:@%+,=-]*\Z")
UNSAFE_COMMANDS = {"source", ".", "eval", "exec", "command", "builtin", "if", "then", "fi",
                   "for", "while", "until", "case", "esac", "do", "done", "function",
                   "select", "declare", "typeset", "local", "readonly", "read", "export",
                   "alias", "unalias", "trap", "set", "shopt", "emulate", "autoload",
                   "enable", "disable", "getopts", "let", "readarray", "mapfile"}


def quote(value):
    return "'" + value.replace("'", "'\\''") + "'"


def scan_word(text, start=0, depth=0):
    """Return decoded literal (None if dynamic), end offset. Never evaluate."""
    if depth > 32:
        raise SyncError("shell nesting exceeds limit")
    value, dynamic, index = [], False, start
    while index < len(text) and text[index] not in " \t":
        char = text[index]
        if char in ";|&<>{}()":
            raise SyncError("compound shell syntax is unsupported")
        if char == "'":
            end = text.find("'", index + 1)
            if end < 0:
                raise SyncError("unterminated shell quote")
            value.append(text[index + 1:end])
            index = end + 1
        elif char == '"':
            index += 1
            while index < len(text) and text[index] != '"':
                char = text[index]
                if char == "\\":
                    index += 1
                    if index == len(text):
                        raise SyncError("shell continuation is unsupported")
                    if text[index] not in '\\"$`':
                        raise SyncError("unsupported quoted escape")
                    value.append(text[index])
                    index += 1
                elif text.startswith("$(", index):
                    index = skip_substitution(text, index + 2, depth + 1)
                    dynamic = True
                elif char in "$`":
                    dynamic = True
                    if char == "`":
                        raise SyncError("backtick syntax is unsupported")
                    index += 1
                else:
                    value.append(char)
                    index += 1
            if index == len(text):
                raise SyncError("unterminated shell quote")
            index += 1
        elif text.startswith("$(", index):
            index = skip_substitution(text, index + 2, depth + 1)
            dynamic = True
        elif char == "\\":
            if index + 1 >= len(text):
                raise SyncError("shell continuation is unsupported")
            if text[index + 1] != "'":
                raise SyncError("unsupported unquoted escape")
            value.append("'")
            index += 2
        elif char == "$":
            dynamic = True
            index += 1
            # Parameter expansion may contain braces, but is always ineligible.
            if index < len(text) and text[index] == "{":
                end = text.find("}", index + 1)
                if end < 0 or any(c in text[index + 1:end] for c in "{}()'\"\\"):
                    raise SyncError("unsupported parameter expansion")
                index = end + 1
        elif not SAFE.fullmatch(char):
            raise SyncError("unsupported unquoted literal")
        else:
            value.append(char)
            index += 1
    return None if dynamic else "".join(value), index


def skip_substitution(text, index, depth):
    if depth > 32:
        raise SyncError("shell nesting exceeds limit")
    # Only balanced single-line substitutions are retained, never evaluated.
    while index < len(text):
        if text[index] == ")":
            return index + 1
        if text.startswith("$(", index):
            index = skip_substitution(text, index + 2, depth + 1)
        elif text[index] in "'\"":
            delimiter = text[index]
            index += 1
            while index < len(text) and text[index] != delimiter:
                if text[index] == "\\" and delimiter == '"':
                    index += 1
                index += 1
            if index == len(text):
                raise SyncError("unterminated substitution quote")
            index += 1
        elif text[index] in "(<`\\":
            raise SyncError("unsupported substitution syntax")
        else:
            index += 1
    raise SyncError("unterminated command substitution")


@dataclass(repr=False)
class Definition:
    line: int
    start: int
    end: int
    value: str


@dataclass(repr=False)
class Document:
    lines: list[str]
    values: dict[str, str] = field(default_factory=dict)
    definitions: dict[str, Definition] = field(default_factory=dict)
    blocked: set[str] = field(default_factory=set)
    assignments: set[str] = field(default_factory=set)
    block_reasons: dict[str, set[str]] = field(default_factory=dict)

    def block(self, name, reason):
        self.blocked.add(name)
        self.block_reasons.setdefault(name, set()).add(reason)

    def render(self, source, actions):
        lines = list(self.lines)
        additions = []
        for name, action in sorted(actions.items()):
            if action == "add":
                additions.append(f"export {name}={quote(source[name])}\n")
            elif action == "overwrite":
                definition = self.definitions[name]
                line = lines[definition.line]
                lines[definition.line] = line[:definition.start] + quote(source[name]) + line[definition.end:]
            elif action == "remove":
                definition = self.definitions[name]
                suffix = lines[definition.line][definition.end:]
                lines[definition.line] = suffix.lstrip(" \t") if suffix.lstrip(" \t").startswith("#") else ""
        result = "".join(lines)
        if additions and result and not result.endswith("\n"):
            result += "\n"
        result += "".join(additions)
        encoded = result.encode("utf-8")
        if len(encoded) > MAX_PLAIN:
            raise SyncError("local secrets exceed size limit")
        return encoded


def parse(raw, blocked=()):
    if len(raw) > MAX_PLAIN:
        raise SyncError("local secrets exceed size limit")
    try:
        text = raw.decode("utf-8")
    except UnicodeError:
        raise SyncError("invalid local secrets encoding") from None
    if any((ord(c) < 32 and c not in "\n\t") or 127 <= ord(c) <= 159 for c in text):
        raise SyncError("unsupported control character in local file")
    document = Document(text.splitlines(keepends=True), blocked=set(blocked))
    for name in document.blocked:
        document.block_reasons[name] = {"excluded by configuration"}
    seen = set()
    for number, raw_line in enumerate(document.lines):
        line = raw_line.removesuffix("\n")
        stripped = line.lstrip(" \t")
        if not stripped or stripped.startswith("#"):
            continue
        try:
            assignment = ASSIGNMENT.match(line)
            if assignment:
                name = assignment[1]
                if not NAME.fullmatch(name):
                    raise SyncError("invalid variable name")
                start = assignment.end()
                value, end = scan_word(line, start)
                tail = line[end:].lstrip(" \t")
                if tail and not tail.startswith("#"):
                    raise SyncError("multiple statements are unsupported")
                if name in seen:
                    document.block(name, "duplicate assignment")
                if value is None:
                    document.block(name, "dynamic assignment")
                seen.add(name)
                document.assignments.add(name)
                if value is not None:
                    validate_entries({name: value})
                    document.values[name] = value
                    document.definitions[name] = Definition(number, start, end, value)
            else:
                head, end = scan_word(stripped)
                if not head or head in UNSAFE_COMMANDS or "=" in head:
                    raise SyncError("indirect or compound shell definitions are unsupported")
                words = []
                while end < len(stripped):
                    while end < len(stripped) and stripped[end] in " \t":
                        end += 1
                    if end == len(stripped) or stripped[end] == "#":
                        break
                    word, end = scan_word(stripped, end)
                    words.append(word)
                if head == "unset":
                    if not words or any(w is None or not NAME.fullmatch(w) for w in words):
                        raise SyncError("unsupported unset statement")
                    for name in words:
                        document.block(name, "unset in local file")
                elif head == "printf" and "-v" in words:
                    raise SyncError("indirect variable assignment is unsupported")
        except SyncError as error:
            raise SyncError(f"local line {number + 1}: {error}") from None
    validate_entries(document.values)
    return document


def classify(source, destination, blocked=(), candidates=()):
    rows = {}
    for name in sorted(set(source) | set(destination) | set(candidates)):
        if name in blocked:
            state = "blocked"
        elif name not in destination:
            state = "source-only"
        elif name not in source:
            state = "destination-only"
        elif source[name] == destination[name]:
            state = "identical"
        else:
            state = "differing"
        rows[name] = state
    return rows


def validate_action(state, action):
    allowed = {"source-only": {"add"}, "differing": {"overwrite", "remove"},
               "destination-only": {"remove"}, "identical": {"remove"}, "blocked": set()}
    if action != "keep" and action not in allowed.get(state, set()):
        raise SyncError("selected action is not permitted for that entry")


def apply_map(source, destination, actions):
    result = dict(destination)
    for name, action in actions.items():
        if action in {"add", "overwrite"}:
            result[name] = source[name]
        elif action == "remove":
            del result[name]
    return result
