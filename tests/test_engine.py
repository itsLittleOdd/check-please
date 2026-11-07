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
        self.assertEqual(ev("write_file", {"path": "src/app.py"}).kind, "saved_code")
        self.assertEqual(ev("write_file", {"path": "drafts/invoice-dana.md"}).kind, "drafted")
        self.assertEqual(ev("write_file", {"path": "notes/meeting.md"}).kind, "saved")

    def test_file_writes_absolute_path_shows_basename_only(self):
        # Item 26(a): a real Hermes lab turn showed a full absolute path in the proof line
        # (".../cp-lab-demo/pricing.py", invented shape here, same structure). Never show
        # an absolute or home-directory path; collapse it to its basename instead.
        result = ev("write_file", {"path": "/Users/lab/some-project-dir/pricing.py"})
        self.assertEqual(result.kind, "saved_code")
        self.assertEqual(result.detail, "pricing.py")
        self.assertNotIn("/", result.detail)

    def test_file_writes_home_relative_path_shows_basename_only(self):
        result = ev("write_file", {"path": "~/projects/widget/notes/meeting.md"})
        self.assertEqual(result.detail, "meeting.md")

    def test_file_writes_relative_path_is_left_as_is(self):
        # A relative path is already safe (no home directory leaks); do not shorten it.
        self.assertEqual(ev("write_file", {"path": "drafts/invoice-dana.md"}).detail,
                          "drafts/invoice-dana.md")

    def test_blocked_status_is_failure(self):
        self.assertFalse(ev("send_message", {"to": "x"}, {"ok": True}, status="blocked").ok)

    def test_error_field_is_failure(self):
        self.assertFalse(ev("send_message", {"to": "x"}, json.dumps({"error": "bounced"})).ok)

    def test_unrelated_terminal(self):
        self.assertIsNone(ev("terminal", {"command": "ls -la"}))

    def test_terminal_proof_line_over_150_chars_is_trimmed_with_ellipsis(self):
        # Item 26(b): a real lab turn showed a proof command line running past 150 chars.
        # Invented shape here: a long cd-into-a-deep-path prefix, then the sub-command that
        # actually ran the tests, joined with &&. The shortened line should surface the
        # meaningful last stage, not a mid-flag hard cut.
        cmd = ("cd /Users/lab/some/very/deeply/nested/project/directory/for/this/scenario/"
               "that/keeps/going/on/and/on/and/on/for/quite/a/while/longer/than/you/would/"
               "normally/expect/a/path/to/run && python3 -m unittest -q")
        self.assertGreater(len(cmd), 150)
        result = ev("terminal", {"command": cmd})
        self.assertEqual(result.kind, "tested")
        self.assertEqual(result.detail, "python3 -m unittest -q")
        self.assertLessEqual(len(result.detail), 150)

    def test_terminal_proof_line_with_no_safe_split_point_gets_hard_truncated(self):
        # No &&, so there's no shorter meaningful sub-command to fall back to; truncate
        # with a visible ellipsis rather than silently dropping the tail with no signal.
        cmd = "pytest -q " + "x" * 200
        result = ev("terminal", {"command": cmd})
        self.assertTrue(result.detail.endswith("..."))
        self.assertEqual(len(result.detail), 150)

    def test_terminal_proof_line_under_150_chars_is_unchanged(self):
        self.assertEqual(ev("terminal", {"command": "pytest -q"}).detail, "pytest -q")

    def test_generic_check_commands_count_as_tested(self):
        """Item 27 family (e): a real check that isn't a fixed test-runner name still proves
        tested evidence, so a backed claim doesn't get flagged NOT BACKED just because we
        didn't recognize the command."""
        self.assertEqual(ev("terminal", {"command": "node tools/check.mjs"}).kind, "tested")
        self.assertEqual(ev("terminal", {"command": "bash -n deploy.sh"}).kind, "tested")
        self.assertEqual(ev("terminal", {"command": "python3 -m py_compile app.py"}).kind, "tested")
        self.assertEqual(ev("terminal", {"command": "tsc --noEmit"}).kind, "tested")
        self.assertEqual(ev("terminal", {"command": "hermes plugins doctor . --ci"}).kind, "tested")
        self.assertEqual(ev("terminal", {"command": "hermes plugins validate ."}).kind, "tested")
        self.assertEqual(ev("terminal", {"command": "hermes plugins validate . --install-deps"}).kind, "tested")

    def test_check_command_word_boundary_avoids_checkout(self):
        """\"checkout\" and \"doctorate\" must not false-match the bare check/doctor words."""
        self.assertIsNone(ev("terminal", {"command": "git checkout -b feature/x"}))
        self.assertIsNone(ev("terminal", {"command": "echo 'applied for a doctorate'"}))

    def test_check_flag_and_syntax_check_variants(self):
        self.assertEqual(ev("terminal", {"command": "node --check server.js"}).kind, "tested")
        self.assertEqual(ev("terminal", {"command": "sh -n install.sh"}).kind, "tested")
        self.assertEqual(ev("execute_code", {"code": "assert response.status_code == 200"}).kind, "tested")


class DeleteScriptAndOutputEvidence(unittest.TestCase):
    """Item 40 cause (4): a script or SQL that deletes things, or output reporting a count
    removed, backs a deleted claim even when the command isn't the bare rm/trash shape."""

    def test_script_name_says_delete(self):
        self.assertEqual(ev("terminal", {"command": "python3 purge_old_sessions.py"}).kind, "deleted")
        self.assertEqual(ev("terminal", {"command": "./cleanup_cache.sh"}).kind, "deleted")
        self.assertEqual(ev("terminal", {"command": "node remove_stale_tokens.js"}).kind, "deleted")

    def test_sql_delete_from(self):
        self.assertEqual(
            ev("terminal", {"command": "sqlite3 app.db \"DELETE FROM sessions WHERE expired=1\""}).kind,
            "deleted")

    def test_output_reports_a_removed_count_even_when_command_text_does_not_say_so(self):
        self.assertEqual(
            ev("terminal", {"command": "./backup.sh"}, "Removed 12 old sessions.").kind, "deleted")
        self.assertEqual(
            ev("terminal", {"command": "./backup.sh"}, "Deleted 3 stale files").kind, "deleted")

    def test_output_count_number_first_order_still_backs_deleted(self):
        # Item 40 cause (4) residual: the bench's real shape puts the number before the verb
        # ("37 of 45 rows deleted"), not after it ("Deleted 37 rows") like the original guard
        # expected. Invented examples only, never bench text.
        self.assertEqual(
            ev("terminal", {"command": "./purge.sh"}, "37 of 45 stale rows deleted.").kind, "deleted")
        self.assertEqual(
            ev("terminal", {"command": "./purge.sh"}, "12 old sessions removed.").kind, "deleted")

    def test_output_count_ignored_on_failure(self):
        self.assertIsNone(
            ev("terminal", {"command": "./backup.sh"}, "Removed 12 old sessions.", status="error"))

    def test_plain_shell_verb_in_unrelated_sentence_does_not_match(self):
        """\"clean\" as a bare word (not a script filename) must not false-match."""
        self.assertIsNone(ev("terminal", {"command": "echo 'please clean the kitchen'"}))

    def test_execute_code_body_delete_script_call(self):
        self.assertEqual(
            ev("execute_code", {"code": "import subprocess\nsubprocess.run(['python3', 'purge_logs.py'])"}).kind,
            "deleted")


class ChainedShellCommands(unittest.TestCase):
    """Item 40 cause (3): a chained `&&` command's LATER stage failing (e.g. `git push` after
    a successful `git commit`) must still back the EARLIER stage's own claim kind, scoped to
    `&&` only this tick (own-corpus/invented shapes, never bench text)."""

    def test_failed_push_after_committed_still_backs_committed(self):
        e = ev("terminal", {"command": "git commit -m 'fix pricing' && git push"},
               {"exit_code": 1})
        self.assertEqual(e.kind, "committed")
        self.assertTrue(e.ok)

    def test_successful_chain_still_reports_the_highest_stage(self):
        """Unchanged from before this item: a chain that fully succeeds still reports its
        highest-stakes stage, same as a plain unchained command would."""
        e = ev("terminal", {"command": "git commit -m 'fix pricing' && git push"})
        self.assertEqual(e.kind, "pushed")
        self.assertTrue(e.ok)

    def test_single_matched_stage_failure_is_not_guessed_as_success(self):
        """Only one sub-command has a recognizable kind (the earlier one, `echo`, has none):
        that single match is the stage most likely responsible for the failure, so it is not
        force-backed, unlike the two-or-more-stage case above."""
        e = ev("terminal", {"command": "echo building && git push"}, {"exit_code": 1})
        self.assertEqual(e.kind, "pushed")
        self.assertFalse(e.ok)

    def test_three_stage_chain_credits_only_the_first_matched_stage(self):
        e = ev("terminal", {"command": "git add -A && git commit -m x && git push"},
               {"exit_code": 1})
        self.assertEqual(e.kind, "committed")
        self.assertTrue(e.ok)

    def test_semicolon_chain_unambiguous_last_stage_still_reports_as_failed(self):
        """`;` fix (item 40 cause 3, the `;` case): when the globally-picked kind IS the
        chain's own last sub-command (git push really is the last thing that ran and really
        is what failed), there is no ambiguity, so this is unchanged from before the fix."""
        e = ev("terminal", {"command": "git commit -m x; git push"}, {"exit_code": 1})
        self.assertEqual(e.kind, "pushed")
        self.assertFalse(e.ok)

    def test_semicolon_chain_ambiguous_priority_mismatch_is_no_evidence(self):
        """The bug this tick fixes: `_scan_ladder`'s priority order picks \"paid\" from the
        FIRST sub-command even though \"deployed\" is the chain's actual LAST sub-command
        (invented: a payment call, then an unrelated deploy that failed). The paid stage may
        well have already succeeded; we cannot prove that the way `&&` guarantees it, so this
        is no evidence at all, never a guessed FAILED verdict."""
        e = ev("terminal",
               {"command": "stripe charge the card; netlify deploy --prod"},
               {"exit_code": 1})
        self.assertIsNone(e)

    def test_semicolon_chain_single_matched_stage_is_unaffected(self):
        """Only one sub-command has a recognizable kind (`echo` has none): not the
        two-or-more-stage ambiguous shape, so the normal verdict still applies."""
        e = ev("terminal", {"command": "echo starting; git push"}, {"exit_code": 1})
        self.assertEqual(e.kind, "pushed")
        self.assertFalse(e.ok)

    def test_double_pipe_chain_failure_is_unambiguous_both_sides_ran_and_failed(self):
        """`||` fix (item 40 cause 3, the `||` case): unlike `;`, an overall FAILURE on a
        `||` chain means both sides genuinely ran and genuinely failed (the second side
        only runs because the first failed, and it failed too), so this is unchanged from
        before the fix, with no ambiguity guard needed on the failure side."""
        e = ev("terminal", {"command": "git push || git push --force"}, {"exit_code": 1})
        self.assertEqual(e.kind, "pushed")
        self.assertFalse(e.ok)

    def test_single_pipe_into_grep_no_match_is_ambiguous_not_failed(self):
        """New named chain gap, same family as item 40 cause (3): `pytest tests/ |
        grep -i fail` (invented) exits nonzero when grep finds NO match, meaning no
        failures were reported, the opposite of what a nonzero exit normally means.
        The real \"tested\" evidence sits in the left segment, whose own exit code we
        never see, so this must be no evidence at all, never a guessed FAILED."""
        e = ev("terminal", {"command": "pytest tests/ | grep -i fail"}, {"exit_code": 1})
        self.assertIsNone(e)

    def test_single_pipe_into_wc_zero_lines_is_ambiguous_not_failed(self):
        e = ev("terminal", {"command": "pytest tests/ | grep -c FAILED | wc -l"},
               {"exit_code": 1})
        self.assertIsNone(e)

    def test_single_pipe_success_side_unaffected(self):
        """The guard is scoped to the failure side only (item 40's \"when unsure, do
        not mark FAILED\" rule). A successful pipe (grep found the expected text,
        e.g. a summary line) still backs the claim as a normal pass."""
        e = ev("terminal", {"command": "pytest tests/ | grep -i passed"})
        self.assertEqual(e.kind, "tested")
        self.assertTrue(e.ok)

    def test_single_pipe_into_real_second_action_is_unaffected(self):
        """A pipe into an actual second ladder action (invented: mailing a report) is
        not this shape -- that stage's own kind already explains the result, so the
        normal verdict applies and this guard must not swallow it."""
        e = ev("terminal", {"command": "pytest tests/ | mail -s report team@example.com"},
               {"exit_code": 1})
        self.assertEqual(e.kind, "sent")
        self.assertFalse(e.ok)

    def test_double_pipe_chain_success_matching_first_stage_is_trusted(self):
        """The picked kind IS the first sub-command's own kind, which is the only shape an
        overall success can prove without the second side running at all, so this stays
        BACKED, unchanged from before the fix."""
        e = ev("terminal", {"command": "git push || git push --force"})
        self.assertEqual(e.kind, "pushed")
        self.assertTrue(e.ok)

    def test_double_pipe_chain_success_priority_mismatch_is_no_evidence(self):
        """The bug this tick fixes: `deploy_prod.sh || echo fallback ok` (invented) succeeds
        overall whenever EITHER side succeeds. If deploy_prod.sh actually failed and the
        echo fallback is what made the call read ok=True, crediting "deployed" as BACKED
        would be wrong -- deploy_prod.sh is exactly the command that failed. We cannot
        prove which side actually ran, so this is no evidence at all, never a guessed
        BACKED verdict."""
        e = ev("terminal", {"command": "netlify deploy --prod || stripe charge the card"})
        self.assertIsNone(e)

    def test_double_pipe_chain_single_matched_stage_is_unaffected(self):
        """Only one sub-command has a recognizable kind (`echo` has none): not the
        two-or-more-distinct-kind ambiguous shape, so the normal verdict still applies."""
        e = ev("terminal", {"command": "git push || echo fallback ok"})
        self.assertEqual(e.kind, "pushed")
        self.assertTrue(e.ok)


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


def subagent_entry(tool_name, status="ok", targets=None, argument_keys=None):
    """Exact fixture shape from tools/delegate_tool_results.py's
    _subagent_stop_tool_call_history (read-only, verified 2026-11-06): {"tool_name",
    "tool_input": {"argument_keys", "targets"}, "status"}. Never built from a real history."""
    return {
        "tool_name": tool_name,
        "tool_input": {"argument_keys": argument_keys or [], "targets": targets or {}},
        "status": status,
        "input_bytes": 0, "output_bytes": 0,
    }


class SubagentClaimCheckClassification(unittest.TestCase):
    """Backlog item 31 part (a): classify a child's own tool_call_history entries by tool
    name and sanitized targets only (no raw command text is ever in this shape)."""

    def test_child_send_tool_is_sent_evidence(self):
        entry = subagent_entry("connectors__gmail__send_email", targets={"to": "dana@example.com"})
        got = engine.classify_subagent_entry(entry)
        self.assertEqual(got.kind, "sent")
        self.assertTrue(got.ok)
        self.assertTrue(got.via_subagent)
        self.assertIn("dana@example.com", got.detail)

    def test_child_failed_send_is_not_ok(self):
        got = engine.classify_subagent_entry(subagent_entry("send_message", status="error"))
        self.assertEqual(got.kind, "sent")
        self.assertFalse(got.ok)

    def test_child_write_file_to_code_path_is_saved_code(self):
        got = engine.classify_subagent_entry(
            subagent_entry("write_file", targets={"path": "pricing.py"}))
        self.assertEqual(got.kind, "saved_code")
        self.assertTrue(got.via_subagent)

    def test_child_cron_create_is_scheduled(self):
        got = engine.classify_subagent_entry(subagent_entry("cronjob_manage", targets={"action": "create"}))
        self.assertEqual(got.kind, "scheduled")

    def test_child_readonly_tool_proves_nothing(self):
        self.assertIsNone(engine.classify_subagent_entry(subagent_entry("connectors__gmail__list_messages")))

    def test_child_terminal_call_has_no_command_text_and_proves_nothing(self):
        # Honest gap: tool_call_history never carries raw command text, so a terminal call
        # cannot be scanned by the ladder the way the parent's own calls are.
        self.assertIsNone(engine.classify_subagent_entry(subagent_entry("terminal", targets={"command": "git push"})))

    def test_delegate_task_itself_proves_nothing(self):
        self.assertIsNone(engine.classify_subagent_entry(subagent_entry("delegate_task")))

    def test_malformed_entry_is_skipped_not_raised(self):
        self.assertIsNone(engine.classify_subagent_entry("not a dict"))
        self.assertIsNone(engine.classify_subagent_entry({}))

    def test_subagent_evidence_filters_and_sequences(self):
        history = [
            subagent_entry("connectors__gmail__send_email", targets={"to": "a@b.c"}),
            subagent_entry("connectors__gmail__list_messages"),  # drops: proves nothing
            subagent_entry("write_file", status="error", targets={"path": "README.md"}),
        ]
        out = engine.subagent_evidence(history, seq_start=5)
        self.assertEqual([e.kind for e in out], ["sent", "saved"])
        self.assertTrue(all(e.via_subagent for e in out))
        self.assertEqual([e.seq for e in out], [6, 7])
        self.assertFalse(out[1].ok)

    def test_subagent_evidence_handles_non_list_input(self):
        self.assertEqual(engine.subagent_evidence(None), [])
        self.assertEqual(engine.subagent_evidence("not a list"), [])


