"""Fable 5.2 review: target matching must not call a true claim a mismatch.

Item 39 compares the target a claim names with the target the call touched. Its claim side
took ANY word after "to", and its evidence side read flags and path components as targets, so
everyday true claims read NOT BACKED with a wrong note ("Pushed to `main`." -> "pushed to
main, not code"). Real mismatches must still be caught. All examples invented.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import engine  # noqa: E402

OK = json.dumps({"success": True})


def term(cmd, out="ok"):
    return engine.classify_call("terminal", {"command": cmd}, json.dumps({"output": out, "exit_code": 0}), None, seq=1)


def send(to):
    return engine.classify_call("send_message", {"to": to, "body": "x"}, OK, None, seq=1)


def write(path):
    return engine.classify_call("write_file", {"path": path, "content": "x"}, OK, None, seq=1)


def verdict(reply, ev):
    v = engine.check_turn(reply, [ev])[0]
    return v.status, v.note


class TrueClaimsAreNotMismatches(unittest.TestCase):
    def test_everyday_true_claims_stay_backed(self):
        for reply, ev in (
            ("Pushed to `main`.", term("git push origin main")),
            ("Pushed to GitHub.", term("git push origin main")),
            ("Pushed to origin.", term("git push origin main")),
            ("Pushed to main.", term("git push -u origin main")),
            ("Pushed to main.", term("git push --force-with-lease origin HEAD:main")),
            ("Pushed to feature/login.", term("git push origin feature/login")),
            ("Pushed to fix the CI build.", term("git push origin main")),
            ("Committed to `main` as `a1b2c3d`.", term("git commit -m x && git push origin main", "[main a1b2c3d] x")),
            ("Invoice emailed to Dana.", send("dana.smith@acme.com")),
            ("Invoice emailed to Dana.", send("dsmith@acme.com")),
            ("Invoice emailed to Dana.", send("billing@acme.com")),
            ("Emailed the invoice to confirm the total.", send("dana@acme.com")),
            ("Emailed it to all three recipients.", send("dana@acme.com")),
            ("Sent the report to your inbox.", send("justin@acme.com")),
            ("Deployed to prod.", term("cd ~/dev/site && vercel --prod")),
            ("Deployed to production.", term("vercel --prod > /dev/null 2>&1")),
            ("Deployed to staging.", term("npm run test && ./deploy.sh staging")),
            ("Saved the notes file next to config.json as summary.md.", write("docs/summary.md")),
        ):
            self.assertEqual(verdict(reply, ev), ("BACKED", ""), reply)


class RealMismatchesAreStillCaught(unittest.TestCase):
    def test_wrong_person(self):
        self.assertEqual(verdict("Invoice emailed to Dana.", send("sam@acme.com")),
                         ("NOT_BACKED", "sent to sam@acme.com, not Dana"))

    def test_wrong_address(self):
        status, note = verdict("Emailed dana@acme.com the invoice.", send("sam@acme.com"))
        self.assertEqual((status, note), ("NOT_BACKED", "sent to sam@acme.com, not dana@acme.com"))

    def test_wrong_environment(self):
        self.assertEqual(verdict("Deployed to prod.", term("./deploy.sh staging")),
                         ("NOT_BACKED", "deployed to staging, not prod"))

    def test_wrong_branch(self):
        self.assertEqual(verdict("Pushed to main.", term("git push -u origin feature/login")),
                         ("NOT_BACKED", "pushed to feature/login, not main"))

    def test_wrong_file(self):
        status, note = verdict("Saved it to config.json.", write("notes/summary.md"))
        self.assertEqual((status, note), ("NOT_BACKED", "saved to summary.md, not config.json"))


class Pieces(unittest.TestCase):
    def test_git_push_refs_skip_flags_and_redirects(self):
        self.assertEqual(engine._git_push_refs("git push -u origin main"), ["origin", "main"])
        self.assertEqual(engine._git_push_refs("git push origin HEAD:release-1.2 2>&1 | tail -3"),
                         ["origin", "release-1.2"])
        self.assertEqual(engine._git_push_refs("git push"), [])

    def test_bare_git_push_names_no_branch(self):
        self.assertIsNone(engine.evidence_target("pushed", term("git push origin")))

    def test_environment_word_inside_a_path_is_not_an_environment(self):
        self.assertEqual(engine._evidence_envs("cd ~/dev/site && vercel --prod > /dev/null"), ["prod"])


if __name__ == "__main__":
    unittest.main()
