"""Value-free selection grammar and readable text views (no terminal IO)."""
import re

from .errors import SyncError
from .model import validate_action

ACTIONS = {"a": "add", "o": "overwrite", "d": "remove", "k": "keep"}
# Adjacent digits are one ID. Action groups require a space or comma separator.
COMMAND = re.compile(r"[aodk][ \t]*[0-9]+(?:(?:[ \t]*,[ \t]*|[ \t]+)(?:[aodk][ \t]*)?[0-9]+)*\Z")
EXAMPLE = "Example: a1,2 o3 - add entries 1 and 2; overwrite entry 3."


def color_enabled(environment):
    """Conservative ANSI support; the caller must also require a real TTY."""
    return ("NO_COLOR" not in environment and environment.get("TERM", "").startswith(
        ("xterm", "screen", "tmux", "rxvt", "ansi", "linux", "foot", "alacritty", "wezterm")))


def styled(text, role, color=False):
    codes = {"add": "32", "overwrite": "33", "remove": "31", "error": "31",
             "keep": "2", "heading": "1"}
    return f"\033[{codes[role]}m{text}\033[0m" if color else text


class SelectionError(ValueError):
    """Safe, recoverable selection feedback; never includes raw input."""


def select_actions(text, rows, current, reasons=None, permitted=None):
    """Validate the complete line, then return a separate candidate selection."""
    text = text.strip()
    if text in ACTIONS:
        raise SelectionError(f"{ACTIONS[text].capitalize()} needs entry numbers. Try {text}1 or {text}1,2.")
    if len(text) > 256 or not COMMAND.fullmatch(text):
        raise SelectionError("Use actions such as a1,2 o3, or list, apply, help/? or cancel on its own.")
    names = list(rows)
    updates = {}
    action = None
    for token in re.findall(r"[aodk]|[0-9]+", text):
        if token in ACTIONS:
            action = ACTIONS[token]
            continue
        index = int(token)
        if not 1 <= index <= len(names):
            if not names:
                raise SelectionError("There are no entries to select; use apply to finish or cancel.")
            raise SelectionError(f"Entry number is out of range; use 1 through {len(names)}.")
        name = names[index - 1]
        if name in updates and updates[name] != action:
            raise SelectionError(f"Entry {index} ({name}) has conflicting actions. Choose one action per entry on a line.")
        updates[name] = action
    for name, action in updates.items():
        if action != "keep" and permitted is not None and action not in permitted.get(name, set()):
            raise SelectionError(f"{action.capitalize()} is not supported for {name} in this operation; keep it unchanged.")
        try:
            validate_action(rows[name], action)
        except SyncError:
            detail = "; ".join(sorted((reasons or {}).get(name, ())))
            if rows[name] == "blocked":
                message = f"{name} is blocked: {detail or 'ambiguous assignment'}; only keep is allowed."
            else:
                message = f"{action.capitalize()} is not available for {name} in its current comparison state; see help."
            raise SelectionError(message) from None
    candidate = dict(current)
    for name, action in updates.items():
        if action == "keep":
            candidate.pop(name, None)
        else:
            candidate[name] = action
    return candidate


def sides(operation):
    return ("Local file", "Repository") if operation == "export" else ("Repository", "Local file")


def comparison(state, operation):
    local_source = operation == "export"
    return {"source-only": "Local only" if local_source else "Repository only",
            "destination-only": "Repository only" if local_source else "Local only",
            "identical": "Same value", "differing": "Values differ", "blocked": "Blocked"}[state]


def action_label(action, operation, name=""):
    destination = "repository" if operation == "export" else ("saved login" if name.startswith("gh:") else "local file")
    return {"keep": "Keep unchanged", "add": f"Add to {destination}",
            "overwrite": f"Overwrite {destination}", "remove": f"Remove from {destination}"}[action]


def counts(actions):
    values = list(actions.values())
    return " | ".join(f"{values.count(action)} {label}{'' if values.count(action) == 1 else 's'}"
                      for action, label in (("add", "addition"), ("overwrite", "overwrite"), ("remove", "removal")))