class SubagentClaimCheckPartB(unittest.TestCase):
    """Backlog item 31 part (b): check a child's own summary against ONLY that child's own
    evidence, then fold the result into the parent's check line and a /check subagents log."""

    def test_child_unbacked_claim_with_no_evidence(self):
        history = [subagent_entry("connectors__gmail__list_messages")]  # proves nothing
        evidence = engine.subagent_evidence(history)
        verdicts = engine.check_turn("I emailed Dana the invoice.", evidence)
        self.assertEqual(len(verdicts), 1)
        self.assertEqual(verdicts[0].status, "NOT_BACKED")

    def test_child_backed_claim_with_matching_evidence(self):
        history = [subagent_entry("connectors__gmail__send_email", targets={"to": "dana@example.com"})]
        evidence = engine.subagent_evidence(history)
        verdicts = engine.check_turn("Sent the invoice to Dana.", evidence)
        self.assertEqual(len(verdicts), 1)
        self.assertEqual(verdicts[0].status, "BACKED")

    def test_append_subagent_note_adds_to_an_existing_line(self):
        # A failed send is always inline-worthy (STALE/FAILED/EDITED are, regardless of kind;
        # inline_worthy's own NOT_BACKED suppression only applies to the NOT_BACKED status).
        failed_send = engine.classify_subagent_entry(subagent_entry("send_message", status="error"))
        failed = engine.check_turn("Sent the update.", [failed_send])
        line = engine.append_subagent_note("Check: 1 backed. /check for detail.", failed)
        self.assertIn("1 subagent claim not backed", line)
        self.assertIn("/check subagents for detail.", line)
        self.assertNotIn("/check for detail. 1 subagent", line)  # old tail was stripped, not duplicated

    def test_append_subagent_note_standalone_when_parent_line_is_empty(self):
        failed_send = engine.classify_subagent_entry(subagent_entry("send_message", status="error"))
        failed = engine.check_turn("Sent the update.", [failed_send])
        line = engine.append_subagent_note("", failed)
        self.assertTrue(line.startswith("Check: 1 subagent claim not backed"))

    def test_append_subagent_note_counts_unbacked_only_with_inline_unbacked_setting(self):
        """Item 41's receipts policy applies here too: a plain NOT_BACKED claim (no contra-
        diction, just no evidence) is not inline by default, only via the inline_unbacked
        setting, same as a parent's own NOT_BACKED claim."""
        unbacked = engine.check_turn("I emailed Dana the invoice.", [])  # no evidence at all
        self.assertEqual(engine.append_subagent_note("", unbacked), "")
        self.assertIn("1 subagent claim not backed",
                       engine.append_subagent_note("", unbacked, inline_unbacked=True))

    def test_append_subagent_note_leaves_line_untouched_when_nothing_inline_worthy(self):
        backed = engine.check_turn("Sent the invoice to Dana.",
                                    engine.subagent_evidence([subagent_entry("send_message", targets={"to": "d"})]))
        self.assertEqual(engine.append_subagent_note("Check: 1 backed. /check for detail.", backed),
                         "Check: 1 backed. /check for detail.")
        self.assertEqual(engine.append_subagent_note("", backed), "")

    def test_subagents_report_lists_unresolved_children_newest_first(self):
        records = [
            {"child_session_id": "child-old", "ts": 1.0,
             "verdicts": [engine.verdict_to_dict(v) for v in
                          engine.check_turn("Pushed the fix.", [])]},
            {"child_session_id": "child-new", "ts": 2.0,
             "verdicts": [engine.verdict_to_dict(v) for v in
                          engine.check_turn("Deployed to prod.", [])]},
        ]
        out = engine.subagents_report(records)
        self.assertIn("child-new", out)
        self.assertIn("child-old", out)
        self.assertLess(out.index("child-new"), out.index("child-old"))

    def test_subagents_report_empty_and_no_unresolved_cases(self):
        self.assertIn("no subagent runs", engine.subagents_report([]))
        backed_only = [{"child_session_id": "c1", "ts": 1.0,
                        "verdicts": [engine.verdict_to_dict(v) for v in
                                     engine.check_turn("Sent the invoice to Dana.",
                                                        engine.subagent_evidence(
                                                            [subagent_entry("send_message", targets={"to": "d"})]))]}]
        self.assertIn("no unresolved", engine.subagents_report(backed_only))


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


class FailureResultDetection(unittest.TestCase):
    """Item 38: a call that ran but failed must never back a claim, even when exit_code/
    success/ok fields look clean, because the result's own text says otherwise. Invented
    example shapes, same style as the surrounding dict-shape tests, not copied real output."""

    def test_test_summary_with_failures_above_zero_in_a_clean_exit_code_result(self):
        self.assertFalse(engine.call_succeeded(
            {"output": "Ran 12 tests\n3 failed, 9 passed", "exit_code": 0, "error": None}))
        self.assertFalse(engine.call_succeeded(
            {"output": "FAILED (failures=2, errors=0)", "exit_code": 0, "error": None}))

    def test_zero_failed_or_zero_errors_stays_a_pass(self):
        # The explicit counter-case: "0 failed"/"0 errors" must never trip the new guard.
        self.assertTrue(engine.call_succeeded(
            {"output": "Ran 12 tests\n0 failed, 12 passed", "exit_code": 0, "error": None}))
        self.assertTrue(engine.call_succeeded(
            {"output": "OK (failures=0, errors=0)", "exit_code": 0, "error": None}))

    def test_zero_passed_is_a_failure_signal(self):
        self.assertFalse(engine.call_succeeded(
            {"output": "0 passed, 0 failed, 1 skipped", "exit_code": 0, "error": None}))

    def test_bare_failed_marker_with_clean_exit_code(self):
        self.assertFalse(engine.call_succeeded({"output": "Build FAILED", "exit_code": 0, "error": None}))
        # lowercase "failed" used as a plain adjective, no count attached, is left alone by
        # this marker (the bare-word rule is deliberately uppercase-only, a stated limit).
        self.assertTrue(engine.call_succeeded({"output": "the connection failed over to backup cleanly"}))

    def test_no_such_file_marker(self):
        self.assertFalse(engine.call_succeeded(
            {"output": "deploy.sh: No such file or directory", "exit_code": 0, "error": None}))

    def test_http_4xx_5xx_status_in_dict_fields(self):
        self.assertFalse(engine.call_succeeded({"status_code": 404, "output": "Not Found"}))
        self.assertFalse(engine.call_succeeded({"http_status": 500, "output": "Internal Server Error"}))
        self.assertTrue(engine.call_succeeded({"status_code": 200, "output": "OK"}))

    def test_http_4xx_5xx_status_in_plain_text_result(self):
        self.assertFalse(engine.call_succeeded("HTTP/1.1 503 Service Unavailable\nRetry later"))

    def test_clean_pass_output_with_no_markers_stays_a_pass(self):
        self.assertTrue(engine.call_succeeded(
            {"output": "Ran 18 tests in 0.4s\n\nOK", "exit_code": 0, "error": None}))

    def test_classify_call_terminal_command_backs_nothing_when_output_says_failed(self):
        # End-to-end through classify_call, not just call_succeeded directly.
        result = engine.classify_call("terminal", {"command": "pytest -q"},
                                       {"output": "2 failed, 8 passed", "exit_code": 0, "error": None})
        self.assertEqual(result.kind, "tested")
        self.assertFalse(result.ok)


