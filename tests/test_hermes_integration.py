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
        self.assertTrue(transformed)
        self.assertIn("1 not backed", transformed)


if __name__ == "__main__":
    unittest.main()
