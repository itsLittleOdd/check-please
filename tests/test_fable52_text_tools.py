"""Fable 5.2 review: a command that only reads or prints is never a test run.

`_check_cmd_match` counted a bare check word (check/verify/validate/lint/doctor/smoke/probe/
assert) in ANY argument token, so `grep -rn validate src/` was "tested" evidence: the receipt
under a BACKED "tests pass", a FAILED test run when grep found nothing (exit 1), and the "pass"
a later edit made STALE. All examples invented.
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


class ReadAndPrintCommandsAreNotChecks(unittest.TestCase):
    def test_text_tools_with_a_check_word_argument_are_no_evidence(self):
        for cmd in ("grep -rn validate src/app.py",
                    "cat lint.log",
                    'echo "smoke ok"',
                    "git rev-parse --verify HEAD",
                    "git log --oneline --grep lint",
                    "head -20 check.txt",
                    "ls -la verify",
                    "cd verify && ls",
                    "printf 'check done\\n'"):
            self.assertIsNone(term(cmd), cmd)

    def test_an_option_that_switches_a_check_off_is_not_a_check(self):
        self.assertIsNone(term("npm install --no-audit --skip-lint"))
        self.assertIsNone(term("python3 build.py --no-verify"))

    def test_grep_with_no_match_is_not_a_failed_test_run(self):
        evidence = [term("pytest -q", "5 passed in 0.2s", 0, 1),
                    term("grep -rn lint setup.cfg", "", 1, 2)]
        evidence = [e for e in evidence if e]
        v = engine.check_turn("Tests pass.", evidence)
        self.assertEqual(v[0].status, "BACKED")

    def test_grep_is_not_a_receipt_for_tests_pass(self):
        evidence = [e for e in (term("grep -c assert tests/test_app.py", "12", 0, 1),) if e]
        v = engine.check_turn("Verified the fix works now.", evidence)
        self.assertEqual(v[0].status, "NOT_BACKED")


class RealChecksStillCount(unittest.TestCase):
    def test_check_programs_and_runner_targets_still_count(self):
        for cmd in ("npm run lint", "make check", "ruff check .", "cargo check",
                    "python3 bin/verify-thing", "node tools/check.mjs", "bin/ci",
                    "hermes plugins validate .", "git diff --check", "black --check src",
                    "python3 build.py --validate", "cd app && npm run lint",
                    "cat data.json | python3 validate.py", "mkdir -p out && ./smoke.sh"):
            result = term(cmd)
            self.assertIsNotNone(result, cmd)
            self.assertEqual(result.kind, "tested", cmd)

    def test_check_on_a_later_line_of_a_multi_line_command_still_counts(self):
        result = term("cd app\nls -la\n./verify.sh")
        self.assertEqual(result.kind, "tested")


if __name__ == "__main__":
    unittest.main()