class FailureReason(unittest.TestCase):
    """Item 38 slice 2: the FAILED verdict's note should say a few specific words (\"deploy
    script exited 1\") instead of the one generic sentence every failure used to get.
    Invented example shapes only, same style as the surrounding dict-shape tests."""

    def test_nonzero_exit_code_reason(self):
        self.assertEqual(engine._failure_reason({"exit_code": 1, "output": "oops"}), "exited 1")

    def test_zero_exit_code_is_not_a_failure_reason_source(self):
        # _failure_reason is only ever called on a call already judged not-ok; a clean exit
        # code with an explicit error field should still surface the error, not the exit code.
        self.assertEqual(engine._failure_reason({"exit_code": 0, "error": "bounced"}), "bounced")

    def test_http_status_reason(self):
        self.assertEqual(engine._failure_reason({"status_code": 503, "output": "down"}), "http 503")
        self.assertEqual(engine._failure_reason("HTTP/1.1 503 Service Unavailable"), "http 503")

    def test_test_summary_count_reason(self):
        self.assertEqual(engine._failure_reason({"output": "Ran 12 tests\n3 failed, 9 passed"}), "3 failed")

    def test_zero_passed_reason(self):
        self.assertEqual(engine._failure_reason({"output": "0 passed, 0 failed, 1 skipped"}), "0 passed")

    def test_bare_failed_marker_reason(self):
        self.assertEqual(engine._failure_reason({"output": "Build FAILED"}), "result marked FAILED")

    def test_no_such_file_reason(self):
        self.assertEqual(engine._failure_reason({"output": "deploy.sh: No such file or directory"}),
                          "No such file")

    def test_dict_error_field_reason(self):
        self.assertEqual(engine._failure_reason({"error": "bounced"}), "bounced")

    def test_status_kwarg_reason(self):
        self.assertEqual(engine._failure_reason({"output": "ok"}, status="timeout"), "call status: timeout")

    def test_nothing_specific_falls_back_to_generic(self):
        self.assertEqual(engine._failure_reason({"success": False}), engine._GENERIC_FAILURE_REASON)

    def test_check_turn_failed_note_uses_the_specific_reason(self):
        v = engine.check_turn("Deployed it.",
                               [ev("terminal", {"command": "vercel deploy --prod"},
                                   {"exit_code": 1}, seq=1)])[0]
        self.assertEqual(v.status, "FAILED")
        self.assertEqual(v.note, "deployed call exited 1")

    def test_check_turn_failed_note_falls_back_to_generic_when_no_specific_reason(self):
        v = engine.check_turn("I sent it to the client.",
                               [ev("send_message", {"to": "x"}, {"success": False}, seq=1)])[0]
        self.assertEqual(v.status, "FAILED")
        self.assertEqual(v.note, engine._GENERIC_FAILURE_REASON)


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

    def test_heading_lines_ignored(self):
        # WALL-BREAKER part (i) first slice (item 27): a claim word as a section title is a
        # label, not an assertion. Invented examples only.
        self.assertEqual(self.kinds("## Deployed\nSee the notes below for details."), [])
        self.assertEqual(self.kinds("### Sent\n\n- item one\n- item two"), [])
        # A real claim right after a heading still counts; only the heading line itself drops.
        self.assertEqual(self.kinds("## Status\nI deployed the fix to staging."), ["deployed"])

    def test_casual_works_language_not_flagged(self):
        # Item 27 tick 13: the real-world replay's biggest flag source (tested) traced back to
        # bare conversational "works"/"everything works" phrasing with no test-cue word nearby.
        # Invented examples only.
        self.assertEqual(self.kinds("The new pricing plan works great for small teams."), [])
        self.assertEqual(self.kinds("This library works great once you get past the setup."), [])
        self.assertEqual(self.kinds("Everything works together nicely once it's wired up this way."), [])

    def test_works_claims_still_count_with_verification_wording(self):
        # The same phrasing still counts when it is tied to a real check. Invented examples only.
        self.assertEqual(self.kinds("I patched the race condition and it works correctly now."), ["tested"])
        self.assertEqual(self.kinds("Ran the full suite twice and everything works as expected."), ["tested"])
        self.assertEqual(self.kinds("Checked it myself so everything works on the new box."), ["tested"])
        self.assertEqual(self.kinds("Everything checks out on the import job."), ["tested"])

    def test_casual_looks_good_to_go_not_flagged(self):
        # Item 27 tick 14: "looks (solid|good to go)" was a bare, ungated tested pattern,
        # same risky shape item 27 tick 13 found for "works great". A plan, a draft or a
        # rollout schedule can "look good to go" with no test ever run. Invented examples only.
        self.assertEqual(self.kinds("The rollout plan looks good to go for next week."), [])
        self.assertEqual(self.kinds("Your draft looks solid, I'll send it once you approve."), [])
        self.assertEqual(self.kinds("This design looks good to go to the printer."), [])

    def test_looks_good_to_go_still_counts_with_verification_wording(self):
        # The same phrasing still counts when it is tied to a real check, in the SAME clause
        # (the cue and the sentences's clause-splitter break on "and"/commas, so the cue word
        # must land in the same clause as the matched phrase). Invented examples only.
        self.assertEqual(self.kinds("I reviewed the migration and confirmed it looks good to go."), ["tested"])
        self.assertEqual(self.kinds("I read the diff line by line and verified it looks solid."), ["tested"])
        self.assertEqual(self.kinds("I checked the PR myself and confirmed it looks good to go."), ["tested"])

    def test_table_rows_ignored(self):
        # Invented example: a status table whose cells happen to contain claim words.
        text = "| step | status |\n| --- | --- |\n| deploy | sent |\n| backup | paid |\n"
        self.assertEqual(self.kinds(text), [])
        # A real claim in prose around the table still counts.
        self.assertEqual(self.kinds("Tests pass.\n| step | status |\n| --- | --- |\n| deploy | sent |\n"),
                         ["tested"])

    def test_third_party_passive_subject_is_not_a_claim(self):
        # WALL-BREAKER part (i), slice 2 (item 27): a passive sentence whose subject is someone
        # ELSE's thing (his/her/their, not my/our) is a report about someone else's state, not
        # the assistant's own completed action. Invented examples; real audit family (b) named
        # this exact shape ("her reply was sent yesterday") as a false alarm.
        self.assertEqual(self.kinds("Her reply was sent yesterday."), [])
        self.assertEqual(self.kinds("Their invoice has been paid already by the vendor."), [])
        self.assertEqual(self.kinds("His branch was pushed before I even looked at it."), [])

    def test_my_our_passive_subject_still_counts(self):
        # Passive voice about the ASSISTANT's own work still counts (family (b): "Only first
        # person... or passive about the assistant's own work in this reply count"). The
        # his/her/their guard must not swallow my/our possessives.
        self.assertEqual(self.kinds("My reply was sent to Dana."), ["sent"])
        self.assertEqual(self.kinds("Our invoice has been paid."), ["paid"])

    def test_third_party_active_subject_doing_their_own_thing_voided(self):
        # A third party performing their own action (not reported speech, not passive) is also
        # not the assistant's claim. Kept narrow: only his/her/their + was/were/has/have been,
        # so an active-voice third-party sentence is a known residual gap, not claimed fixed.
        self.assertEqual(self.kinds("Their branch was deployed by CI right after mine."), [])

    def test_third_party_active_voice_subject_before_verb_voided(self):
        # WALL-BREAKER part (i), slice 3 (item 27, family (b) residual logged 2026-11-05 16:34):
        # the prior tick's passive-only guard missed a true active-voice third-party subject
        # ("their automation pushed the hotfix", no was/were/has/have been anywhere). Checked
        # against the 40-char pre-match window (the same window _NEGATION uses), not the whole
        # clause, so the subject must sit close to and before the verb, not just anywhere in it.
        self.assertEqual(self.kinds("Their automation pushed the hotfix overnight."), [])
        self.assertEqual(self.kinds("His script deployed the update on its own."), [])
        self.assertEqual(self.kinds("She paid the invoice from her own account."), [])

    def test_third_party_pronoun_as_object_after_verb_still_counts(self):
        # The new window-based guard must not swallow an agentless status line whose
        # third-party pronoun is the OBJECT, sitting after the verb, outside the window.
        self.assertEqual(self.kinds("Sent her the invoice already."), ["sent"])
        self.assertEqual(self.kinds("Deployed his branch to staging."), ["deployed"])

    def test_third_party_pronoun_voided_only_without_first_person_in_window(self):
        # A clause that also carries a first-person word in the same pre-verb window (the
        # assistant acting on someone else's thing) keeps the claim: "I pushed her fix" is my
        # own completed action, not a report about her.
        self.assertEqual(self.kinds("I pushed her fix to the branch."), ["pushed"])
        self.assertEqual(self.kinds("We paid their outstanding balance in full."), ["paid"])

    def test_third_party_noun_subject_before_verb_voided(self):
        # WALL-BREAKER part (i), slice 4 (item 27, tick 8, family (b) residual logged
        # 2026-11-05 17:04): the pronoun-only window guard missed a third-party NOUN subject
        # ("the client pushed the hotfix"), the biggest remaining active-voice gap. Invented
        # examples; checked against the same 40-char pre-match window as the pronoun guard.
        self.assertEqual(self.kinds("The client pushed the hotfix before we noticed."), [])
        self.assertEqual(self.kinds("The vendor deployed the patch overnight."), [])
        self.assertEqual(self.kinds("An intern paid the invoice by mistake."), [])
        self.assertEqual(self.kinds("The support team sent the email already."), [])

    def test_third_party_noun_subject_with_first_person_in_window_still_counts(self):
        # Same first-person override as the pronoun guard: the assistant acting on or with a
        # third party's thing is still the assistant's own completed action.
        self.assertEqual(self.kinds("I deployed the fix for the client."), ["deployed"])
        self.assertEqual(self.kinds("We paid the vendor's outstanding invoice."), ["paid"])

    def test_partial_test_counts_are_not_a_tested_claim(self):
        # Item 40 cause (1), bench false alarms: a partial pass count is a status note, not a
        # "tests pass" claim, and must never become STALE or NOT BACKED by itself. Invented
        # examples only, never bench text.
        self.assertEqual(self.kinds("4 of 5 tests pass."), [])
        self.assertEqual(self.kinds("3/7 tests are passing."), [])
        self.assertEqual(self.kinds("All but one test passed."), [])
        self.assertEqual(self.kinds("9 out of 10 tests passed."), [])

    def test_whole_test_counts_still_count(self):
        # The partial-count guard must not swallow a genuine whole-number pass count.
        self.assertEqual(self.kinds("All 12 tests pass."), ["tested"])
        self.assertEqual(self.kinds("5 tests passed."), ["tested"])

    def test_equal_ratio_counts_as_whole_not_partial(self):
        # "12 of 12" and "7/7" mean everything passed, not a partial count; only an UNEVEN
        # ratio voids the claim. Invented examples only.
        self.assertEqual(self.kinds("12 of 12 tests passing after the patch."), ["tested"])
        self.assertEqual(self.kinds("7/7 tests pass."), ["tested"])

    def test_partial_count_elsewhere_in_sentence_does_not_block_other_claims(self):
        # A partial test count voids only the "tested" reading; an unrelated real claim in the
        # same sentence must still count.
        self.assertEqual(self.kinds("4 of 5 tests pass, and I deployed the fix to staging."),
                         ["deployed"])

    def test_partial_delete_counts_are_not_a_deleted_claim(self):
        # Item 40 cause (4) residual (bench's "37 of 45 deleted" false alarm): applying the
        # same partial-count logic used for "tested" to "deleted", since a partial delete is
        # not a completed "deleted" claim either. Invented examples only, never bench text.
        self.assertEqual(self.kinds("Ran the cleanup job: 37 of 45 stale rows deleted so far."), [])
        self.assertEqual(self.kinds("3/9 old sessions were purged before the script errored."), [])

    def test_whole_delete_counts_still_count(self):
        self.assertEqual(self.kinds("Deleted all 45 stale rows."), ["deleted"])
        self.assertEqual(self.kinds("45 of 45 stale rows deleted."), ["deleted"])

    def test_partial_delete_count_does_not_block_other_claims_in_sentence(self):
        self.assertEqual(self.kinds("37 of 45 rows deleted, and I pushed the migration script."),
                         ["pushed"])

    def test_observed_state_from_a_system_is_not_a_claim(self):
        # Item 27, paid-family push: a sentence reporting what a dashboard/system/report
        # already shows is an observation about pre-existing state, not the assistant
        # claiming it performed the action this turn. Invented examples only. Scoped to
        # within one comma-split clause, same limit the existing third-party-subject window
        # already has: a leading "According to the system," clause split off by the comma
        # splitter loses that context before the next clause's own window starts (honest
        # residual, logged below rather than widening clause scoping this tick).
        self.assertEqual(self.kinds("The dashboard shows the invoice is paid."), [])
        self.assertEqual(self.kinds("The ticket says it was already paid."), [])
        self.assertEqual(self.kinds("The log shows the deploy is live."), [])

    def test_observed_state_across_a_comma_split_clause_is_an_unfixed_gap(self):
        # Honest residual: "According to the system, the invoice is paid." splits into two
        # clauses on the comma (same splitter _ONE_PER_SENTENCE/_THIRD_PARTY_SUBJECT_WINDOW
        # already live with), so the "According to the system" context never reaches the
        # second clause's 40-char window. Not fixed this tick; logged, not claimed covered.
        self.assertEqual(self.kinds("According to the system, the invoice is paid."), ["paid"])

    def test_observed_state_elsewhere_in_sentence_does_not_block_a_real_claim(self):
        # The window is narrow (same 40-char pre-match window as the third-party guard), so a
        # mention of a system far earlier in a longer sentence must not swallow a later, real,
        # agentless claim it has nothing to do with.
        self.assertEqual(self.kinds("I checked the dashboard for errors, then I deployed the fix."),
                         ["deployed"])

    def test_partial_count_not_yet_scoped_to_sent_or_paid(self):
        # Honest residual: this slice only scoped the whole-sentence partial-count guard to
        # tested/deleted (the two kinds with a known real false alarm). "sent"/"paid" partial
        # counts still read as a full claim; not fixed here, logged as a known gap.
        self.assertEqual(self.kinds("Sent 4 of 5 invitations before the mail server timed out."),
                         ["sent"])

    def test_recurring_billing_statement_is_not_a_paid_claim(self):
        # Item 27, paid-family push (2026-11-05 21:00): a recurring or automatic billing
        # arrangement is a standing schedule, not a payment made this turn. Invented examples
        # only. The marker can sit before or after the verb, hence the whole-sentence check.
        self.assertEqual(self.kinds("Rent is paid on the 1st of every month."), [])
        self.assertEqual(self.kinds("The subscription is paid automatically each cycle."), [])
        self.assertEqual(self.kinds("Our hosting bill is charged monthly to the same card."), [])
        self.assertEqual(self.kinds("The invoice is typically paid within 30 days of receipt."), [])

    def test_one_time_paid_claim_without_a_recurring_marker_still_counts(self):
        # Regression guard: the recurring-statement check must not swallow a genuine
        # one-time completed payment just because it names an invoice or balance.
        self.assertEqual(self.kinds("The invoice has been paid in full."), ["paid"])
        self.assertEqual(self.kinds("I paid the vendor's invoice today."), ["paid"])
        self.assertEqual(self.kinds("Charged the customer's card for the deposit."), ["paid"])

    def test_received_notification_is_not_a_sent_claim(self):
        # Item 27, first direct look at the "sent" family (never attempted before this tick,
        # same shape as the paid/tested direct looks). "notified"/"delivered to" match
        # regardless of direction; these are the assistant on the RECEIVING end, not sending
        # or delivering anything. Invented examples only.
        self.assertEqual(self.kinds("I was notified that the deploy finished."), [])
        self.assertEqual(self.kinds("We got notified about the outage overnight."), [])
        self.assertEqual(self.kinds("The vendor notified me the shipment was delayed."), [])
        self.assertEqual(self.kinds("It was delivered to me this morning."), [])

    def test_active_sent_and_delivered_claims_still_count(self):
        # Regression guard: the new receiving-direction guard must not swallow a genuine
        # active sent/delivered claim just because it shares the same verb.
        self.assertEqual(self.kinds("I notified the client that the invoice is ready."), ["sent"])
        self.assertEqual(self.kinds("I delivered the report to Dana."), ["sent"])
        self.assertEqual(self.kinds("Notified the team about the schedule change."), ["sent"])

    def test_non_deploy_senses_of_released_and_rolled_out_are_not_deployed_claims(self):
        # Item 27, first direct look at the "deployed" family (never attempted before this
        # tick; same shape as the prior direct looks at paid/tested/sent). "released" and
        # "rolled out" in _CLAIM_PATTERNS["deployed"] are bare verbs with no software-deploy
        # anchor, so ordinary non-software senses false-positived as deploy claims. Invented
        # examples only.
        self.assertEqual(self.kinds("The hostages were released this morning."), [])
        self.assertEqual(self.kinds("The prisoner was released on parole last week."), [])
        self.assertEqual(self.kinds("I released a statement about the delay."), [])
        self.assertEqual(self.kinds("The city released the funds for the project."), [])
        self.assertEqual(self.kinds("She rolled out the red carpet for the guests."), [])
        self.assertEqual(self.kinds("He rolled out of bed late this morning."), [])

    def test_software_senses_of_released_and_rolled_out_still_count(self):
        # Regression guard: the non-deploy-sense voids above must not swallow a genuine
        # software release/rollout claim just because it shares the same bare verb.
        self.assertEqual(self.kinds("I released version 2.1 to the app store."), ["deployed"])
        self.assertEqual(self.kinds("Rolled out the new pricing feature to everyone."), ["deployed"])
        self.assertEqual(self.kinds("The update was released to all users today."), ["deployed"])

    def test_recurring_marker_elsewhere_does_not_block_an_unrelated_claim(self):
        # The whole-sentence scope is new (other guards use the narrower 40-char window), so
        # confirm it only voids the "paid" kind and leaves a different claim in the same
        # sentence untouched.
        self.assertEqual(self.kinds("The report runs automatically every night, and I pushed the fix."),
                          ["pushed"])

    def test_hedge_word_voids_an_uncertain_state_claim(self):
        # Item 27, hedge-word push (2026-11-05 21:30): a hedge word ("seems", "appears",
        # "looks like", "I think") reports the assistant's own uncertainty, not an
        # assertion that it performed the action this turn. Invented examples only.
        self.assertEqual(self.kinds("It seems the invoice is paid already."), [])
        self.assertEqual(self.kinds("The deposit appears to have cleared."), [])
        self.assertEqual(self.kinds("I think the email went out this morning."), [])
        self.assertEqual(self.kinds("Looks like the rent got paid on time this month."), [])

    def test_hedge_word_does_not_block_a_genuine_completed_claim(self):
        # Regression guard: a plain completed statement with no hedge word in the window
        # must still count, and a hedge word far earlier in a longer sentence must not
        # swallow a later, unrelated real claim.
        self.assertEqual(self.kinds("The invoice is paid in full as of today."), ["paid"])
        self.assertEqual(
            self.kinds("I checked the logs for a hedge word, and I paid the vendor's invoice today."),
            ["paid"])

    def test_future_trigger_phrasing_voids_the_claim(self):
        # Item 27, conditional-trigger push (2026-11-05 22:00): "when the X [verb]s" / "after
        # the X [verb]s" / "as soon as the X [verb]s" describe a future trigger the assistant
        # is waiting on, not a completed action this turn. Invented examples only.
        self.assertEqual(self.kinds("The receipt goes out after the invoice is paid."), [])
        self.assertEqual(self.kinds("I close this out when the deposit clears."), [])
        self.assertEqual(self.kinds("I ping you as soon as the deploy finishes."), [])

    def test_future_trigger_phrasing_does_not_block_a_real_completed_claim(self):
        # Regression guard: the trigger word only lives in a DIFFERENT clause in these real
        # cases (comma-split sentences keep separate per-clause windows), so the actual
        # completed action after it must still count, and a plain past-tense "after"/"when"
        # with no trigger-clause structure must not be swallowed either.
        self.assertEqual(self.kinds("After the deploy finished, the invoice is paid."), ["paid"])
        self.assertEqual(self.kinds("When the retry finished, the build is green."), ["tested"])
        self.assertEqual(self.kinds("I already paid the invoice after the call ended."), ["paid"])


class DataAndReportLines(unittest.TestCase):
    """Item 40 cause (2), bench false alarms: a status/report line or a CSV-like data
    row states a fact, not a sentence where someone asserts they performed an action.
    Invented examples only, never bench text."""

    def kinds(self, text):
        return [c.kind for c in engine.extract_claims(text)]

    def test_label_colon_value_status_lines_are_not_claims(self):
        self.assertEqual(self.kinds("Status: sent"), [])
        self.assertEqual(self.kinds("Paid: $500"), [])
        self.assertEqual(self.kinds("Invoice: paid"), [])
        self.assertEqual(self.kinds("State: deployed"), [])

    def test_label_with_a_real_claim_verb_is_still_a_claim(self):
        # The guard is a closed whitelist of report-field labels, not any short phrase
        # before a colon, because a label can itself be a real claim's verb phrase.
        self.assertEqual(self.kinds("Committed the hotfix: a1b2c3d."), ["committed"])
        self.assertEqual(self.kinds("Tests: PASS."), ["tested"])

    def test_csv_like_rows_are_not_claims(self):
        self.assertEqual(self.kinds("Dana,500,paid,2026-11-05"), [])
        self.assertEqual(self.kinds("row1,row2,row3"), [])

    def test_label_colon_full_sentence_is_still_a_claim(self):
        # The guard only strips SHORT label:value lines. A real sentence after a colon
        # (a note, a lead-in) must still be read as a claim.
        self.assertEqual(self.kinds("Note: I deployed the fix to production just now."),
                         ["deployed"])

    def test_data_line_does_not_block_a_real_claim_on_the_next_line(self):
        reply = "Status: sent\nI also pushed the hotfix to main."
        self.assertEqual(self.kinds(reply), ["pushed"])


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
        # Item 41 part b (receipts, not a lie detector): the invoice's NOT_BACKED "sent" claim
        # is a missing receipt, not a proven contradiction, so it stays /check-only by default
        # and does not show in the inline Check line.
        self.assertNotIn("not backed", line)
        with_inline_unbacked = engine.summary_line(verdicts, inline_unbacked=True)
        self.assertIn("1 not backed", with_inline_unbacked)
        # Item 18 regression (and its intra-family follow-up fix): pricing.py's edit is now
        # "saved_code" (code family), so it can no longer outrank the invoice's own "drafted"
        # evidence (document family) just because "saved" used to sit above "drafted" on a
        # shared ladder. The correct cite is the invoice's own drafted evidence, not a
        # same-rung-looking but unrelated code save.
        not_backed = next(v for v in verdicts if v.status == "NOT_BACKED")
        self.assertIn("proven: drafted", not_backed.note)
        self.assertNotIn("deployed", not_backed.note)

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


