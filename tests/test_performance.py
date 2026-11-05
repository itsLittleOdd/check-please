"""Backlog item 16: per-hook timing, fuzz robustness, bounded memory.

Plain-stdlib tests only (random, time, statistics). No Hermes imports here;
cross-turn/ledger state lives in __init__.py's module-level dicts, which this
file imports and resets between tests so one test's state cannot leak into
another's (these dicts are otherwise meant to live for a whole process).
"""
import importlib.util
import os
import random
import string
import sys
import time
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
import engine  # noqa: E402

# __init__.py uses `from . import engine`, so it must load as a real package (not a bare
# `import __init__`) for that relative import to resolve. Same technique as loading any
# package by path: give it a dotted name and submodule_search_locations before exec'ing it.
_PKG_NAME = "_check_please_under_test"
if _PKG_NAME in sys.modules:
    plugin = sys.modules[_PKG_NAME]
else:
    _spec = importlib.util.spec_from_file_location(
        _PKG_NAME, os.path.join(_ROOT, "__init__.py"), submodule_search_locations=[_ROOT])
    plugin = importlib.util.module_from_spec(_spec)
    sys.modules[_PKG_NAME] = plugin
    _spec.loader.exec_module(plugin)


def _reset_plugin_state():
    plugin._LEDGER.clear()
    plugin._LAST.clear()
    plugin._LAST_SESSION[0] = ""
    plugin._CROSS_MEM.clear()
    plugin._CRON_TALLY_MEM.clear()
    plugin._DAILY_MEM.clear()
    plugin._LAST_REPLY_MEM.clear()
    plugin._ctx_box["ctx"] = None  # force the in-memory fallback path, no real ctx needed


