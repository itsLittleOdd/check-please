import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import engine  # noqa: E402


def ev(name, args, result=None, status=None, seq=0):
    return engine.classify_call(name, args, result if result is not None else {"exit_code": 0}, status, seq=seq)


class ClassifyCall(unittest.TestCase):
    def test_pytest_pass_and_fail(self):
        self.assertEqual(ev("terminal", {"command": "pytest -q"}).kind, "tested")
        self.assertTrue(ev("terminal", {"command": "pytest -q"}).ok)
        self.assertFalse(ev("terminal", {"command": "pytest -q"}, {"exit_code": 1}).ok)

    def test_deploy_push_commit(self):
        self.assertEqual(ev("terminal", {"command": "npx vercel deploy --prod"}).kind, "deployed")
        self.assertEqual(ev("terminal", {"command": "git push origin feature"}).kind, "pushed")
        self.assertEqual(ev("terminal", {"command": "git add -A && git commit -m x"}).kind, "committed")

    def test_send_tools_and_readonly(self):
        self.assertEqual(ev("send_message", {"to": "dana@example.com"}).kind, "sent")
        self.assertEqual(ev("mcp__gmail__send_email", {"to": "a@b.c"}).kind, "sent")
        self.assertIsNone(ev("mcp__gmail__list_messages", {}))
        self.assertEqual(ev("mcp__gmail__create_draft", {"to": "a@b.c"}).kind, "drafted")

    def test_file_writes(self):
        self.assertEqual(ev("write_file", {"path": "src/app.py"}).kind, "saved")
        self.assertEqual(ev("write_file", {"path": "drafts/invoice-dana.md"}).kind, "drafted")

    def test_blocked_status_is_failure(self):
        self.assertFalse(ev("send_message", {"to": "x"}, {"ok": True}, status="blocked").ok)

    def test_error_field_is_failure(self):
        self.assertFalse(ev("send_message", {"to": "x"}, json.dumps({"error": "bounced"})).ok)

    def test_unrelated_terminal(self):
        self.assertIsNone(ev("terminal", {"command": "ls -la"}))


class ClassifyCallToolFamilies(unittest.TestCase):
    """One test per real Hermes tool family (names verified in the installed source)."""

    def test_execute_code_scans_the_code_body(self):
        self.assertEqual(ev("execute_code", {"code": "import os\nos.system('git push origin main')"}).kind,
                          "pushed")
        self.assertEqual(ev("execute_code", {"code": "import os; os.system('pytest -q')"}).kind,
                          "tested")
        self.assertIsNone(ev("execute_code", {"code": "print(sum(range(10)))"}))

    def test_browser_exec_form_submit_is_not_a_send(self):
        self.assertIsNone(ev("browser_exec", {"code": "page.fill('#amount', '40')\npage.click('#submit')"}))

    def test_browser_exec_webhook_post_is_a_send(self):
        self.assertEqual(ev("browser_exec", {"code": "requests.post('https://hooks.slack.com/services/x')"}).kind,
                          "sent")

    def test_delegate_task_proves_nothing_itself(self):
        self.assertIsNone(ev("delegate_task", {"tasks": [{"prompt": "research pricing"}]}))
        self.assertIsNone(ev("delegate_task", {"tasks": []}, {"status": "completed"}))

    def test_cronjob_manage_create_and_readonly(self):
        self.assertEqual(ev("cronjob_manage", {"action": "create", "name": "nightly backup"}).kind, "scheduled")
        self.assertIsNone(ev("cronjob_manage", {"action": "list"}))

    def test_connector_tool_naming(self):
        self.assertEqual(ev("connectors__gmail__send_email", {"to": "a@b.c"}).kind, "sent")
        self.assertIsNone(ev("connectors__gmail__list_messages", {}))
        self.assertEqual(ev("connectors__notion__create_draft", {}).kind, "drafted")