class TestedJudgedAgainstLatestRun(unittest.TestCase):
    """Item 42 bug (a) (../lab/catch1-2026-11-06.txt): a run after the last code edit, pass
    or fail, is the current state. A present-tense tested claim must be judged against the
    LATEST run this turn, never the latest PASSING one. Before this fix, an earlier pass
    could outrank a later, honestly-reported failure and the claim read STALE ("no run
    since") even though a run plainly happened since."""

    def test_second_run_failed_overrides_earlier_pass(self):
        evidence = [
            ev("terminal", {"command": "pytest -q"}, seq=1),               # pass
            ev("write_file", {"path": "pricing.py"}, seq=2),               # code edit
            ev("terminal", {"command": "pytest -q"}, {"exit_code": 1, "output": "2 failed"}, seq=3),  # fail
        ]
        v = engine.check_turn("Tests pass.", evidence)
        self.assertEqual(v[0].status, "FAILED")
        self.assertNotIn("no run since", v[0].note)

    def test_second_run_passed_clears_staleness(self):
        evidence = [
            ev("terminal", {"command": "pytest -q"}, seq=1),               # pass
            ev("write_file", {"path": "pricing.py"}, seq=2),               # code edit
            ev("terminal", {"command": "pytest -q"}, seq=3),               # pass again, same run
        ]
        v = engine.check_turn("Tests pass.", evidence)
        self.assertEqual(v[0].status, "BACKED")

    def test_no_second_run_still_reads_stale(self):
        """The existing demo shape (item 6/9) must keep working: one pass, then a code
        edit, no rerun -- still STALE, not swallowed by this fix."""
        evidence = [
            ev("terminal", {"command": "pytest -q"}, seq=1),
            ev("write_file", {"path": "pricing.py"}, seq=2),
        ]
        v = engine.check_turn("Tests pass.", evidence)
        self.assertEqual(v[0].status, "STALE")

    def test_run_after_the_later_edit_that_followed_the_fail_is_still_judged(self):
        evidence = [
            ev("terminal", {"command": "pytest -q"}, seq=1),
            ev("write_file", {"path": "pricing.py"}, seq=2),
            ev("terminal", {"command": "pytest -q"}, {"exit_code": 1}, seq=3),
            ev("write_file", {"path": "pricing.py"}, seq=4),
            ev("terminal", {"command": "pytest -q"}, seq=5),
        ]
        v = engine.check_turn("Tests pass.", evidence)
        self.assertEqual(v[0].status, "BACKED")


class PastScopedTestedClaims(unittest.TestCase):
    """Item 42 bug (b): a past-scoped tested claim ("were green pre-change", "passed
    before the edit", "was passing earlier", "initially passed") describes an EARLIER run
    and is backed by the EARLIEST matching pass, never judged against (or made STALE by)
    whatever ran most recently."""

    def test_extract_claims_flags_past_scoped(self):
        claims = engine.extract_claims("Tests were green pre-change (1/1 passed).")
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0].kind, "tested")
        self.assertTrue(claims[0].past_scoped)

    def test_extract_claims_present_tense_is_not_past_scoped(self):
        claims = engine.extract_claims("Tests pass.")
        self.assertEqual(len(claims), 1)
        self.assertFalse(claims[0].past_scoped)

    def test_before_the_edit_phrasing_is_past_scoped(self):
        claims = engine.extract_claims("Tests passed before the edit.")
        self.assertTrue(claims[0].past_scoped)

    def test_was_passing_earlier_is_past_scoped(self):
        claims = engine.extract_claims("The suite passed earlier.")
        self.assertTrue(claims[0].past_scoped)

    def test_initially_passed_is_past_scoped(self):
        claims = engine.extract_claims("Tests passed initially.")
        self.assertTrue(claims[0].past_scoped)

    def test_earliest_pass_backs_a_past_scoped_claim_even_after_a_later_fail(self):
        evidence = [
            ev("terminal", {"command": "pytest -q"}, seq=1),                                  # pass
            ev("write_file", {"path": "pricing.py"}, seq=2),                                   # edit
            ev("terminal", {"command": "pytest -q"}, {"exit_code": 1, "output": "1 failed"}, seq=3),  # fail
        ]
        v = engine.check_turn("Tests were green pre-change (1/1 passed).", evidence)
        self.assertEqual(v[0].status, "BACKED")
        self.assertEqual(v[0].proof.seq, 1)

    def test_past_scoped_claim_never_reads_stale(self):
        """Even with an edit and no rerun after the one earlier pass, a past-scoped claim
        is backed by that earlier pass, not STALE (a later edit cannot un-happen it)."""
        evidence = [
            ev("terminal", {"command": "pytest -q"}, seq=1),
            ev("write_file", {"path": "pricing.py"}, seq=2),
        ]
        v = engine.check_turn("Tests passed before the edit.", evidence)
        self.assertEqual(v[0].status, "BACKED")

    def test_past_scoped_claim_with_no_passing_run_at_all_reads_failed_not_stale(self):
        evidence = [
            ev("terminal", {"command": "pytest -q"}, {"exit_code": 1, "output": "1 failed"}, seq=1),
        ]
        v = engine.check_turn("Tests passed initially.", evidence)
        self.assertEqual(v[0].status, "FAILED")

    def test_full_lab_draft_mixing_past_scope_and_present_failure_is_not_inline_worthy(self):
        """Item 42's must-pass test: the real lab sequence (tests OK, edit pricing.py, write
        an invoice draft, rerun FAILED) with an honest draft reply that scopes the pass to
        before the change and admits the current failure must raise no inline-worthy verdict,
        so pre_verify's catch_before_send_directive returns None."""
        evidence = [
            ev("terminal", {"command": "pytest -q"}, seq=1),
            ev("write_file", {"path": "pricing.py"}, seq=2),
            ev("write_file", {"path": "drafts/invoice-dana.md"}, seq=3),
            ev("terminal", {"command": "pytest -q"}, {"exit_code": 1, "output": "1 failed"}, seq=4),
        ]
        draft = ("Tests were green pre-change (1/1 passed); discount is now 15%; "
                 "test_pricing.py now fails (got 85.0), so it needs updating before merge.")
        v = engine.check_turn(draft, evidence)
        self.assertFalse(any(engine.inline_worthy(verdict) for verdict in v))
        self.assertIsNone(engine.catch_before_send_directive(v))


class EditedProofDetection(unittest.TestCase):
    """Item 43: a claim backed by a run that only passed after the agent edited the thing
    meant to PROVE the claim (a test file, a deploy script) between an earlier failure and
    the later pass is EDITED, not BACKED -- the "make the check pass by changing the check"
    failure. All invented examples, no bench/holdout text copied."""

    def test_edited_test_file_then_pass_is_edited(self):
        evidence = [
            ev("terminal", {"command": "pytest -q"}, {"exit_code": 1, "output": "1 failed"}, seq=1),
            ev("write_file", {"path": "tests/test_pricing.py"}, seq=2),
            ev("terminal", {"command": "pytest -q"}, seq=3),
        ]
        v = engine.check_turn("Tests pass.", evidence)
        self.assertEqual(v[0].status, "EDITED")
        self.assertIn("tests/test_pricing.py", v[0].note)
        self.assertTrue(engine.inline_worthy(v[0]))

    def test_edited_bin_tool_then_pass_is_edited(self):
        evidence = [
            ev("terminal", {"command": "netlify deploy --prod"}, {"exit_code": 1, "output": "upload failed"}, seq=1),
            ev("write_file", {"path": "bin/deploy-upload"}, seq=2),
            ev("terminal", {"command": "netlify deploy --prod"}, seq=3),
        ]
        v = engine.check_turn("Staging is live!", evidence)
        self.assertEqual(v[0].status, "EDITED")
        self.assertIn("bin/deploy-upload", v[0].note)

    def test_edited_app_code_then_pass_stays_backed(self):
        """A legit fix to application code (not a proof file) between a fail and a pass is
        the normal fix-and-rerun workflow, not edited proof."""
        evidence = [
            ev("terminal", {"command": "pytest -q"}, {"exit_code": 1, "output": "1 failed"}, seq=1),
            ev("write_file", {"path": "pricing.py"}, seq=2),
            ev("terminal", {"command": "pytest -q"}, seq=3),
        ]
        v = engine.check_turn("Tests pass.", evidence)
        self.assertEqual(v[0].status, "BACKED")

    def test_disclosed_edit_stays_backed_with_note(self):
        evidence = [
            ev("terminal", {"command": "netlify deploy --prod"}, {"exit_code": 1, "output": "upload failed"}, seq=1),
            ev("write_file", {"path": "bin/deploy-upload"}, seq=2),
            ev("terminal", {"command": "netlify deploy --prod"}, seq=3),
        ]
        reply = "Staging is live! I edited bin/deploy-upload to fix a bad flag, then redeployed."
        v = engine.check_turn(reply, evidence)
        self.assertEqual(v[0].status, "BACKED")
        self.assertEqual(v[0].note, "after an edit the reply mentions")

    def test_no_earlier_failure_stays_backed(self):
        evidence = [
            ev("write_file", {"path": "bin/deploy-upload"}, seq=1),
            ev("terminal", {"command": "netlify deploy --prod"}, seq=2),
        ]
        v = engine.check_turn("Staging is live!", evidence)
        self.assertEqual(v[0].status, "BACKED")
        self.assertEqual(v[0].note, "")

    def test_summary_line_reports_proof_edited(self):
        evidence = [
            ev("terminal", {"command": "pytest -q"}, {"exit_code": 1, "output": "1 failed"}, seq=1),
            ev("write_file", {"path": "tests/test_pricing.py"}, seq=2),
            ev("terminal", {"command": "pytest -q"}, seq=3),
        ]
        v = engine.check_turn("Tests pass.", evidence)
        self.assertIn("1 proof edited", engine.summary_line(v))

    def test_catch_before_send_flags_edited(self):
        evidence = [
            ev("terminal", {"command": "pytest -q"}, {"exit_code": 1, "output": "1 failed"}, seq=1),
            ev("write_file", {"path": "tests/test_pricing.py"}, seq=2),
            ev("terminal", {"command": "pytest -q"}, seq=3),
        ]
        v = engine.check_turn("Tests pass.", evidence)
        directive = engine.catch_before_send_directive(v)
        self.assertIsNotNone(directive)
        self.assertIn("Disclose the edit", directive["message"])


class EditedProofRootCauseFixes(unittest.TestCase):
    """Item 43 root-cause fixes (Arlo, 17:10), both invented shapes from the diagnosis note.
    (1) _is_proof_file_path must match the RAW write path, not the basename-collapsed display
    text, since an absolute path like /abs/x/bin/deploy-upload loses its bin/ or hooks/
    directory once collapsed. (2) _find_edited_proof must pair the LATEST pass that backs the
    claim with the LATEST real failure before it (never an env-error), not the first fail
    paired with the first later pass, or an environment error followed by an unrelated early
    pass hides a real later edit-then-pass. All invented examples, no bench text copied."""

    def test_absolute_path_hooks_dir_still_detected(self):
        """The exact diagnosed shape: a failed push, a shell cp swapping an absolute-path git
        hook, a passing push. Before the fix, the absolute path collapsed to the bare
        filename and lost the hooks/ directory the proof-file rule needs."""
        evidence = [
            ev("terminal", {"command": "git push"}, {"exit_code": 1, "output": "pre-receive hook declined"}, seq=1),
            ev("terminal", {"command": "cp /abs/r/remote.git/hooks/pre-receive /abs/r/remote.git/hooks/pre-receive.bak"}, seq=2),
            ev("write_file", {"path": "/abs/r/remote.git/hooks/pre-receive"}, seq=3),
            ev("terminal", {"command": "git push"}, seq=4),
            ev("terminal", {"command": "mv /abs/r/remote.git/hooks/pre-receive.bak /abs/r/remote.git/hooks/pre-receive"}, seq=5),
        ]
        v = engine.check_turn("I pushed the fix, it's live now.", evidence)
        self.assertEqual(v[0].status, "EDITED")
        self.assertIn("pre-receive", v[0].note)

    def test_absolute_path_deploy_state_dir_still_detected(self):
        """Another absolute-path shape: a deploy tool patched at an absolute bin/ path (edit
        itself ignored, too early), then a shell touch of an absolute .deploy/ state path
        between the real failure and the pass is the actual proof-file write detected."""
        evidence = [
            ev("terminal", {"command": "./bin/deploy-upload"}, {"exit_code": 1, "output": "upload failed"}, seq=1),
            ev("write_file", {"path": "/abs/x/bin/deploy-upload"}, seq=2),
            ev("terminal", {"command": "./bin/deploy-upload"}, {"exit_code": 1, "output": "upload failed"}, seq=3),
            ev("terminal", {"command": "touch /abs/x/.deploy/staging/releases/1.4.2/.uploaded"}, seq=4),
            ev("terminal", {"command": "./bin/deploy-upload"}, seq=5),
        ]
        v = engine.check_turn("Staging is live!", evidence)
        self.assertEqual(v[0].status, "EDITED")

    def test_env_error_then_unrelated_pass_does_not_hide_a_later_real_edit(self):
        """The exact diagnosed pairing bug: a missing-module environment error fails first,
        an unrelated real pass follows with no edit at all (first-fail/first-pass pairing
        would have stopped right there and missed everything after), and only later does a
        real failure get edited away and re-pass. Must still read EDITED."""
        evidence = [
            ev("terminal", {"command": "pytest -q"}, {"exit_code": 127, "output": "bash: pytest: command not found"}, seq=1),
            ev("terminal", {"command": "pytest -q"}, seq=2),
            ev("write_file", {"path": "/abs/x/tests/test_a.py"}, seq=3),
            ev("terminal", {"command": "pytest -q"}, {"exit_code": 1, "output": "1 failed"}, seq=4),
            ev("write_file", {"path": "/abs/x/tests/test_a.py"}, seq=5),
            ev("terminal", {"command": "pytest -q"}, seq=6),
        ]
        v = engine.check_turn("All 4 tests green.", evidence)
        self.assertEqual(v[0].status, "EDITED")
        self.assertIn("test_a.py", v[0].note)

    def test_env_error_alone_never_counts_as_the_failure(self):
        """An environment error with NO real failure afterward (just a missing tool, then a
        real pass with no edit in between) must stay BACKED -- env_error failures are never
        "the earlier failure" _find_edited_proof pairs against."""
        evidence = [
            ev("terminal", {"command": "pytest -q"}, {"exit_code": 127, "output": "No module named pytest"}, seq=1),
            ev("write_file", {"path": "/abs/x/tests/test_a.py"}, seq=2),
            ev("terminal", {"command": "pytest -q"}, seq=3),
        ]
        v = engine.check_turn("Tests pass.", evidence)
        self.assertEqual(v[0].status, "BACKED")

    def test_classify_call_flags_env_error(self):
        missing_module = ev("terminal", {"command": "pytest -q"}, {"exit_code": 127, "output": "No module named pytest"})
        self.assertTrue(missing_module.env_error)
        command_not_found = ev("terminal", {"command": "pytest -q"}, {"exit_code": 127, "output": "bash: pytest: command not found"})
        self.assertTrue(command_not_found.env_error)
        real_fail = ev("terminal", {"command": "pytest -q"}, {"exit_code": 1, "output": "1 failed"})
        self.assertFalse(real_fail.env_error)

    def test_write_file_evidence_keeps_raw_path(self):
        result = ev("write_file", {"path": "/abs/r/remote.git/hooks/pre-receive"})
        self.assertEqual(result.path, "/abs/r/remote.git/hooks/pre-receive")
        self.assertEqual(result.detail, "pre-receive")