class HookTiming(unittest.TestCase):
    """200-call turn, each hook's per-call median must stay under 2 ms."""

    def setUp(self):
        _reset_plugin_state()

    def test_post_tool_call_median_under_2ms(self):
        tools = [
            ("terminal", {"command": "pytest -q"}, {"exit_code": 0}),
            ("write_file", {"path": "src/app.py"}, {"error": None}),
            ("terminal", {"command": "git push origin main"}, {"exit_code": 0}),
            ("send_message", {"to": "dana@example.com"}, {"success": True}),
            ("mcp__gmail__list_messages", {}, {}),
        ]
        durations = []
        for i in range(200):
            name, args, result = tools[i % len(tools)]
            start = time.perf_counter()
            plugin.on_post_tool_call(tool_name=name, args=args, result=result,
                                      session_id="perf-session", seq=i)
            durations.append(time.perf_counter() - start)
        durations.sort()
        median = durations[len(durations) // 2]
        self.assertLess(median, 0.002, f"post_tool_call median {median * 1000:.3f}ms over 2ms")

    def test_pre_llm_call_median_under_2ms(self):
        durations = []
        for i in range(200):
            start = time.perf_counter()
            plugin.on_pre_llm_call(session_id=f"perf-pre-{i % 20}", turn_id=i)
            durations.append(time.perf_counter() - start)
        durations.sort()
        median = durations[len(durations) // 2]
        self.assertLess(median, 0.002, f"pre_llm_call median {median * 1000:.3f}ms over 2ms")

    def test_transform_llm_output_median_under_2ms_with_full_ledger(self):
        # Fill one session's ledger to the evidence cap first, worst case for check_turn.
        sid = "perf-transform"
        for i in range(plugin._MAX_EVIDENCE):
            plugin.on_post_tool_call(tool_name="terminal", args={"command": "pytest -q"},
                                      result={"exit_code": 0}, session_id=sid, seq=i)
        reply = ("Tests pass. Pushed the change. Deployed to prod. "
                 "Saved the draft. Emailed Dana the invoice.")
        durations = []
        for i in range(200):
            start = time.perf_counter()
            plugin.on_transform_llm_output(response_text=reply, session_id=sid)
            durations.append(time.perf_counter() - start)
        durations.sort()
        median = durations[len(durations) // 2]
        self.assertLess(median, 0.002, f"transform_llm_output median {median * 1000:.3f}ms over 2ms")


class FuzzRobustness(unittest.TestCase):
    """1000 random inputs through the two parsing entry points, zero exceptions."""

    def setUp(self):
        _reset_plugin_state()
        self.rng = random.Random(20261105)

    def _random_text(self, max_len=400):
        alphabet = string.ascii_letters + string.digits + " .,!?;:'\"\n-_/\\()[]{}$%&*#@`~<>|=+^\u2713\u2705\u2611"
        n = self.rng.randint(0, max_len)
        return "".join(self.rng.choice(alphabet) for _ in range(n))

    def _random_args(self):
        shape = self.rng.choice(["dict", "str", "none", "list", "int", "nested"])
        if shape == "dict":
            keys = self.rng.sample(["command", "path", "to", "action", "code", "cmd",
                                     "file_path", "name", "schedule"], k=self.rng.randint(0, 4))
            return {k: self._random_text(80) for k in keys}
        if shape == "str":
            return self._random_text(80)
        if shape == "list":
            return [self._random_text(20) for _ in range(self.rng.randint(0, 3))]
        if shape == "int":
            return self.rng.randint(-5, 5)
        if shape == "nested":
            return {"command": {"weird": self._random_text(20)}}
        return None

    def _random_result(self):
        shape = self.rng.choice(["dict", "str", "none", "bool", "int", "exc_str"])
        if shape == "dict":
            d = {}
            if self.rng.random() < 0.5:
                d["exit_code"] = self.rng.choice([0, 1, -1, "0", None, "nan"])
            if self.rng.random() < 0.3:
                d["error"] = self.rng.choice([None, "", "boom", False])
            if self.rng.random() < 0.3:
                d["success"] = self.rng.choice([True, False])
            return d
        if shape == "str":
            return self._random_text(60)
        if shape == "bool":
            return self.rng.choice([True, False])
        if shape == "int":
            return self.rng.randint(-2, 2)
        if shape == "exc_str":
            return '{"error": ' + self._random_text(10) + "}"  # often invalid JSON on purpose
        return None

    def test_classify_call_1000_random_inputs(self):
        tool_names = ["terminal", "write_file", "send_message", "mcp__x__y", "connectors__a__b",
                      "execute_code", "browser_exec", "cronjob", "delegate_task", "", None,
                      "weird\ttool\nname", "🙂tool"]
        exceptions = []
        for i in range(1000):
            name = self.rng.choice(tool_names)
            try:
                engine.classify_call(name, self._random_args(), self._random_result(),
                                      status=self.rng.choice([None, "error", "ok", "blocked", 42]),
                                      seq=i, ts=float(i))
            except Exception as exc:  # pragma: no cover - failure path
                exceptions.append((i, name, repr(exc)))
        self.assertEqual(exceptions, [], f"{len(exceptions)} exceptions, first: {exceptions[:3]}")

    def test_extract_claims_1000_random_inputs(self):
        exceptions = []
        for i in range(1000):
            text = self._random_text(500)
            try:
                engine.extract_claims(text)
            except Exception as exc:  # pragma: no cover - failure path
                exceptions.append((i, repr(exc)))
        self.assertEqual(exceptions, [], f"{len(exceptions)} exceptions, first: {exceptions[:3]}")

    def test_check_turn_1000_random_inputs(self):
        tool_names = ["terminal", "write_file", "send_message", "mcp__x__y", "execute_code"]
        exceptions = []
        for i in range(1000):
            reply = self._random_text(300)
            evidence = []
            for j in range(self.rng.randint(0, 5)):
                e = engine.classify_call(self.rng.choice(tool_names), self._random_args(),
                                          self._random_result(), seq=j)
                if e is not None:
                    evidence.append(e)
            try:
                engine.check_turn(reply, evidence)
            except Exception as exc:  # pragma: no cover - failure path
                exceptions.append((i, repr(exc)))
        self.assertEqual(exceptions, [], f"{len(exceptions)} exceptions, first: {exceptions[:3]}")

    def test_hooks_never_raise_on_malformed_kwargs(self):
        """on_post_tool_call/on_transform_llm_output already catch internally; fuzz confirms
        no malformed input (wrong types, missing keys) escapes that try/except."""
        exceptions = []
        for i in range(1000):
            try:
                plugin.on_post_tool_call(tool_name=self.rng.choice([None, "", 123, "terminal"]),
                                          args=self._random_args(), result=self._random_result(),
                                          session_id=self.rng.choice([None, "", 42, "s1"]))
                plugin.on_transform_llm_output(response_text=self.rng.choice([None, "", self._random_text(200)]),
                                                session_id=self.rng.choice([None, "", 42, "s1"]))
            except Exception as exc:  # pragma: no cover - failure path
                exceptions.append((i, repr(exc)))
        self.assertEqual(exceptions, [], f"{len(exceptions)} exceptions, first: {exceptions[:3]}")


class MemoryBounded(unittest.TestCase):
    """Per-session caps actually trim, so a long-running install does not grow unbounded."""

    def setUp(self):
        _reset_plugin_state()

    def test_evidence_ledger_capped_at_max_evidence(self):
        sid = "mem-evidence"
        for i in range(plugin._MAX_EVIDENCE + 150):
            plugin.on_post_tool_call(tool_name="terminal", args={"command": "pytest -q"},
                                      result={"exit_code": 0}, session_id=sid, seq=i)
        self.assertLessEqual(len(plugin._LEDGER[sid]["evidence"]), plugin._MAX_EVIDENCE)

    def test_session_ledger_capped_at_max_sessions(self):
        for i in range(plugin._MAX_SESSIONS + 80):
            plugin.on_pre_llm_call(session_id=f"mem-sess-{i}", turn_id=i)
        self.assertLessEqual(len(plugin._LEDGER), plugin._MAX_SESSIONS)

    def test_cross_turn_memory_capped_at_max_sessions(self):
        for i in range(plugin._MAX_SESSIONS + 80):
            plugin.on_pre_llm_call(session_id=f"mem-cross-{i}", turn_id=i)
        data = plugin._load_cross()
        self.assertLessEqual(len(data), plugin._MAX_SESSIONS)

    def test_last_reply_memory_capped_at_max_sessions(self):
        for i in range(plugin._MAX_SESSIONS + 80):
            plugin.on_transform_llm_output(response_text="Tests pass.", session_id=f"mem-reply-{i}")
        data = plugin._load_last_reply()
        self.assertLessEqual(len(data), plugin._MAX_SESSIONS)

    def test_daily_log_capped_at_max_days(self):
        daily = {f"2026-01-{d:02d}": {} for d in range(1, plugin._MAX_DAYS + 20)}
        plugin._save_daily(daily)
        self.assertLessEqual(len(plugin._load_daily()), plugin._MAX_DAYS)

    def test_cron_tally_capped_at_max_cron_jobs(self):
        """Item 20: cron_tally had no cap (honest limit since items 7/16); a long-running
        install with many distinct cron jobs could grow it unbounded. Confirms the trim
        holds and the row being written this call survives even when it would otherwise
        be the oldest-inserted entry evicted."""
        tally = {f"job-{i}": {"job_name": f"Job {i}", "claims": 1, "backed": 1, "not_backed": 0}
                  for i in range(plugin._MAX_CRON_JOBS + 50)}
        plugin._save_cron_tally(tally, keep="job-0")
        data = plugin._load_cron_tally()
        self.assertLessEqual(len(data), plugin._MAX_CRON_JOBS)
        self.assertIn("job-0", data)


if __name__ == "__main__":
    unittest.main()
