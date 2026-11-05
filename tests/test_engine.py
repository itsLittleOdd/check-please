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


if __name__ == "__main__":
    unittest.main()
