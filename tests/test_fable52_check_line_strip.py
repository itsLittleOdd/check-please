"""Fable 5.2 review: only the plugin's own lines are skipped when reading a reply for claims.

_CHECK_LINE dropped ANY line starting with "check:", so a line the agent wrote itself
("Check: all tests pass and it is deployed.") was never read for claims. It also did not match
the cron guard line ("Check, Please: 1 claim not backed: ..."), so an audit of a stored cron
reply read the plugin's own quote of the flagged phrase as a new claim. All examples invented.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import engine  # noqa: E402


def kinds(text):
    return [c.kind for c in engine.extract_claims(text)]


class CheckLineStrip(unittest.TestCase):
    def test_an_agent_written_check_colon_line_is_still_read_for_claims(self):
        self.assertEqual(kinds("Check: all tests pass and it is deployed."), ["tested", "deployed"])

    def test_the_plugins_own_bottom_line_is_skipped(self):
        reply = ('Done.\n\nCheck: 1 backed, 1 stale. First issue: "Tests pass" (tests passed, then '
                 "pricing.py changed with no run since). /check for detail.")
        self.assertEqual(kinds(reply), [])

    def test_the_subagent_line_is_skipped(self):
        self.assertEqual(kinds("Check: 2 subagent claims not backed. /check subagents for detail."), [])

    def test_the_cron_guard_line_is_skipped(self):
        reply = ("Check, Please: 1 claim not backed: Tests pass (tested call 1 failed).\n\n"
                 "Nightly digest: nothing else to report.")
        self.assertEqual(kinds(reply), [])

    def test_line_built_by_summary_line_is_always_skipped(self):
        claim = engine.Claim("tested", "Tests pass.", "Tests pass")
        for status in ("BACKED", "STALE", "FAILED", "EDITED", "PARTIAL"):
            line = engine.summary_line([engine.Verdict(claim, status, None, None, "tests passed, then x changed")])
            self.assertEqual(kinds("Done.\n\n" + line), [], line)
        guard = engine.cron_guard_line([engine.Verdict(claim, "STALE", None, None, "tests passed, then x changed")])
        self.assertEqual(kinds(guard + "\n\nDigest."), [], guard)


if __name__ == "__main__":
    unittest.main()