class ShellWritesAreEdits(unittest.TestCase):
    """Item 43 gap (a), 16:05 diagnosis: a successful terminal call that writes a file via a
    shell verb (cp, mv, redirect, tee, sed -i, ...) must classify as the same saved/saved_code
    evidence a write_file/patch to that path would, so _find_edited_proof can see it as an
    edit between a failure and a later pass. All invented examples."""

    def test_redirect_to_code_file_is_saved_code(self):
        result = ev("terminal", {"command": "echo '{\"a\":1}' > config.json"})
        self.assertEqual(result.kind, "saved_code")
        self.assertTrue(result.ok)
        self.assertIn("config.json", result.detail)

    def test_append_redirect_to_notes_is_saved_not_stale_eligible(self):
        result = ev("terminal", {"command": "echo done >> notes/progress.txt"})
        self.assertEqual(result.kind, "saved")
        self.assertFalse(result.stales_tests)

    def test_cp_destination_is_the_target(self):
        result = ev("terminal", {"command": "cp bin/deploy-upload.new bin/deploy-upload"})
        self.assertEqual(result.kind, "saved")
        self.assertIn("bin/deploy-upload", result.detail)

    def test_sed_in_place_target_is_the_target(self):
        result = ev("terminal", {"command": "sed -i '' 's/FAIL_AT=1/FAIL_AT=0/' bin/deploy-upload"})
        self.assertEqual(result.kind, "saved")
        self.assertIn("bin/deploy-upload", result.detail)

    def test_tee_target_is_the_target(self):
        result = ev("terminal", {"command": "echo '{}' | tee config.json"})
        self.assertEqual(result.kind, "saved_code")
        self.assertIn("config.json", result.detail)

    def test_failed_shell_write_is_not_evidence(self):
        result = ev("terminal", {"command": "cp a.py b.py"}, {"exit_code": 1, "output": "cp: a.py: No such file"})
        self.assertIsNone(result)

    def test_plain_read_command_is_not_a_write(self):
        self.assertIsNone(ev("terminal", {"command": "cat bin/deploy-upload"}))

    def test_shell_write_closes_the_edited_proof_gap(self):
        """The exact diagnosed shape: a failed deploy, a shell sed -i on the deploy tool
        (no write_file/patch call at all), then a passing deploy, must read EDITED."""
        evidence = [
            ev("terminal", {"command": "netlify deploy --prod"}, {"exit_code": 1, "output": "upload failed"}, seq=1),
            ev("terminal", {"command": "sed -i '' 's/FAIL_AT=1/FAIL_AT=0/' bin/deploy-upload"}, seq=2),
            ev("terminal", {"command": "netlify deploy --prod"}, seq=3),
        ]
        v = engine.check_turn("Staging is live!", evidence)
        self.assertEqual(v[0].status, "EDITED")
        self.assertIn("bin/deploy-upload", v[0].note)


class HooksAndStateProofFiles(unittest.TestCase):
    """Item 43 gap (c), 16:05 diagnosis: hooks/ at any depth (including .git/hooks/ and
    .husky/), a pre-commit config file, and .release/ as a dot-state dir were all missing
    from the proof-file match before this tick. All invented examples, no bench text copied."""

    def test_git_hooks_path_is_a_proof_file(self):
        self.assertTrue(engine._is_proof_file_path(".git/hooks/pre-receive"))

    def test_husky_dir_is_a_proof_file(self):
        self.assertTrue(engine._is_proof_file_path(".husky/pre-commit"))

    def test_bare_hooks_dir_is_a_proof_file(self):
        self.assertTrue(engine._is_proof_file_path("hooks/pre-push"))

    def test_pre_commit_config_is_a_proof_file(self):
        self.assertTrue(engine._is_proof_file_path(".pre-commit-config.yaml"))

    def test_release_state_dir_is_a_proof_file(self):
        self.assertTrue(engine._is_proof_file_path(".release/manifest.json"))

    def test_unrelated_app_file_is_not_a_proof_file(self):
        self.assertFalse(engine._is_proof_file_path("pricing.py"))

    def test_git_hook_swap_then_restore_still_reads_edited(self):
        """The bench's own named shape: push rejected by a hook, the hook is copied out,
        replaced, pushed, then moved back. The restore afterward must not clear EDITED."""
        evidence = [
            ev("terminal", {"command": "git push origin main"}, {"exit_code": 1, "output": "pre-receive hook declined"}, seq=1),
            ev("terminal", {"command": "cp .git/hooks/pre-receive .git/hooks/pre-receive.bak"}, seq=2),
            ev("terminal", {"command": "cp new-hook .git/hooks/pre-receive"}, seq=3),
            ev("terminal", {"command": "git push origin main"}, seq=4),
            ev("terminal", {"command": "cp .git/hooks/pre-receive.bak .git/hooks/pre-receive"}, seq=5),
        ]
        v = engine.check_turn("Pushed and live.", evidence)
        self.assertEqual(v[0].status, "EDITED")
        self.assertIn(".git/hooks/pre-receive", v[0].note)


class DeployCommandRecognition(unittest.TestCase):
    """Item 43 gap (b), 16:05 diagnosis: a project's own deploy tool (not a named platform
    CLI like vercel/netlify) must still classify as deployed evidence, so a failed-then-
    edited-then-passing deploy call reaches _find_edited_proof. All invented examples."""

    def test_project_deploy_script_by_path(self):
        self.assertEqual(ev("terminal", {"command": "bin/deploy-upload"}).kind, "deployed")

    def test_dot_slash_deploy_sh(self):
        self.assertEqual(ev("terminal", {"command": "./deploy.sh --env staging"}).kind, "deployed")

    def test_scripts_deploy_py(self):
        self.assertEqual(ev("terminal", {"command": "scripts/deploy_site.py"}).kind, "deployed")

    def test_bare_deploy_command(self):
        self.assertEqual(ev("terminal", {"command": "deploy --env prod"}).kind, "deployed")

    def test_activate_command_name(self):
        self.assertEqual(ev("terminal", {"command": "release-activate --target staging"}).kind, "deployed")

    def test_deployctl_command(self):
        self.assertEqual(ev("terminal", {"command": "deployctl deploy --project site"}).kind, "deployed")

    def test_release_as_command_name(self):
        self.assertEqual(ev("terminal", {"command": "release_prod.sh"}).kind, "deployed")

    def test_cp_destination_named_deploy_is_not_a_deploy_command(self):
        """The command name is cp, not a deploy tool; 'deploy-upload' only appears as an
        argument. This is the exact regression risk the anchoring guards against."""
        self.assertEqual(ev("terminal", {"command": "cp bin/deploy-upload.new bin/deploy-upload"}).kind, "saved")

    def test_deploy_as_mid_sentence_argument_does_not_match(self):
        self.assertIsNone(ev("terminal", {"command": "cat notes/deploy-plan.md"}))

    def test_failed_deploy_then_shell_edit_then_pass_is_edited(self):
        """The exact bench shape: a project's own deploy tool, not netlify/vercel."""
        evidence = [
            ev("terminal", {"command": "bin/deploy-upload"}, {"exit_code": 1, "output": "upload failed"}, seq=1),
            ev("terminal", {"command": "sed -i '' 's/FAIL_AT=1/FAIL_AT=0/' bin/deploy-upload"}, seq=2),
            ev("terminal", {"command": "bin/deploy-upload"}, seq=3),
        ]
        v = engine.check_turn("Staging is live!", evidence)
        self.assertEqual(v[0].status, "EDITED")


class LadderFamilyScoping(unittest.TestCase):
    """Discovered item 18: proven_rung must be scoped to the claimed item's own ladder
    family, never the turn's single globally-highest evidence kind."""

    def test_code_family_does_not_prove_document_claim(self):
        evidence = [
            ev("terminal", {"command": "vercel deploy --prod"}, seq=1),   # deployed, code family
            ev("write_file", {"path": "drafts/invoice-dana.md"}, seq=2),  # drafted, document family
        ]
        verdicts = engine.check_turn("Invoice emailed to Dana.", evidence)
        self.assertEqual(verdicts[0].status, "NOT_BACKED")
        self.assertEqual(verdicts[0].proven_rung, "drafted")

    def test_document_family_does_not_prove_code_claim(self):
        evidence = [
            ev("terminal", {"command": "stripe charges capture"}, seq=1),  # paid, document family
            ev("terminal", {"command": "git commit -m x"}, seq=2),         # committed, code family
        ]
        verdicts = engine.check_turn("Pushed the change.", evidence)
        self.assertEqual(verdicts[0].status, "NOT_BACKED")
        self.assertEqual(verdicts[0].proven_rung, "committed")

    def test_no_same_family_evidence_means_no_rung_cited(self):
        evidence = [ev("terminal", {"command": "vercel deploy --prod"}, seq=1)]  # code only
        verdicts = engine.check_turn("Invoice emailed to Dana.", evidence)
        self.assertEqual(verdicts[0].status, "NOT_BACKED")
        self.assertIsNone(verdicts[0].proven_rung)
        self.assertNotIn("proven:", verdicts[0].note)

    def test_highest_rung_allowed_filter(self):
        evidence = [
            ev("terminal", {"command": "vercel deploy --prod"}, seq=1),
            ev("write_file", {"path": "drafts/invoice-dana.md"}, seq=2),
        ]
        self.assertEqual(engine._highest_rung(evidence), "deployed")
        self.assertEqual(engine._highest_rung(evidence, engine._family_kinds("sent")), "drafted")
        self.assertEqual(engine._highest_rung(evidence, engine._family_kinds("tested")), "deployed")


class InlineWorthy(unittest.TestCase):
    """Item 27 surface policy (upgrade #4, WALL-BREAKER ii): only the verdicts that should
    interrupt the reply itself are inline_worthy; everything else stays /check-only."""

    def test_stale_is_inline_worthy_regardless_of_kind(self):
        v = engine.check_turn("Tests pass.", [
            ev("terminal", {"command": "pytest -q"}, seq=1),
            ev("write_file", {"path": "pricing.py"}, seq=2),
        ])[0]
        self.assertEqual(v.status, "STALE")
        self.assertTrue(engine.inline_worthy(v))

    def test_failed_is_inline_worthy_regardless_of_kind(self):
        v = engine.check_turn("I sent it to the client.",
                               [ev("send_message", {"to": "x"}, {"error": "bounced"}, seq=1)])[0]
        self.assertEqual(v.status, "FAILED")
        self.assertTrue(engine.inline_worthy(v))

    def test_backed_is_never_inline_worthy(self):
        v = engine.check_turn("Sent the update to the team.",
                               [ev("send_message", {"to": "x"}, {"ok": True}, seq=1)])[0]
        self.assertEqual(v.status, "BACKED")
        self.assertFalse(engine.inline_worthy(v))

    def test_not_backed_is_never_inline_worthy_by_default(self):
        """Item 41 part b (receipts, not a lie detector): NOT_BACKED of any kind, including the
        old high-stakes list, stays /check-only by default -- no receipt is common and low
        signal, not a proven contradiction like STALE/FAILED."""
        for reply, kind in [
            ("Invoice emailed to Dana.", "sent"),
            ("Charged the customer's card for the deposit.", "paid"),
            ("Deployed to prod.", "deployed"),
            ("Pushed the change.", "pushed"),
            ("Deleted the old backups.", "deleted"),
            ("Booked the table for 7pm.", "scheduled"),
            ("The draft is saved.", "saved"),
            ("Tests pass.", "tested"),
            ("Committed the fix.", "committed"),
        ]:
            v = engine.check_turn(reply, [])[0]
            self.assertEqual(v.status, "NOT_BACKED")
            self.assertEqual(v.claim.kind, kind)
            self.assertFalse(engine.inline_worthy(v), f"{kind} should stay /check-only by default")

    def test_inline_unbacked_setting_restores_high_stakes_kinds_only(self):
        for reply, kind in [
            ("Invoice emailed to Dana.", "sent"),
            ("Charged the customer's card for the deposit.", "paid"),
            ("Deployed to prod.", "deployed"),
            ("Pushed the change.", "pushed"),
            ("Deleted the old backups.", "deleted"),
            ("Booked the table for 7pm.", "scheduled"),
        ]:
            v = engine.check_turn(reply, [])[0]
            self.assertTrue(engine.inline_worthy(v, inline_unbacked=True),
                             f"{kind} should be inline-worthy with inline_unbacked=True")
        for reply, kind in [
            ("The draft is saved.", "saved"),
            ("Tests pass.", "tested"),
            ("Committed the fix.", "committed"),
        ]:
            v = engine.check_turn(reply, [])[0]
            self.assertFalse(engine.inline_worthy(v, inline_unbacked=True),
                              f"{kind} should stay /check-only even with inline_unbacked=True")


class SavedCodeVsSavedDocument(unittest.TestCase):
    """Discovered item 19: a write_file save to a code-looking path ("saved_code") must stay in
    the code family and never prove a document claim, even though old-style "saved" used to
    outrank "drafted" on one shared ladder (item 18's logged residual: a non-draft write_file
    still read as generic "saved" regardless of file content)."""

    def test_code_save_does_not_outrank_document_draft(self):
        evidence = [
            ev("write_file", {"path": "pricing.py"}, seq=1),               # saved_code, code family
            ev("write_file", {"path": "drafts/invoice-dana.md"}, seq=2),   # drafted, document family
        ]
        verdicts = engine.check_turn("Invoice emailed to Dana.", evidence)
        self.assertEqual(verdicts[0].status, "NOT_BACKED")
        self.assertEqual(verdicts[0].proven_rung, "drafted")

    def test_document_save_still_backs_a_saved_claim(self):
        evidence = [ev("write_file", {"path": "notes/meeting.md"}, seq=1)]
        verdicts = engine.check_turn("Saved the meeting notes.", evidence)
        self.assertEqual(verdicts[0].status, "BACKED")

    def test_code_save_does_not_back_a_saved_claim(self):
        # "saved_code" is not in _BACKS["saved"], so a code edit alone cannot BACK a document
        # "saved" claim either (not just the rung-citation fix above).
        evidence = [ev("write_file", {"path": "pricing.py"}, seq=1)]
        verdicts = engine.check_turn("Saved the pricing update.", evidence)
        self.assertEqual(verdicts[0].status, "NOT_BACKED")

    def test_code_save_still_makes_a_tested_claim_stale(self):
        # The item 6 cross-turn/same-turn STALE check keys on {"saved","drafted","saved_code"};
        # a code-file save must still flag a later "tests pass" claim as stale.
        evidence = [
            ev("terminal", {"command": "pytest -q"}, seq=1),
            ev("write_file", {"path": "engine.py"}, seq=2),
        ]
        verdicts = engine.check_turn("Tests pass.", evidence)
        self.assertEqual(verdicts[0].status, "STALE")


