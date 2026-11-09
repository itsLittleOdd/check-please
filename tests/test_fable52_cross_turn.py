"""Fable 5.2 review: a contradiction does not expire when the turn ends.

Before this fix, the plugin flagged a claim in turn N (STALE, or EDITED because the agent
rewrote the mail program), and when the user asked "are you sure?" and the agent repeated the
claim in turn N+1 with no new tool call, apply_session_evidence backed it with the very call
that had just been flagged: BACKED, "earlier this session", no line under the reply.
All examples invented.
"""
import importlib.util
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import engine  # noqa: E402

_NAME = "_check_please_fable52_cross_turn"
if _NAME in sys.modules:
    plugin = sys.modules[_NAME]
else:
    _spec = importlib.util.spec_from_file_location(_NAME, os.path.join(ROOT, "__init__.py"),
                                                   submodule_search_locations=[ROOT])
    plugin = importlib.util.module_from_spec(_spec)
    sys.modules[_NAME] = plugin
    _spec.loader.exec_module(plugin)

OK = json.dumps({"success": True})


def out(text, code=0):
    return json.dumps({"output": text, "exit_code": code, "error": None})


class Session:
    def __init__(self, sid):
        self.sid, self.n = sid, 0

    def turn(self, reply, calls=()):
        self.n += 1
        plugin.on_pre_llm_call(session_id=self.sid, turn_id=f"t{self.n}")
        for name, args, result in calls:
            plugin.on_post_tool_call(tool_name=name, args=args, result=result, session_id=self.sid)
        text = plugin.on_transform_llm_output(response_text=reply, session_id=self.sid)
        return text, list(plugin._LAST[self.sid])


class Base(unittest.TestCase):
    def setUp(self):
        for store in (plugin._LEDGER, plugin._LAST, plugin._CROSS_MEM, plugin._SESSION_EVIDENCE_MEM,
                      plugin._DAILY_MEM, plugin._LAST_REPLY_MEM, plugin._CRON_TALLY_MEM):
            store.clear()
        plugin._LAST_SESSION[0] = ""
        plugin._ctx_box["ctx"] = None
        plugin._settings.clear()
        plugin._settings.update({"line": "issues"})


REWRITE_MAIL = [("write_file", {"path": "bin/mail", "content": "#!/bin/sh\necho sent\n"}, OK),
                ("terminal", {"command": "bin/mail dana@example.com invoice.pdf"}, out("sent"))]
PASS_THEN_EDIT = [("terminal", {"command": "pytest -q"}, out("5 passed in 0.2s")),
                  ("patch", {"path": "pricing.py", "old_string": "a", "new_string": "b"}, OK)]


class AskTwice(Base):
    def test_rewritten_mail_program_stays_flagged_when_the_claim_is_repeated(self):
        s = Session("s-mail")
        line, v = s.turn("Invoice emailed to Dana.", REWRITE_MAIL)
        self.assertEqual(v[0].status, "EDITED")
        line, v = s.turn("Yes, the invoice was emailed to Dana.")
        self.assertEqual(v[0].status, "EDITED", v[0].note)
        self.assertIn("flagged in turn 1", v[0].note)
        self.assertIsInstance(line, str)
        self.assertIn("1 proof edited", line)
        self.assertIn("EDITED", plugin.handle_check("", session_id="s-mail"))

    def test_stale_tests_stay_stale_when_the_claim_is_repeated(self):
        s = Session("s-stale")
        _line, v = s.turn("Changed the rounding. Tests pass.", PASS_THEN_EDIT)
        self.assertEqual(v[0].status, "STALE")
        line, v = s.turn("Yes, tests pass.")
        self.assertEqual(v[0].status, "STALE", v[0].note)
        self.assertIsInstance(line, str)

    @unittest.expectedFailure
    def test_pass_then_edit_in_one_turn_is_stale_in_the_next_even_if_never_claimed_before(self):
        # KNOWN GAP, left open on purpose (see FABLE52-REVIEW.md): the cross-turn record only
        # counts an edit made in a LATER turn than the pass. A pass and an edit in the SAME
        # turn, first claimed one turn later, is still backed "earlier this session". Closing
        # it measured 6 more inline alerts in 3,339 real turns (+0.18 per 100) of unknown
        # precision, so it needs an owner's decision, not a freeze-week patch.
        s = Session("s-quiet")
        _line, v = s.turn("Changed the rounding as asked.", PASS_THEN_EDIT)
        self.assertEqual(v, [])
        _line, v = s.turn("Tests pass.")
        self.assertEqual(v[0].status, "STALE", v[0].note)
        self.assertIn("passed in turn 1", v[0].note)

    def test_catch_before_send_sees_the_standing_contradiction(self):
        s = Session("s-verify")
        s.turn("Invoice emailed to Dana.", REWRITE_MAIL)
        plugin.on_pre_llm_call(session_id="s-verify", turn_id="t2")
        directive = plugin.on_pre_verify(session_id="s-verify", coding=True, attempt=0,
                                         final_response="Yes, the invoice was emailed to Dana.")
        self.assertIsInstance(directive, dict)
        self.assertIn("rewritten this session", directive["message"])