class CallSucceededRealResultShapes(unittest.TestCase):
    """call_succeeded against the EXACT post_tool_call result shapes the installed Hermes source
    returns, not guessed ones. Sources (read-only, ~/.hermes/hermes-agent, verified 2026-11-05):
      terminal      -> tools/terminal_tool_result.py:259 result_dict = {"output":.., "exit_code":.., "error": None}
      write_file    -> tools/file_operations_common.py:42-58 WriteResult.to_dict() (bytes_written,
                       dirs_created always present; verified/lint/lsp_diagnostics/error/warning only if not None)
      patch         -> tools/file_operations_common.py:62-96 PatchResult.to_dict() ("success" ALWAYS
                       present; diff/files_modified/.../error only if truthy)
      generic error -> tools/registry.py:1057 tool_error() -> {"error": msg, **extra} (no success/ok key;
                       used by write_file's own guard checks via file_tools_write_guards.py:600 _stale_write_refusal
                       and by patch's precheck_err path in file_tools.py)
      send_message  -> tools/send_message_tool.py:283 checks result.get("success"); on partial media
                       failure it force-sets result["success"] = False (line 291) even though the text sent
    """

    def test_terminal_result_dict(self):
        self.assertTrue(engine.call_succeeded({"output": "ok\n", "exit_code": 0, "error": None}))
        self.assertFalse(engine.call_succeeded({"output": "boom\n", "exit_code": 1, "error": None}))

    def test_write_file_success_has_no_success_key(self):
        # WriteResult.to_dict() never emits a "success" field; absence of "error" IS success.
        self.assertTrue(engine.call_succeeded({"bytes_written": 42, "dirs_created": False, "verified": True}))
        self.assertTrue(engine.call_succeeded({"bytes_written": 10, "dirs_created": True}))

    def test_write_file_error_has_no_success_key_either(self):
        self.assertFalse(engine.call_succeeded({"bytes_written": 0, "dirs_created": False, "error": "disk full"}))

    def test_write_file_stale_refusal_shape(self):
        # file_tools_write_guards.py:600 _stale_write_refusal: {"error": ..., "stale_write_blocked": True, "path": ...}
        self.assertFalse(engine.call_succeeded(
            {"error": "Refusing to overwrite x: stale.", "stale_write_blocked": True, "path": "x.py"}))

    def test_patch_success_key_always_present(self):
        self.assertTrue(engine.call_succeeded({"success": True, "diff": "...", "files_modified": ["a.py"]}))
        self.assertFalse(engine.call_succeeded({"success": False, "error": "Could not find old_string"}))

    def test_patch_no_change_is_still_success(self):
        # PatchResult.to_dict(): success True and no_change True are independent; no_change is a
        # success-shaped no-op, never a failure signal on its own.
        self.assertTrue(engine.call_succeeded({"success": True, "no_change": True, "note": "already present"}))

    def test_patch_generic_tool_error_has_no_success_key(self):
        # tool_error() precheck path (e.g. "path required") has no "success" field at all.
        self.assertFalse(engine.call_succeeded({"error": "path required"}))

    def test_send_message_success_key(self):
        self.assertTrue(engine.call_succeeded({"success": True, "platform": "discord"}))
        self.assertFalse(engine.call_succeeded({"success": False, "error": "Unknown platform: x"}))

    def test_send_message_partial_media_failure_is_not_success(self):
        # send_message_tool.py:288-294: text delivered but a requested attachment was dropped ->
        # the tool itself force-flips success to False so a caller watching "success" doesn't book
        # a delivery that never fully happened.
        self.assertFalse(engine.call_succeeded({
            "success": False, "partial_success": True, "media_dropped": ["photo.png"],
            "error": "Delivery incomplete: 1 requested MEDIA attachment(s) dropped before delivery"}))