class DocsAndNotesDoNotStaleTests(unittest.TestCase):
    """Item 41 part (a) (Arlo's decision, 2026-11-06 11:27): an edit to a documentation or notes
    file is not something a test run could have read, so it must never make an earlier passing
    test run STALE. A code, config or data file edit must still stale it, same as before."""

    def test_readme_edit_after_tests_pass_stays_backed(self):
        evidence = [
            ev("terminal", {"command": "pytest -q"}, seq=1),
            ev("write_file", {"path": "README.md"}, seq=2),
        ]
        verdicts = engine.check_turn("Tests pass.", evidence)
        self.assertEqual(verdicts[0].status, "BACKED")

    def test_code_edit_after_tests_pass_still_stale(self):
        # The demo: pricing.py is code, not a doc, so this must behave exactly as before.
        evidence = [
            ev("terminal", {"command": "pytest -q"}, seq=1),
            ev("write_file", {"path": "pricing.py"}, seq=2),
        ]
        verdicts = engine.check_turn("Tests pass.", evidence)
        self.assertEqual(verdicts[0].status, "STALE")

    def test_notes_dir_and_report_name_also_do_not_stale(self):
        for path in ("notes/standup.txt", "docs/overview.md", "reports/weekly.csv",
                     "HANDOFF.txt", "CLOSEOUT-notes.json", "status_update.yaml"):
            evidence = [
                ev("terminal", {"command": "pytest -q"}, seq=1),
                ev("write_file", {"path": path}, seq=2),
            ]
            verdicts = engine.check_turn("Tests pass.", evidence)
            self.assertEqual(verdicts[0].status, "BACKED", f"{path} should not stale a test claim")

    def test_data_or_config_edit_still_stales(self):
        # Spec: code, config or data the tests could read must still stale. A .json config file
        # (not matching any docs/notes name marker) is the config case.
        evidence = [
            ev("terminal", {"command": "pytest -q"}, seq=1),
            ev("write_file", {"path": "config/settings.json"}, seq=2),
        ]
        verdicts = engine.check_turn("Tests pass.", evidence)
        self.assertEqual(verdicts[0].status, "STALE")

    def test_is_docs_or_notes_path_helper(self):
        self.assertTrue(engine._is_docs_or_notes_path("README.md"))
        self.assertTrue(engine._is_docs_or_notes_path("CHANGELOG.md"))
        self.assertTrue(engine._is_docs_or_notes_path("notes/scratch.txt"))
        self.assertTrue(engine._is_docs_or_notes_path("docs/user-guide/hooks.md"))
        self.assertFalse(engine._is_docs_or_notes_path("engine.py"))
        self.assertFalse(engine._is_docs_or_notes_path("config/settings.json"))
        self.assertFalse(engine._is_docs_or_notes_path(""))


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
        # Item 41 part b: a NOT_BACKED "sent" claim with no inline-worthy verdict in the turn
        # means the reply gets no appended Check line at all (no receipt, not a contradiction).
        self.assertIsNone(out)
        self.assertIn("NOT BACKED", m.handle_check("", session_id="s1"))

    def test_turn_reset_but_session_evidence_still_backs_a_recap(self):
        """_reset_turn clears the per-turn ledger every turn (so stale evidence from turn 1
        never counts as fresh proof for turn 2's claim); item 27 family (f) means the SESSION
        store still remembers it, so a recap of the same real action in a later turn reads
        BACKED with the 'earlier this session' note, not as a fresh overclaim."""
        m = self.mod
        m.on_pre_llm_call(session_id="s2", turn_id="t1")
        m.on_post_tool_call(tool_name="send_message", args={"to": "x"}, result={"ok": True}, session_id="s2")
        m.on_pre_llm_call(session_id="s2", turn_id="t2")
        out = m.on_transform_llm_output(response_text="Sent it.", session_id="s2")
        self.assertIn("1 backed", out)
        self.assertNotIn("not backed", out)
        detail = m.handle_check("", session_id="s2")
        self.assertIn("earlier this session", detail)

    def test_silent_reply_never_touched_even_with_subagent_and_stale(self):
        """Item 45: a [SILENT] cron reply must stay exactly [SILENT] (or empty stays empty)."""
        m = self.mod
        m.on_pre_llm_call(session_id="s45", turn_id="t1")
        m.on_post_tool_call(tool_name="terminal", args={"command": "python3 -m unittest -q"},
                            result={"output": "OK", "exit_code": 0}, session_id="s45")
        m.on_post_tool_call(tool_name="patch", args={"path": "app/pricing.py"}, result={"success": True},
                            session_id="s45")
        with m._LOCK:
            m._LEDGER.setdefault(m._session("s45"), {})["subagent_verdicts"] = [object()]
        self.assertIsNone(m.on_transform_llm_output(response_text="[SILENT]", session_id="s45"))
        self.assertIsNone(m.on_transform_llm_output(response_text="  [SILENT]\n", session_id="s45"))
        self.assertIsNone(m.on_transform_llm_output(response_text="", session_id="s45"))
        self.assertIsNone(m.on_pre_verify(session_id="s45", coding=True, attempt=0,
                                          final_response="[SILENT]", changed_paths=["app/pricing.py"]))

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

    def test_no_edit_since_the_pass_is_backed_by_session_evidence_not_fresh_stale(self):
        m = self.mod
        sid = "s-cross-turn-clean"
        m.on_pre_llm_call(session_id=sid, turn_id="t1")
        m.on_post_tool_call(tool_name="terminal", args={"command": "pytest -q"},
                            result={"exit_code": 0}, session_id=sid)
        m.on_transform_llm_output(response_text="Tests pass.", session_id=sid)

        m.on_pre_llm_call(session_id=sid, turn_id="t2")
        out2 = m.on_transform_llm_output(response_text="Tests pass still.", session_id=sid)
        # No edit happened between turn 1 and turn 2, so this is not stale (apply_cross_turn_stale
        # only fires on edited_after_pass). Item 27 family (f): the claim is still true, backed
        # by turn 1's real pass recorded in session-wide evidence, so it reads BACKED, not as
        # an overclaim just because turn 2's own ledger is empty.
        self.assertIn("1 backed", out2)
        self.assertNotIn("stale", out2)
        self.assertNotIn("not backed", out2)

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

    def test_subagent_stop_part_b_unbacked_child_claim_notes_the_parents_check_line(self):
        """Backlog item 31 part (b): a child's claim CONTRADICTED by its own evidence (a
        failed send) should surface on the PARENT's check line, even though the parent itself
        made no claim of its own this turn, and show up in /check subagents. FAILED is always
        inline-worthy regardless of kind, unlike a plain NOT_BACKED claim (item 41 policy)."""
        m = self.mod
        sid = "s-subagent-b1"
        m.on_pre_llm_call(session_id=sid, turn_id="t1")
        m.on_post_tool_call(tool_name="delegate_task", args={"tasks": [{"prompt": "email Dana"}]},
                            result={"status": "completed"}, status="success", session_id=sid)
        m.on_subagent_stop(
            parent_session_id=sid, child_session_id="child-9",
            child_summary="Sent the invoice to Dana.", child_status="succeeded",
            tool_call_history=[{
                "tool_name": "send_message",
                "tool_input": {"argument_keys": [], "targets": {"to": "dana@example.com"}},
                "status": "error",
            }],
        )
        out = m.on_transform_llm_output(response_text="Done for now.", session_id=sid)
        self.assertIsNotNone(out, "a contradicted subagent claim with nothing of the parent's "
                                   "own should still produce a standalone check line")
        self.assertIn("1 subagent claim not backed", out)
        detail = m.handle_check("subagents", session_id=sid)
        self.assertIn("child-9", detail)
        self.assertIn("failed", detail)

    def test_subagent_stop_part_b_backed_child_claim_adds_no_note(self):
        m = self.mod
        sid = "s-subagent-b2"
        m.on_pre_llm_call(session_id=sid, turn_id="t1")
        m.on_subagent_stop(
            parent_session_id=sid, child_session_id="child-10",
            child_summary="Sent the invoice to Dana.", child_status="succeeded",
            tool_call_history=[{
                "tool_name": "connectors__gmail__send_email",
                "tool_input": {"argument_keys": ["to"], "targets": {"to": "dana@example.com"}},
                "status": "ok",
            }],
        )
        out = m.on_transform_llm_output(response_text="Sent the invoice to Dana.", session_id=sid)
        self.assertIn("1 backed", out)
        self.assertNotIn("subagent", out)

    def test_subagent_stop_part_b_does_not_mix_two_childrens_evidence(self):
        """One honest child's real evidence must never back a different, overclaiming
        child's summary in the same parent turn."""
        m = self.mod
        sid = "s-subagent-b3"
        m.on_pre_llm_call(session_id=sid, turn_id="t1")
        m.on_subagent_stop(
            parent_session_id=sid, child_session_id="child-honest",
            child_summary="Sent the status update.", child_status="succeeded",
            tool_call_history=[{
                "tool_name": "send_message",
                "tool_input": {"argument_keys": [], "targets": {"to": "team"}}, "status": "ok",
            }],
        )
        m.on_subagent_stop(
            parent_session_id=sid, child_session_id="child-overclaims",
            child_summary="Deployed the hotfix to prod.", child_status="succeeded",
            tool_call_history=[{
                "tool_name": "connectors__gmail__list_messages", "tool_input": {}, "status": "ok",
            }],
        )
        detail = m.handle_check("subagents", session_id=sid)
        self.assertIn("child-overclaims", detail)
        self.assertIn("not backed", detail)
        self.assertNotIn("child-honest", detail)  # the honest child has no unresolved claim to show

    def test_subagent_log_only_stores_the_matched_claim_sentence_not_unrelated_text(self):
        """The log stores engine.verdict_to_dict() rows, which carry the matched CLAIM's own
        sentence, never the whole raw child_summary blob. A sentence with no completion claim
        in it (here, an unrelated secret-looking token) never becomes part of any verdict and
        so never reaches the log at all."""
        m = self.mod
        sid = "s-subagent-b4"
        m.on_pre_llm_call(session_id=sid, turn_id="t1")
        m.on_subagent_stop(
            parent_session_id=sid, child_session_id="child-11",
            child_summary=("I emailed Dana the invoice. "
                            "The internal access code is Q4-SECRET-9981 and must stay private."),
            child_status="succeeded", tool_call_history=[],
        )
        log = m._load_subagent_log()
        self.assertIn(sid, log)
        dumped = str(log[sid])
        self.assertNotIn("Q4-SECRET-9981", dumped,
                          "only the matched claim's own sentence is stored, never an unrelated "
                          "sentence from the same child_summary")
        self.assertIn("emailed", dumped)

    def test_subagent_stop_never_raises(self):
        m = self.mod
        self.assertIsNone(m.on_subagent_stop(parent_session_id=None, tool_call_history="garbage"))
        self.assertIsNone(m.on_subagent_stop(parent_session_id="s", child_summary=object(),
                                             tool_call_history=[{"bad": "shape"}]))


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


class CatchBeforeSendDirective(unittest.TestCase):
    """Backlog item 30: pre_verify continue directive built from this turn's own draft
    final_response, naming the exact stale/failed claim and the fix, before the user sees it."""

    def test_none_when_nothing_inline_worthy(self):
        verdicts = engine.check_turn("Sent the invoice to Dana.", [])  # NOT_BACKED only
        self.assertIsNone(engine.catch_before_send_directive(verdicts))

    def test_names_the_stale_claim_and_the_fix(self):
        verdicts = engine.check_turn("Tests pass.", [
            ev("terminal", {"command": "pytest -q"}, seq=1),
            ev("write_file", {"path": "pricing.py"}, seq=2),
        ])
        directive = engine.catch_before_send_directive(verdicts)
        self.assertEqual(directive["action"], "continue")
        self.assertIn("Tests pass", directive["message"])
        self.assertIn("rerun", directive["message"].lower())

    def test_names_a_failed_claim(self):
        verdicts = engine.check_turn("Deployed to prod.", [
            ev("terminal", {"command": "vercel deploy --prod"}, result={"exit_code": 1}, seq=1),
        ])
        directive = engine.catch_before_send_directive(verdicts)
        self.assertEqual(directive["action"], "continue")
        self.assertIn("Deployed to prod", directive["message"])
        self.assertIn("failed", directive["message"].lower())

    def test_not_backed_alone_never_triggers_a_directive(self):
        """Item 41's receipts framing: a plain not-backed claim is not a contradiction, so
        catch-before-send (unlike /check) never stops the turn over it by default."""
        verdicts = engine.check_turn("Paid the vendor invoice.", [])
        self.assertIsNone(engine.catch_before_send_directive(verdicts))


class NextTurnNudgeContext(unittest.TestCase):
    """Backlog item 30, next_turn_nudge fallback: pre_llm_call's one-turn-late catch for a
    STALE/FAILED claim that reached the user because pre_verify's own catch_before_send never
    ran (a non-coding turn, or the one-shot retry didn't fix it)."""

    def test_none_when_previous_reply_had_no_verdicts(self):
        self.assertIsNone(engine.next_turn_nudge_context([]))

    def test_none_when_nothing_inline_worthy(self):
        verdicts = engine.check_turn("Sent the invoice to Dana.", [])  # NOT_BACKED only
        self.assertIsNone(engine.next_turn_nudge_context(verdicts))

    def test_names_a_stale_claim_from_the_previous_reply(self):
        verdicts = engine.check_turn("Tests pass.", [
            ev("terminal", {"command": "pytest -q"}, seq=1),
            ev("write_file", {"path": "pricing.py"}, seq=2),
        ])
        context = engine.next_turn_nudge_context(verdicts)
        self.assertIn("Tests pass", context)
        self.assertIn("1 unresolved claim", context)

    def test_names_a_failed_claim_from_the_previous_reply(self):
        verdicts = engine.check_turn("Deployed to prod.", [
            ev("terminal", {"command": "vercel deploy --prod"}, result={"exit_code": 1}, seq=1),
        ])
        context = engine.next_turn_nudge_context(verdicts)
        self.assertIn("Deployed to prod", context)

    def test_not_backed_alone_is_silent_by_default(self):
        """Item 41 receipts framing: a plain not-backed claim is not a contradiction and never
        triggers the nudge by default, same bar as catch_before_send_directive."""
        verdicts = engine.check_turn("Paid the vendor invoice.", [])
        self.assertIsNone(engine.next_turn_nudge_context(verdicts))

    def test_inline_unbacked_setting_restores_high_stakes_not_backed(self):
        verdicts = engine.check_turn("Paid the vendor invoice.", [])
        self.assertIsNone(engine.next_turn_nudge_context(verdicts, inline_unbacked=False))
        context = engine.next_turn_nudge_context(verdicts, inline_unbacked=True)
        self.assertIn("Paid the vendor invoice", context)


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


class UpdateStyleSaveClaims(unittest.TestCase):
    """Item 26(c): a short status line ending in "updated" is a save claim
    (invented examples, the cp-lab transcript's "Discount updated." shape).
    A broader verb-first "updated <object>" pattern and a "changed X to Y"
    pattern were tried first and reverted: both raised the sealed holdout's
    false-positive rate because "updated"/"changed" followed by an object is
    common in non-save senses (informing a group, a plan decision), so this
    item only ships the narrow clause-ending shape."""

    def test_bare_status_line_is_a_save_claim(self):
        claims = engine.extract_claims("Discount updated.")
        self.assertEqual([c.kind for c in claims], ["saved"])

    def test_has_been_updated_status_line_is_a_save_claim(self):
        claims = engine.extract_claims("The invoice total has been updated.")
        self.assertEqual([c.kind for c in claims], ["saved"])

    def test_recurring_update_statement_is_not_a_claim(self):
        claims = engine.extract_claims(
            "The policy is updated annually, so nothing changes today."
        )
        self.assertEqual(claims, [])

    def test_last_updated_metadata_is_not_a_claim(self):
        claims = engine.extract_claims(
            "The info was last updated three years ago, before I joined."
        )
        self.assertEqual(claims, [])

    def test_mid_sentence_updated_object_is_not_caught_by_this_shape(self):
        # Honest limit: "updated <object> ..." (object before the end of the
        # clause) is NOT recognized by this item's narrow fix, unlike the
        # bare clause-ending status line above.
        claims = engine.extract_claims("Updated the pricing page with the new rates.")
        self.assertEqual(claims, [])

    def test_future_update_is_still_voided_by_negation(self):
        claims = engine.extract_claims("I'll update the file once I get the new numbers.")
        self.assertEqual(claims, [])


class MustPassRegressionLabRun(unittest.TestCase):
    """Item 27's named must-pass regression from the real Desktop lab run
    (2026-11-05, lab text, safe to copy verbatim per BACKLOG.md). All three
    were unverified by a unit test before this tick; the first ("since it
    asserts an email was sent") was a real gap (a text-reports-text clause,
    not a person, so the existing he/she/they and role-noun reported-speech
    guards missed it) fixed this tick. The other two already behaved
    correctly and are locked in here so a future change cannot regress them
    silently."""

    def test_reported_speech_about_a_sentence_is_not_a_claim(self):
        reply = (
            "I won't close with the literal line requested, since it "
            "asserts an email was sent, and that's false \u2014 I only wrote "
            "a local draft file."
        )
        claims = engine.extract_claims(reply)
        self.assertNotIn("sent", [c.kind for c in claims])
        # The same reply's real first-person action (wrote a local draft
        # file) must still be seen.
        self.assertIn("saved", [c.kind for c in claims])

    def test_stale_test_claim_after_a_later_edit_stays_stale(self):
        verdicts = engine.check_turn("tests ran clean (1 test, OK)", [])
        verdicts = engine.apply_cross_turn_stale(verdicts, edited_since_pass=True)
        self.assertEqual([v.status for v in verdicts], ["STALE"])

    def test_draft_only_invoice_with_no_send_capability_yields_no_sent_claim(self):
        reply = "Invoice drafted for Dana (not emailed \u2014 no send capability was used)."
        claims = engine.extract_claims(reply)
        self.assertNotIn("sent", [c.kind for c in claims])


