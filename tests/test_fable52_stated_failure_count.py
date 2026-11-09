"""Fable 5.2 review: a reply that states the failing run's own failure count is reporting it.

Item 54 clears a FAILED verdict only for a closed list of admission phrases ("still fails",
"is red", "failed with"). The plainest honest report is the number: "2 failed, 71 passed".
When the reply states a failure count the latest failing run itself printed, a pass clause in
the same reply is not contradicted by that run. All examples invented.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import engine  # noqa: E402


def term(cmd, out="", code=0, seq=0):
    return engine.classify_call("terminal", {"command": cmd},
                                json.dumps({"output": out, "exit_code": code, "error": None}), None, seq=seq)


def edit(path, seq):
    return engine.classify_call("patch", {"path": path, "old_string": "a", "new_string": "b"},
                                json.dumps({"success": True}), None, seq=seq)


def inline(verdicts):
    return [v for v in verdicts if engine.inline_worthy(v)]


class StatedFailureCount(unittest.TestCase):
    def test_count_report_of_the_failing_run_is_not_an_alert(self):
        evidence = [term("npx vitest run", "Tests  73 passed (73)", 0, 1), edit("src/parser.ts", 2),
                    term("cd app && npx vitest run 2>&1 | tail -5", "Tests  2 failed | 71 passed (73)", 0, 3)]
        v = engine.check_turn("Reran the suite: 71 passed, 2 failed. Both failures are in the "
                              "date tests and were failing before my change.", evidence)
        self.assertEqual(inline(v), [], [(x.status, x.note) for x in v])

    def test_a_different_check_that_passed_after_the_last_edit_keeps_its_receipt(self):
        evidence = [edit("src/parser.ts", 1), term("npm run lint", "", 0, 2),
                    term("npx vitest run", "Tests  3 failed | 70 passed (73)", 1, 3)]
        v = engine.check_turn("Lint passed. The test run has 3 failures, all in the date "
                              "module I did not touch.", evidence)
        self.assertEqual([x.status for x in v if x.claim.kind == "tested"], ["BACKED"])

    def test_pass_clause_with_only_a_superseded_pass_has_no_receipt_and_no_alert(self):
        evidence = [term("pytest -q", "40 passed", 0, 1), edit("src/parser.py", 2),
                    term("pytest -q", "3 failed, 37 passed in 1.2s", 1, 3)]
        v = engine.check_turn("The parser tests pass. 3 failed in the e2e folder.", evidence)
        tested = [x for x in v if x.claim.kind == "tested"]
        self.assertEqual([x.status for x in tested], ["NOT_BACKED"])
        self.assertIn("3 failing, which the reply states", tested[0].note)

    def test_overclaim_with_no_count_still_reads_failed(self):
        evidence = [term("pytest -q", "2 failed, 38 passed in 1.1s", 1, 1)]
        v = engine.check_turn("All tests pass.", evidence)
        self.assertEqual(v[0].status, "FAILED")

    def test_a_count_the_run_did_not_print_does_not_clear_it(self):
        evidence = [term("pytest -q", "2 failed, 38 passed in 1.1s", 1, 1)]
        v = engine.check_turn("Tests pass. There were 5 failures last week.", evidence)
        self.assertEqual(v[0].status, "FAILED")

    def test_a_count_that_only_appears_in_an_old_check_line_does_not_clear_it(self):
        evidence = [term("pytest -q", "2 failed, 38 passed in 1.1s", 1, 1)]
        reply = ('Tests pass.\n\nCheck: 1 failed. First issue: "Tests pass" (tested call 2 failed). '
                 "/check for detail.")
        self.assertEqual(engine.check_turn(reply, evidence)[0].status, "FAILED")

    def test_zero_failed_is_not_a_failure_count(self):
        self.assertEqual(engine._reply_states_run_failure(
            "0 failed, 40 passed", term("pytest -q", "2 failed, 38 passed", 1, 1)), "")


if __name__ == "__main__":
    unittest.main()