class ExtractClaims(unittest.TestCase):
    def kinds(self, text):
        return [c.kind for c in engine.extract_claims(text)]

    def test_basic_claims(self):
        self.assertEqual(self.kinds("All set. Tests pass, preview is live, invoice emailed to Dana."),
                         ["tested", "deployed", "sent"])
        self.assertEqual(self.kinds("I didn't push, and nothing was sent."), [])
        k = self.kinds("Tests pass.\nThe preview is live.\nI emailed the invoice to Dana.")
        self.assertEqual(k, ["tested", "deployed", "sent"])

    def test_negations_and_future(self):
        self.assertEqual(self.kinds("I have not sent the email yet."), [])
        self.assertEqual(self.kinds("I will deploy once you approve."), [])
        self.assertEqual(self.kinds("The draft is ready to send."), [])
        self.assertEqual(self.kinds("Should I push this?"), [])
        self.assertEqual(self.kinds("I didn't deploy."), [])

    def test_code_blocks_ignored(self):
        self.assertEqual(self.kinds("Run this:\n```\ngit push\necho deployed\n```"), [])

    def test_existing_check_line_ignored(self):
        self.assertEqual(self.kinds("Check: 1 backed. /check for detail."), [])


class CheckTurn(unittest.TestCase):
    def test_demo_scenario(self):
        evidence = [
            ev("terminal", {"command": "pytest -q"}, seq=1),
            ev("write_file", {"path": "pricing.py"}, seq=2),
            ev("terminal", {"command": "vercel deploy"}, seq=3),
            ev("write_file", {"path": "drafts/invoice-dana.md"}, seq=4),
        ]
        reply = "All set.\nTests pass.\nThe preview is live.\nInvoice emailed to Dana."
        verdicts = engine.check_turn(reply, evidence)
        self.assertEqual([v.status for v in verdicts], ["STALE", "BACKED", "NOT_BACKED"])
        line = engine.summary_line(verdicts)
        self.assertIn("1 backed", line)
        self.assertIn("1 stale", line)
        self.assertIn("1 not backed", line)

    def test_failed_send(self):
        evidence = [ev("send_message", {"to": "x"}, {"error": "bounced"}, seq=1)]
        v = engine.check_turn("I sent it to the client.", evidence)
        self.assertEqual(v[0].status, "FAILED")

    def test_backed_send(self):
        evidence = [ev("send_message", {"to": "x"}, {"ok": True}, seq=1)]
        v = engine.check_turn("Sent the update to the team.", evidence)
        self.assertEqual(v[0].status, "BACKED")

    def test_no_claims_no_line(self):
        self.assertEqual(engine.summary_line(engine.check_turn("Here is the plan.", [])), "")


class TallyCronJob(unittest.TestCase):
    def test_folds_claims_into_job_row(self):
        tally = {}
        verdicts = engine.check_turn(
            "Tests pass. Deployed it.",
            [ev("terminal", {"command": "pytest -q"}, seq=1)],
        )
        engine.tally_cron_job(tally, "job-1", "Nightly QA", verdicts)
        self.assertEqual(tally["job-1"], {"job_name": "Nightly QA", "claims": 2, "backed": 1, "not_backed": 1})

    def test_accumulates_across_calls_same_job(self):
        tally = {}
        v1 = engine.check_turn("Sent the update.", [ev("send_message", {"to": "x"}, {"ok": True}, seq=1)])
        v2 = engine.check_turn("Pushed the branch.", [])
        engine.tally_cron_job(tally, "job-1", "Nightly QA", v1)
        engine.tally_cron_job(tally, "job-1", "Nightly QA", v2)
        self.assertEqual(tally["job-1"]["claims"], 2)
        self.assertEqual(tally["job-1"]["backed"], 1)
        self.assertEqual(tally["job-1"]["not_backed"], 1)

    def test_empty_verdicts_leaves_tally_untouched(self):
        tally = {}
        engine.tally_cron_job(tally, "job-1", "Nightly QA", [])
        self.assertEqual(tally, {})


