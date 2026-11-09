"""Fable 5.2 review: Hermes' patch tool in V4A mode names its files inside the patch text.

classify_call read only the "path" argument, which a mode="patch" call does not have, so the
write had an empty path: a README-only patch staled a test pass ("then a file changed"), and
a patch to a test file was invisible to the proof-edited check. All examples invented.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import engine  # noqa: E402

OK = json.dumps({"success": True})


def v4a(body, seq=0):
    return engine.classify_call("patch", {"mode": "patch", "patch": "*** Begin Patch\n" + body + "\n*** End Patch"},
                                OK, None, seq=seq)


def term(cmd, out="", code=0, seq=0):
    return engine.classify_call("terminal", {"command": cmd},
                                json.dumps({"output": out, "exit_code": code, "error": None}), None, seq=seq)


class V4APatchPaths(unittest.TestCase):
    def test_code_patch_is_saved_code_with_its_path(self):
        e = v4a("*** Update File: src/pricing.py\n@@ def total @@\n-    return a\n+    return a + b")
        self.assertEqual((e.kind, e.path, e.detail), ("saved_code", "src/pricing.py", "src/pricing.py"))
        self.assertTrue(e.stales_tests)

    def test_docs_only_patch_does_not_stale_a_test_pass(self):
        evidence = [term("pytest -q", "5 passed", 0, 1),
                    v4a("*** Update File: README.md\n-old line\n+new line", seq=2)]
        self.assertFalse(evidence[1].stales_tests)
        self.assertEqual(engine.check_turn("Tests pass.", evidence)[0].status, "BACKED")

    def test_docs_plus_code_patch_still_stales_and_names_the_code_file(self):
        evidence = [term("pytest -q", "5 passed", 0, 1),
                    v4a("*** Update File: README.md\n-a\n+b\n*** Update File: src/app.py\n-x = 1\n+x = 2", seq=2)]
        v = engine.check_turn("Tests pass.", evidence)[0]
        self.assertEqual(v.status, "STALE")
        self.assertIn("src/app.py", v.note)

    def test_add_delete_and_move_headers(self):
        self.assertEqual(v4a("*** Add File: tools/new_check.py\n+print('hi')").path, "tools/new_check.py")
        self.assertEqual(v4a("*** Delete File: old/legacy.py").path, "old/legacy.py")
        self.assertEqual(v4a("*** Move File: a/old.py -> b/new.py").path, "b/new.py")

    def test_test_file_expectation_edit_between_fail_and_pass_reads_edited(self):
        evidence = [term("pytest -q", "1 failed, 4 passed", 1, 1),
                    v4a("*** Update File: tests/test_pricing.py\n@@ def test_total @@\n"
                        "-    assert total(2, 3) == 5\n+    assert total(2, 3) == 6", seq=2),
                    term("pytest -q", "5 passed", 0, 3)]
        self.assertTrue(evidence[1].expectation_edit)
        v = engine.check_turn("Tests pass.", evidence)[0]
        self.assertEqual(v.status, "EDITED")
        self.assertIn("tests/test_pricing.py", v.note)

    def test_replace_mode_patch_is_unchanged(self):
        e = engine.classify_call("patch", {"path": "src/app.py", "old_string": "a", "new_string": "b"}, OK)
        self.assertEqual((e.kind, e.path), ("saved_code", "src/app.py"))

    def test_patch_text_with_no_file_header_keeps_the_old_empty_path_behavior(self):
        e = engine.classify_call("patch", {"mode": "patch", "patch": "garbage"}, OK)
        self.assertEqual((e.kind, e.path), ("saved", ""))


if __name__ == "__main__":
    unittest.main()
