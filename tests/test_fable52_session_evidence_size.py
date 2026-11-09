"""Fable 5.2 review: session evidence keeps one row per kind, so plugin state stays small.

Every successful tool call re-read and re-wrote the whole session-evidence store through
ctx.state (one JSON file for the plugin). Measured on the real PluginState: 47 ms per tool
call at 2.3 MB, 194 ms at 9.4 MB, and at the plugin's own caps (200 sessions x 200 rows) the
file passes Hermes' 10 MB per-plugin quota, after which every state write fails silently.
Only the newest successful row of a kind is ever read back, so only that row is kept.
"""
import importlib.util
import json
import os
import random
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import engine  # noqa: E402

_NAME = "_check_please_fable52_size"
if _NAME in sys.modules:
    plugin = sys.modules[_NAME]
else:
    _spec = importlib.util.spec_from_file_location(_NAME, os.path.join(ROOT, "__init__.py"),
                                                   submodule_search_locations=[ROOT])
    plugin = importlib.util.module_from_spec(_spec)
    sys.modules[_NAME] = plugin
    _spec.loader.exec_module(plugin)

OK = json.dumps({"output": "5 passed", "exit_code": 0, "error": None})
CALLS = [("terminal", {"command": "pytest -q"}), ("terminal", {"command": "git push origin main"}),
         ("terminal", {"command": "git commit -am wip"}), ("write_file", {"path": "notes/a.md", "content": "x"}),
         ("write_file", {"path": "src/app.py", "content": "x"}), ("send_message", {"to": "dana@example.com"}),
         ("terminal", {"command": "vercel --prod"}), ("terminal", {"command": "ls -la"})]
CLAIMS = "Tests pass. Pushed. Committed. Saved the notes. Invoice emailed to Dana. Deployed."


class OneRowPerKind(unittest.TestCase):
    def setUp(self):
        for store in (plugin._LEDGER, plugin._LAST, plugin._CROSS_MEM, plugin._SESSION_EVIDENCE_MEM):
            store.clear()
        plugin._ctx_box["ctx"] = None

    def test_a_long_session_stores_at_most_one_row_per_kind(self):
        plugin.on_pre_llm_call(session_id="long", turn_id="t1")
        for i in range(300):
            name, args = CALLS[i % len(CALLS)]
            plugin.on_post_tool_call(tool_name=name, args=args, result=OK, session_id="long")
        rows = plugin._SESSION_EVIDENCE_MEM["long"]
        kinds = [r["kind"] for r in rows]
        self.assertEqual(len(kinds), len(set(kinds)))
        self.assertLessEqual(len(rows), len(engine.LADDER))

    def test_recap_verdicts_are_identical_to_keeping_every_row(self):
        rng = random.Random(52)
        every_row = []
        plugin.on_pre_llm_call(session_id="eq", turn_id="t1")
        for seq in range(1, 121):
            name, args = rng.choice(CALLS)
            plugin.on_post_tool_call(tool_name=name, args=args, result=OK, session_id="eq")
            ev = engine.classify_call(name, args, OK, None, seq=seq)
            if ev is not None and ev.ok:
                every_row.append(ev)
        base = engine.check_turn(CLAIMS, [])
        kept = engine.apply_session_evidence(base, plugin._load_session_evidence("eq"))
        full = engine.apply_session_evidence(base, every_row)
        self.assertEqual([(v.claim.kind, v.status, v.proven_rung, v.proof.detail if v.proof else None) for v in kept],
                         [(v.claim.kind, v.status, v.proven_rung, v.proof.detail if v.proof else None) for v in full])


if __name__ == "__main__":
    unittest.main()