class FreshEvidenceClears(Base):
    def test_a_real_send_clears_the_flag_and_later_recaps_are_backed(self):
        s = Session("s-clear")
        s.turn("Invoice emailed to Dana.", REWRITE_MAIL)
        _line, v = s.turn("Sent it again through the mail tool; the invoice was emailed to Dana.",
                          [("send_message", {"to": "dana@example.com", "body": "invoice"}, OK)])
        self.assertEqual(v[0].status, "BACKED")
        line, v = s.turn("As I said, the invoice was emailed to Dana.")
        self.assertEqual((v[0].status, v[0].note), ("BACKED", "earlier this session"))
        self.assertIsNone(line)

    def test_a_rerun_that_passes_clears_stale(self):
        s = Session("s-rerun")
        s.turn("Changed the rounding. Tests pass.", PASS_THEN_EDIT)
        _line, v = s.turn("I reran the suite. Tests pass.", [("terminal", {"command": "pytest -q"}, out("5 passed"))])
        self.assertEqual(v[0].status, "BACKED")
        line, v = s.turn("Yes, tests pass.")
        self.assertEqual((v[0].status, v[0].note), ("BACKED", "earlier this session"))

    def test_an_honest_recap_with_no_prior_flag_is_still_backed(self):
        s = Session("s-recap")
        s.turn("Invoice emailed to Dana.", [("send_message", {"to": "dana@example.com"}, OK)])
        line, v = s.turn("I already emailed that invoice to Dana.")
        self.assertEqual((v[0].status, v[0].note), ("BACKED", "earlier this session"))
        self.assertIsNone(line)


class LastRunFailed(Base):
    def test_an_older_pass_is_not_a_receipt_after_a_later_failing_run(self):
        s = Session("s-fail")
        s.turn("Tests pass.", [("terminal", {"command": "pytest -q"}, out("5 passed"))])
        s.turn("The suite still fails after this change.",
               [("terminal", {"command": "pytest -q"}, out("1 failed, 4 passed", 1))])
        line, v = s.turn("Tests pass.")
        self.assertEqual(v[0].status, "NOT_BACKED", v[0].note)
        self.assertIn("the last test run (turn 2) failed", v[0].note)
        self.assertIsNone(line)   # no receipt, and no alert: the reply may be about something else

    def test_a_turn_that_ran_its_own_tests_is_not_told_there_was_no_rerun(self):
        s = Session("s-reran")
        s.turn("Tests pass.", [("terminal", {"command": "pytest -q"}, out("5 passed"))])
        _line, v = s.turn("The suite still fails after this change. Lint passed.",
                          [("patch", {"path": "pricing.py", "old_string": "a", "new_string": "b"}, OK),
                           ("terminal", {"command": "pytest -q"}, out("1 failed, 4 passed", 1))])
        self.assertTrue(v)
        for verdict in v:
            self.assertNotIn("no rerun this turn", verdict.note)


class PureFunctions(unittest.TestCase):
    def test_record_sets_on_contradiction_clears_on_backed_and_ignores_not_backed(self):
        claim = engine.Claim("sent", "Emailed Dana.", "Emailed")
        rec = engine.contradiction_record([engine.Verdict(claim, "EDITED", None, None, "rewritten")], {}, 3)
        self.assertEqual(rec, {"sent": {"status": "EDITED", "note": "rewritten", "turn": 3}})
        same = engine.contradiction_record([engine.Verdict(claim, "NOT_BACKED")], rec, 4)
        self.assertEqual(same, rec)
        self.assertEqual(engine.contradiction_record([engine.Verdict(claim, "BACKED")], rec, 5), {})

    def test_only_not_backed_verdicts_inherit(self):
        claim = engine.Claim("sent", "Emailed Dana.", "Emailed")
        rec = {"sent": {"status": "EDITED", "note": "rewritten", "turn": 3}}
        backed = engine.Verdict(claim, "BACKED")
        self.assertEqual(engine.apply_prior_contradictions([backed], rec), [backed])
        carried = engine.apply_prior_contradictions([engine.Verdict(claim, "NOT_BACKED")], rec)[0]
        self.assertEqual(carried.status, "EDITED")


if __name__ == "__main__":
    unittest.main()
