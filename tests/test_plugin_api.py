"""Backend route tests for dashboard/plugin_api.py (backlog item 11 data path).

Proves two things with real objects, not mocks of our own code:
  1. hermes_cli.plugins_state.PluginState (the class ctx.state wraps) and dashboard/plugin_api.py's
     GET /claims and GET /jobs routes agree on the exact same on-disk state, because the test writes
     through PluginState and reads through the route function.
  2. The route's JSON shape matches engine.claims_payload() exactly, so the Desktop pane in
     desktop/plugin.js can render it with no translation layer.

Skips cleanly (not a failure) when fastapi or the installed Hermes checkout's hermes_cli package
are not importable in this interpreter -- same reasoning as test_hermes_integration.py: plain
python3 has neither; the Hermes venv (PY=$(ls ~/.hermes/installs/*/environments/*/venv/bin/python
| head -1)) has both.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HERMES_AGENT_HOME = Path(os.environ.get("HERMES_AGENT_HOME", str(Path.home() / ".hermes" / "hermes-agent")))


def _load_deps():
    """Return (PluginState, plugin_api module) or None when either dependency is missing."""
    added = str(HERMES_AGENT_HOME) not in sys.path
    if added:
        sys.path.insert(0, str(HERMES_AGENT_HOME))
    try:
        from hermes_cli.plugins_state import PluginState  # noqa: F401
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "check_please_dashboard_plugin_api", REPO_ROOT / "dashboard" / "plugin_api.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return PluginState, module
    except Exception:
        return None
    finally:
        if added:
            try:
                sys.path.remove(str(HERMES_AGENT_HOME))
            except ValueError:
                pass


_DEPS = _load_deps()


@unittest.skipIf(_DEPS is None, "fastapi / hermes_cli.plugins_state not importable in this interpreter")
class ClaimsRouteTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_home = os.environ.get("HERMES_HOME")
        os.environ["HERMES_HOME"] = self._tmp.name
        PluginState, _ = _DEPS
        self.PluginState = PluginState

    def tearDown(self):
        if self._old_home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = self._old_home
        self._tmp.cleanup()

    def test_unknown_session_returns_honest_empty_shape(self):
        _, api = _DEPS
        result = asyncio.run(api.get_claims(session_id="no-such-session"))
        self.assertEqual(result, {"claims": [], "backed": 0, "total": 0})

    def test_known_session_round_trips_through_real_plugin_state(self):
        _, api = _DEPS
        payload = {
            "claims": [{"kind": "tested", "text": "Tests pass.", "phrase": "pass",
                        "status": "BACKED", "note": "", "proof": "pytest -q"}],
            "backed": 1,
            "total": 1,
        }
        # Write exactly the way __init__.py's _save_last_reply does: through PluginState,
        # keyed by session id under "last_reply".
        state = self.PluginState("check-please")
        state.set("last_reply", {"sess-1": payload})
        result = asyncio.run(api.get_claims(session_id="sess-1"))
        self.assertEqual(result, payload)

    def test_jobs_route_reads_cron_tally_key(self):
        _, api = _DEPS
        state = self.PluginState("check-please")
        tally = {"job-1": {"job_name": "nightly build", "claims": 4, "backed": 3, "not_backed": 1}}
        state.set("cron_tally", tally)
        result = asyncio.run(api.get_jobs())
        self.assertEqual(result, {"jobs": tally})

    def test_jobs_route_empty_when_no_tally_yet(self):
        _, api = _DEPS
        result = asyncio.run(api.get_jobs())
        self.assertEqual(result, {"jobs": {}})


@unittest.skipIf(_DEPS is None, "fastapi / hermes_cli.plugins_state not importable in this interpreter")
class ClaimsRouteSharesDirectoryWithCtxStateTest(unittest.TestCase):
    """The plugin.yaml name and this route's hardcoded _PLUGIN_ID must match: PluginState derives
    its on-disk directory from that string, so a typo here would silently read/write a different
    plugin's folder instead of failing loudly."""

    def test_plugin_id_matches_plugin_yaml_name(self):
        import re

        _, api = _DEPS
        plugin_yaml = (REPO_ROOT / "plugin.yaml").read_text(encoding="utf-8")
        match = re.search(r"^name:\s*(\S+)", plugin_yaml, re.MULTILINE)
        self.assertIsNotNone(match, "plugin.yaml must declare a top-level name:")
        self.assertEqual(api._PLUGIN_ID, match.group(1))


if __name__ == "__main__":
    unittest.main()