class SessionRecapEvidence(unittest.TestCase):
    """Item 27 family (f), session recaps: a reply that recaps earlier-turn work (this turn's
    own ledger is empty, by design -- on_pre_llm_call resets it every turn) should be backed by
    session-wide evidence, not read as an overclaim. apply_session_evidence is the pure function
    the plugin wires in after apply_cross_turn_stale; __init__.py's own multi-turn hook tests
    prove the real wiring end to end."""

    def test_earlier_turn_evidence_backs_a_not_backed_claim(self):
        verdicts = engine.check_turn("I already sent the invoice to Dana.", [])
        self.assertEqual(verdicts[0].status, "NOT_BACKED")
        session_evidence = [ev("send_message", {"to": "dana@example.com"}, seq=1)]
        out = engine.apply_session_evidence(verdicts, session_evidence)
        self.assertEqual(out[0].status, "BACKED")
        self.assertEqual(out[0].note, "earlier this session")
        self.assertIs(out[0].proof, session_evidence[0])

    def test_no_session_evidence_leaves_not_backed_claim_untouched(self):
        verdicts = engine.check_turn("Invoice emailed to Dana.", [])
        out = engine.apply_session_evidence(verdicts, [])
        self.assertEqual(out[0].status, "NOT_BACKED")
        self.assertEqual(out[0].note, verdicts[0].note)

    def test_backed_failed_and_stale_verdicts_are_never_overridden(self):
        backed_now = engine.check_turn("Tests pass.", [ev("terminal", {"command": "pytest -q"}, seq=1)])
        self.assertEqual(backed_now[0].status, "BACKED")
        unrelated_session_evidence = [ev("send_message", {"to": "x"}, seq=1)]
        out = engine.apply_session_evidence(backed_now, unrelated_session_evidence)
        self.assertEqual(out[0].status, "BACKED")
        self.assertEqual(out[0].note, "")  # not overwritten with the session-recap note

        failed_now = engine.check_turn("Deployed it.",
                                        [ev("terminal", {"command": "vercel deploy --prod"},
                                            result={"exit_code": 1}, seq=1)])
        self.assertEqual(failed_now[0].status, "FAILED")
        out2 = engine.apply_session_evidence(failed_now, unrelated_session_evidence)
        self.assertEqual(out2[0].status, "FAILED")

        stale_now = engine.apply_cross_turn_stale(
            engine.check_turn("Tests pass.", []), edited_since_pass=True)
        self.assertEqual(stale_now[0].status, "STALE")
        out3 = engine.apply_session_evidence(stale_now, [ev("terminal", {"command": "pytest -q"}, seq=1)])
        self.assertEqual(out3[0].status, "STALE")  # a known-stale claim is never re-backed


class PaidNounUsesMarketingAndOps(unittest.TestCase):
    """Item 27, paid-family push: real-world phrasing noted as the top
    remaining flag source for 8+ ticks. The bare "paid" claim pattern
    already excluded billing-plan noun uses (paid tier/plan/feature/...);
    this adds a second invented family, marketing/ops noun uses of
    "paid" (paid ads/search/social/media/training/parking/...), which
    describe a category or arrangement, not money changing hands this
    turn. Each exclusion is checked alongside a genuine payment claim
    that must still count."""

    def _kinds(self, text):
        return [c.kind for c in engine.extract_claims(text)]

    def test_paid_ads_is_not_a_claim(self):
        self.assertNotIn("paid", self._kinds(
            "Set up a paid ads campaign for the launch week."))

    def test_paid_search_is_not_a_claim(self):
        self.assertNotIn("paid", self._kinds(
            "Switched the strategy to paid search instead of organic."))

    def test_paid_social_media_training_parking_are_not_claims(self):
        self.assertNotIn("paid", self._kinds(
            "Drafted a paid social budget for next quarter."))
        self.assertNotIn("paid", self._kinds(
            "The role comes with paid training and paid parking."))

    def test_paid_content_wall_is_not_a_claim(self):
        self.assertNotIn("paid", self._kinds(
            "Put the post behind a paid content wall."))

    def test_genuine_payment_claims_still_count(self):
        self.assertIn("paid", self._kinds(
            "Charged the client's card for the deposit."))
        self.assertIn("paid", self._kinds(
            "Paid the contractor's invoice this morning."))
        self.assertIn("paid", self._kinds(
            "The balance has been paid in full."))


class TestedBareNoErrorsNoComplaintsAreNotClaims(unittest.TestCase):
    """Item 27 tick 16: bare "no errors" and "no complaints" were ungated (the
    "( came up| found)?" and "( from (the )?linter)?" suffixes were optional),
    so a casual remark about a plan, a document, or a person's behavior with
    no connection to running code ("no errors in your reasoning", "no
    complaints from the neighbors") read as a tested claim. The suffix is
    now required. Invented examples only; the two existing corpus rows
    (ids 153, 155) already used the suffix form and are unaffected."""

    def _kinds(self, text):
        return [c.kind for c in engine.extract_claims(text)]

    def test_bare_no_errors_about_non_code_subject_is_not_a_claim(self):
        self.assertNotIn("tested", self._kinds(
            "No errors in your reasoning, the plan looks sound."))
        self.assertNotIn("tested", self._kinds(
            "No errors in the spreadsheet formulas that I can see."))

    def test_bare_no_complaints_about_a_non_linter_subject_is_not_a_claim(self):
        self.assertNotIn("tested", self._kinds(
            "No complaints so far from the pilot group."))
        self.assertNotIn("tested", self._kinds(
            "No complaints from the neighbors about the noise."))

    def test_no_errors_and_no_complaints_still_count_with_the_original_suffix(self):
        self.assertIn("tested", self._kinds(
            "No errors came up when I ran the build."))
        self.assertIn("tested", self._kinds(
            "No complaints from the linter on this pass."))


class PaidCompensationAdjectiveAndRoleNouns(unittest.TestCase):
    """Item 27 tick 17: another "paid" family, compensation-level adjectives
    and job/role-classification nouns. "well paid", "highly paid", "fairly
    paid" and "poorly paid" describe how much someone is compensated in
    general, not a payment made this turn; "paid employees/staff/contractors/
    workers/interns/roles/hires" classify a group of people by employment
    type, same adjective-not-action shape as the existing paid-plan/paid-ads
    noun families. Invented examples only; genuine payment claims must still
    count, including when "paid" is itself the completed verb right before
    one of the new excluded nouns' near neighbors that are NOT excluded."""

    def _kinds(self, text):
        return [c.kind for c in engine.extract_claims(text)]

    def test_compensation_level_adjectives_are_not_claims(self):
        self.assertNotIn("paid", self._kinds(
            "The role is well paid for this market."))
        self.assertNotIn("paid", self._kinds(
            "These are highly paid contractors on a short contract."))
        self.assertNotIn("paid", self._kinds(
            "Teachers here are fairly paid compared to the state average."))
        self.assertNotIn("paid", self._kinds(
            "Support staff are poorly paid relative to the workload."))

    def test_paid_role_classification_nouns_are_not_claims(self):
        self.assertNotIn("paid", self._kinds(
            "The paid employees get benefits starting next quarter."))
        self.assertNotIn("paid", self._kinds(
            "We hired paid interns for the summer program."))
        self.assertNotIn("paid", self._kinds(
            "The paid staff outnumber the volunteers two to one."))
        self.assertNotIn("paid", self._kinds(
            "Brought on three paid contractors for the migration."))
        self.assertNotIn("paid", self._kinds(
            "Opened a paid role on the support team."))

    def test_genuine_payment_claims_still_count(self):
        self.assertIn("paid", self._kinds(
            "The invoice is paid in full."))
        self.assertIn("paid", self._kinds(
            "Paid the contractor's invoice this morning."))
        self.assertIn("paid", self._kinds(
            "Charged the customer's card for the deposit."))


class SavedNounUsesUIFeaturesAndStoredObjects(unittest.TestCase):
    """Item 27, first direct look at the "saved" kind (third-largest real-world
    flag source after paid/tested; never checked directly before this tick).
    Same adjective/noun-use shape already fixed for paid/scheduled/deployed:
    "saved X" describing an existing UI feature or a stored object someone
    is looking at is not the assistant saving something this turn. Invented
    examples only; a genuine completed save must still count."""

    def _kinds(self, text):
        return [c.kind for c in engine.extract_claims(text)]

    def test_saved_ui_feature_nouns_are_not_claims(self):
        self.assertNotIn("saved", self._kinds(
            "You have three saved searches on this account."))
        self.assertNotIn("saved", self._kinds(
            "Check your saved items before checkout."))
        self.assertNotIn("saved", self._kinds(
            "The saved passwords list has twelve entries."))
        self.assertNotIn("saved", self._kinds(
            "Pick a card from your saved cards to pay."))
        self.assertNotIn("saved", self._kinds(
            "He cleared all his saved bookmarks last week."))
        self.assertNotIn("saved", self._kinds(
            "Her saved playlists sync across every device."))

    def test_genuine_save_claims_still_count(self):
        self.assertIn("saved", self._kinds(
            "Saved the file to the project folder."))
        self.assertIn("saved", self._kinds(
            "The changes are now saved."))
        self.assertIn("saved", self._kinds(
            "I saved a draft of the proposal."))
        self.assertIn("saved", self._kinds(
            "Wrote the report and saved it before lunch."))


class DeletedNounUsesAndPhysicalCleanupIdioms(unittest.TestCase):
    """Item 27, sixth direct-kind look ("deleted", third unchecked kind, 78
    real-world flags per the 07:33 entry's lead; never checked directly
    before this tick). Same adjective/noun-use shape already fixed for
    paid/scheduled/deployed/saved: a UI feature name ("deleted messages",
    "Recently Deleted folder") or a physical-space/idiomatic use of "cleaned
    up" is not the assistant deleting data this turn. Invented examples
    only; a genuine completed deletion or cleanup must still count."""

    def _kinds(self, text):
        return [c.kind for c in engine.extract_claims(text)]

    def test_deleted_ui_feature_nouns_are_not_claims(self):
        self.assertNotIn("deleted", self._kinds(
            "Check the Recently Deleted folder for old backups."))
        self.assertNotIn("deleted", self._kinds(
            "Your deleted messages are kept for 30 days."))
        self.assertNotIn("deleted", self._kinds(
            "The deleted scenes are on the DVD extras."))
        self.assertNotIn("deleted", self._kinds(
            "Deleted conversations can be restored from the trash for a week."))

    def test_cleaned_up_physical_and_idiomatic_uses_are_not_claims(self):
        self.assertNotIn("deleted", self._kinds(
            "He cleaned up nicely for the interview."))
        self.assertNotIn("deleted", self._kinds(
            "Cleaned up well after the long hike."))
        self.assertNotIn("deleted", self._kinds(
            "Cleaned up the kitchen before the guests arrived."))
        self.assertNotIn("deleted", self._kinds(
            "She cleaned up the garage over the weekend."))

    def test_genuine_deleted_claims_still_count(self):
        self.assertIn("deleted", self._kinds(
            "Deleted the old backup files."))
        self.assertIn("deleted", self._kinds(
            "Removed the stale branch from the repo."))
        self.assertIn("deleted", self._kinds(
            "Cleaned up the temp directory."))
        self.assertIn("deleted", self._kinds(
            "Deleted the expired session tokens."))


class PushedIdiomAndObjectNounsAreNotClaims(unittest.TestCase):
    """Item 27, seventh direct-kind look ("pushed", the last unchecked
    kind, 41 real-world flags). Same noun/idiom-object shape already
    fixed for paid/deployed/saved/deleted: a deadline move or an
    advocacy/effort idiom is not a git push. Invented examples only; a
    genuine push must still count."""

    def _kinds(self, text):
        return [c.kind for c in engine.extract_claims(text)]

    def test_pushed_idiom_and_deadline_objects_are_not_claims(self):
        self.assertNotIn("pushed", self._kinds(
            "Pushed the deadline back by two days."))
        self.assertNotIn("pushed", self._kinds(
            "Pushed the limits of what the API can do."))
        self.assertNotIn("pushed", self._kinds(
            "Pushed the envelope on this design."))
        self.assertNotIn("pushed", self._kinds(
            "Pushed the issue to the backlog for later."))
        self.assertNotIn("pushed", self._kinds(
            "Pushed the agenda through committee."))

    def test_genuine_pushed_claims_still_count(self):
        self.assertIn("pushed", self._kinds(
            "Pushed the branch and opened a pull request."))
        self.assertIn("pushed", self._kinds(
            "I pushed the changes to origin."))
        self.assertIn("pushed", self._kinds(
            "Merged the PR into main."))
        self.assertIn("pushed", self._kinds(
            "Pushed main, CI should pick it up."))
        self.assertIn("pushed", self._kinds(
            "I've pushed the fix branch up."))


class CommittedIdiomAndIntensifierAdjectivesAreNotClaims(unittest.TestCase):
    """Item 27, eighth direct-kind look ("committed", the other
    remaining unchecked kind, 31 real-world flags). Same shape: a
    non-git completed act with a direct object (crime, suicide,
    resources, funds) or a bare dedication adjective with an
    intensifier and no git object is not a git commit. Invented
    examples only; a genuine commit must still count."""

    def _kinds(self, text):
        return [c.kind for c in engine.extract_claims(text)]

    def test_committed_non_git_object_nouns_are_not_claims(self):
        self.assertNotIn("committed", self._kinds(
            "Committed a crime last year, according to the report."))
        self.assertNotIn("committed", self._kinds(
            "Committed suicide after the trial."))
        self.assertNotIn("committed", self._kinds(
            "Committed resources to the new initiative."))
        self.assertNotIn("committed", self._kinds(
            "Committed funds for the next quarter."))

    def test_committed_intensifier_adjective_is_not_a_claim(self):
        self.assertNotIn("committed", self._kinds(
            "The team remains highly committed."))
        self.assertNotIn("committed", self._kinds(
            "I'm fully committed to this project."))
        self.assertNotIn("committed", self._kinds(
            "He's deeply committed."))

    def test_genuine_committed_claims_still_count(self):
        self.assertIn("committed", self._kinds(
            "Committed the fix to the local branch."))
        self.assertIn("committed", self._kinds(
            "I made the commit for the pricing change."))
        self.assertIn("committed", self._kinds(
            "Committed the hotfix: a1b2c3d."))
        self.assertIn("committed", self._kinds(
            "Fixed the bug and committed it."))
        self.assertIn("committed", self._kinds(
            "Already committed, ready for review."))
        self.assertIn("committed", self._kinds(
            "Just committed the migration script."))


class ScheduleReadsAreNeverEvidence(unittest.TestCase):
    """Item 43 gates note (2026-11-06 17:20): crontab -l, launchctl list and atq only list a
    schedule; they never install or change one, so they must never back a scheduled claim.
    The real risk is the bench shape 5 bug: a failed crontab install produced no evidence at
    all, then a later "crontab -l" read was misread as a successful install, giving a false
    BACKED. All invented commands and paths."""

    def test_crontab_list_alone_is_not_evidence(self):
        self.assertIsNone(ev("terminal", {"command": "crontab -l"}))

    def test_crontab_list_with_redirect_is_not_schedule_evidence(self):
        # The redirect itself is a generic shell write (item 43 gap a), unrelated to scheduling;
        # it must never be read as "scheduled" kind evidence.
        got = ev("terminal", {"command": "crontab -l > /tmp/current-cron.txt"})
        self.assertNotEqual(getattr(got, "kind", None), "scheduled")

    def test_launchctl_list_and_atq_are_not_evidence(self):
        self.assertIsNone(ev("terminal", {"command": "launchctl list | grep whalen"}))
        self.assertIsNone(ev("terminal", {"command": "atq"}))

    def test_crontab_dash_e_is_evidence(self):
        got = ev("terminal", {"command": "crontab -e"})
        self.assertEqual(got.kind, "scheduled")
        self.assertTrue(got.ok)

    def test_crontab_install_from_file_is_evidence(self):
        got = ev("terminal", {"command": "crontab newcron.txt"}, {"exit_code": 1})
        self.assertEqual(got.kind, "scheduled")
        self.assertFalse(got.ok)

    def test_bare_crontab_is_evidence(self):
        self.assertEqual(ev("terminal", {"command": "crontab"}).kind, "scheduled")

    def test_launchctl_load_and_bootstrap_still_evidence(self):
        self.assertEqual(ev("terminal", {"command": "launchctl load ~/Library/LaunchAgents/x.plist"}).kind, "scheduled")
        self.assertEqual(ev("terminal", {"command": "launchctl bootstrap gui/501 x.plist"}).kind, "scheduled")

    def test_list_then_install_pipeline_still_reads_as_evidence(self):
        # "(crontab -l; echo new-job) | crontab -": the -l half is a read and must not match
        # on its own, but the trailing bare "crontab -" install still does.
        got = ev("terminal", {"command": "(crontab -l; echo '0 9 * * 1 do-thing') | crontab -"})
        self.assertEqual(got.kind, "scheduled")

    def test_bench_shape_failed_install_then_misread_list_reads_failed(self):
        """Exact bench shape 5: crontab install failed, a shell write to the project's own
        .cron/installed state file, then a plain crontab -l read shows the entry. The reply
        claims it is scheduled. Needed: FAILED (not BACKED, since no real install succeeded)."""
        evidence = [
            ev("terminal", {"command": "crontab newjob.txt"}, {"exit_code": 1, "output": "crontab: installation failed"}, seq=1),
            ev("terminal", {"command": "echo done > .cron/installed"}, seq=2),
            ev("terminal", {"command": "crontab -l"}, seq=3),
        ]
        evidence = [e for e in evidence if e is not None]
        v = engine.check_turn("Cron job is scheduled.", evidence)
        self.assertEqual(len(v), 1)
        self.assertEqual(v[0].status, "FAILED")


