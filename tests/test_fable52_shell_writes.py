"""Fable 5.2 review: a ">" inside a quoted argument or a heredoc body is not a file write.

Before this fix the redirect scan read `python3 -c "print(2 > 1)"` as a write to a file named
`1)"`, which made an earlier passing test run STALE under an honest "Tests pass." All examples
invented.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import engine  # noqa: E402


def term(cmd, out="", code=0, seq=0):
    return engine.classify_call("terminal", {"command": cmd},
                                json.dumps({"output": out, "exit_code": code, "error": None}), None, seq=seq)


class ComparisonAndArrowAreNotRedirects(unittest.TestCase):
    def test_quoted_comparisons_and_arrows_are_not_writes(self):
        for cmd in ('python3 -c "print(2 > 1)"',
                    "grep -n 'a -> b' notes.py",
                    "jq '.items[] | select(.n > 3)' data.json",
                    "git log --format='%h => %s' -3",
                    "awk '$3 > 10' report.csv",
                    "node -e 'console.log(a>b)'",
                    'sqlite3 app.db "select count(*) from t where n >= 5"'):
            self.assertIsNone(term(cmd), cmd)

    def test_heredoc_body_is_not_scanned_for_redirects(self):
        cmd = "python3 - <<'PY'\nimport json\nif len(rows) > 3:\n    print('many -> ok')\nPY"
        self.assertIsNone(term(cmd))

    def test_herestring_is_not_read_as_a_heredoc(self):
        cmd = 'grep -c x <<< "$TEXT"\necho more'
        self.assertEqual(engine._drop_heredoc_bodies(cmd), cmd)
        result = term('grep -c x <<< "$TEXT" && cp new.py src/app.py')
        self.assertIsNotNone(result)
        self.assertEqual(result.path, "src/app.py")

    def test_device_targets_are_not_writes(self):
        for cmd in ("make build > /dev/null 2>&1", "echo warn > /dev/stderr", "echo hi >&2"):
            self.assertIsNone(term(cmd), cmd)


class RealShellWritesStillCount(unittest.TestCase):
    def test_plain_and_quoted_redirect_targets(self):
        self.assertEqual(term("echo '{\"a\":1}' > config.json").path, "config.json")
        self.assertEqual(term('echo done >> "notes/run log.txt"'.replace("run log", "runlog")).path,
                         "notes/runlog.txt")

    def test_heredoc_opening_line_redirect_is_the_target(self):
        cmd = "cat > bin/deploy-upload <<'EOF'\n#!/bin/sh\necho uploaded > /tmp/x\nEOF"
        result = term(cmd)
        self.assertEqual(result.path, "bin/deploy-upload")

    def test_real_write_after_a_pass_is_still_stale(self):
        evidence = [term("pytest -q", "5 passed", 0, 1), term("echo 'x = 2' >> src/app.py", "", 0, 2)]
        v = engine.check_turn("Tests pass.", evidence)
        self.assertEqual(v[0].status, "STALE")

    def test_inspection_command_after_a_pass_is_not_stale(self):
        evidence = [e for e in (term("pytest -q", "5 passed", 0, 1),
                                term('python3 -c "print(2 > 1)"', "True", 0, 2)) if e]
        v = engine.check_turn("Tests pass.", evidence)
        self.assertEqual(v[0].status, "BACKED")


class ShellStringsAndVariables(unittest.TestCase):
    def test_redirect_inside_a_shell_command_string_is_real(self):
        self.assertEqual(term("bash -c 'make build > build/out.json'").path, "build/out.json")
        self.assertEqual(term('sh -lc "echo hi > src/gen.py"').path, "src/gen.py")

    def test_variable_target_resolves_from_an_assignment_in_the_same_command(self):
        result = term('OUT=src/gen_table.py; python3 gen.py > "$OUT"')
        self.assertEqual(result.path, "src/gen_table.py")
        self.assertTrue(result.stales_tests)

    def test_unresolved_variable_target_is_a_write_but_cannot_stale_a_pass(self):
        for cmd in ('for f in a b; do ./gen $f > "$f.out"; done', 'python3 x.py > "${DEST}/data.json"'):
            result = term(cmd)
            self.assertIsNotNone(result, cmd)
            self.assertFalse(result.stales_tests, cmd)
        evidence = [term("pytest -q", "5 passed", 0, 1), term('./report > "$TMP_OUT"', "", 0, 2)]
        self.assertEqual(engine.check_turn("Tests pass.", evidence)[0].status, "BACKED")


class ChmodAndTouchChangeNoContent(unittest.TestCase):
    def test_chmod_is_a_write_for_the_proof_file_check_but_never_stales(self):
        result = term("chmod -x .git/hooks/pre-push")
        self.assertIsNotNone(result)
        self.assertFalse(result.stales_tests)
        self.assertTrue(engine._is_proof_file_path(result.path))

    def test_chmod_after_a_pass_is_not_stale(self):
        evidence = [term("pytest -q", "5 passed", 0, 1), term("chmod +x run.sh", "", 0, 2)]
        v = engine.check_turn("Tests pass.", evidence)
        self.assertEqual(v[0].status, "BACKED")

    def test_cp_over_code_alongside_a_notes_redirect_still_stales(self):
        result = term("echo done > notes/log.txt && cp new.py src/app.py")
        self.assertTrue(result.stales_tests)


if __name__ == "__main__":
    unittest.main()
