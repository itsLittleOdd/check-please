"""Demo rig for Check, Please (backlog item 13).

Lives under tests/, not the shipped plugin tree, so `hermes plugins validate` never scans
it for private core imports (catalog rule 9) even though it deliberately imports
hermes_cli.plugins read-only, the same seam tests/test_hermes_integration.py already uses.
Moved here from demo/run_demo.py in backlog item 21 for exactly that reason; it is not
plugin code and is never loaded by discover_and_load.

Reproduces the storyboard scenario from Wren's IDEAS.md ("Check, Please: 83 seconds",
shots 3-6) through the real Hermes hook path: PluginManager.invoke_hook, the same dispatch
run_agent.py and the gateway use, no model in the loop. One command, real output, no
scripted agent words.

Scenario (invented names only):
  Turn 1: tests actually run and pass (terminal, pytest, exit 0).
  Turn 2: the file is edited again after that pass, with no rerun.
  Turn 3: a deploy actually runs (terminal, exit 0) and an invoice draft is actually saved
          (write_file), but nothing sends it. The reply claims all three: tests pass,
          preview is live, invoice emailed to Dana. Only the deploy is BACKED; the test
          claim is STALE (passed earlier, edited since, no rerun this turn); the invoice
          claim is NOT BACKED (drafted/saved is not sent).

Prints the exact "Check:" line transform_llm_output appends, then the /check detail report
for that same turn, both through handle_check -> engine, same functions a real turn uses.

Usage: python3 tests/demo_hermes.py
Exits 0 with the scenario output printed, or exits 1 with one line explaining that this
host's ~/.hermes/hermes-agent checkout has no importable dev dependencies (ruamel.yaml,
hermes_cli), the same gap the real-hook integration test skips on cleanly. Set
HERMES_AGENT_HOME to point at a different checkout.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HERMES_AGENT_HOME = Path(os.environ.get("HERMES_AGENT_HOME", str(Path.home() / ".hermes" / "hermes-agent")))


def _load_hermes_plugins_module():
    """Same read-only loader pattern as tests/test_hermes_integration.py."""
    if not (HERMES_AGENT_HOME / "hermes_cli" / "plugins.py").is_file():
        return None
    added = str(HERMES_AGENT_HOME) not in sys.path
    if added:
        sys.path.insert(0, str(HERMES_AGENT_HOME))
    try:
        import importlib
        return importlib.import_module("hermes_cli.plugins")
    except Exception:
        if added:
            try:
                sys.path.remove(str(HERMES_AGENT_HOME))
            except ValueError:
                pass
        return None


def run() -> int:
    plugins = _load_hermes_plugins_module()
    if plugins is None:
        print(
            "Demo rig needs the Hermes dev venv (hermes_cli + ruamel.yaml), not found at "
            f"{HERMES_AGENT_HOME}. Run under that interpreter, e.g.:\n"
            '  "$(ls ~/.hermes/installs/*/environments/*/venv/bin/python | head -1)" '
            "tests/demo_hermes.py",
            file=sys.stderr,
        )
        return 1

    home = Path(tempfile.mkdtemp(prefix="check-please-demo-"))
    old_home = os.environ.get("HERMES_HOME")
    try:
        plugin_dir = home / "plugins" / "check-please"
        plugin_dir.mkdir(parents=True)
        for name in ("plugin.yaml", "__init__.py", "engine.py"):
            shutil.copy(REPO_ROOT / name, plugin_dir / name)
        (home / "config.yaml").write_text(
            "plugins:\n  enabled:\n    - check-please\n", encoding="utf-8",
        )
        os.environ["HERMES_HOME"] = str(home)
        plugins._reset_plugin_managers_for_tests()

        manager = plugins.get_plugin_manager()
        manager.discover_and_load(force=True)
        if not manager.has_hook("transform_llm_output"):
            print("check-please did not load through a real discover_and_load.", file=sys.stderr)
            return 1

        session_id = "demo-check-please"

        print("--- Turn 1: tests run and pass ---")
        manager.invoke_hook("pre_llm_call", session_id=session_id, turn_id="turn-1")
        manager.invoke_hook(
            "post_tool_call", tool_name="terminal",
            args={"command": "python3 -m unittest discover -s tests"},
            result={"exit_code": 0, "output": "OK (24 tests)"},
            status="success", session_id=session_id,
        )
        results_1 = manager.invoke_hook(
            "transform_llm_output", response_text="Tests pass.", session_id=session_id,
        )
        line_1 = next((r for r in results_1 if isinstance(r, str)), "")
        print(line_1.splitlines()[-1] if line_1 else "(no check line)")

        print("\n--- Turn 2: pricing.py edited after that pass, no rerun ---")
        manager.invoke_hook("pre_llm_call", session_id=session_id, turn_id="turn-2")
        manager.invoke_hook(
            "post_tool_call", tool_name="write_file", args={"path": "pricing.py"},
            result={"error": None}, status="success", session_id=session_id,
        )
        manager.invoke_hook(
            "transform_llm_output", response_text="Updated the pricing function.",
            session_id=session_id,
        )
        print("(no claim this turn, just the edit that makes turn 3's test claim stale)")

        print("\n--- Turn 3: deploy runs, invoice is drafted and saved, nothing sends it ---")
        manager.invoke_hook("pre_llm_call", session_id=session_id, turn_id="turn-3")
        manager.invoke_hook(
            "post_tool_call", tool_name="terminal",
            args={"command": "vercel deploy --prod"},
            result={"exit_code": 0, "output": "https://preview-dana.vercel.app"},
            status="success", session_id=session_id,
        )
        manager.invoke_hook(
            "post_tool_call", tool_name="write_file",
            args={"path": "drafts/invoice-dana.md"},
            result={"error": None}, status="success", session_id=session_id,
        )
        results_3 = manager.invoke_hook(
            "transform_llm_output",
            response_text=(
                "All set. Tests pass, preview is live, invoice emailed to Dana."
            ),
            session_id=session_id,
        )
        reply_3 = next((r for r in results_3 if isinstance(r, str)), "")
        print(reply_3)

        print("\n--- /check (same turn, full detail) ---")
        print(_run_check_command(manager, session_id))
        return 0
    finally:
        plugins._reset_plugin_managers_for_tests()
        if old_home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = old_home
        shutil.rmtree(home, ignore_errors=True)


def _run_check_command(manager, session_id: str) -> str:
    """Call the plugin's registered /check handler straight from the manager's own command
    table (``PluginManager._plugin_commands["check"]["handler"]``, read-only use of the same
    structure ``register_command`` fills; confirmed in the installed hermes_cli/plugins.py).
    This is the same handler Discord/Telegram/CLI call when a user types /check; there is no
    separate public ``invoke_command`` helper to go through instead."""
    entry = manager._plugin_commands.get("check")
    if entry is None:
        return "(the /check command did not register)"
    return entry["handler"](raw_args="", session_id=session_id)


if __name__ == "__main__":
    raise SystemExit(run())