class TallyDayAndDigests(unittest.TestCase):
    """Backlog item 8: /check today and /check jobs digests."""

    def test_tally_day_folds_counts_and_collects_non_backed(self):
        day = {}
        verdicts = engine.check_turn(
            "Tests pass. Deployed it.",
            [ev("terminal", {"command": "pytest -q"}, seq=1)],
        )
        engine.tally_day(day, verdicts)
        self.assertEqual(day["claims"], 2)
        self.assertEqual(day["backed"], 1)
        self.assertEqual(day["not_backed"], 1)
        self.assertEqual(len(day["issues"]), 1)
        self.assertEqual(day["issues"][0]["status"], "NOT_BACKED")

    def test_tally_day_accumulates_across_calls_same_day(self):
        day = {}
        v1 = engine.check_turn("Sent the update.", [ev("send_message", {"to": "x"}, {"ok": True}, seq=1)])
        v2 = engine.check_turn("Pushed the branch.", [])
        engine.tally_day(day, v1)
        engine.tally_day(day, v2)
        self.assertEqual(day["claims"], 2)
        self.assertEqual(day["backed"], 1)
        self.assertEqual(day["not_backed"], 1)
        self.assertEqual(len(day["issues"]), 1)

    def test_tally_day_caps_issue_list(self):
        day = {}
        for i in range(5):
            engine.tally_day(day, engine.check_turn(f"Pushed branch {i}.", []), max_issues=3)
        self.assertEqual(len(day["issues"]), 3)
        # oldest dropped first: the last 3 pushes survive (branches 2, 3, 4)
        self.assertIn("branch 2", day["issues"][0]["text"])
        self.assertIn("branch 4", day["issues"][-1]["text"])

    def test_tally_day_empty_verdicts_leaves_day_untouched(self):
        day = {}
        engine.tally_day(day, [])
        self.assertEqual(day, {})

    def test_daily_report_empty_day(self):
        out = engine.daily_report("2026-11-05", {})
        self.assertIn("no completion claims logged yet today", out)
        self.assertIn("2026-11-05", out)

    def test_daily_report_lists_counts_and_non_backed(self):
        day = {}
        engine.tally_day(day, engine.check_turn("Deployed it. Pushed it.", []))
        out = engine.daily_report("2026-11-05", day)
        self.assertIn("2 claims", out)
        self.assertIn("2 not backed", out)
        self.assertIn("Non-backed today:", out)
        self.assertLess(len(out), 1500)

    def test_daily_report_stays_under_phone_limit_with_many_issues(self):
        day = {}
        for i in range(100):
            engine.tally_day(day, engine.check_turn(f"Pushed branch number {i}.", []), max_issues=100)
        out = engine.daily_report("2026-11-05", day, char_limit=1500)
        self.assertLessEqual(len(out), 1500)

    def test_jobs_report_empty_tally(self):
        self.assertIn("no cron job runs logged yet", engine.jobs_report({}))

    def test_jobs_report_lists_busiest_job_first(self):
        tally = {}
        engine.tally_cron_job(tally, "job-1", "Quiet Job",
                              engine.check_turn("Sent it.", [ev("send_message", {"to": "x"}, {"ok": True}, seq=1)]))
        engine.tally_cron_job(tally, "job-2", "Busy Job",
                              engine.check_turn("Pushed it. Deployed it. Sent it.", []))
        out = engine.jobs_report(tally)
        self.assertLess(out.index("Busy Job"), out.index("Quiet Job"))
        self.assertLess(len(out), 1500)

    def test_jobs_report_caps_many_jobs_under_phone_limit(self):
        tally = {}
        for i in range(200):
            engine.tally_cron_job(tally, f"job-{i}", f"Job {i}",
                                  engine.check_turn("Pushed it.", []))
        out = engine.jobs_report(tally)
        self.assertLessEqual(len(out), 1500)
        self.assertIn("more jobs", out)