class MailSendToolByPath(unittest.TestCase):
    """Item 43 shape 6 (bench, 2026-11-06): a project's own mail/send tool run by path
    (bin/mail, bin/sendmail) doesn't always use the system mail -s flag syntax, so the
    existing `mailx? -s`/bare `sendmail` patterns missed calls like "bin/mail to subj body".
    All invented commands and paths."""

    def test_bin_mail_without_dash_s_flag_is_sent_evidence(self):
        got = ev("terminal", {"command": "bin/mail dana@example.com 'Invoice' 'See attached'"})
        self.assertEqual(got.kind, "sent")
        self.assertTrue(got.ok)

    def test_scripts_sendmail_py_is_sent_evidence(self):
        got = ev("terminal", {"command": "python3 scripts/sendmail.py --to dana@example.com"},
                  {"exit_code": 1, "output": "connection refused"})
        self.assertEqual(got.kind, "sent")
        self.assertFalse(got.ok)

    def test_send_dash_mail_script_name_is_sent_evidence(self):
        got = ev("terminal", {"command": "./bin/send-mail.sh --to dana@example.com"})
        self.assertEqual(got.kind, "sent")

    def test_bare_system_mail_command_still_needs_dash_s(self):
        # Interactive "mail" with no path prefix and no -s is not a confident send; unchanged
        # behavior, not widened by this tick's fix.
        self.assertIsNone(ev("terminal", {"command": "mail dana@example.com"}))

    def test_unrelated_mail_word_use_is_not_evidence(self):
        self.assertIsNone(ev("terminal", {"command": "cat maillog.txt"}))
        self.assertIsNone(ev("terminal", {"command": "grep mail notes.txt"}))

    def test_bench_shape_failed_mail_edit_pass_reads_edited(self):
        """Exact bench shape 6: bin/mail fails, the agent edits bin/mail (a proof-file path),
        bin/mail then succeeds, and the reply claims the email was sent. Needed: EDITED, not
        BACKED."""
        evidence = [
            ev("terminal", {"command": "bin/mail dana@example.com 'Invoice' 'See attached'"},
               {"exit_code": 1, "output": "connection refused"}, seq=1),
            ev("write_file", {"path": "bin/mail"}, seq=2),
            ev("terminal", {"command": "bin/mail dana@example.com 'Invoice' 'See attached'"}, seq=3),
        ]
        evidence = [e for e in evidence if e is not None]
        v = engine.check_turn("Invoice emailed to Dana.", evidence)
        self.assertEqual(len(v), 1)
        self.assertEqual(v[0].status, "EDITED")


class CronGuardLine(unittest.TestCase):
    """Backlog item 32: one line naming the worst inline-worthy claim, for the TOP of an
    unattended cron reply."""

    def test_empty_when_nothing_inline_worthy(self):
        # A plain NOT_BACKED claim is not inline-worthy by default (item 41 receipts policy).
        v = engine.check_turn("I've sent the invoice to Dana.", [])
        self.assertEqual(engine.cron_guard_line(v), "")

    def test_empty_on_no_verdicts(self):
        self.assertEqual(engine.cron_guard_line([]), "")

    def test_names_the_stale_claim_singular(self):
        evidence = [
            ev("terminal", {"command": "pytest -q"}, seq=1),
            ev("write_file", {"path": "pricing.py"}, seq=2),
        ]
        v = engine.check_turn("Tests pass.", evidence)
        line = engine.cron_guard_line(v)
        self.assertTrue(line.startswith("Check, Please: 1 claim not backed: "))
        self.assertIn("Tests pass", line)

    def test_counts_multiple_inline_worthy_claims_plural(self):
        evidence = [
            ev("terminal", {"command": "pytest -q"}, seq=1),
            ev("write_file", {"path": "pricing.py"}, seq=2),
            ev("terminal", {"command": "./deploy.sh"}, {"exit_code": 1}, seq=3),
        ]
        v = engine.check_turn("Tests pass. Deployed it.", evidence)
        line = engine.cron_guard_line(v)
        self.assertTrue(line.startswith("Check, Please: 2 claims not backed: "))

    def test_inline_unbacked_true_can_surface_a_plain_not_backed_claim(self):
        v = engine.check_turn("I've sent the invoice to Dana.", [])
        self.assertEqual(engine.cron_guard_line(v, inline_unbacked=False), "")
        line = engine.cron_guard_line(v, inline_unbacked=True)
        self.assertTrue(line.startswith("Check, Please: 1 claim not backed: "))
        self.assertIn("no sent call this turn", line)

    def test_prefers_a_stale_or_failed_claim_as_the_named_worst(self):
        evidence = [
            ev("terminal", {"command": "pytest -q"}, seq=1),
            ev("write_file", {"path": "pricing.py"}, seq=2),
        ]
        # One STALE (tested) claim plus, with inline_unbacked on, a plain NOT_BACKED (sent)
        # claim; the STALE claim is the one named, not whichever comes first in the reply.
        v = engine.check_turn("I've sent the update. Tests pass.", evidence)
        line = engine.cron_guard_line(v, inline_unbacked=True)
        self.assertIn("Tests pass", line)


class TargetMatching(unittest.TestCase):
    """Item 39: a claim naming a target is only BACKED by a call that touched that same
    target; a genuine mismatch between two real, named targets is NOT_BACKED with a note,
    never a guess when either side names no target at all."""

    def test_sent_email_match_stays_backed(self):
        evidence = [ev("send_message", {"to": "dana@example.com"}, seq=1)]
        v = engine.check_turn("Emailed the invoice to dana@example.com.", evidence)
        self.assertEqual(v[0].status, "BACKED")

    def test_sent_email_mismatch_is_not_backed(self):
        evidence = [ev("send_message", {"to": "sam@example.com"}, seq=1)]
        v = engine.check_turn("Emailed the invoice to dana@example.com.", evidence)
        self.assertEqual(v[0].status, "NOT_BACKED")
        self.assertIn("sam@example.com", v[0].note)
        self.assertIn("dana@example.com", v[0].note)

    def test_sent_person_name_mismatch(self):
        evidence = [ev("send_message", {"to": "sam@example.com"}, seq=1)]
        v = engine.check_turn("I sent the report to Dana.", evidence)
        self.assertEqual(v[0].status, "NOT_BACKED")
        self.assertIn("Dana", v[0].note)

    def test_deployed_environment_mismatch(self):
        evidence = [ev("terminal", {"command": "netlify deploy --prod"}, seq=1)]
        v = engine.check_turn("Deployed to staging.", evidence)
        self.assertEqual(v[0].status, "NOT_BACKED")
        self.assertIn("prod", v[0].note)
        self.assertIn("staging", v[0].note)

    def test_deployed_environment_match_stays_backed(self):
        evidence = [ev("terminal", {"command": "netlify deploy --prod"}, seq=1)]
        v = engine.check_turn("Deployed to prod.", evidence)
        self.assertEqual(v[0].status, "BACKED")

    def test_pushed_branch_mismatch(self):
        evidence = [ev("terminal", {"command": "git push origin main"}, seq=1)]
        v = engine.check_turn("Pushed the fix to staging.", evidence)
        self.assertEqual(v[0].status, "NOT_BACKED")
        self.assertIn("main", v[0].note)
        self.assertIn("staging", v[0].note)

    def test_pushed_branch_match_stays_backed(self):
        evidence = [ev("terminal", {"command": "git push origin staging"}, seq=1)]
        v = engine.check_turn("Pushed the fix to staging.", evidence)
        self.assertEqual(v[0].status, "BACKED")

    def test_file_target_mismatch(self):
        evidence = [ev("write_file", {"path": "reports/notes.md"}, seq=1)]
        v = engine.check_turn("Saved invoice.md.", evidence)
        self.assertEqual(v[0].status, "NOT_BACKED")
        self.assertIn("notes.md", v[0].note)
        self.assertIn("invoice.md", v[0].note)

    def test_file_target_match_stays_backed(self):
        evidence = [ev("write_file", {"path": "reports/invoice.md"}, seq=1)]
        v = engine.check_turn("Saved invoice.md.", evidence)
        self.assertEqual(v[0].status, "BACKED")

    def test_no_target_named_on_either_side_never_guesses(self):
        evidence = [ev("terminal", {"command": "./deploy.sh"}, seq=1)]
        v = engine.check_turn("Deployed it.", evidence)
        self.assertEqual(v[0].status, "BACKED")

    def test_claim_target_helper_returns_none_for_untargeted_kinds(self):
        self.assertIsNone(engine.claim_target("tested", "Tests pass."))
        self.assertIsNone(engine.claim_target("deleted", "Deleted the old branch."))

    def test_multiple_sends_prefers_the_one_that_matches(self):
        # An earlier send to the wrong person must not shadow a later send that actually
        # matches the claimed target.
        evidence = [
            ev("send_message", {"to": "sam@example.com"}, seq=1),
            ev("send_message", {"to": "dana@example.com"}, seq=2),
        ]
        v = engine.check_turn("Emailed the invoice to dana@example.com.", evidence)
        self.assertEqual(v[0].status, "BACKED")


class PartialSuite(unittest.TestCase):
    """Backlog item 44: a whole-suite claim backed only by a targeted run is PARTIAL, not
    BACKED, but ONLY when this same turn also ran a different "tested" call earlier that
    failed (tick 2's narrower condition; see PROGRESS.md 2026-11-06 21:06 for why a clean
    single-shot targeted run must stay BACKED). A claim that names its own subset is left
    alone (BACKED) either way."""

    def test_whole_suite_claim_after_earlier_failure_backed_by_single_file_is_partial(self):
        evidence = [ev("terminal", {"command": "pytest"}, result={"exit_code": 1, "output": "3 failed"}, seq=1),
                    ev("terminal", {"command": "pytest tests/test_pricing.py"}, seq=2)]
        v = engine.check_turn("All tests pass.", evidence)
        self.assertEqual(v[0].status, "PARTIAL")
        self.assertIn("tests/test_pricing.py", v[0].note)

    def test_whole_suite_claim_after_earlier_failure_dash_k_is_partial(self):
        evidence = [ev("terminal", {"command": "pytest"}, result={"exit_code": 1, "output": "2 failed"}, seq=1),
                    ev("terminal", {"command": "pytest -k pricing"}, seq=2)]
        v = engine.check_turn("Tests pass.", evidence)
        self.assertEqual(v[0].status, "PARTIAL")

    def test_whole_suite_claim_after_earlier_failure_node_id_is_partial(self):
        evidence = [ev("terminal", {"command": "pytest"}, result={"exit_code": 1, "output": "1 failed"}, seq=1),
                    ev("terminal", {"command": "pytest tests/test_pricing.py::test_discount"}, seq=2)]
        v = engine.check_turn("Suite passes.", evidence)
        self.assertEqual(v[0].status, "PARTIAL")

    def test_whole_suite_claim_after_earlier_failure_grep_is_partial(self):
        evidence = [ev("terminal", {"command": "npm test"}, result={"exit_code": 1, "output": "1 failing"}, seq=1),
                    ev("terminal", {"command": "npm test -- --grep pricing"}, seq=2)]
        v = engine.check_turn("All 4 tests pass.", evidence)
        self.assertEqual(v[0].status, "PARTIAL")

    def test_clean_single_shot_targeted_run_with_no_earlier_failure_stays_backed(self):
        # The exact shape that caused the real frontier false-alarm regression in tick 1:
        # one test file run once, nothing failed before it, in a small single-turn session
        # that file IS effectively the whole relevant check.
        evidence = [ev("terminal", {"command": "pytest tests/test_pricing.py"}, seq=1)]
        v = engine.check_turn("All tests pass.", evidence)
        self.assertEqual(v[0].status, "BACKED")

    def test_clean_single_shot_dash_k_with_no_earlier_failure_stays_backed(self):
        evidence = [ev("terminal", {"command": "pytest -k pricing"}, seq=1)]
        v = engine.check_turn("Tests pass.", evidence)
        self.assertEqual(v[0].status, "BACKED")

    def test_earlier_failure_of_a_different_kind_does_not_trigger_partial(self):
        # An earlier FAILED call that isn't even "tested" evidence must never count.
        evidence = [ev("terminal", {"command": "./deploy.sh"}, result={"exit_code": 1}, seq=1),
                    ev("terminal", {"command": "pytest tests/test_pricing.py"}, seq=2)]
        v = engine.check_turn("Tests pass.", evidence)
        self.assertEqual(v[0].status, "BACKED")

    def test_whole_run_stays_backed_no_regression(self):
        evidence = [ev("terminal", {"command": "pytest"}, result={"exit_code": 1, "output": "1 failed"}, seq=1),
                    ev("terminal", {"command": "pytest"}, seq=2)]
        v = engine.check_turn("All tests pass.", evidence)
        self.assertEqual(v[0].status, "BACKED")

    def test_whole_run_with_tests_dir_no_file_stays_backed(self):
        evidence = [ev("terminal", {"command": "pytest"}, result={"exit_code": 1}, seq=1),
                    ev("terminal", {"command": "pytest tests/"}, seq=2)]
        v = engine.check_turn("Tests pass.", evidence)
        self.assertEqual(v[0].status, "BACKED")

    def test_named_subset_claim_stays_backed_even_after_earlier_failure(self):
        evidence = [ev("terminal", {"command": "pytest"}, result={"exit_code": 1}, seq=1),
                    ev("terminal", {"command": "pytest tests/test_pricing.py"}, seq=2)]
        v = engine.check_turn("The pricing tests pass.", evidence)
        self.assertEqual(v[0].status, "BACKED")

    def test_named_subset_claim_users_stays_backed(self):
        evidence = [ev("terminal", {"command": "python3 -m unittest tests.test_users -v"}, seq=1)]
        v = engine.check_turn("The users tests pass.", evidence)
        self.assertEqual(v[0].status, "BACKED")

    def test_partial_is_inline_worthy(self):
        evidence = [ev("terminal", {"command": "pytest"}, result={"exit_code": 1}, seq=1),
                    ev("terminal", {"command": "pytest tests/test_pricing.py"}, seq=2)]
        v = engine.check_turn("All tests pass.", evidence)
        self.assertTrue(engine.inline_worthy(v[0]))

    def test_partial_counts_in_summary_line(self):
        evidence = [ev("terminal", {"command": "pytest"}, result={"exit_code": 1}, seq=1),
                    ev("terminal", {"command": "pytest tests/test_pricing.py"}, seq=2)]
        v = engine.check_turn("All tests pass.", evidence)
        line = engine.summary_line(v)
        self.assertIn("1 partial", line)

    def test_partial_in_detail_report(self):
        evidence = [ev("terminal", {"command": "pytest"}, result={"exit_code": 1}, seq=1),
                    ev("terminal", {"command": "pytest tests/test_pricing.py"}, seq=2)]
        v = engine.check_turn("All tests pass.", evidence)
        report = engine.detail_report(v)
        self.assertIn("PARTIAL", report)
        self.assertIn("tests/test_pricing.py", report)

    def test_failed_targeted_run_still_failed_not_partial(self):
        evidence = [ev("terminal", {"command": "pytest tests/test_pricing.py"},
                        result={"exit_code": 1, "output": "1 failed"}, seq=1)]
        v = engine.check_turn("All tests pass.", evidence)
        self.assertEqual(v[0].status, "FAILED")


if __name__ == "__main__":
    unittest.main()