def selection_view(rows, actions, operation, source, target, reasons=None, width=80, color=False, destinations=None):
    before, after = sides(operation)
    if destinations is not None:
        before, after = (("Local stores", "Repository") if operation == "export" else ("Repository", "Local stores"))
    lines = ["", f"{operation.upper()}   {before} -> {after}", f"From: {source}", f"To:   {target}", ""]
    data = [(str(index), name, comparison(state, operation), action_label(actions.get(name, "keep"), operation, name))
            for index, (name, state) in enumerate(rows.items(), 1)]
    headings = ("ID", "ENTRY", "COMPARISON", "PLANNED ACTION")
    widths = [max(len(row[column]) for row in [headings, *data]) for column in range(4)]
    if sum(widths) + 6 <= width:
        def line(row):
            return "  ".join(value.ljust(size) for value, size in zip(row, widths)).rstrip()
        lines.extend((styled(line(headings), "heading", color), line(tuple("-" * size for size in widths))))
        lines.extend(styled(line(row), actions.get(row[1], "keep"), color) for row in data)
    else:
        # Never truncate a name to fit a narrow terminal; details get their own line.
        lines.append("ENTRIES (comparison / planned action)")
        for index, name, state, action in data:
            lines.extend((f"{index}  {name}", styled(f"   {state} / {action}", actions.get(name, "keep"), color)))
    if not data:
        lines.append("No entries to select.")
    if destinations is not None:
        lines.extend(["", "Destinations:", *(f"  {name} -> {destinations[name]}" for name in rows)])
    for index, (name, state) in enumerate(rows.items(), 1):
        if state == "blocked":
            detail = "; ".join(sorted((reasons or {}).get(name, ()))) or "ambiguous assignment"
            lines.append(styled(f"Blocked {index} ({name}): {detail}.", "error", color))
    lines.extend(("", f"Pending: {counts(actions)}", "All other entries stay unchanged. Nothing has been written.", "",
                  "a Add   o Overwrite   d Remove   k Keep unchanged",
                  "list - show all | apply - review and confirm | help/? | cancel", EXAMPLE, ""))
    return "\n".join(lines)


def pending_view(actions):
    return f"Pending: {counts(actions)}. Nothing written.\n"


def updates_view(rows, previous, actions, operation, color=False):
    lines = [""]
    for index, name in enumerate(rows, 1):
        if previous.get(name) != actions.get(name):
            action = actions.get(name, "keep")
            lines.append(styled(f"  {index}  {name}  -> {action_label(action, operation, name)}", action, color))
    if len(lines) == 1:
        lines.append("Selection already matches.")
    return "\n".join(lines) + "\n\n" + pending_view(actions) + "\n"


def help_view(operation, confirmation=False, color=False):
    if confirmation:
        return ("\n" + styled("Confirmation", "heading", color) + "\n"
                "  y / yes   Write the reviewed changes (enter separately).\n"
                "  n / Enter Return to selection; keep your choices.\n"
                "  cancel    Exit without writing.\n\n")
    return ("\n" + styled("Actions", "heading", color) + "\n"
            "  a Add    o Overwrite    d Remove    k Reset to keep\n"
            f"  {EXAMPLE}\n"
            "  Spaces/commas separate IDs; a12 means entry 12. No ranges.\n"
            "  Invalid batches change nothing. Later lines can revise choices.\n\n"
            + styled("Commands", "heading", color) + "\n"
            "  list     Show all entries and current selections\n"
            "  apply    Review changes, then confirm with y/N\n"
            "  help/?   Show this help without changing selections\n"
            "  cancel   Exit without writing (also Ctrl-C or EOF)\n\n")


def completion_view(operation, target, actions, shell_loaded=False):
    number = len(actions)
    noun = "entry" if number == 1 else "entries"
    if operation == "export":
        return (f"\nExported {number} selected {noun} to {target}.\n"
                f"Changes: {counts(actions)}.\nLocal secrets unchanged. Git commit/push not performed.\n")
    if operation == "setup":
        message = f"\nCreated {target} with {number} selected {noun}; permissions 0600.\n"
    else:
        message = f"\nUpdated {number} selected {noun} in {target}; permissions 0600.\nChanges: {counts(actions)}.\n"
    message += "Repository ciphertext unchanged.\n"
    if shell_loaded:
        message += "Start a fresh shell to load this local secrets file.\n"
    return message