class PluginHooks(unittest.TestCase):
    def setUp(self):
        import importlib.util
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        spec = importlib.util.spec_from_file_location("check_please_pkg", os.path.join(root, "__init__.py"),
                                                      submodule_search_locations=[root])
        self.mod = importlib.util.module_from_spec(spec)
        sys.modules["check_please_pkg"] = self.mod
        spec.loader.exec_module(self.mod)

    def test_end_to_end_hooks(self):
        m = self.mod
        m.on_pre_llm_call(session_id="s1", turn_id="t1")
        m.on_post_tool_call(tool_name="write_file", args={"path": "drafts/note.md"}, result={"ok": True}, session_id="s1")
        out = m.on_transform_llm_output(response_text="I emailed the note to Sam.", session_id="s1")
        self.assertIn("Check: 1 not backed", out)
        self.assertIn("NOT BACKED", m.handle_check("", session_id="s1"))

    def test_turn_reset(self):
        m = self.mod
        m.on_pre_llm_call(session_id="s2", turn_id="t1")
        m.on_post_tool_call(tool_name="send_message", args={"to": "x"}, result={"ok": True}, session_id="s2")
        m.on_pre_llm_call(session_id="s2", turn_id="t2")
        out = m.on_transform_llm_output(response_text="Sent it.", session_id="s2")
        self.assertIn("not backed", out)

    def test_hooks_never_raise(self):
        m = self.mod
        self.assertIsNone(m.on_post_tool_call(tool_name=None, args="garbage{", result=object()))
        self.assertIsNone(m.on_transform_llm_output(response_text=None))

    def test_cross_turn_stale_tests_pass_then_edit_then_claim_two_turns_later(self):
        """Backlog item 6: tests passed in turn 1, code edited in turn 2 (no rerun), turn 3
        claims tests pass again with no evidence that turn. Should read STALE, not NOT_BACKED."""
        m = self.mod
        sid = "s-cross-turn"
        m.on_pre_llm_call(session_id=sid, turn_id="t1")
        m.on_post_tool_call(tool_name="terminal", args={"command": "pytest -q"},
                            result={"exit_code": 0}, session_id=sid)
        out1 = m.on_transform_llm_output(response_text="Tests pass.", session_id=sid)
        self.assertIn("1 backed", out1)

        m.on_pre_llm_call(session_id=sid, turn_id="t2")
        m.on_post_tool_call(tool_name="write_file", args={"path": "engine.py"},
                            result={"ok": True}, session_id=sid)
        m.on_transform_llm_output(response_text="Updated the function.", session_id=sid)

        m.on_pre_llm_call(session_id=sid, turn_id="t3")
        out3 = m.on_transform_llm_output(response_text="Tests pass now.", session_id=sid)
        self.assertIn("1 stale", out3)
        self.assertNotIn("not backed", out3)

    def test_cross_turn_stale_does_not_fire_when_no_edit_happened_since_the_pass(self):
        m = self.mod
        sid = "s-cross-turn-clean"
        m.on_pre_llm_call(session_id=sid, turn_id="t1")
        m.on_post_tool_call(tool_name="terminal", args={"command": "pytest -q"},
                            result={"exit_code": 0}, session_id=sid)
        m.on_transform_llm_output(response_text="Tests pass.", session_id=sid)

        m.on_pre_llm_call(session_id=sid, turn_id="t2")
        out2 = m.on_transform_llm_output(response_text="Tests pass still.", session_id=sid)
        # No edit happened between turn 1 and turn 2, so this is a plain unbacked claim,
        # not a stale one: nothing to be stale about.
        self.assertIn("1 not backed", out2)
        self.assertNotIn("stale", out2)

    def test_cron_tally_tags_reply_with_job_and_updates_per_job_counts(self):
        """Backlog item 7: when ctx.current_cron_execution() returns a run, the turn's claims
        fold into a per-job tally (claims/backed/not_backed) keyed on job_id, kept across turns
        via a stubbed ctx.state (same persistence contract as the cross-turn record)."""
        m = self.mod

        class _StubState:
            def __init__(self):
                self._d = {}

            def get(self, key, default=None):
                return self._d.get(key, default)

            def set(self, key, value):
                self._d[key] = value

        class _StubRun:
            job_id = "job-7"
            job_name = "Nightly QA"

        class _StubCtx:
            def __init__(self):
                self.state = _StubState()

            def current_cron_execution(self):
                return _StubRun()

        m._ctx_box["ctx"] = _StubCtx()
        try:
            sid = "s-cron-1"
            m.on_pre_llm_call(session_id=sid, turn_id="t1")
            m.on_post_tool_call(tool_name="terminal", args={"command": "pytest -q"},
                                result={"exit_code": 0}, session_id=sid)
            out = m.on_transform_llm_output(response_text="Tests pass. Deployed it.", session_id=sid)
            self.assertIn("backed", out)
            tally = m._load_cron_tally()
            self.assertIn("job-7", tally)
            self.assertEqual(tally["job-7"]["job_name"], "Nightly QA")
            self.assertEqual(tally["job-7"]["claims"], 2)  # tested (backed) + deployed (not backed)
            self.assertEqual(tally["job-7"]["backed"], 1)
            self.assertEqual(tally["job-7"]["not_backed"], 1)

            # A second turn's claims accumulate onto the same job row.
            sid2 = "s-cron-2"
            m.on_pre_llm_call(session_id=sid2, turn_id="t1")
            m.on_transform_llm_output(response_text="Sent the email.", session_id=sid2)
            tally2 = m._load_cron_tally()
            self.assertEqual(tally2["job-7"]["claims"], 3)
            self.assertEqual(tally2["job-7"]["not_backed"], 2)
        finally:
            m._ctx_box["ctx"] = None

    def test_no_cron_run_means_no_tally_write(self):
        """Outside cron (ctx.current_cron_execution() is None, the default with no ctx at all),
        the tally stays untouched: tagging is cron-only."""
        m = self.mod
        m._ctx_box["ctx"] = None
        sid = "s-no-cron"
        m.on_pre_llm_call(session_id=sid, turn_id="t1")
        m.on_transform_llm_output(response_text="Pushed the branch.", session_id=sid)
        self.assertEqual(m._load_cron_tally(), {})

    def test_last_reply_persists_to_ctx_state_for_the_desktop_pane(self):
        """Backlog item 11: every turn's claims_payload lands in ctx.state under "last_reply",
        keyed by session id, in the exact shape dashboard/plugin_api.py's GET /claims route
        returns. Proven here through a stubbed ctx.state, same contract as the cross-turn and
        cron-tally records above."""
        m = self.mod

        class _StubState:
            def __init__(self):
                self._d = {}

            def get(self, key, default=None):
                return self._d.get(key, default)

            def set(self, key, value):
                self._d[key] = value

        class _StubCtx:
            def __init__(self):
                self.state = _StubState()

            def current_cron_execution(self):
                return None

        m._ctx_box["ctx"] = _StubCtx()
        try:
            sid = "s-pane-1"
            m.on_pre_llm_call(session_id=sid, turn_id="t1")
            m.on_post_tool_call(tool_name="terminal", args={"command": "pytest -q"},
                                result={"exit_code": 0}, session_id=sid)
            m.on_transform_llm_output(response_text="Tests pass. I emailed Dana the invoice.", session_id=sid)
            stored = m._load_last_reply()
            self.assertIn(sid, stored)
            self.assertEqual(stored[sid]["total"], 2)
            self.assertEqual(stored[sid]["backed"], 1)
            kinds = {row["kind"] for row in stored[sid]["claims"]}
            self.assertEqual(kinds, {"tested", "sent"})
        finally:
            m._ctx_box["ctx"] = None

    def test_last_reply_falls_back_to_memory_with_no_ctx(self):
        """No ctx at all (plain unit test, or a plugin host that hasn't registered yet) still
        records the last reply, just in the module's in-memory fallback, same pattern as the
        cross-turn record's _CROSS_MEM."""
        m = self.mod
        m._ctx_box["ctx"] = None
        sid = "s-pane-no-ctx"
        m.on_pre_llm_call(session_id=sid, turn_id="t1")
        m.on_transform_llm_output(response_text="Pushed the branch.", session_id=sid)
        stored = m._load_last_reply()
        self.assertIn(sid, stored)
        self.assertEqual(stored[sid]["total"], 1)

    def test_check_today_reports_counts_and_non_backed(self):
        """Backlog item 8: /check today logs every turn's claims, cron or not."""
        m = self.mod
        m._DAILY_MEM.clear()
        sid = "s-today-1"
        m.on_pre_llm_call(session_id=sid, turn_id="t1")
        m.on_transform_llm_output(response_text="Pushed the branch. Deployed it.", session_id=sid)
        out = m.handle_check("today", session_id=sid)
        self.assertIn("2 claims", out)
        self.assertIn("2 not backed", out)
        self.assertIn("Non-backed today:", out)
        self.assertLess(len(out), 1500)

    def test_check_jobs_reports_per_job_tally(self):
        """Backlog item 8: /check jobs reads the cron tally built by backlog item 7."""
        m = self.mod

        class _StubState:
            def __init__(self):
                self._d = {}

            def get(self, key, default=None):
                return self._d.get(key, default)

            def set(self, key, value):
                self._d[key] = value

        class _StubRun:
            job_id = "job-8"
            job_name = "Phone Digest"

        class _StubCtx:
            def __init__(self):
                self.state = _StubState()

            def current_cron_execution(self):
                return _StubRun()

        m._ctx_box["ctx"] = _StubCtx()
        try:
            sid = "s-jobs-1"
            m.on_pre_llm_call(session_id=sid, turn_id="t1")
            m.on_transform_llm_output(response_text="Sent the report.", session_id=sid)
            out = m.handle_check("jobs", session_id=sid)
            self.assertIn("Phone Digest", out)
            self.assertIn("1 claims", out)
            self.assertLess(len(out), 1500)
        finally:
            m._ctx_box["ctx"] = None

    def test_pre_verify_nudges_when_coding_and_tests_predate_latest_edit(self):
        """Backlog item 9: tests passed turn 1, code edited turn 2, no rerun; the pre_verify
        gate for turn 2 should return hooks.md's exact continue-directive shape."""
        m = self.mod
        sid = "s-verify-1"
        m.on_pre_llm_call(session_id=sid, turn_id="t1")
        m.on_post_tool_call(tool_name="terminal", args={"command": "pytest -q"},
                            result={"exit_code": 0}, session_id=sid)
        m.on_transform_llm_output(response_text="Tests pass.", session_id=sid)

        m.on_pre_llm_call(session_id=sid, turn_id="t2")
        m.on_post_tool_call(tool_name="write_file", args={"path": "engine.py"},
                            result={"ok": True}, session_id=sid)
        directive = m.on_pre_verify(session_id=sid, platform="cli", model="x", coding=True,
                                    attempt=0, final_response="Done.", changed_paths=["engine.py"])
        self.assertEqual(directive["action"], "continue")
        self.assertIsInstance(directive["message"], str)
        self.assertTrue(directive["message"])

    def test_pre_verify_is_noop_outside_coding_or_on_later_attempts_or_with_no_stale_pass(self):
        m = self.mod
        sid = "s-verify-2"
        m.on_pre_llm_call(session_id=sid, turn_id="t1")
        m.on_post_tool_call(tool_name="terminal", args={"command": "pytest -q"},
                            result={"exit_code": 0}, session_id=sid)
        m.on_transform_llm_output(response_text="Tests pass.", session_id=sid)
        m.on_pre_llm_call(session_id=sid, turn_id="t2")
        m.on_post_tool_call(tool_name="write_file", args={"path": "engine.py"},
                            result={"ok": True}, session_id=sid)
        # Not a coding turn: no nudge.
        self.assertIsNone(m.on_pre_verify(session_id=sid, coding=False, attempt=0,
                                          changed_paths=["engine.py"]))
        # Already nudged once this turn (attempt > 0): no second nudge.
        self.assertIsNone(m.on_pre_verify(session_id=sid, coding=True, attempt=1,
                                          changed_paths=["engine.py"]))
        # A session with no prior pass recorded at all has nothing stale to flag.
        sid2 = "s-verify-3"
        m.on_pre_llm_call(session_id=sid2, turn_id="t1")
        m.on_post_tool_call(tool_name="write_file", args={"path": "engine.py"},
                            result={"ok": True}, session_id=sid2)
        self.assertIsNone(m.on_pre_verify(session_id=sid2, coding=True, attempt=0,
                                          changed_paths=["engine.py"]))

    def test_pre_verify_disabled_by_config_returns_none(self):
        m = self.mod
        sid = "s-verify-4"
        m.on_pre_llm_call(session_id=sid, turn_id="t1")
        m.on_post_tool_call(tool_name="terminal", args={"command": "pytest -q"},
                            result={"exit_code": 0}, session_id=sid)
        m.on_transform_llm_output(response_text="Tests pass.", session_id=sid)
        m.on_pre_llm_call(session_id=sid, turn_id="t2")
        m.on_post_tool_call(tool_name="write_file", args={"path": "engine.py"},
                            result={"ok": True}, session_id=sid)
        m._settings["verify_nudge"] = False
        try:
            self.assertIsNone(m.on_pre_verify(session_id=sid, coding=True, attempt=0,
                                              changed_paths=["engine.py"]))
        finally:
            m._settings["verify_nudge"] = True

    def test_pre_verify_never_raises(self):
        m = self.mod
        self.assertIsNone(m.on_pre_verify(session_id=None, coding=True, attempt="not-an-int"))


