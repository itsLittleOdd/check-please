"""Real-hook harness: loads Check, Please through Hermes' own PluginManager.

Pattern taken read-only from the installed Hermes checkout's own tests:
``~/.hermes/hermes-agent/tests/agent/test_hold_stream_edges.py`` (write a plugin into a temp
``HERMES_HOME``'s ``plugins/`` dir, enable it in ``config.yaml``, call
``hermes_cli.plugins._reset_plugin_managers_for_tests()`` then ``get_plugin_manager()``) and
``tests/hermes_cli/test_transformed_stream_output.py`` (how a ``transform_llm_output`` return
value reaches the text the user actually sees).

This test does not spin up a fake LLM server: it drives ``PluginManager.invoke_hook`` directly,
which is the same dispatch path ``run_agent.py`` and the gateway call, with no model in the
loop. That proves the hook wiring, not a specific model's wording.

Skips cleanly, not a failure, when the installed Hermes checkout's own dependencies (for
example ``ruamel.yaml``) are not importable in this interpreter, so plain
``python3 -m unittest discover -s tests`` always passes even without the Hermes dev venv.
Set ``HERMES_AGENT_HOME`` to point at a different checkout than the default
``~/.hermes/hermes-agent``.
"""
from __future__ import annotations

import importlib
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HERMES_AGENT_HOME = Path(os.environ.get("HERMES_AGENT_HOME", str(Path.home() / ".hermes" / "hermes-agent")))


def _load_hermes_plugins_module():
    """Import ``hermes_cli.plugins`` from the installed checkout.

    Returns the module, or ``None`` when the checkout is missing or its own dependencies are
    not importable in this interpreter (for example a plain system ``python3`` without
    ``ruamel.yaml``, which the Hermes dev environment normally supplies through its own venv).
    """
    if not (HERMES_AGENT_HOME / "hermes_cli" / "plugins.py").is_file():
        return None
    added = str(HERMES_AGENT_HOME) not in sys.path
    if added:
        sys.path.insert(0, str(HERMES_AGENT_HOME))
    try:
        return importlib.import_module("hermes_cli.plugins")
    except Exception:
        if added:
            try:
                sys.path.remove(str(HERMES_AGENT_HOME))
            except ValueError:
                pass
        return None


