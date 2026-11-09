"""Fable 5.2 review: what a `&&` chain proves, both ways.

classify_call returns one Evidence per call, the highest-stakes kind in the command. So a
successful `pytest -q && git commit -am fix` was "committed" evidence only, and a reply that
honestly said "Tests pass" after an earlier failing run read FAILED. In the other direction, a
chain that failed AT its test stage (`pytest && git push`) credited the test stage as a pass.
All examples invented.
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


def status(verdicts, kind):
    return [v.status for v in verdicts if v.claim.kind == kind]


class SuccessfulChainProvesEveryStage(unittest.TestCase):
    def test_honest_rerun_chained_with_a_commit_is_not_failed(self):
        evidence = [term("pytest -q", "1 failed, 4 passed in 0.2s", 1, 1), edit("pricing.py", 2),
                    term("pytest -q && git commit -am 'fix rounding'",
                         "5 passed in 0.2s\n[main 1a2b3c4] fix rounding\n 1 file changed", 0, 3)]
        v = engine.check_turn("Fixed the bug. Tests pass and the fix is committed.", evidence)
        self.assertEqual(status(v, "tested"), ["BACKED"])
        self.assertEqual(status(v, "committed"), ["BACKED"])

    def test_test_then_push_backs_both_claims(self):
        e = term("npm test && git push origin main", "12 passing\n abc..def main -> main", 0, 1)
        self.assertEqual((e.kind, e.also_kinds), ("pushed", ("tested",)))
        v = engine.check_turn("Tests pass and it's pushed.", [e])
        self.assertEqual((status(v, "tested"), status(v, "pushed")), (["BACKED"], ["BACKED"]))

    def test_chained_pass_is_still_stale_after_a_later_edit(self):
        evidence = [term("pytest -q && git commit -am wip", "5 passed\n[main 1a2b3c4] wip", 0, 1),
                    edit("pricing.py", 2)]
        self.assertEqual(status(engine.check_turn("Tests pass.", evidence), "tested"), ["STALE"])

    def test_targeted_test_stage_keeps_its_narrow_scope(self):
        e = term("pytest tests/test_one.py -q && git commit -am wip", "1 passed\n[main 1a2b3c4] wip", 0, 1)
        self.assertIn("tests/test_one.py", e.narrow_scope)

    def test_semicolon_chain_credits_only_a_test_stage(self):
        # `;` runs the next stage whatever happened, so only a test stage (whose failure
        # would have printed a failure count and failed the whole call) is credited.
        e = term("cd app; pytest -q; git commit -am wip", "5 passed\n[main 1a2b3c4] wip", 0, 1)
        self.assertEqual((e.kind, e.also_kinds), ("committed", ("tested",)))
        e = term("git commit -am wip; git push origin main", "ok", 0, 1)
        self.assertEqual((e.kind, e.also_kinds), ("pushed", ()))

    def test_semicolon_inside_a_commit_message_is_not_a_separator(self):
        e = term("pytest -q && git commit -m 'fix; tidy' && git push", "5 passed", 0, 1)
        self.assertEqual((e.kind, set(e.also_kinds)), ("pushed", {"tested", "committed"}))

    def test_double_pipe_chain_gets_no_extra_credit(self):
        e = term("pytest -q && git push || echo skipped", "ok", 0, 1)
        self.assertEqual(getattr(e, "also_kinds", ()), ())

    def test_failing_test_stage_in_a_semicolon_chain_fails_the_call(self):
        e = term("pytest -q; git commit -am wip", "1 failed, 4 passed\n[main 1a2b3c4] wip", 0, 1)
        self.assertFalse(e.ok)
        self.assertEqual(e.also_kinds, ())

    def test_single_kind_chain_is_unchanged(self):
        e = term("cd app && pytest -q", "5 passed", 0, 1)
        self.assertEqual((e.kind, e.also_kinds), ("tested", ()))


class FailedChainDoesNotCreditTheStageThatFailed(unittest.TestCase):
    def test_chain_that_failed_at_the_test_stage_is_a_failed_test_run(self):
        e = term("pytest -q && git push origin main",
                 "collected 5 items\n\ntests/test_a.py F....\n\n1 failed, 4 passed in 0.3s", 1, 1)
        self.assertEqual((e.kind, e.ok), ("tested", False))
        v = engine.check_turn("Tests pass and the branch is pushed.", [e])
        self.assertEqual(status(v, "tested"), ["FAILED"])

    def test_chain_that_failed_at_the_push_still_credits_the_tests(self):
        e = term("pytest -q && git push origin main",
                 "5 passed in 0.3s\nerror: failed to push some refs to 'origin'", 1, 1)
        self.assertEqual((e.kind, e.ok), ("tested", True))

    def test_commit_refused_by_a_hook_is_not_a_commit(self):
        e = term("git commit -am wip && git push",
                 "husky - pre-commit hook exited with code 1 (error)", 1, 1)
        self.assertEqual((e.kind, e.ok), ("committed", False))
        v = engine.check_turn("Committed and pushed.", [e])
        self.assertEqual(status(v, "committed"), ["FAILED"])

    def test_first_stage_error_on_a_later_output_line_is_seen(self):
        e = term("crontab jobs.txt && git push",
                 "installing...\ncrontab: Operation not permitted", 1, 1)
        self.assertEqual((e.kind, e.ok), ("scheduled", False))

    def test_same_kind_test_chain_keeps_the_existing_behavior(self):
        e = term("python3 -c 'assert 1' && pytest -q", "1 failed, 4 passed", 1, 1)
        self.assertEqual((e.kind, e.ok), ("tested", True))


class SessionEvidenceRoundTrip(unittest.TestCase):
    def test_earlier_turn_chain_backs_a_later_recap_of_either_stage(self):
        e = term("npm test && git push origin main", "12 passing", 0, 1)
        v = engine.check_turn("Tests pass.", [])
        v = engine.apply_session_evidence(v, [e])
        self.assertEqual([(x.status, x.note) for x in v], [("BACKED", "earlier this session")])


def _load_plugin():
    import importlib.util
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    name = "_check_please_fable52_chain"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, os.path.join(root, "__init__.py"),
                                                  submodule_search_locations=[root])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class CrossTurnSeesAChainedPass(unittest.TestCase):
    def setUp(self):
        self.p = _load_plugin()
        for store in (self.p._LEDGER, self.p._LAST, self.p._CROSS_MEM, self.p._SESSION_EVIDENCE_MEM,
                      self.p._DAILY_MEM, self.p._LAST_REPLY_MEM):
            store.clear()
        self.p._ctx_box["ctx"] = None
        self.p._settings.update({"line": "issues"})

    def test_chained_pass_then_edit_next_turn_is_cross_turn_stale(self):
        p, sid = self.p, "s-chain"
        p.on_pre_llm_call(session_id=sid, turn_id="t1")
        p.on_post_tool_call(tool_name="terminal", args={"command": "pytest -q && git commit -am wip"},
                            result=json.dumps({"output": "5 passed\n[main 1a2b3c4] wip", "exit_code": 0}),
                            session_id=sid)
        p.on_transform_llm_output(response_text="Tests pass and committed.", session_id=sid)
        p.on_pre_llm_call(session_id=sid, turn_id="t2")
        p.on_post_tool_call(tool_name="patch", args={"path": "pricing.py", "old_string": "a", "new_string": "b"},
                            result=json.dumps({"success": True}), session_id=sid)
        out = p.on_transform_llm_output(response_text="Tweaked the rounding. Tests pass.", session_id=sid)
        self.assertIsInstance(out, str)
        self.assertIn("1 stale", out)


if __name__ == "__main__":
    unittest.main()