class VerifyNudgeDirective(unittest.TestCase):
    def test_none_when_no_prior_pass_recorded(self):
        self.assertIsNone(engine.verify_nudge_directive(True, None, ""))

    def test_none_when_not_edited_after_pass(self):
        self.assertIsNone(engine.verify_nudge_directive(False, 1, "pytest -q"))

    def test_directive_shape_matches_hooks_md(self):
        """hooks.md's pre_verify continue return is exactly {"action": "continue", "message": str}
        with a non-empty message; both keys and only those two matter to the dispatcher."""
        directive = engine.verify_nudge_directive(True, 2, "pytest -q")
        self.assertEqual(set(directive), {"action", "message"})
        self.assertEqual(directive["action"], "continue")
        self.assertIn("turn 2", directive["message"])
        self.assertIn("pytest -q", directive["message"])
        self.assertIn("rerun", directive["message"].lower())


class ClaimsPayloadSerialization(unittest.TestCase):
    """Backlog item 11: the exact JSON shape persisted to ctx.state and served by
    dashboard/plugin_api.py's GET /claims route for the Desktop pane."""

    def test_backed_claim_shape(self):
        verdicts = engine.check_turn("Tests pass.", [ev("terminal", {"command": "pytest -q"}, seq=1)])
        payload = engine.claims_payload(verdicts)
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["backed"], 1)
        row = payload["claims"][0]
        self.assertEqual(row["kind"], "tested")
        self.assertEqual(row["status"], "BACKED")
        self.assertEqual(row["proof"], "pytest -q")
        self.assertEqual(row["note"], "")

    def test_not_backed_claim_has_no_proof_and_a_note(self):
        verdicts = engine.check_turn("I emailed Dana the invoice.", [])
        payload = engine.claims_payload(verdicts)
        row = payload["claims"][0]
        self.assertEqual(row["status"], "NOT_BACKED")
        self.assertIsNone(row["proof"])
        self.assertIn("no sent call", row["note"])

    def test_no_claims_is_the_pane_empty_state(self):
        payload = engine.claims_payload(engine.check_turn("Here is the plan.", []))
        self.assertEqual(payload, {"claims": [], "backed": 0, "total": 0})

    def test_every_value_is_json_serializable(self):
        verdicts = engine.check_turn(
            "Tests pass. I emailed Dana the invoice.",
            [ev("terminal", {"command": "pytest -q"}, seq=1)],
        )
        json.dumps(engine.claims_payload(verdicts))  # raises on anything non-JSON-safe


if __name__ == "__main__":
    unittest.main()