class RealHookHarnessTest(unittest.TestCase):
    """Fires pre_llm_call, post_tool_call and transform_llm_output through the real
    PluginManager and proves the appended Check line reaches the final response text."""

    plugins = None

    @classmethod
    def setUpClass(cls):
        cls.plugins = _load_hermes_plugins_module()
        if cls.plugins is None:
            raise unittest.SkipTest(
                "hermes_cli.plugins is not importable in this interpreter (Hermes dev "
                f"dependencies unavailable, checked {HERMES_AGENT_HOME}). Real-hook check "
                "skipped, not failed; see LOOP-PROMPT item 1."
            )

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="check-please-hermes-home-"))
        plugin_dir = self.home / "plugins" / "check-please"
        plugin_dir.mkdir(parents=True)
        for name in ("plugin.yaml", "__init__.py", "engine.py"):
            shutil.copy(REPO_ROOT / name, plugin_dir / name)
        (self.home / "config.yaml").write_text(
            "plugins:\n  enabled:\n    - check-please\n",
            encoding="utf-8",
        )
        self._old_home = os.environ.get("HERMES_HOME")
        os.environ["HERMES_HOME"] = str(self.home)
        self.plugins._reset_plugin_managers_for_tests()

    def tearDown(self):
        self.plugins._reset_plugin_managers_for_tests()
        if self._old_home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = self._old_home
        shutil.rmtree(self.home, ignore_errors=True)

    def test_check_line_reaches_the_final_response_text(self):
        manager = self.plugins.get_plugin_manager()
        manager.discover_and_load(force=True)
        self.assertTrue(
            manager.has_hook("transform_llm_output"),
            "check-please did not register transform_llm_output through a real discover_and_load",
        )

        session_id = "sess-real-hook-1"
        manager.invoke_hook("pre_llm_call", session_id=session_id, turn_id="turn-1")
        manager.invoke_hook(
            "post_tool_call",
            tool_name="terminal",
            args={"command": "python3 -m unittest"},
            result={"exit_code": 0, "output": "OK"},
            status="success",
            session_id=session_id,
        )
        results = manager.invoke_hook(
            "transform_llm_output",
            response_text="Done, tests pass.",
            session_id=session_id,
        )
        transformed = next((r for r in results if isinstance(r, str)), "")
        self.assertTrue(transformed, "transform_llm_output returned no replacement text")
        self.assertIn("Done, tests pass.", transformed)
        self.assertIn("Check:", transformed)
        self.assertIn("1 backed", transformed)

    def test_claim_with_no_matching_tool_call_is_not_backed(self):
        """A NOT_BACKED 'sent' claim with no evidence: item 41 part b means it does not show in
        the reply's own Check line by default (no receipt is not a proven contradiction), but it
        still shows up for real through the plugin's own /check command, same handler Discord/
        Telegram/CLI call when a user types /check (manager._plugin_commands['check']['handler'],
        read-only use of the structure register_command fills; no separate invoke_command API)."""
        manager = self.plugins.get_plugin_manager()
        manager.discover_and_load(force=True)

        session_id = "sess-real-hook-2"
        manager.invoke_hook("pre_llm_call", session_id=session_id, turn_id="turn-1")
        # No post_tool_call at all this turn: an email-sent claim has no evidence behind it.
        results = manager.invoke_hook(
            "transform_llm_output",
            response_text="I've sent the invoice to the client.",
            session_id=session_id,
        )
        transformed = next((r for r in results if isinstance(r, str)), "")
        # No inline-worthy verdict this turn, so transform_llm_output returns None (no
        # replacement text, nothing appended) rather than a line with no issues to show.
        self.assertEqual(transformed, "")
        entry = manager._plugin_commands.get("check")
        self.assertIsNotNone(entry, "check-please did not register the /check command")
        detail = entry["handler"](raw_args="", session_id=session_id)
        self.assertIn("NOT BACKED", detail)
        self.assertIn("invoice", detail)

    def test_tests_pass_two_turns_after_an_edit_is_caught_as_stale_across_a_module_reload(self):
        """Backlog item 6: cross-turn stale, with the record read back from ctx.state (file-backed
        under plugin-data/) after a forced plugin reload, not from in-process module globals."""
        manager = self.plugins.get_plugin_manager()
        manager.discover_and_load(force=True)
        session_id = "sess-real-hook-3"

        # Turn 1: tests actually run and pass.
        manager.invoke_hook("pre_llm_call", session_id=session_id, turn_id="turn-1")
        manager.invoke_hook(
            "post_tool_call", tool_name="terminal", args={"command": "pytest -q"},
            result={"exit_code": 0, "output": "OK"}, status="success", session_id=session_id,
        )
        results_1 = manager.invoke_hook(
            "transform_llm_output", response_text="Tests pass.", session_id=session_id,
        )
        transformed_1 = next((r for r in results_1 if isinstance(r, str)), "")
        self.assertIn("1 backed", transformed_1)

        # Turn 2: the file is edited again, no rerun, and the reply makes no test claim.
        manager.invoke_hook("pre_llm_call", session_id=session_id, turn_id="turn-2")
        manager.invoke_hook(
            "post_tool_call", tool_name="write_file", args={"path": "engine.py"},
            result={"error": None}, status="success", session_id=session_id,
        )
        manager.invoke_hook(
            "transform_llm_output", response_text="Updated the function.", session_id=session_id,
        )

        # Force a real plugin module reload between turns 2 and 3: in-memory globals
        # (_ctx_box, _CROSS_MEM, _LEDGER) are gone; only ctx.state on disk can carry the fact
        # forward that tests passed in turn 1 and a later edit has not been retested.
        manager.discover_and_load(force=True)

        # Turn 3: claims tests pass again, but nothing reran them this turn.
        manager.invoke_hook("pre_llm_call", session_id=session_id, turn_id="turn-3")
        results_3 = manager.invoke_hook(
            "transform_llm_output", response_text="Tests pass now.", session_id=session_id,
        )
        transformed_3 = next((r for r in results_3 if isinstance(r, str)), "")
        self.assertTrue(transformed_3, "transform_llm_output returned no replacement text on turn 3")
        self.assertIn("1 stale", transformed_3)
        self.assertNotIn("not backed", transformed_3)


    def test_pre_verify_catches_a_stale_claim_in_final_response_through_the_real_dispatch(self):
        """Backlog item 30 (catch before send): tests passed turn 1, code edited turn 2 with
        no rerun, and the agent's own draft final_response still claims they pass. pre_verify
        should return a continue directive naming the stale claim, dispatched through the real
        PluginManager (not a direct function call)."""
        manager = self.plugins.get_plugin_manager()
        manager.discover_and_load(force=True)
        session_id = "sess-real-hook-4"

        manager.invoke_hook("pre_llm_call", session_id=session_id, turn_id="turn-1")
        manager.invoke_hook(
            "post_tool_call", tool_name="terminal", args={"command": "pytest -q"},
            result={"exit_code": 0, "output": "OK"}, status="success", session_id=session_id,
        )
        manager.invoke_hook("transform_llm_output", response_text="Tests pass.", session_id=session_id)

        manager.invoke_hook("pre_llm_call", session_id=session_id, turn_id="turn-2")
        manager.invoke_hook(
            "post_tool_call", tool_name="write_file", args={"path": "pricing.py"},
            result={"error": None}, status="success", session_id=session_id,
        )
        results = manager.invoke_hook(
            "pre_verify", session_id=session_id, platform="cli", model="x", coding=True,
            attempt=0, final_response="Tests pass.", changed_paths=["pricing.py"],
        )
        directive = next((r for r in results if isinstance(r, dict) and r.get("action") == "continue"), None)
        self.assertIsNotNone(directive, "pre_verify did not return a continue directive")
        self.assertIn("Tests pass", directive["message"])
        self.assertIn("rerun", directive["message"].lower())

    def test_pre_llm_call_nudges_about_a_stale_claim_that_reached_the_user_last_turn(self):
        """Backlog item 30, next_turn_nudge fallback: a same-turn STALE claim slipped through
        transform_llm_output (no pre_verify catch happened, simulating a non-coding turn where
        pre_verify never fires). The NEXT turn's pre_llm_call should return a plain context
        string naming it, dispatched through the real PluginManager."""
        manager = self.plugins.get_plugin_manager()
        manager.discover_and_load(force=True)
        session_id = "sess-real-hook-5"

        manager.invoke_hook("pre_llm_call", session_id=session_id, turn_id="turn-1")
        manager.invoke_hook(
            "post_tool_call", tool_name="terminal", args={"command": "pytest -q"},
            result={"exit_code": 0, "output": "OK"}, status="success", session_id=session_id,
        )
        manager.invoke_hook(
            "post_tool_call", tool_name="write_file", args={"path": "pricing.py"},
            result={"error": None}, status="success", session_id=session_id,
        )
        delivered = manager.invoke_hook(
            "transform_llm_output", response_text="Tests pass.", session_id=session_id,
        )
        final_text = next((r for r in delivered if isinstance(r, str)), "")
        self.assertIn("1 stale", final_text, "setup did not reach the user as a stale claim")

        results = manager.invoke_hook("pre_llm_call", session_id=session_id, turn_id="turn-2")
        nudge = next((r for r in results if isinstance(r, str) and r), "")
        self.assertIn("Tests pass", nudge)
        self.assertIn("unresolved claim", nudge)

    def test_subagent_stop_backs_the_parents_relayed_claim(self):
        """Backlog item 31 part (a): a child's real send, replayed through subagent_stop's
        tool_call_history (metadata-only shape, see tools/delegate_tool_results.py), should
        back the PARENT session's own claim about it, even though the parent itself never
        called send_message."""
        manager = self.plugins.get_plugin_manager()
        manager.discover_and_load(force=True)
        self.assertTrue(manager.has_hook("subagent_stop"),
                         "check-please did not register subagent_stop through a real discover_and_load")
        session_id = "sess-real-hook-6"

        manager.invoke_hook("pre_llm_call", session_id=session_id, turn_id="turn-1")
        # The parent itself only delegated; delegate_task proves nothing on its own ledger.
        manager.invoke_hook(
            "post_tool_call", tool_name="delegate_task", args={"tasks": [{"prompt": "email Dana"}]},
            result={"status": "completed"}, status="success", session_id=session_id,
        )
        manager.invoke_hook(
            "subagent_stop",
            parent_session_id=session_id, child_session_id="child-1",
            child_summary="Sent the invoice to Dana.", child_status="succeeded",
            tool_call_history=[{
                "tool_name": "connectors__gmail__send_email",
                "tool_input": {"argument_keys": ["to", "subject"], "targets": {"to": "dana@example.com"}},
                "status": "ok",
            }],
        )
        results = manager.invoke_hook(
            "transform_llm_output", response_text="I sent the invoice to Dana.", session_id=session_id,
        )
        transformed = next((r for r in results if isinstance(r, str)), "")
        self.assertIn("1 backed", transformed, "the child's real send did not back the parent's claim")


if __name__ == "__main__":
    unittest.main()
