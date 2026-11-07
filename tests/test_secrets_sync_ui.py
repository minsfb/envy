"""Pure selection/layout tests: synthetic names, no filesystem or credentials."""
import sys
import re
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from secrets_sync.ui import (SelectionError, select_actions, selection_view, completion_view,
                             help_view, updates_view, color_enabled)
from secrets_sync.model import parse


class SelectionTest(unittest.TestCase):
    def test_equivalent_spellings(self):
        rows = {"A": "source-only", "B": "source-only", "C": "differing"}
        for value in ("a 1, a 2, o 3", "a 1 2 o 3", "a1,2 o3", "a 1 , 2, o 3", "a1\t2\to3"):
            with self.subTest(command=value):
                self.assertEqual(select_actions(value, rows, {}), {"A": "add", "B": "add", "C": "overwrite"})

    def test_adjacent_digits_are_one_id(self):
        rows = {f"ENTRY_{i}": "source-only" for i in range(1, 13)}
        self.assertEqual(select_actions("a12", rows, {}), {"ENTRY_12": "add"})
        self.assertEqual(select_actions("a1,2", rows, {}), {"ENTRY_1": "add", "ENTRY_2": "add"})
        self.assertEqual(select_actions("a1 2", rows, {}), {"ENTRY_1": "add", "ENTRY_2": "add"})

    def test_bad_lines_do_not_mutate_existing_selection(self):
        rows = {"A": "source-only", "B": "identical", "C": "differing"}
        for value in ("a", "a1 o2", "a1 d1", "o3 k3", "a1 o99", "a0", "1 2", "a1o3",
                      "a1,,2", "a1,", ",a1", "a1-3", "a*", "a1 review", "help a1", "a1 apply",
                      "a1; o3", "a١", "a" + "1" * 256):
            current = {"C": "overwrite"}
            with self.subTest(command=value), self.assertRaises(SelectionError):
                select_actions(value, rows, current)
            self.assertEqual(current, {"C": "overwrite"})

    def test_repeated_same_action_and_later_reset(self):
        rows = {"A": "source-only", "B": "differing"}
        result = select_actions("a1, a1 o2", rows, {})
        self.assertEqual(result, {"A": "add", "B": "overwrite"})
        self.assertEqual(select_actions("d2", rows, result), {"A": "add", "B": "remove"})
        self.assertEqual(select_actions("k1,2", rows, result), {})

    def test_blocked_reasons_accumulate_without_values(self):
        document = parse(b"A=x\nA=$B\nunset A\n", ["A"])
        self.assertEqual(document.block_reasons["A"],
                         {"duplicate assignment", "dynamic assignment", "unset in local file", "excluded by configuration"})
        with self.assertRaisesRegex(SelectionError, "blocked.*duplicate assignment"):
            select_actions("d1", {"A": "blocked"}, {}, document.block_reasons)
        self.assertEqual(select_actions("k1", {"A": "blocked"}, {}, document.block_reasons), {})

    def test_layout_direction_counts_and_example(self):
        rows = {"A": "source-only", "B": "destination-only", "C": "differing"}
        view = selection_view(rows, {"A": "add", "C": "overwrite"}, "export", "/local", "/repo", width=100)
        for snippet in ("Local file -> Repository", "PLANNED ACTION", "Local only", "Repository only",
                        "1 addition | 1 overwrite | 0 removals", "Example: a1,2 o3", "Nothing has been written"):
            self.assertIn(snippet, view)
        merged = selection_view(rows, {}, "merge", "/repo", "/local", width=100)
        self.assertIn("Repository -> Local file", merged)
        self.assertIn("Keep unchanged", merged)

    def test_narrow_layout_preserves_complete_names(self):
        name = "A" * 128
        view = selection_view({name: "differing"}, {}, "merge", "/repo", "/local", width=40)
        self.assertIn("1  " + name + "\n", view)
        self.assertIn("Values differ / Keep unchanged", view)

    def test_empty_layout_and_help_are_value_free(self):
        self.assertIn("No entries to select", selection_view({}, {}, "setup", "/repo", "/local"))
        with self.assertRaisesRegex(SelectionError, "no entries to select"):
            select_actions("a1", {}, {})
        self.assertIn("a12 means entry 12", help_view("merge"))
        self.assertLessEqual(len(help_view("merge").splitlines()), 14)
        confirmation = help_view("merge", True)
        self.assertIn("y / yes", confirmation)
        self.assertIn("enter separately", confirmation)
        self.assertNotIn("a12", confirmation)
        self.assertLessEqual(len(confirmation.splitlines()), 7)

    def test_compact_updates_keep_stable_ids_and_only_changed_rows(self):
        rows = {"A": "source-only", "B": "source-only", "C": "differing"}
        previous = {"A": "add", "C": "overwrite"}
        view = updates_view(rows, previous, {"C": "overwrite"}, "export")
        self.assertIn("1  A  -> Keep unchanged", view)
        self.assertNotIn("2  B", view)
        self.assertNotIn("3  C", view)
        self.assertIn("0 additions | 1 overwrite | 0 removals", view)
        self.assertNotIn("PLANNED ACTION", view)
        self.assertIn("already matches", updates_view(rows, previous, previous, "merge"))

    def test_color_is_optional_and_does_not_change_plaintext_or_alignment(self):
        for environment in ({}, {"TERM": "dumb"}, {"TERM": "unknown"},
                            {"TERM": "xterm-256color", "NO_COLOR": ""}):
            self.assertFalse(color_enabled(environment))
        self.assertTrue(color_enabled({"TERM": "xterm-256color"}))
        rows = {"A": "source-only", "B": "differing", "C": "destination-only", "D": "identical"}
        actions = {"A": "add", "B": "overwrite", "C": "remove"}
        for width in (40, 100):
            plain = selection_view(rows, actions, "export", "/local", "/repo", width=width)
            colored = selection_view(rows, actions, "export", "/local", "/repo", width=width, color=True)
            self.assertEqual(re.sub(r"\x1b\[[0-9;]*m", "", colored), plain)
            for code in ("32", "33", "31", "2"):
                self.assertIn(f"\x1b[{code}m", colored)

    def test_completion_messages_are_direction_and_target_specific(self):
        exported = completion_view("export", "/repo", {"A": "add"}, shell_loaded=True)
        self.assertIn("Exported 1 selected entry", exported)
        self.assertIn("Local secrets unchanged", exported)
        self.assertIn("Git commit/push not performed", exported)
        self.assertNotIn("fresh shell", exported)
        self.assertNotIn("fresh shell", completion_view("merge", "/trial", {"A": "overwrite"}))
        self.assertIn("fresh shell", completion_view("merge", "/local", {"A": "overwrite"}, True))
        self.assertIn("Created /local with 1 selected entry; permissions 0600",
                      completion_view("setup", "/local", {"A": "add"}))
