"""Fable 5.2 review: the audit replays a deferred tool call as the tool it named.

Hermes saves a deferred tool reached through the Tool Search bridge as a call to `tool_call`
with {"calls": [{"name", "arguments"}]}. The live hook sees the real tool; the audit's replay
of the store saw `tool_call`, which proves nothing, so a real send read as NOT BACKED.
In the three real-history stores 1,781 stored calls are bridge calls. All examples invented.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import audit  # noqa: E402


def session(tool_call):
    return [
        {"role": "user", "content": "send Dana the invoice"},
        {"role": "assistant", "content": "", "tool_calls": [tool_call]},
        {"role": "tool", "tool_call_id": "c1", "content": json.dumps({"success": True})},
        {"role": "assistant", "content": "Invoice emailed to Dana."},
    ]


class BridgeCallsAreReplayedAsTheRealTool(unittest.TestCase):
    def test_single_entry_bridge_call_backs_the_claim(self):
        call = {"id": "c1", "function": {"name": "tool_call", "arguments": json.dumps(
            {"calls": [{"name": "send_message", "arguments": {"to": "dana@example.com", "body": "invoice"}}]})}}
        rows = audit.audit_session("s1", session(call))
        self.assertEqual([v.status for v in rows[0]["verdicts"]], ["BACKED"])
        self.assertEqual(rows[0]["verdicts"][0].proof.tool, "send_message")

    def test_direct_call_is_unchanged(self):
        call = {"id": "c1", "function": {"name": "send_message", "arguments": json.dumps({"to": "dana@example.com"})}}
        rows = audit.audit_session("s1", session(call))
        self.assertEqual([v.status for v in rows[0]["verdicts"]], ["BACKED"])

    def test_multi_entry_batch_and_malformed_bridge_calls_are_left_alone(self):
        self.assertEqual(audit._unwrap_bridge_call("tool_call", {"calls": [{"name": "a"}, {"name": "b"}]})[0], "tool_call")
        self.assertEqual(audit._unwrap_bridge_call("tool_call", {"calls": "nope"})[0], "tool_call")
        self.assertEqual(audit._unwrap_bridge_call("tool_call", None), ("tool_call", None))
        self.assertEqual(audit._unwrap_bridge_call("terminal", {"command": "ls"}), ("terminal", {"command": "ls"}))


if __name__ == "__main__":
    unittest.main()
