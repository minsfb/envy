import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from secrets_sync.model import parse, quote, classify, validate_action
from secrets_sync.errors import SyncError


class ModelTest(unittest.TestCase):
    def test_quoting_round_trip_and_preservation(self):
        for value in ("", "word", "has spaces", "a'b", "$not-expanded", 'quotes"\\'):
            raw = ("# comment\nexport A=" + quote(value) + " # tail\nOTHER=x\n").encode()
            document = parse(raw)
            self.assertTrue(document.values["A"] == value)
            self.assertTrue(document.render({}, {}) == raw)
            result = document.render({"A": "replacement"}, {"A": "overwrite"})
            self.assertTrue(result.endswith(b" # tail\nOTHER=x\n"))

    def test_dynamic_duplicate_and_unset_names_are_blocked(self):
        document = parse(b"export A=$(touch '/not-executed')\nB=x\nB=y\nunset C\nD=literal\n")
        self.assertEqual(document.blocked, {"A", "B", "C"})
        self.assertEqual(classify({}, document.values, document.blocked, document.assignments)["A"], "blocked")

    def test_unsafe_contexts_are_refused(self):
        for raw in (b"if true; then\nA=x\nfi\n", b"A=x; B=y", b"source path", b"eval x",
                    b"cat <<EOF\nA=x\nEOF\n", b"A='unterminated", b"A=x\\\n",
                    b"printf -v A x", b"A=$(unterminated", b"function f() { A=x; }",
                    b"A+=x", b"alias export=echo", b"trap 'A=x' EXIT", b"set -a", b"let A=1"):
            with self.subTest(case=len(raw)), self.assertRaises(SyncError):
                parse(raw)

    def test_add_remove_comment_and_no_final_newline(self):
        document = parse(b"export A=x # retain\nB=y")
        result = document.render({"C": "z"}, {"A": "remove", "C": "add"})
        self.assertTrue(result == b"# retain\nB=y\nexport C='z'\n")

    def test_actions_are_state_specific(self):
        for state, action in (("blocked", "add"), ("identical", "overwrite"),
                              ("destination-only", "add"), ("source-only", "remove")):
            with self.assertRaises(SyncError):
                validate_action(state, action)
