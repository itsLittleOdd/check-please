"""Fable 5.2 review: /check must only ever show the asking session's own claims.

handle_check fell back to the most recent session in the process whenever the asking session
had no verdicts of its own (`_LAST.get(sid) or _LAST.get(_LAST_SESSION[0])`). On a gateway,
one process serves many chats: a user whose last reply made no claims, or whose chat has no
session yet, got another chat's claim sentences and proof commands. All examples invented.
"""
import importlib.util
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_NAME = "_check_please_fable52_scope"
if _NAME in sys.modules:
    plugin = sys.modules[_NAME]
else:
    _spec = importlib.util.spec_from_file_location(_NAME, os.path.join(ROOT, "__init__.py"),
                                                   submodule_search_locations=[ROOT])
    plugin = importlib.util.module_from_spec(_spec)
    sys.modules[_NAME] = plugin
    _spec.loader.exec_module(plugin)

OK = json.dumps({"success": True})


def reply(sid, text, calls=()):
    plugin.on_pre_llm_call(session_id=sid, turn_id="t")
    for name, args, result in calls:
        plugin.on_post_tool_call(tool_name=name, args=args, result=result, session_id=sid)
    plugin.on_transform_llm_output(response_text=text, session_id=sid)


class CheckIsScopedToTheAskingSession(unittest.TestCase):
    def setUp(self):
        for store in (plugin._LEDGER, plugin._LAST, plugin._CROSS_MEM, plugin._SESSION_EVIDENCE_MEM,
                      plugin._DAILY_MEM, plugin._LAST_REPLY_MEM, plugin._SUBAGENT_MEM):
            store.clear()
        plugin._LAST_SESSION[0] = ""
        plugin._ctx_box["ctx"] = None
        plugin._settings.clear()
        plugin._settings.update({"line": "issues"})

    def _alice_then(self):
        reply("chat-bob", "Here is the summary you asked for.")
        reply("chat-alice", "Wire of $48,200 sent to Halvorsen Legal.",
              [("send_message", {"to": "ap@halvorsen.example"}, OK)])

    def test_a_session_with_no_claims_does_not_see_another_sessions_claims(self):
        self._alice_then()
        report = plugin.handle_check("", session_id="chat-bob")
        self.assertNotIn("Halvorsen", report)
        self.assertIn("no completion claims", report)

    def test_an_unknown_session_does_not_see_another_sessions_claims(self):
        self._alice_then()
        self.assertNotIn("Halvorsen", plugin.handle_check("", session_id="chat-carol"))

    def test_a_missing_session_id_with_several_sessions_shows_nobody(self):
        self._alice_then()
        report = plugin.handle_check("", session_id="")
        self.assertNotIn("Halvorsen", report)

    def test_a_missing_session_id_with_one_session_still_works(self):
        reply("only", "Wire sent to Halvorsen Legal.", [("send_message", {"to": "ap@halvorsen.example"}, OK)])
        self.assertIn("Halvorsen", plugin.handle_check(""))

    def test_the_asking_session_sees_its_own_claims(self):
        self._alice_then()
        self.assertIn("Halvorsen", plugin.handle_check("", session_id="chat-alice"))

    def test_subagents_report_is_scoped_the_same_way(self):
        plugin._SUBAGENT_MEM["chat-alice"] = [{"child_session_id": "c1", "child_status": "ok", "ts": 1.0,
                                               "verdicts": [{"status": "NOT_BACKED", "text": "Halvorsen wire sent",
                                                             "note": "no sent call"}]}]
        self._alice_then()
        self.assertNotIn("Halvorsen", plugin.handle_check("subagents", session_id="chat-bob"))
        self.assertIn("Halvorsen", plugin.handle_check("subagents", session_id="chat-alice"))


if __name__ == "__main__":
    unittest.main()
