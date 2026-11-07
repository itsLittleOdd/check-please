"""Check, Please core engine. Pure stdlib, no Hermes imports, no model calls.

Two halves:
  1. classify_call(): turn one tool call (name, args, result, status) into evidence:
     which rung of the state ladder it proves, and whether it succeeded.
  2. extract_claims(): find past-tense "done" claims in the agent's final reply.
check_turn() pairs each claim with the evidence recorded during the same turn.

State ladder (low to high): drafted < saved < saved_code < tested < committed < pushed <
deployed < sent < paid. A claim only counts at the rung the tool log proves.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional

LADDER = ["drafted", "saved", "saved_code", "tested", "committed", "pushed", "deployed",
          "scheduled", "sent", "paid", "deleted"]

# Ladder families: "proven: X" should only cite evidence from the same claim's own ladder,
# never an unrelated family's higher rung (Discovered item 18). A deploy proves nothing about
# whether an invoice was sent; a commit proves nothing about whether a meeting was scheduled.
# "saved_code" (a write_file/patch to a code-looking path) sits in the code family, not the
# document family, so editing pricing.py never "proves" an unrelated invoice save (Discovered
# item 18's logged residual: a non-draft write_file still read as generic "saved" regardless
# of file content).
_LADDER_FAMILIES = (
    ("code", ("saved_code", "tested", "committed", "pushed", "deployed")),
    ("document", ("drafted", "saved", "scheduled", "sent", "paid")),
)


def _family_kinds(kind: str) -> Optional[tuple]:
    """The family (tuple of kinds) kind belongs to, or None if it is in no family
    (e.g. "deleted", which _highest_rung already excludes)."""
    for _name, kinds in _LADDER_FAMILIES:
        if kind in kinds:
            return kinds
    return None

_I = re.IGNORECASE


# ---------------------------------------------------------------- evidence ----

@dataclass
class Evidence:
    kind: str                 # one of LADDER (or "other")
    ok: bool                  # did the call succeed
    tool: str
    detail: str               # short human description (command, path)
    seq: int = 0              # order within the session
    ts: float = 0.0
    reason: str = ""          # item 38 slice 2: why a failed call failed, a few words
    stales_tests: bool = True # item 41 part (a): False for a docs/notes-only write, which a
                               # test run could not have read, so it never makes a prior test
                               # pass STALE.
    via_subagent: bool = False  # item 31: this evidence came from a delegated child's own
                                 # tool call, replayed into the parent's ledger from
                                 # subagent_stop's tool_call_history, not the parent's own call.
    path: str = ""             # item 43 root-cause fix (1), 17:10: the RAW write target, before
                               # _display_path's basename collapse for absolute/home paths. detail
                               # is for display only; _is_proof_file_path must match the real path,
                               # since an absolute "/abs/x/bin/deploy-upload" collapses to the
                               # display text "deploy-upload" and loses the "bin/" directory a
                               # proof-file dir rule needs. Empty for non-write evidence.
    env_error: bool = False    # item 43 root-cause fix (2), 17:10: True when a failed call's own
                               # result text looks like an environment problem (command not
                               # found, a missing Python module, exit code 127), not a real test
                               # or check failure. _find_edited_proof must never treat this as
                               # "the earlier failure" a later edit explains away.
    cmd: str = ""              # item 43 (Arlo, 2026-11-07): the raw shell command for terminal
                               # evidence, so check_turn can see which program actually ran.
    narrow_scope: str = ""     # item 44 (PARTIAL SUITE): set on "tested" evidence when the
                               # command only ran a single test file/module, -k expr, node id
                               # (::name) or --grep filter, never the whole suite. Empty for a
                               # whole-suite run or any non-"tested" evidence.
    fail_text: str = ""        # item 48 (GATE DATA FILES): the failed call's own result text
                               # (capped, terminal/execute_code only), so _find_edited_proof can
                               # check whether a later write's filename was actually NAMED by
                               # this gate's own refusal (an allowlist/approval/lock file quoted
                               # in the error), not just guessed from a closed path-shape list.
                               # Empty for a successful call or any call whose text was not
                               # captured.


_TEST_CMD = re.compile(
    r"\b(pytest|py\.test|python3?\s+-m\s+(pytest|unittest)|npm\s+(run\s+)?test|pnpm\s+(run\s+)?test|yarn\s+test|"
    r"bun\s+test|go\s+test|cargo\s+test|jest|vitest|mocha|rspec|phpunit|make\s+(test|check)|tox|nox|ctest)\b", _I)
# Item 44 (PARTIAL SUITE): a test command that only runs a single file/module, a -k filter, a
# node id (tests/test_x.py::test_y) or a --grep/-t name filter proves less than "all tests" or
# "the suite". Matched on the command text only (never claim text). A bare `pytest`, `pytest
# tests/` (a directory, no file extension) or `python3 -m unittest discover` is a whole-suite
# run and must NOT match here.
_TARGETED_TEST_SCOPE = re.compile(
    r"(-k\s*=?\s*['\"]?[\w.*]+['\"]?)|"
    r"(::[\w:.\[\]-]+)|"
    r"(--grep[= ]\s*['\"]?[^'\"\n]+['\"]?)|"
    r"(-t\s+['\"][^'\"\n]+['\"])|"
    r"(\btests?/[\w./-]+\.(py|js|ts|rb|go)\b)|"
    r"(\bunittest\s+([\w.]+\.[\w.]+))", _I)


def _targeted_test_scope(cmd: str) -> str:
    """The matched targeted-run token, or "" when the command looks like a whole-suite run."""
    m = _TARGETED_TEST_SCOPE.search(cmd)
    return m.group(0).strip() if m else ""


# Words that keep a "<word> tests" phrase a generic whole-suite claim, not a named subset.
# Anything else immediately before "test(s)" (e.g. "the pricing tests pass", "the users tests
# pass") names its own subset and must stay BACKED even when the evidence only ran that subset.
_SUBSET_STOPWORDS = {"all", "every", "no", "the", "my", "our", "these", "those", "a", "an",
                     "both", "its", "their", "unit", "integration", "regression", "end", "to"}
_NAMED_SUBSET_WORD = re.compile(r"\b([a-zA-Z][\w-]*)\s+tests?\b", _I)


def _claims_named_subset(text: str) -> bool:
    for m in _NAMED_SUBSET_WORD.finditer(text or ""):
        if m.group(1).lower() not in _SUBSET_STOPWORDS:
            return True
    return False
# Real checks we don't otherwise recognize as test evidence (item 27 family (e)): a script or
# flag whose own name says it's a check, not a fixed test-runner name. Scoped to command/code
# text only (never claim text), so a false match here just over-credits evidence, which is the
# direction this item wants; still word-bounded to avoid "checkout"/"doctorate"-style substrings.
_CHECK_CMD = re.compile(
    r"\b(check|verify|validate|lint|doctor|smoke|probe|assert)\b|--check\b|\bpy_compile\b|\btsc\b|"
    r"\bbash\s+-n\b|\bsh\s+-n\b|\bzsh\s+-n\b|\bnode\s+--check\b", _I)
# Item 49 (Arlo, 2026-11-07): the bare check words above matched inside an ARGUMENT path
# (invented: `cd /x/app-verify-tool`, `ls /srv/check-tool/`), so a plain `cd` into a directory
# whose name contained "verify" counted as a passing check, and in a failing `cd ... && python3
# bin/verify-thing` chain the `cd` stage was then credited as the pass. A bare check word now
# counts only as a command's own name (the first token of a stage, or the script after an
# interpreter such as python3/node/bash), or in any token with no slash in it (`make check`,
# `npm run lint`); never inside a slashed argument. The flag forms (--check, py_compile, tsc,
# bash -n) are unchanged.
_CHECK_WORD = re.compile(r"\b(check|verify|validate|lint|doctor|smoke|probe|assert)\b", _I)
_CHECK_FLAGS = re.compile(
    r"--check\b|\bpy_compile\b|\btsc\b|\bbash\s+-n\b|\bsh\s+-n\b|\bzsh\s+-n\b|\bnode\s+--check\b", _I)
_INTERPRETERS = {"python", "python3", "python2", "node", "nodejs", "bash", "sh", "zsh", "ruby",
                 "perl", "php", "deno", "bun", "npx", "uv", "uvx", "pipx", "tsx", "ts-node"}
_STAGE_SPLIT = re.compile(r"&&|\|\||;|\|")


def _stage_words(stage: str) -> list:
    words = [w for w in stage.strip().split() if not re.match(r"^\w+=", w)]
    while words and words[0] in ("sudo", "env", "exec", "nohup", "time", "command", "then", "do"):
        words = words[1:]
    return words


def _program_index(words: list) -> int:
    """Index of the token that names the real program: 0, or the script after an interpreter
    (skipping interpreter flags and `run`/`exec`, and the module after -m)."""
    if not words:
        return 0
    if words[0].rsplit("/", 1)[-1].lower() not in _INTERPRETERS or len(words) < 2:
        return 0
    j = 1
    while j < len(words) and words[j] != "-m" and (words[j].startswith("-") or words[j] in ("run", "exec")):
        j += 1
    if j < len(words) and words[j] == "-m":
        j += 1
    return j if j < len(words) else 0


def _check_cmd_match(text: str) -> bool:
    if _CHECK_FLAGS.search(text or ""):
        return True
    for stage in _STAGE_SPLIT.split(text or ""):
        words = _stage_words(stage)
        if not words:
            continue
        prog = _program_index(words)
        for i, w in enumerate(words):
            tok = _strip_quotes(w)
            if "/" in tok:
                if i == prog and _CHECK_WORD.search(tok.rsplit("/", 1)[-1]):
                    return True
                continue
            if _CHECK_WORD.search(tok):
                return True
    return False


# Item 49: a stage that only sets up the shell (cd, export, source, mkdir, echo ...) is never
# evidence of anything, whatever words its arguments contain.
_SETUP_PROGRAMS = {"cd", "pushd", "popd", "export", "source", ".", "set", "unset", "echo", "printf",
                   "mkdir", "true", "false", "test", "[", "sleep", "alias", "ulimit", "umask"}


def _stage_kind(stage: str) -> Optional[str]:
    progs = []
    for seg in re.split(r"\|\||;|\|", stage):
        words = _stage_words(seg)
        if words:
            progs.append(words[0].rsplit("/", 1)[-1].lower())
    if progs and all(p in _SETUP_PROGRAMS for p in progs):
        return None
    return _scan_ladder(stage)
_COMMIT_CMD = re.compile(r"\bgit\s+commit\b", _I)
_PUSH_CMD = re.compile(r"\bgit\s+push\b|\bgh\s+pr\s+(create|merge)\b", _I)
# Item 43 gap (b), 16:05 diagnosis: the original list only covered a closed set of named
# platform CLIs (vercel, netlify, ...). A bench scenario's own deploy tool (e.g. a project's
# bin/deploy-upload or a plain ./deploy.sh) never matched, so a failed-then-edited-then-passing
# deploy call had no "deployed" evidence to even reach _find_edited_proof. Added: a deploy
# script run by path (any dir, .sh/.py/.rb/.js/.ts extension, filename containing "deploy"),
# a bare deploy/deploy-<name> COMMAND (first token of the call, not an argument elsewhere in
# the line -- a `cp x deploy-upload` destination argument must never match), a command name
# ending "-activate", containing "deployctl", or containing release/publish/promote as its own
# command token. Each new alternative is anchored to the start of the command or right after a
# shell separator (;/&/|) so a path argument elsewhere on the line never falsely matches.
_DEPLOY_CMD_NAME = r"(^|[;&|]\s*)(sudo\s+)?\.?/?(bin/|scripts/)?[\w.-]*(deploy|deployctl|release|publish|promote)[\w.-]*(-activate)?"
_DEPLOY_CMD = re.compile(
    r"\bvercel\b[^\n;|&]*(--prod\b|\bdeploy\b|\bpromote\b)|^\s*vercel\s*$|\bnetlify\s+deploy\b|\bwrangler\s+(deploy|publish|pages\s+deploy)\b|"
    r"\bfly(ctl)?\s+deploy\b|\brailway\s+up\b|\bheroku\b[^\n]*\b(deploy|releases)\b|\bgit\s+push\s+heroku\b|"
    r"\bnpm\s+publish\b|\bpnpm\s+publish\b|\btwine\s+upload\b|\buv\s+publish\b|\bpoetry\s+publish\b|\bgh\s+release\s+create\b|"
    r"\bkubectl\s+(apply|rollout)\b|\bhelm\s+(install|upgrade)\b|\bfirebase\s+deploy\b|\bsupabase\s+functions\s+deploy\b|"
    + _DEPLOY_CMD_NAME + r"(?=\s|$)|" + _DEPLOY_CMD_NAME + r"\.(sh|py|rb|js|ts)\b", _I)
# Item 43 shape 6 (bench, 2026-11-06): a project's own mail/send tool run by path (bin/mail,
# bin/sendmail, scripts/send-mail.py) does not always use the system mail -s flag syntax, so the
# bare `mailx? -s`/`sendmail` alternatives above never matched a call like "bin/mail to@x subj
# body". Same shape as _DEPLOY_CMD_NAME's widening: anchor to the command's own first token (not
# an argument elsewhere in the line) so e.g. `cat maillog.txt` or `grep mail notes.txt` never
# falsely match.
_SEND_CMD_NAME = r"(^|[;&|]\s*)(sudo\s+)?\.?/?(bin/|scripts/)?[\w.-]*(sendmail|send[_-]?mail|send[_-]?email)[\w.-]*"
_SEND_CMD = re.compile(
    r"\bsendmail\b|\bmailx?\s+-s\b|\bhimalaya\b[^\n]*\bsend\b|\bgws\b[^\n]*\bsend\b|api\.resend\.com|smtplib|\bmsmtp\b|"
    r"discord(app)?\.com/api/(v\d+/)?(webhooks|channels/\d+/messages)|hooks\.slack\.com|slack\.com/api/chat\.post|"
    r"api\.telegram\.org/bot\S*/send|\bhermes\s+send\b|\bimsg\s+send\b|osascript[^\n]*\bMessages\b[^\n]*\bsend\b|"
    r"\bxurl\s+(post|reply|dm)\b|api\.(twitter|x)\.com/2/(tweets|dm)|api\.twilio\.com|api\.sendgrid\.com|api\.postmarkapp\.com|"
    r"(^|[;&|]\s*)(sudo\s+)?\.?/?(bin/|scripts/)mail\b|" + _SEND_CMD_NAME, _I)
_PAY_CMD = re.compile(r"\bstripe\b[^\n]*\b(pay|charge|capture|finalize)|api\.stripe\.com/v1/(charges|payment_intents|invoices/\S+/pay)", _I)
_DELETE_CMD = re.compile(r"(^|[;&|]\s*)(sudo\s+)?(rm|trash|git\s+rm|rmdir)\s", _I)
# Item 40 cause (4), bench false alarms: a script or SQL whose own name or text says it
# deletes things is delete evidence even when it isn't the bare `rm`/`trash` shape above
# (invented examples: "python purge_old_sessions.py", "sqlite3 app.db \"DELETE FROM logs ...\"",
# "./cleanup_cache.sh"). Scoped to a script filename ending in a code extension so a plain
# shell verb like "clean" in an unrelated sentence can't match; SQL DELETE FROM matched as its
# own clause. Command/code text only, same over-credit-is-the-goal rule as _CHECK_CMD above.
_DELETE_SCRIPT_CMD = re.compile(
    r"\b[\w./-]*\b(delete|purge|prune|remove|cleanup|clean_up)\w*\.(py|sh|rb|js|ts|pl|bash)\b|"
    r"\bdelete\s+from\s+\w+", _I)
# Output evidence for a delete claim when the command text itself doesn't say so (invented
# examples: "Removed 12 old sessions.", "Deleted 3 stale files", and the number-first order
# "37 of 45 rows deleted."). Scanned on the call's result text only, never on claim text.
_DELETE_OUTPUT = re.compile(r"\b(removed|deleted)\s+\d+\b|\b\d+\s+(?:\w+\s+){0,2}(?:removed|deleted)\b", _I)
# Item 43 gates note (2026-11-06 17:20): crontab -l, launchctl list, atq and similar list a
# schedule but never install or change one, so they are reads and must never back a scheduled
# claim, even when a failed install command isn't recognized as scheduling evidence at all (the
# real risk: a failed crontab install, no matching evidence for the failure, then a bare
# "crontab -l" misread as a successful install). Match any crontab invocation EXCEPT the list
# form (-l): -e, a bare install (piping a table into "crontab -" or installing from a file,
# "crontab mycron.txt"), interactive "crontab" alone. launchctl load/bootstrap still counts;
# launchctl list and atq never do (not matched at all).
_SCHEDULE_CMD = re.compile(r"\bhermes\s+cron\s+(create|add|edit)\b|\bcrontab\b(?!\s+-l\b)|\blaunchctl\s+(load|bootstrap)\b", _I)

_SEND_TOOL = re.compile(r"(^|_|\.|-)(send|post|reply|tweet|email|mail|message|dm|publish_post|create_post|chat_post)", _I)
_WRITE_TOOLS = {"write_file", "patch", "edit_file", "str_replace", "create_file", "apply_patch", "file_write", "replace_in_file"}
_DRAFT_TOOL = re.compile(r"draft", _I)
# Source/config file extensions: a write_file/patch to one of these is "saved_code" (code
# family), not generic "saved" (document family) — see Discovered item 18's logged residual.
# Deliberately excludes .md/.txt/.html (those are usually documents, not code, even though an
# .html file can be code; a path-extension heuristic cannot be perfect, only better than nothing).
_CODE_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".rs", ".java", ".kt", ".swift",
    ".c", ".h", ".cpp", ".cc", ".hpp", ".cs", ".rb", ".php", ".sh", ".bash", ".zsh", ".sql",
    ".yaml", ".yml", ".json", ".toml", ".ini", ".cfg", ".css", ".scss", ".graphql", ".proto",
    ".lua", ".pl", ".r", ".scala", ".clj", ".ex", ".exs", ".vue",
}


def _is_code_path(path: str) -> bool:
    low = path.lower().rsplit("?", 1)[0].rsplit("#", 1)[0]
    for ext in _CODE_EXTENSIONS:
        if low.endswith(ext):
            return True
    return False


# Item 41 part (a): a test run cannot have read a documentation or notes file, so writing one
# must never make an earlier passing test run STALE. Closed, invented list from Arlo's decision
# (2026-11-06 11:27): extensions, directories, and filename markers for exactly that shape of
# file. Deliberately does not touch what KIND of evidence the write still counts as (saved/
# drafted/saved_code are unchanged); it only flags the write as unable to stale a test claim.
_DOCS_NOTES_EXTENSIONS = {".md", ".markdown", ".txt", ".rst", ".adoc", ".log"}
_DOCS_NOTES_DIR = re.compile(r"(^|/)(docs|notes|reports)/", _I)
_DOCS_NOTES_NAME = re.compile(r"(README|CHANGELOG|NOTES|REPORT|HANDOFF|RESUME|CLOSEOUT|STATUS)", _I)


def _is_docs_or_notes_path(path: str) -> bool:
    if not path:
        return False
    low = path.lower().rsplit("?", 1)[0].rsplit("#", 1)[0]
    for ext in _DOCS_NOTES_EXTENSIONS:
        if low.endswith(ext):
            return True
    if _DOCS_NOTES_DIR.search(path):
        return True
    if _DOCS_NOTES_NAME.search(path):
        return True
    return False


# Item 43 (edited-proof detection): a claim backed by a successful call that only succeeded
# because the agent edited the thing meant to PROVE the claim (a test file, a CI/build script,
# a deploy tool, a git hook that gated the push/commit) between an earlier failure and the
# later pass is not real proof -- it is the classic "make the check pass by changing the
# check" failure. Proof-file shape is a closed, path-only heuristic (never file content,
# which classify_call does not retain): test file names/dirs, bin/scripts/tools/.github/ci
# dirs, hooks dirs at any depth (.git/hooks/, .husky/, a bench's own hooks/), a pre-commit
# config file, and dot-directory state dirs including .release. Item 43 gap (c), 16:05
# diagnosis: hooks/.husky/.release were the three named shapes missing from this list; a
# restore write AFTER the proof-file edit does not clear the detection (see _find_edited_proof,
# which only needs one matching write strictly between the failure and the pass). Honest
# limit: the backlog also names "a shebang file" and "basename appears in the failed/successful
# command text or output" as proof-file shapes; neither is implemented this tick (classify_call
# does not keep file content or the full raw command/output text on Evidence), logged here
# rather than guessed around.
_PROOF_FILE_DIR = re.compile(r"(^|/)(tests?|spec|__tests__|bin|scripts|tools|\.github|ci|hooks|\.husky)/", _I)
_PROOF_FILE_NAME = re.compile(
    r"(^|/)(test_[\w.-]+|[\w.-]+_test\.\w+|[\w.-]+\.test\.\w+|[\w.-]+\.spec\.\w+|conftest\.py"
    r"|\.pre-commit-config\.ya?ml)$", _I)
_PROOF_STATE_DIR = re.compile(r"(^|/)\.(cron|deploy|state|release)/", _I)


def _is_proof_file_path(path: str) -> bool:
    """Item 43: True when *path* (already basename-collapsed for absolute paths by
    _display_path, so a dir match only works for a relative path) looks like a test file,
    a CI/build/deploy script, or a dot-directory state file -- the kind of file editing it
    to force a prior failure to pass is the "edited the check, not the code" failure mode."""
    if not path:
        return False
    low = path.lower().rsplit("?", 1)[0].rsplit("#", 1)[0]
    return bool(_PROOF_FILE_DIR.search(low) or _PROOF_FILE_NAME.search(low) or _PROOF_STATE_DIR.search(low))


# Item 48 (GATE DATA FILES): the proof-file set above covers code (tests, CI/build scripts,
# hooks). A gate can also be defeated by editing the DATA it reads as policy instead of the
# check itself: an allowlist/enum/type file the gate validates against, an approval or
# sign-off record the gate requires before it passes, a lock/checksum file the gate compares
# against, or the request/state file of the tool that refused. Closed, invented filename
# shapes for those four categories only (never file content, which classify_call does not
# retain). A filename matching one of these shapes is not proof-file-worthy by itself --
# almost any config file could coincidentally match one of these broad extensions -- so this
# is paired with _named_in_failure_output below: the file must also be the one the FAILING
# gate's own output actually named, so a coincidental filename never counts.
_GATE_DATA_NAME = re.compile(
    r"(^|/)([\w.-]+\.lock$|[\w.-]*checksums?[\w.-]*$|[\w.-]*allow-?list[\w.-]*\.(json|ya?ml|txt)$|"
    r"[\w.-]*\benum\b[\w.-]*\.(json|ya?ml|txt)$|[\w.-]*\btypes?\b[\w.-]*\.(json|ya?ml)$|"
    r"[\w.-]*approv(ed|al)[\w.-]*\.(json|ya?ml|txt)?$|[\w.-]*\brequest[\w.-]*\.(json|ya?ml|txt)$|"
    r"[\w.-]*\.state$)", _I)


def _is_gate_data_name(path: str) -> bool:
    if not path:
        return False
    low = path.lower().rsplit("?", 1)[0].rsplit("#", 1)[0]
    return bool(_GATE_DATA_NAME.search(low))


def _named_in_failure_output(path: str, fail_text: str) -> bool:
    """True when *path*'s own basename (with or without its extension) appears as a whole
    word in the failing call's own output text -- the gate itself named this file, so editing
    it is editing the gate's policy data, not writing an unrelated file that happens to match
    one of the closed shapes above."""
    if not path or not fail_text:
        return False
    base = _basename(path)
    if not base:
        return False
    stem = base.rsplit(".", 1)[0] if "." in base else base
    for needle in {base, stem}:
        if len(needle) > 2 and re.search(r"\b" + re.escape(needle) + r"\b", fail_text, _I):
            return True
    return False


def _is_gate_data_path(path: str, fail_text: str) -> bool:
    """Item 48: True when *path* both looks like a gate-data shape (allowlist/enum/type,
    approval record, lock/checksum, or request/state file) AND was actually named in the
    failing gate's own output -- both conditions required, so a merely-similarly-named file
    the gate never mentioned does not count."""
    return _is_gate_data_name(path) and _named_in_failure_output(path, fail_text)


def _disclosed_edit(reply: str, path_detail: str) -> bool:
    """Item 43 disclosure exception: if the reply names the edited proof file (by its own
    basename) or plainly says it changed the script/test/tool, the edit is not hidden, so the
    claim is BACKED with a note instead of EDITED."""
    basename = _basename(path_detail or "")
    # Item 43 shape 6 fix: a bare word-boundary search let a short basename like "mail"
    # (bin/mail) false-match inside an unrelated word in the claim itself ("emailed" contains
    # "mail"), wrongly reading the proof-file edit as disclosed. Require the basename as its
    # own word (or with a code-extension suffix immediately after), not a substring of another
    # word.
    if basename and len(basename) > 2 and re.search(r"\b" + re.escape(basename) + r"\b", reply, _I):
        return True
    return bool(re.search(r"\b(edited|changed|modified|updated|fixed)\b.{0,40}\b(script|test|tool|config)\b",
                           reply, _I))


def _find_edited_proof(all_evidence: list, backers: list):
    """Item 43 root-cause fix (2), 17:10: among *backers* (same-kind evidence for one claim),
    pair the LATEST pass (the one that actually backs the claim check_turn is judging -- the
    same "latest backer by seq" rule item 42 already uses for the tested kind) with the LATEST
    real failure before it, then look for a successful proof-file write strictly between those
    two. Earlier first-fail/first-pass pairing missed the real diagnosed shape: an environment
    error (missing command/module) fails first, an unrelated real pass follows with no edit at
    all, and only LATER does a real failure get edited away and re-pass; pairing on the first
    fail/pass wrongly paired the env-error with the unedited early pass and never looked past it.
    Real failures only: an env_error-flagged failure (see classify_call) is never treated as "the
    earlier failure" here, so a chain of env-error -> real pass -> edit -> real fail -> edit ->
    real pass still finds the real fail/pass pair, not the env-error/early-pass one.
    Returns (failed_evidence, passing_evidence, proof_write_evidence) or None."""
    failed = [e for e in backers if not e.ok and not e.env_error]
    passed = [e for e in backers if e.ok]
    if not failed or not passed:
        return None
    last_pass = max(passed, key=lambda e: e.seq)
    earlier_fails = [e for e in failed if e.seq < last_pass.seq]
    if not earlier_fails:
        return None
    last_fail = max(earlier_fails, key=lambda e: e.seq)
    writes = [e for e in all_evidence
              if e.kind in {"saved", "saved_code", "drafted"} and e.ok
              and last_fail.seq < e.seq < last_pass.seq]
    for w in writes:
        target = w.path or w.detail
        if _is_proof_file_path(target) or _is_gate_data_path(target, last_fail.fail_text):
            return (last_fail, last_pass, w)
    return None


_READONLY_HINT = re.compile(r"(^|_)(list|get|read|search|fetch|view|show|describe|status|query)(_|$)", _I)


def _as_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            loaded = json.loads(value)
            return loaded if isinstance(loaded, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


def call_succeeded(result: Any, status: Optional[str] = None) -> bool:
    """Best-effort success test that never guesses success from silence on failure markers."""
    if status and str(status).lower() in {"error", "blocked", "denied", "failed", "timeout", "cancelled"}:
        return False
    data = _as_dict(result)
    # Item 38: a result's own text can say the call ran but failed (a test-summary count
    # above zero, a bare FAILED marker, "No such file", an HTTP 4xx/5xx status) even when
    # the call's own success/ok/exit_code fields look fine on this one host's tool shape.
    # Checked before everything else so these markers are never shadowed by a dict branch
    # that would otherwise return True early on a zero/missing exit_code.
    text_blob = _result_text_blob(data) if data else str(result or "")
    if _has_failure_marker(text_blob):
        return False
    if data:
        if data.get("success") is False or data.get("ok") is False:
            return False
        err = data.get("error")
        if err not in (None, "", False, [], {}):
            return False
        http_status = data.get("status_code", data.get("http_status"))
        if http_status is not None:
            try:
                if 400 <= int(http_status) < 600:
                    return False
            except (TypeError, ValueError):
                pass
        code = data.get("exit_code", data.get("returncode", data.get("exitCode")))
        if code is not None:
            try:
                return int(code) == 0
            except (TypeError, ValueError):
                return False
        if str(data.get("status", "")).lower() in {"error", "failed", "blocked", "denied"}:
            return False
        return True
    return True


# Item 38 (failure-result detection): markers in a call's own output text that a human
# would read as "it ran, but it failed", so such a call must never back a claim even when
# exit_code/success/ok look clean. Scoped to never-regress the explicit counter-case: a
# pass summary that says "0 failed"/"0 errors" must stay a pass (both count patterns
# require a leading 1-9 digit, so a leading 0 never matches).
_FAILURE_COUNT = re.compile(r"\b[1-9]\d*\s*(?:failed|failures?|errors?)\b", _I)
_FAILURE_EQ = re.compile(r"\b(?:failures?|errors?)\s*=\s*[1-9]\d*\b", _I)
_ZERO_PASSED = re.compile(r"\b0\s+(?:tests?\s+)?passed\b", _I)
_BARE_FAILED_MARKER = re.compile(r"(?<![A-Za-z0-9])FAILED(?![A-Za-z0-9])")
_RESULT_FAILURE_TEXT = re.compile(
    r"\btraceback\b|\berror:|\bpermission denied\b|\bcommand not found\b|\bexit code [1-9]|\bno such file\b", _I)
_HTTP_ERROR_STATUS = re.compile(r"\bhttp/\d(?:\.\d)?\"?\s+([45]\d{2})\b", _I)

# Item 43 root-cause fix (2), 17:10 diagnosis: a failed terminal/code call whose own result
# text says the environment itself was broken (no interpreter on PATH, a missing Python
# module, a plain "command not found", or a 127 exit code) never ran the actual check or
# test at all. _find_edited_proof must not treat that as "the earlier real failure" an edit
# then explains away; it is noise before the real attempt. Text-only, same over-credit
# direction as the other evidence-widening regexes in this module (a false env_error=True
# just makes the loop look one failure further back, never hides a real one entirely, since
# _find_edited_proof still requires its own real failed call to exist).
_ENV_ERROR_TEXT = re.compile(
    r"\bcommand not found\b|\bno module named\b|\bexit code 127\b|\bexited 127\b", _I)


def _capture_fail_text(ok: bool, result: Any) -> str:
    """Item 48: the failed call's own output text, capped, for _is_gate_data_path's
    name-match. Only captured on failure (a success never needs it); empty string otherwise."""
    if ok:
        return ""
    data = _as_dict(result)
    text_blob = _result_text_blob(data) if data else str(result or "")
    return text_blob[:1000]


def _is_env_error(result: Any) -> bool:
    data = _as_dict(result)
    text_blob = _result_text_blob(data) if data else str(result or "")
    if _ENV_ERROR_TEXT.search(text_blob):
        return True
    if data:
        code = data.get("exit_code", data.get("returncode", data.get("exitCode")))
        try:
            if code is not None and int(code) == 127:
                return True
        except (TypeError, ValueError):
            pass
    return False

_RESULT_TEXT_KEYS = ("output", "stdout", "stderr", "message", "body", "text", "content")


def _result_text_blob(data: dict) -> str:
    """Join the result dict's own text fields for marker scanning; falls back to str(data)
    when none of the known keys hold a string, so an unexpected shape is still scanned."""
    parts = [v for k in _RESULT_TEXT_KEYS if isinstance((v := data.get(k)), str) and v]
    return "\n".join(parts) if parts else str(data)


def _has_failure_marker(text: str) -> bool:
    if not text:
        return False
    return bool(
        _RESULT_FAILURE_TEXT.search(text)
        or _FAILURE_COUNT.search(text)
        or _FAILURE_EQ.search(text)
        or _ZERO_PASSED.search(text)
        or _BARE_FAILED_MARKER.search(text)
        or _HTTP_ERROR_STATUS.search(text)
    )


_GENERIC_FAILURE_REASON = "the call ran but did not succeed"


def _failure_reason(result: Any, status: Optional[str] = None) -> str:
    """Item 38 slice 2: a few words on WHY a failed call failed, for the FAILED verdict's
    note, instead of the one generic sentence every failure used to get. Mirrors
    call_succeeded()'s own checks, in the same priority order, so a reason is only ever
    reported for a call this module already decided is a failure; never guess a reason for
    a call that actually passed. Falls back to the old generic sentence when nothing more
    specific is found, so no caller sees an empty note."""
    if status and str(status).lower() in {"error", "blocked", "denied", "failed", "timeout", "cancelled"}:
        return f"call status: {status}"
    data = _as_dict(result)
    text_blob = _result_text_blob(data) if data else str(result or "")
    m = _HTTP_ERROR_STATUS.search(text_blob)
    if m:
        return f"http {m.group(1)}"
    m = _FAILURE_COUNT.search(text_blob) or _FAILURE_EQ.search(text_blob)
    if m:
        return m.group(0)
    if _ZERO_PASSED.search(text_blob):
        return "0 passed"
    if _BARE_FAILED_MARKER.search(text_blob):
        return "result marked FAILED"
    m = _RESULT_FAILURE_TEXT.search(text_blob)
    if m:
        return m.group(0)
    if data:
        err = data.get("error")
        if err not in (None, "", False, [], {}):
            return str(err)[:80]
        http_status = data.get("status_code", data.get("http_status"))
        if http_status is not None:
            try:
                if 400 <= int(http_status) < 600:
                    return f"http {http_status}"
            except (TypeError, ValueError):
                pass
        code = data.get("exit_code", data.get("returncode", data.get("exitCode")))
        if code is not None:
            try:
                if int(code) != 0:
                    return f"exited {int(code)}"
            except (TypeError, ValueError):
                pass
        st = str(data.get("status", "")).lower()
        if st in {"error", "failed", "blocked", "denied"}:
            return f"call status: {st}"
    return _GENERIC_FAILURE_REASON


# Real Hermes tool names (verified in ~/.hermes/hermes-agent/tools, read-only, 2026-11-05):
# execute_code (code_execution_tool.py), browser_exec (browser_use_cli.py: runs Python against
# browser helpers, not a fixed verb), delegate_task (delegate_tool.py: spins a child agent, proves
# nothing itself — the child's own tool calls are what would prove something, and this plugin only
# ever sees the parent session's ledger), cronjob_manage (cronjob_tools.py).
_CODE_TEXT_TOOLS = {"execute_code", "browser_exec"}
_NO_EVIDENCE_TOOLS = {"delegate_task", "delegate", "oneshot_delegate"}

# Same ladder, reusable against any free-text blob (a shell command or a code_execution/browser_exec
# body): both run things that can match the same command patterns (git push, vercel --prod, a
# requests.post to a webhook, stripe.Charge.create, ...).
_LADDER_PATTERNS = (("paid", _PAY_CMD), ("sent", _SEND_CMD), ("deployed", _DEPLOY_CMD),
                     ("pushed", _PUSH_CMD), ("committed", _COMMIT_CMD), ("scheduled", _SCHEDULE_CMD),
                     ("tested", _TEST_CMD), ("deleted", _DELETE_CMD))


def _scan_ladder(text: str) -> Optional[str]:
    """Highest-stakes rung whose pattern matches anywhere in text, or None."""
    for kind, pat in _LADDER_PATTERNS:
        if pat.search(text):
            return kind
    if _check_cmd_match(text):
        return "tested"
    if _DELETE_SCRIPT_CMD.search(text):
        return "deleted"
    return None


# Item 26(a): a real Hermes lab turn (2026-11-05) showed a full absolute path
# (".../cp-lab-demo/pricing.py") in a note/proof line. Never show an absolute or
# home-directory path; collapse it to its basename, which is still enough to identify
# the file in a short Check line. A relative path is already safe and left as-is.
def _basename(path: str) -> str:
    p = path.rstrip("/")
    return p.rsplit("/", 1)[-1] if "/" in p else p


def _display_path(path: str) -> str:
    if not path:
        return path
    if path.startswith("/") or path.startswith("~"):
        return _basename(path)
    return path


# Item 26(b): the same lab turn showed a proof command line running past 150 chars.
# Prefer the last `&&`-joined sub-command (usually the one that actually matters) when
# that alone fits; otherwise hard-truncate with an ellipsis rather than silently cutting
# off mid-flag with no indication anything was dropped.
_MAX_PROOF_CMD = 150


def _shorten_command(cmd: str) -> str:
    line = cmd.strip().splitlines()[0] if cmd.strip() else ""
    if len(line) <= _MAX_PROOF_CMD:
        return line
    if "&&" in line:
        last = line.rsplit("&&", 1)[-1].strip()
        if last and len(last) <= _MAX_PROOF_CMD:
            return last
    return line[: _MAX_PROOF_CMD - 3].rstrip() + "..."


def _earliest_chain_kind(cmd: str) -> Optional[str]:
    """For a `&&`-joined chain with at least two sub-commands whose own text matches a ladder
    pattern, the kind of the FIRST such match, never the last (item 40 cause (3): a chained
    command's own LATER stage failing, e.g. `git push` after a successful `git commit`, must
    not make the chain's EARLIER stage read as failed too; `&&` only runs the next sub-command
    when the previous one exits 0, so when the whole chain fails, every matched stage before
    the final matched stage already succeeded). A chain with only ONE matched stage (for
    example `echo building && git push`) is not this shape: that single match IS the stage
    most likely responsible for the failure, so it is left alone (returns None, "when unsure,
    do not mark FAILED" cuts the other way here -- do not guess a success either). Scoped to
    `&&` only this tick, not `;`/`||` (the smaller slice per the 18:34 entry's design note);
    `;`/`||` chains are an honest, logged gap.
    """
    kinds = [k for k in (_stage_kind(part) for part in cmd.split("&&")) if k]
    if len(kinds) < 2:
        return None
    # Item 49 note: a same-kind chain (`pytest a && pytest b`) that fails still credits the
    # first stage, as before. Arlo measured the stricter rule on 2026-11-07: it turned an
    # honest "targeted checks passed, full suite not green" reply (stage 1 an inline assert,
    # stage 2 the suite) into a FAILED false alarm. Revisit together with item 54.
    return kinds[0]


_COMMIT_OK = re.compile(r"^\[[^\]\s]+(?: \([^)]*\))? [0-9a-f]{7,}\]", re.M)

# Item 43 (Arlo, 2026-11-07): claims about effects outside the machine. A program the agent
# wrote or overwrote earlier in the same session cannot prove one of these, disclosed or not:
# a mail script it just wrote that prints "delivered" proves nothing reached the recipient.
_OUTWARD_KINDS = {"sent", "paid", "pushed", "deployed", "scheduled"}
_CHAIN_SPLIT = re.compile(r"&&|\|\||;|\|")
_SKIP_WORDS = {"sudo", "env", "exec", "nohup", "time", "command", "then", "do"}


def _programs_run(cmd: str) -> list:
    """First word of every sub-command (env assignments, cd and wrappers skipped), as written."""
    progs = []
    for part in _CHAIN_SPLIT.split(_drop_script_bodies(cmd or "")):
        words = [w for w in part.strip().split() if not re.match(r"^\w+=", w)]
        while words and words[0] in _SKIP_WORDS:
            words = words[1:]
        if words and words[0] not in ("cd", "echo", "printf", "cat", "true", "false", "test", "["):
            progs.append(_strip_quotes(words[0]))
    return progs


def _rewritten_program(evidence: list, backing: "Evidence") -> str:
    """If a program the backing terminal call ran was written earlier this session (a
    write_file/patch/shell write whose target is that program, inside a bin/ folder or by the
    same relative path), return its display name; else ''. Only the program that actually ran
    counts: editing a helper it calls is the proof-file rule's business, not this one."""
    if not backing.cmd:
        return ""
    writes = [e for e in evidence if e.kind in {"saved", "saved_code", "drafted"} and e.ok
              and e.path and e.seq < backing.seq]
    for prog in _programs_run(backing.cmd):
        base = prog.rsplit("/", 1)[-1]
        if not base or len(base) < 2:
            continue
        for w in writes:
            wp = w.path.replace("\\", "/")
            if wp.rsplit("/", 1)[-1] != base:
                continue
            if "/" in prog:
                if wp.endswith(prog.lstrip("./")):
                    return prog
            elif re.search(r"(^|/)bin/[^/]+$", wp):
                return f"bin/{base}"
    return ""


def _first_stage_failed(cmd: str, kind: str, result: Any) -> bool:
    """Item 43 (Arlo, 2026-11-07): in a failed `a && b` chain, `&&` stops at the first failing
    stage, so the earliest matched stage did not necessarily succeed. Evidence that it failed:
    its own program printed an error line starting with "<program>:" (for example
    "crontab: Operation not permitted"), or, for a git commit stage, git saying there was
    nothing to commit."""
    out = str(result or "")
    stage = next((part for part in cmd.split("&&") if _stage_kind(part) == kind), "")
    words = [w for w in stage.strip().split() if "=" not in w.split("/")[0] and w not in ("sudo", "env")]
    prog = words[0].rsplit("/", 1)[-1] if words else ""
    if prog and re.search(rf"(^|\n|\")\s*{re.escape(prog)}:\s", out):
        return True
    if kind == "committed" and re.search(r"nothing to commit|no changes added to commit", out):
        return True
    return False


def _semicolon_kind_is_ambiguous(cmd: str, kind: Optional[str]) -> bool:
    """Item 40 cause (3), the `;` case. Unlike `&&`, every `;`-joined sub-command runs no
    matter what the previous one did, so an earlier stage's own success or failure is
    independent of a later stage's and of the call's overall exit code. `_scan_ladder`'s
    whole-text scan picks a kind by PRIORITY order (paid outranks pushed, for example), not
    by position, so on a chain like `stripe charge the card; deploy_prod.sh` (invented) the
    globally-picked kind is \"paid\" even though \"deployed\" is the LAST sub-command, the one
    whose own exit status plausibly drives the overall `ok`. When the globally-picked kind
    does not match the chain's own last sub-command, trusting the overall `ok` for it is a
    guess in either direction (it may have already succeeded before an unrelated later stage
    failed, exactly like the `&&` case, but we cannot prove that the way `&&` guarantees it) --
    \"when unsure, do not mark FAILED\" cuts here too, as no evidence rather than a verdict.
    When the globally-picked kind IS the last sub-command's own kind, there is no ambiguity:
    that stage's exit status is what the overall result almost certainly reflects, same as an
    unchained command, so this returns False (trust `ok` as normal).
    """
    parts = cmd.split(";")
    if len(parts) < 2:
        return False
    part_kinds = [_stage_kind(part) for part in parts]
    if len([k for k in part_kinds if k]) < 2:
        return False
    return part_kinds[-1] != kind


def _double_pipe_kind_is_ambiguous(cmd: str, kind: Optional[str]) -> bool:
    """Item 40 cause (3), the `||` case (the other named gap from the 02:35 tick, design
    pass done separately from `;` since the semantics are the mirror image). A `cmd1 ||
    cmd2` chain only runs `cmd2` when `cmd1` fails; the whole chain's own exit code is
    nonzero only when BOTH sides fail, and zero when EITHER side succeeds. So an overall
    FAILURE here is unambiguous (every matched sub-command genuinely ran and genuinely
    failed, unlike `;`), and needs no guard -- the risk is on the SUCCESS side instead.
    `_scan_ladder`'s whole-text priority scan can pick a kind from a sub-command that
    never actually ran: for `deploy_prod.sh || echo fallback ok` (invented), if
    deploy_prod.sh fails, echo runs and always exits 0, so the call reads ok=True overall,
    but crediting \"deployed\" as BACKED would be wrong -- deploy_prod.sh is exactly the
    command that failed. Only the FIRST sub-command's own success explains an overall
    success without anything after it needing to run, so a picked kind that is not the
    first sub-command's own kind cannot be trusted as BACKED; this returns True
    (ambiguous, no evidence) for every success case with 2+ distinct matched kinds where
    the picked kind differs from the first sub-command's own kind.
    """
    parts = cmd.split("||")
    if len(parts) < 2:
        return False
    part_kinds = [_stage_kind(part) for part in parts]
    distinct = {k for k in part_kinds if k}
    if len(distinct) < 2:
        return False
    return part_kinds[0] != kind


_PIPE_FILTER_CMD = re.compile(
    r"^\s*(grep|egrep|fgrep|wc|head|tail|sort|uniq|cut|awk|sed)\b", _I)


def _single_pipe_kind_is_ambiguous(cmd: str, kind: Optional[str]) -> bool:
    """A new named chain gap, same family as item 40 cause (3) but for a single `|`
    pipe rather than `&&`/`;`/`||`. A plain `cmd1 | cmd2` pipe runs the left side
    regardless of its own exit code and feeds its stdout to the right side; bash's
    default (non-pipefail) exit status reflects ONLY the right side. A real shape
    this misreads (invented): `pytest tests/ | grep -i fail` exits nonzero when grep
    finds NO match, i.e. no failures were reported -- the opposite of what a nonzero
    exit normally signals -- while the ladder match (\"tested\") sits in the LEFT
    segment, whose own exit code we never see. Scoped narrowly to avoid guessing a
    verdict either way: only when the matched kind comes from the first segment and
    every later segment is a known exit-code-untrustworthy filter/report tool (grep,
    wc, head, tail, sort, uniq, cut, awk, sed) that is not itself the ladder action,
    so a pipe into a real second action (e.g. a mail or deploy command) is left
    alone -- that shape is already provable from the second command's own kind.
    Scoped to the failure side only, matching item 40's \"when unsure, do not mark
    FAILED\" rule; a pipe into one of these tools on the success side already reads
    as a normal pass, which this does not change.
    """
    if "&&" in cmd or ";" in cmd or "||" in cmd or "|" not in cmd:
        return False
    parts = cmd.split("|")
    if len(parts) < 2:
        return False
    first_kind = _stage_kind(parts[0])
    if not first_kind or first_kind != kind:
        return False
    return all(_PIPE_FILTER_CMD.search(p) for p in parts[1:])


# Item 43 gap (a), 16:05 diagnosis: a shell command can edit a proof file just as surely as
# write_file/patch (sed -i on bin/deploy-upload, a redirect into config.json, cp over a test
# fixture). Before this, _find_edited_proof's writes list only ever saw _WRITE_TOOLS calls, so
# a failed-deploy / shell-edit-the-tool / passing-deploy sequence with no write_file call at
# all produced no edit evidence and the claim read plain BACKED. Conservative, best-effort
# target extraction: last token for cp/mv/ln/install (the destination), all remaining tokens
# for touch/chmod, the file after -i for sed/perl, every redirect target, every tee target.
# Quoted targets have their quotes stripped. This function is only called when _scan_ladder
# found no ladder kind at all, so it never competes with a real deploy/test/push match in the
# same command.
_REDIRECT_TARGET = re.compile(r"(?:>>|>)\s*([^\s|&;]+)")
_TEE_TARGET = re.compile(r"\btee\b(?:\s+-\w+)*\s+([^\s|&;]+(?:\s+[^\s|&;]+)*)", _I)
_CP_MV_LN_INSTALL_CMD = re.compile(r"(?:^|[;&|]\s*)(?:sudo\s+)?(?:cp|mv|ln|install)\s+(.+?)(?:[;&|]|$)", _I)
_TOUCH_CMD = re.compile(r"(?:^|[;&|]\s*)touch\s+(.+?)(?:[;&|]|$)", _I)
_CHMOD_CMD = re.compile(r"(?:^|[;&|]\s*)chmod\s+\S+\s+(.+?)(?:[;&|]|$)", _I)
_SED_PERL_I_CMD = re.compile(r"""(?:^|[;&|]\s*)(?:sed|perl)\s+-i\S*\s+(?:['"].*?['"]\s+)?(.+?)(?:[;&|]|$)""", _I)


def _strip_quotes(token: str) -> str:
    token = token.strip()
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "'\"":
        token = token[1:-1]
    return token


_QUOTED_SPAN = re.compile(r"'[^']*'" + r'|"(?:[^"\\]|\\.)*"', re.S)


def _drop_script_bodies(cmd: str) -> str:
    """Item 43 (Arlo, 2026-11-07): a quoted argument that is really a script body (an echo or
    printf of a multi-line file, or anything long) can contain its own redirects and verbs.
    Those describe the file being written, not writes this command performs. Replace such spans
    with a placeholder; short quoted tokens (a quoted path) are kept."""
    def repl(m):
        body = m.group(0)
        if "\n" in body or "\\n" in body or len(body) > 120:
            return "'BODY'"
        return body
    return _QUOTED_SPAN.sub(repl, cmd)


def _shell_write_targets(cmd: str) -> list:
    """Best-effort list of every file path a shell command writes to, or [] when the command
    does not look like a write. Script bodies inside quotes are ignored (_drop_script_bodies).
    All sub-commands of a chain contribute (cp then a redirect in one && chain both count)."""
    cmd = _drop_script_bodies(cmd)
    targets = []
    for m in _REDIRECT_TARGET.finditer(cmd):
        t = _strip_quotes(m.group(1))
        if t and t != "BODY" and not t.startswith("&") and t != "/dev/null":
            targets.append(t)
    for m in _TEE_TARGET.finditer(cmd):
        for tok in m.group(1).split():
            if not tok.startswith("-"):
                targets.append(_strip_quotes(tok))
    for rx, last_only in ((_CP_MV_LN_INSTALL_CMD, True), (_SED_PERL_I_CMD, True),
                          (_TOUCH_CMD, False), (_CHMOD_CMD, False)):
        for m in rx.finditer(cmd):
            toks = [t for t in m.group(1).split() if not t.startswith("-")]
            if toks:
                targets.extend([_strip_quotes(toks[-1])] if last_only else [_strip_quotes(t) for t in toks])
    seen, out = set(), []
    for t in targets:
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def classify_call(tool_name: str, args: Any, result: Any = None, status: Optional[str] = None,
                  seq: int = 0, ts: float = 0.0) -> Optional[Evidence]:
    """Return the evidence a tool call provides, or None when it proves nothing on the ladder."""
    name = (tool_name or "").strip()
    low = name.lower()
    a = _as_dict(args)
    ok = call_succeeded(result, status)
    reason = "" if ok else _failure_reason(result, status)

    if low in _NO_EVIDENCE_TOOLS:
        # A child agent's work is not this parent turn's evidence: the plugin only sees the
        # parent session's ledger, and the child may still be running or may have failed.
        return None

    if low in {"terminal", "shell", "bash", "run_command", "execute_command"}:
        cmd = str(a.get("command") or a.get("cmd") or "")
        short = _shorten_command(cmd)
        kind = _scan_ladder(cmd)
        env_error = (not ok) and _is_env_error(result)
        if kind is None and ok and _DELETE_OUTPUT.search(str(result or "")):
            kind = "deleted"
        if kind is None and ok:
            # Item 43 gap (a): a shell write (cp, redirect, tee, sed -i, ...) edits a file
            # exactly like write_file/patch, so it must be visible to _find_edited_proof too.
            targets = _shell_write_targets(cmd)
            if targets:
                proof = [t for t in targets if _is_proof_file_path(t)]
                path = (proof or targets)[0]
                wkind = "saved_code" if _is_code_path(path) else "saved"
                stales = not _is_docs_or_notes_path(path)
                return Evidence(wkind, True, name, _display_path(path)[:160], seq, ts, "",
                                 stales_tests=stales, path=path)
        if not ok and "&&" in cmd:
            earliest = _earliest_chain_kind(cmd)
            if earliest:
                if _first_stage_failed(cmd, earliest, result):
                    return Evidence(earliest, False, name, short, seq, ts, reason or "the first step failed",
                                    cmd=cmd[:2000])
                return Evidence(earliest, True, name, short, seq, ts, cmd=cmd[:2000])
        if not ok and ";" in cmd and "&&" not in cmd and _semicolon_kind_is_ambiguous(cmd, kind):
            return None
        if ok and "||" in cmd and "&&" not in cmd and _double_pipe_kind_is_ambiguous(cmd, kind):
            return None
        if not ok and "|" in cmd and "&&" not in cmd and ";" not in cmd and "||" not in cmd \
                and _single_pipe_kind_is_ambiguous(cmd, kind):
            return None
        narrow = _targeted_test_scope(cmd) if kind == "tested" else ""
        return Evidence(kind, ok, name, short, seq, ts, reason, env_error=env_error,
                         narrow_scope=narrow, cmd=cmd[:2000],
                         fail_text=_capture_fail_text(ok, result)) if kind else None

    if low in _CODE_TEXT_TOOLS:
        # execute_code runs Python directly; browser_exec runs Python against browser helpers.
        # Neither has a fixed verb, so scan the code body for the same ladder patterns. A plain
        # form fill/click/navigate call matches nothing here, which is the point: a form submit
        # is not a send unless the code actually posts somewhere (webhook URL, mail API, etc.).
        code = str(a.get("code") or a.get("script") or "")
        short = _shorten_command(code)
        kind = _scan_ladder(code)
        if kind is None and ok and _DELETE_OUTPUT.search(str(result or "")):
            kind = "deleted"
        return Evidence(kind, ok, name, short, seq, ts, reason,
                         env_error=(not ok) and _is_env_error(result),
                         fail_text=_capture_fail_text(ok, result)) if kind else None

    if low in _WRITE_TOOLS:
        path = str(a.get("path") or a.get("file_path") or a.get("filename") or "")
        if re.search(r"(^|/)drafts?/|draft", path, _I):
            kind = "drafted"
        elif _is_code_path(path):
            kind = "saved_code"
        else:
            kind = "saved"
        stales = not _is_docs_or_notes_path(path)
        return Evidence(kind, ok, name, _display_path(path)[:160], seq, ts, reason,
                         stales_tests=stales, path=path)

    if low in {"cronjob", "cron", "schedule", "cronjob_manage"}:
        action = str(a.get("action") or "")
        if action.lower() in {"create", "add", "update", "edit", "resume", ""}:
            return Evidence("scheduled", ok, name, str(a.get("name") or a.get("schedule") or "")[:160], seq, ts, reason)
        return None

    # Messaging/MCP/connector tools (mcp__<server>__<tool>, connectors__<connector>__<tool>) and
    # bare names like send_message. Read-only verbs never count as a send.
    tail = re.split(r"__|\.", low)[-1]
    if _DRAFT_TOOL.search(tail):
        return Evidence("drafted", ok, name, _target_hint(a), seq, ts, reason)
    if _READONLY_HINT.search(tail):
        return None
    if _SEND_TOOL.search(tail) or low in {"send_message", "message", "imessage"}:
        return Evidence("sent", ok, name, _target_hint(a), seq, ts, reason)
    return None


# Item 31 (subagent claim check), part (a). subagent_stop's tool_call_history is metadata
# only (see hooks.md "Subagent Lifecycle" and tools/delegate_tool_results.py's
# _subagent_stop_tool_call_history): each entry is {"tool_name": str, "tool_input":
# {"argument_keys": [...], "targets": {...}}, "status": "ok"|"error"|"unknown"}. There is no
# raw command/code text, so a terminal/execute_code child call cannot be scanned by
# _scan_ladder the way the parent's own calls are (an honest, logged gap, not guessed around).
# Classification here is tool-name-and-targets only, exactly as the backlog item specifies.
_NO_EVIDENCE_SUBAGENT_TOOLS = {"terminal", "shell", "bash", "run_command", "execute_command",
                               "execute_code", "browser_exec"}


def classify_subagent_entry(entry: Any) -> Optional[Evidence]:
    """One tool_call_history row from subagent_stop -> Evidence tagged via_subagent, or None.

    Only tool name and the sanitized targets dict are available (see module note above); a
    terminal/execute_code call with no dedicated tool name classifies as nothing, same as a
    read-only or delegate_task call on the parent's own ledger.
    """
    if not isinstance(entry, dict):
        return None
    name = str(entry.get("tool_name") or "").strip()
    low = name.lower()
    if not name or low in _NO_EVIDENCE_SUBAGENT_TOOLS or low in _NO_EVIDENCE_TOOLS:
        return None
    status = str(entry.get("status") or "unknown").lower()
    ok = status == "ok"
    tool_input = entry.get("tool_input")
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    targets = tool_input.get("targets")
    targets = targets if isinstance(targets, dict) else {}
    detail = _target_hint(targets) or ", ".join(sorted(str(k) for k in targets)[:3])

    if low in _WRITE_TOOLS:
        path = str(targets.get("path") or targets.get("file_path") or targets.get("filename") or "")
        if re.search(r"(^|/)drafts?/|draft", path, _I):
            kind = "drafted"
        elif _is_code_path(path):
            kind = "saved_code"
        else:
            kind = "saved"
        stales = not _is_docs_or_notes_path(path)
        return Evidence(kind, ok, name, _display_path(path)[:160] or detail, 0, 0.0, "", stales, True)

    if low in {"cronjob", "cron", "schedule", "cronjob_manage"}:
        return Evidence("scheduled", ok, name, detail, 0, 0.0, "", True, True)

    tail = re.split(r"__|\.", low)[-1]
    if _DRAFT_TOOL.search(tail):
        return Evidence("drafted", ok, name, detail, 0, 0.0, "", True, True)
    if _READONLY_HINT.search(tail):
        return None
    if _SEND_TOOL.search(tail) or low in {"send_message", "message", "imessage"}:
        return Evidence("sent", ok, name, detail, 0, 0.0, "", True, True)
    return None


def subagent_evidence(tool_call_history: Any, seq_start: int = 0, ts: float = 0.0) -> list:
    """Classify every entry in a subagent_stop's tool_call_history into Evidence, in order.

    Only entries that classify to a ladder kind are returned (same "proves nothing -> None"
    contract as classify_call); seq is assigned sequentially starting at seq_start so this
    evidence sorts after whatever the parent turn already logged.
    """
    if not isinstance(tool_call_history, list):
        return []
    out = []
    seq = seq_start
    for entry in tool_call_history:
        ev = classify_subagent_entry(entry)
        if ev is None:
            continue
        seq += 1
        ev.seq = seq
        ev.ts = ts
        out.append(ev)
    return out


def _target_hint(a: dict) -> str:
    for key in ("to", "recipient", "recipients", "channel", "chat_id", "target", "email", "thread"):
        if a.get(key):
            return f"{key}={str(a[key])[:80]}"
    return ""


# Item 39 (target matching): when a claim names a target (a person, an address, a branch, an
# environment, a file) and the matching call's own target is also knowable, a mismatch is a
# real red flag ("sent to sam@..., not Dana"). Only compare when BOTH sides name a target;
# never guess a target from a vague claim or a call with no identifiable target. Scoped to the
# five kinds that typically name a target; "tested" and "deleted" never compute one.
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_ENV_WORD_RE = re.compile(r"\b(prod|production|staging|stage|dev|development|test|testing)\b", _I)
# Claim-side environment extraction is scoped to "to <env>"/"on <env>" so a generic sentence
# like "Staging is live!" (where the environment is the grammatical subject, not necessarily
# the ONLY place deployed this turn) never reads as a hard target claim; evidence-side keeps
# the wider whole-command scan since a command line rarely uses the env word for anything else.
_ENV_TO_RE = re.compile(r"\b(?:to|on)\s+(prod|production|staging|stage|dev|development|test|testing)\b", _I)
_ENV_ALIASES = {"production": "prod", "stage": "staging", "development": "dev", "testing": "test"}
_FILE_EXT_RE = re.compile(
    r"\b[\w-]+\.(?:py|js|ts|jsx|tsx|md|txt|json|ya?ml|csv|html?|css|rb|go|java|cpp|c|sql|sh|env)\b", _I)
_TO_TOKEN_RE = re.compile(r"\bto\s+([A-Za-z][\w'.-]*)\b")
# Words after "to" that are never a real person/branch target (sentence glue, not a name).
_SENT_TO_STOPWORDS = {"the", "it", "this", "that", "them", "everyone", "staging", "prod",
                       "production", "review", "merge"}
_PUSH_TO_STOPWORDS = {"the", "it", "this", "that", "them", "review"}
_GIT_PUSH_BRANCH_RE = re.compile(r"\bgit\s+push\s+(?:[\w.-]+\s+)?([\w][\w./-]*)\b", _I)


def _normalize_env(word: str) -> str:
    w = word.lower()
    return _ENV_ALIASES.get(w, w)


def claim_target(kind: str, text: str) -> Optional[str]:
    """Extract the target a claim names (lowercased except person names), or None when the
    sentence names no target for this kind. Never guesses: a kind with no target pattern
    (e.g. "tested") always returns None."""
    if not text:
        return None
    if kind == "sent":
        m = _EMAIL_RE.search(text)
        if m:
            return m.group(0).lower()
        m = _TO_TOKEN_RE.search(text)
        if m and m.group(1).lower() not in _SENT_TO_STOPWORDS:
            return m.group(1)
        return None
    if kind == "deployed":
        m = _ENV_TO_RE.search(text)
        return _normalize_env(m.group(1)) if m else None
    if kind in ("pushed", "committed"):
        m = _TO_TOKEN_RE.search(text)
        if m and m.group(1).lower() not in _PUSH_TO_STOPWORDS:
            return m.group(1).lower()
        return None
    if kind in ("saved", "drafted"):
        m = _FILE_EXT_RE.search(text)
        return m.group(0).lower() if m else None
    return None


# Item 29(a): short claim labels for the Desktop pane row (backlog's own examples: "Tests ran
# clean", "Invoice draft saved", "Invoice emailed to Dana"). A plain kind word, never a guessed
# object name beyond what claim.target already extracted -- the full sentence stays available
# on hover/expand (rowDetailText in desktop/plugin.js) for anything this label can't show.
_KIND_LABELS = {
    "tested": "Tests ran clean",
    "sent": "Message sent",
    "deployed": "Deploy went live",
    "pushed": "Changes pushed",
    "committed": "Changes committed",
    "saved": "File saved",
    "saved_code": "Code saved",
    "drafted": "Draft saved",
    "paid": "Payment made",
    "scheduled": "Job scheduled",
    "deleted": "File deleted",
}


def claim_label(claim: "Claim") -> str:
    """A short display label for one claim row, built from its kind and (when present) its
    own extracted target (item 39's claim.target) only -- never a free-text guess from the
    sentence. Falls back to the kind word itself for an unlisted kind."""
    base = _KIND_LABELS.get(claim.kind, claim.kind.replace("_", " ").capitalize())
    target = claim.target
    if not target:
        return base
    if claim.kind == "sent":
        return f"{base} to {target}"
    if claim.kind in ("pushed", "committed"):
        return f"{base} to {target}"
    if claim.kind == "deployed":
        return f"{base} on {target}"
    if claim.kind in ("saved", "saved_code", "drafted"):
        return f"{target} saved"
    return base


def evidence_target(kind: str, ev: "Evidence") -> Optional[str]:
    """Extract the target a piece of evidence actually touched, or None when unknowable.
    Reads only ev.detail/ev.path (already-classified, display-safe text), never raw args."""
    if ev is None:
        return None
    if kind == "sent":
        m = _EMAIL_RE.search(ev.detail or "")
        return m.group(0).lower() if m else None
    if kind == "deployed":
        m = _ENV_WORD_RE.search(ev.detail or "")
        return _normalize_env(m.group(1)) if m else None
    if kind in ("pushed", "committed"):
        m = _GIT_PUSH_BRANCH_RE.search(ev.detail or "")
        if m:
            branch = m.group(1).rstrip(".,!?")
            if branch.lower() not in _PUSH_TO_STOPWORDS:
                return branch.lower()
        return None
    if kind in ("saved", "drafted"):
        p = ev.path or ev.detail or ""
        if not p:
            return None
        return p.rsplit("/", 1)[-1].lower()
    return None


def _targets_equivalent(kind: str, a: str, b: str) -> bool:
    """True when two named targets really are the same thing. A plain name ("Dana") and an
    email whose local part matches ("dana@example.com") count as equivalent -- a real send
    tool only ever reports the address, never the person's name back, so without this a
    correct send would always misread as a mismatch."""
    a_l, b_l = a.lower(), b.lower()
    if a_l == b_l:
        return True
    if kind == "sent":
        a_local = a_l.split("@", 1)[0]
        b_local = b_l.split("@", 1)[0]
        if a_local and a_local == b_local:
            return True
    return False


def target_mismatch_note(claim: "Claim", kind: str, ev: Optional["Evidence"]) -> Optional[str]:
    """None when there's nothing to say; otherwise the exact mismatch note ("sent to
    sam@..., not Dana"). Only fires when BOTH the claim and the evidence name a target and
    they genuinely differ (case-insensitive, email-vs-name aware for "sent")."""
    c_target = claim.target
    if not c_target or ev is None:
        return None
    e_target = evidence_target(kind, ev)
    if not e_target:
        return None
    if _targets_equivalent(kind, c_target, e_target):
        return None
    return f"{kind} to {e_target}, not {c_target}"


# ------------------------------------------------------------------ claims ----

@dataclass
class Claim:
    kind: str          # ladder rung the sentence claims
    text: str          # the sentence (trimmed)
    phrase: str        # the matched words
    past_scoped: bool = False  # item 42 bug (b): describes an EARLIER run, not the latest one
    target: Optional[str] = None  # item 39: the person/address/branch/environment/file the
                                   # claim names, or None when the sentence names no target.


# (rung, pattern). Past tense / completed state only.
_CLAIM_PATTERNS: list[tuple[str, re.Pattern]] = [(k, re.compile(p, _I)) for k, p in [
    ("paid", r"\b((?<!well )(?<!highly )(?<!fairly )(?<!poorly )"
             r"paid(?!\s+(attention|off|vacation|leave|time off|internship|plan|tier|feature|version|subscription|account|add[- ]on|service|"
             r"ads?|media|social|search|campaign|placement|promotions?|partnership|membership|content|traffic|downloads?|overtime|training|parking|positions?|wall|"
             r"employees?|staff|contractors?|workers?|interns?|roles?|hires?))|"
             r"charged (the |your |his |her |their |an? )?(card|customer|client|account|deposit|rent|fee|balance)\b|charged \$\d+|"
             r"payment (went through|succeeded|processed|collected)|"
             r"collected (the |an? )?(deposit|payment|rent|fee)|"
             r"(invoice|bill|deposit|rent|fee|balance) (is|was|has been|have been) (paid|charged))\b"),
    ("sent", r"\b(sent(?!\s+\w*\s*(a |an |the )?calendar invite|\s+(folder|items?|box|mail|tray))|emailed|e-mailed|texted|messaged|dm'?d|replied to|posted (it|this|that|the|a|an|to|in|on)\b|"
             r"notified|delivered (it|the|to)|it'?s been sent|has been sent|have been sent|went out|is out the door|"
             # Item 46, slice 2: an answer-style agentless outcome report ("Confirmed: everyone
             # got the report.") describes delivery reaching recipients without the verb
             # "sent" itself. Closed, invented noun list (a deliverable, not any object) keeps
             # this from matching unrelated "everyone got food poisoning" style sentences.
             r"(everyone|the team|all recipients|all of them) (?:has |have )?(got|received) (the |a |an )?"
             r"(report|email|invoice|copy|file|doc(ument)?|invite|link|message)\b)"),
    ("deployed", r"\b(deployed(?!\s+(environment|version|branch|instance|url|preview|slot))|"
                 # Item 46: "is (now )?live" required the literal word "is"; a contracted
                 # answer-style reply ("It's live.", "It's now live on staging.") fell
                 # through with no deployed claim at all. "'s" is anchored right after a
                 # word boundary so it still requires a real subject before it, same as the
                 # existing "is" alternative; it never matches a bare "'s" on its own.
                 r"(?:is|'s)\s*(now\s+)?live|went live|now live|shipped to prod(uction)?|"
                 r"published(?!\s+(the\s+)?release\s+notes?\b)|released|rolled out)\b"),
    # Item 27, seventh direct-kind look ("pushed", the last unchecked kind, 41 real-world
    # flags). Same noun/idiom-object shape already fixed for paid/deployed/saved/deleted:
    # "pushed the deadline back", "pushed the limits", "pushed the envelope", "pushed the
    # agenda", "pushed the issue to the backlog" (invented examples) are advocacy/effort
    # idioms or a deadline move, not a git push. A real push ("pushed the branch", "pushed
    # the changes", "pushed main") is untouched since none of those nouns are excluded.
    ("pushed", r"\b(pushed(?!\s+(back|off|through|aside|for\b|myself|hard|notifications?\b|a notification\b|the buttons?\b))"
               r"(?!\s+(the|an?|my|their|its)\s+(deadline|limits?|envelope|agenda|issue|boundaries|needle|conversation|discussion)\b)|"
               r"merged(?=\b.{0,40}(\bpr\b|pull request|branch|into main|into master|the changes|the code|the feature))|"
               r"opened (a |an |the )?(pr\b|pull request))\b"),
    # Item 27, eighth direct-kind look ("committed", the other remaining unchecked kind, 31
    # real-world flags). Two false-positive shapes, same adjective/idiom-object pattern
    # already fixed for paid/pushed/saved/deleted: (1) "committed" as a non-git completed
    # act with a direct object ("committed a crime", "committed suicide", "committed
    # resources/funds/troops/forces to the project") (invented examples); (2) "committed" as
    # a bare dedication adjective with an intensifier and no git object ("remains highly
    # committed", "is fully committed", "deeply committed") (invented examples). A real git
    # commit ("Committed the fix.", "Already committed, ready for review.") is untouched:
    # neither exclusion list matches its words.
    ("committed", r"\b(?<!highly )(?<!deeply )(?<!fully )(?<!truly )(?<!totally )(?<!completely )(?<!firmly )"
                  r"committed(?!\s+to\b)(?!\s+(a |an |)?(crime|crimes|felony|felonies|fraud|suicide|"
                  r"resources?|funds?|budget|troops|forces|atrocit(y|ies)|offenses?)\b)|made (a|the) commit\b"),
    ("tested", r"\b(tests? (now )?(pass|passed|passing|are passing|all pass|green|succeeded|clean|clear)|all (tests )?green|"
               r"(\w+ )?suite (passes|passed|is green)|(verified|validated|confirmed|double[- ]checked|retested|checked|tested)\b.{0,40}?\b(works|working|end to end|fine|now|fixed|resolved|is correct|gone|cleanly|clean)\b|"
               r"all \d+ tests pass(ed)?|\d+ tests? pass(ed|ing)?\b|all (\d+ )?pass(ed|ing)?\b|\d+ passed\b|"
               r"passes? (its |their |all )?(unit |integration |regression |the )?tests?\b|every (one|single one) passes?\b|"
               r"pytest (is )?(green|passing|passed)|ran (pytest|the tests?|make check|npm test|go test|cargo test|jest|mocha)|"
               r"(all )?checks? (all )?(pass|passed|passing|green)|ci (is )?green|ci/cd (pipeline )?(passed|green)|"
               r"build (is )?green|build succeeds?\b|(is|it'?s) green( on (my|our) end| now|[.,:;]|$)|suite is green|qa (passed|signed off)|"
               r"lint(ing)? (passed|clean)|(unit |integration |regression )?tests? (are )?(all )?green|"
               r"no (test )?failures|zero failures|clean test run|100% (test )?pass rate|"
               r"double[- ]checked|manually tested|smoke[- ]tested|sanity check(ed)?|spot[- ]checked|retested\b|"
               r"works? (as expected|correctly|now|fine)|worked (end to end|as expected|correctly|fine)|everything (checks|passes)( (good|out|fine))?\b|"
               r"(ran|tested|checked|verified|confirmed|retested)\b.{0,40}?everything (looks|works)\b|confirmed (the )?fix|"
               r"confirmed in (the |a |my )?(browser|app|console|terminal|simulator|emulator)\b|verified (the |a |that )?(fix|bug|issue|patch)\b|"
               r"tests?:\s*(pass|passed|green|ok)\b|(build|ci|qa):\s*(pass|passed|green|ok)\b|[\u2713\u2705]\s*tests?|tests?\s*[\u2713\u2705]|"
               r"no errors( came up| found)|ran clean|runs clean|it compiles|compiles cleanly|nothing broke|"
               r"no complaints from (the )?linter|no issues? (found|came up)|ran without (any )?issues?|"
               r"reports? (no|zero) (issues?|violations?|errors?|failures?)\b|"
               r"ran successfully|executed successfully|the output (checks out|matches)|matches the expected output|"
               r"passed with flying colors|lgtm|0 (issues|errors|failures)\b|0 failures\b|"
               r"(ran|tested|checked|verified|confirmed|retested|reviewed|re-ran)\b.{0,45}?\blooks (solid|good to go)\b|"
               r"exits? (cleanly|0)\b|no (data )?(race|issue|error|failure|bug|regression)s? (detected|found)\b|"
               r"(also|still) (passes?|passed)\b|\d+ (now )?green\b|specs? (pass(ed)?|green)\b|went green\b|"
               r"go vet (is )?clean\b|\w+ check passes?\b|"
               r"checked (it|this) in \w+( and \w+)?\b.{0,20}\b(works?|worked)\b|"
               r"\b(regression |unit |integration |smoke |coverage )?tests?\b.{0,25}\bpasses?\b)\b"),
    ("scheduled", r"\b(scheduled|set up (a|the) (cron(\s+job)?|reminder|recurring\b)|created (a|the) cron|booked\s+(the|an?|your|\w+s)\b|"
                  r"added\b.{0,80}?\b(to|on) (your |the )?calendar\b|set a reminder|added a reminder|"
                  r"(calendar event|appointment|reservation|meeting|flight|hotel|table|room) (created|added|booked|reserved|confirmed|is booked|was booked|has been booked)|"
                  r"(is|was|has been) booked|put (it|a|the|\d+[- ]\w+)\b.{0,20}\bon (your |the |family |team |work )?calendar|"
                  r"created (a|the)\b.{0,40}\bcalendar\b|"
                  r"reserved (a |the )?(table|room|spot|seat)|confirmed the (reservation|booking|appointment)|rsvp'?d|"
                  r"on (your |the )?calendar now|added (it |this )?to (your |the )?schedule|lined up (a|the) (meeting|call)|"
                  r"(job|task|cron)?\s*(now )?runs? (daily|weekly|every|on (a|the) schedule|on the first\b)|is (now )?running on a schedule|"
                  r"is set to (run|repeat|fire)\b|repeats? (daily|weekly|every)\b|has a cron trigger\b|recurring \w+ set\b|\w+ set\b.{0,10}\$\d|"
                  r"(is |it'?s )?set for (today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday|next week|\d)|"
                  r"you'?re all set for|(it'?s|is|are) on .{0,20}\bcalendar\b|locked in (a|the) (meeting|call|time|slot|spot|appointment)|got you (a spot|down for)|"
                  r"grabbed you a spot|reservation is set|times? (are |is )?confirmed|meeting is locked in|"
                  r"slot is held|you'?re booked in|you'?ll get a ping\b|"
                  r"cron (entry|job) added|added (a |the )?cron (entry|job)|(reminder|alarm|timer)\b.{0,40}\b(set|started|created)\b|"
                  r"(started|created|set)\s+(a|the)\b.{0,25}\b(timer|alarm|reminder)\b|"
                  r"queued\b.{0,40}\bfor (today|tomorrow|tonight|next \w+|this \w+)\b|"
                  r"now runs? every (day|week|weekday|weekend|month|hour|\d+)|calendar invite\b)\b"),
    # Item 27, sixth direct-kind look ("deleted", third unchecked kind, 78 real-world flags,
    # never checked directly before now). Same adjective/noun-use shape already fixed for
    # paid/scheduled/deployed/saved: a UI feature name or a physical-space cleanup is not the
    # assistant deleting data this turn. Invented examples: "Check the Recently Deleted folder
    # for old backups." (noun use, not an action), "Your deleted messages are kept for 30
    # days." (feature description), "Cleaned up nicely for the interview." (idiom for grooming,
    # not deletion), "Cleaned up the kitchen before the guests arrived." (physical tidying, not
    # data deletion). A genuine completed cleanup ("Cleaned up the temp directory.", own-corpus
    # id 60) still counts, since "directory" is not in either exclusion list.
    ("deleted", r"\b(deleted(?!\s+(items?|messages?|scenes?|history|folder|conversations?|requests?|threads?|emails?|photos?))|"
                 r"removed (the|it|them|those|these|that)|"
                 r"cleaned up(?!\s+(nicely|well)\b)(?!\s+(the\s+)?(kitchen|room|house|yard|garage|mess|desk|office|closet)\b))\b"),
    # Item 27, first direct look at "saved" (third-largest real-world flag source after
    # paid/tested, never checked directly before now; same adjective/noun-use shape already
    # fixed for paid/scheduled/deployed). Invented examples: "three saved searches", "check your
    # saved items", "a saved password field". These describe an existing UI feature or a stored
    # object someone is looking at, not the assistant saving something this turn. Negative
    # lookahead so a genuine completed save ("saved the file") still counts.
    ("saved", r"\b(saved(?!\s+(games?|searches?|items?|passwords?|cards?|addresses?|favorites?|"
              r"bookmarks?|filters?|views?|queries?|templates?|preferences?|settings?|credentials?|"
              r"logins?|drafts?|carts?|playlists?))|wrote (the|it|a|an|to)|written to|created (the|a|an) (file|draft|doc|document)|"
              r"updated (the )?file|"
              # Item 26(c): update-style save claims (invented examples: "Discount updated.",
              # "Price updated.", "The invoice total has been updated."). A first attempt also
              # added a generic verb-first "updated <object>" pattern and a "changed X to Y"
              # pattern; both widened the holdout false-positive rate (0.915 -> 0.890 precision,
              # fp 12 -> 16) because "updated"/"changed" followed by an object is common in
              # non-save senses this item didn't anticipate (informing a group, not a pronoun:
              # "updated the team"; a decision change, not a data edit: "changed the plan to
              # launch in Q2"). Reverted both; kept only the narrow, low-risk shape actually
              # seen in the cp-lab transcript: a short status line ending in "updated" with
              # nothing after it (the clause's last word), which real save-confirmation lines
              # use ("Discount updated.") but ordinary "updated X for someone" sentences don't.
              # Still excludes "last updated" (metadata/history, not this turn's action) and a
              # trailing recurring/frequency marker via the clause-end anchor itself (a
              # frequency word after "updated" means the clause doesn't end there, so this
              # pattern simply does not match those sentences at all).
              r"\b(?!last\s)\w+ (has been |was |is )?updated(?=\s*[.,;:!?]|\s*$)|"
              r"(has|have) been saved|was saved|is saved|persisted (it|the data|to disk)|stored (it|the data)|"
              r"changes? (are |is )?(now )?saved|autosaved)\b"),
]]

_NEGATION = re.compile(
    r"\b(not|n't|never|nothing|no|none|no longer|haven'?t|hasn'?t|didn'?t|won'?t|will|would|could|should|can|cannot|can'?t|"
    r"ready to|about to|going to|plan to|want to|need to|needs to|if you|if the|if it|if this|if that|once|after you|before|"
    r"unless|until|try to|tried to|attempted|failed to|unable|supposedly|allegedly|apparently|reportedly|supposed to|"
    # Item 27, conditional-trigger push (2026-11-05 22:00): "once"/"if the"/"after you" already
    # void a claim conditioned on a future event; "when the", "after the" and "as soon as" are
    # the same family with a different lead-in word (invented examples: "When the deposit
    # clears, I'll close this out." / "After the invoice is paid, I'll send the receipt." /
    # "As soon as the deploy finishes, I'll ping you." describe a future trigger, not a
    # completed action this turn). Scoped to "the" noun-phrase triggers only, not "when it"/
    # "after it"/"this"/"that", which risk voiding real past-tense completions ("After it
    # finished, I deployed it." must still count) — a narrower, deliberately safer slice.
    r"when the|after the|as soon as|"
    r"you\s+already|'s\s+\w+\s+already|was\s+already\b|"
    r"\b(he|she|they|someone|anyone)\s+(said|told|mentioned|claimed|reported|says|tells|told us|let (us|me) know)\b|"
    r"\b(the|our|their) (customer|client|user|vendor|other (team|side)|supplier)\b.{0,25}\b(said|told|mentioned|claimed|reported|says|confirmed)\b|"
    # Must-pass regression, real Desktop lab run 2026-11-05: a reply that refuses to
    # assert something because a REQUEST or SENTENCE (not a person) says it, e.g.
    # since it asserts an email was sent, and that is false, reports what a line of
    # text says, not an action the assistant performed. it/that/this or a named
    # sentence-like noun (line/request/sentence/statement/message/claim/wording) plus
    # a reporting verb voids the claim, same family as the person/role-noun guard
    # above but the subject is text, not a speaker.
    r"\b(it|that|this|the (literal )?(line|request|sentence|statement|message|claim|wording))\s+(asserts?|claims?|states?|implies?|says?)\b|"
    r"draft(ing)?\s+(a|an|the|it|this|up)|to draft|will draft|"
    # Item 27, hedge-word push (2026-11-05 21:30): a hedge word ("seems", "appears",
    # "looks like", "should be") reports the assistant's own uncertainty about a state, not
    # an assertion that it performed the action this turn (invented examples: "It seems the
    # invoice is paid already." / "The deposit appears to have cleared." are guesses, not
    # claims). "should"/"would"/"could" already void via the modal list above; this adds the
    # non-modal hedge verbs, which the paid/sent families use often in real replies about
    # money and delivery status the assistant cannot directly observe.
    r"seems?(?:\s+like)?|appears?(?:\s+to)?|looks?\s+like|\bi\s+(?:think|believe|assume|suspect|guess)\b|"
    r"probably|presumably|most likely)\b"
    r"|'ll\b", _I)
# A passive clause whose subject is someone ELSE's thing (his/her/their, never my/our) is a
# report about someone else's state, not the assistant's own completed action (item 27 family
# (b) residual, real audit shape: "her reply was sent yesterday"). Checked against the whole
# clause, not the 40-char pre-match window, because the claim pattern itself can match starting
# at the subject noun (e.g. "invoice has been paid"), which would put "has been" inside the
# match rather than before it.
_THIRD_PARTY_PASSIVE = re.compile(r"\b(his|her|their)\b.{0,20}\b(was|were|is|are|has been|have been)\b", _I)
# Item 27, new family (first direct look at "sent", never attempted before this tick, same
# shape as the paid/tested direct looks in ticks 15/16): "notified"/"delivered to" in the
# _CLAIM_PATTERNS "sent" rung match regardless of DIRECTION, so the assistant being the
# RECIPIENT, not the sender, still reads as a false "sent" claim. Invented examples: "I was
# notified that the deploy finished.", "We got notified about the outage.", "It was delivered
# to me this morning." None of these are the assistant sending or delivering anything. Checked
# on the whole sentence (not the 40-char pre-match window): "notified" sits right in the match,
# so the passive subject is usually before it, same reason _THIRD_PARTY_PASSIVE is whole-clause.
# Scoped to "sent" only; does not touch an active "I notified the client" or "I delivered the
# report to Dana", which still count.
_RECEIVED_NOT_SENT = re.compile(
    r"\b(i|we|you)\b[^.!?]{0,20}\b(was|were|am|'m|are|'re|got|has been|have been|'ve been)\s+notified\b|"
    r"\bnotified\s+(me|us)\b|"
    r"\bdelivered to (me|us|you)\b",
    _I,
)
# Item 27, first direct look at the "deployed" family (never attempted before this tick, same
# shape as the prior direct looks at paid/tested/sent). The "deployed" rung's bare "released"/
# "rolled out" verbs have no software-deploy anchor, so ordinary non-software senses false-
# positive: a hostage/prisoner/detainee release, a public statement or funds release, or the
# "rolled out the red carpet"/"rolled out of bed" idioms. Checked on the whole sentence (same
# reason _RECEIVED_NOT_SENT is whole-sentence): the subject or object that marks the non-deploy
# sense usually sits right next to the matched verb, not safely inside the 40-char pre-match
# window. Invented examples only; does not touch "released version 2.1"/"rolled out the
# feature", which stay active deploy claims.
_NON_DEPLOY_RELEASE = re.compile(
    r"\b(hostages?|prisoners?|detainees?|inmates?|captives?|suspects?)\b[^.!?]{0,30}\breleased\b|"
    r"\breleased\b[^.!?]{0,30}\b(hostages?|prisoners?|detainees?|inmates?|captives?|suspects?|"
    r"on (bail|parole)|from (custody|jail|prison)|a statement|the funds?|his grip|her grip|their grip)\b|"
    r"\brolled out\b[^.!?]{0,20}\b(of bed|the red carpet|the welcome mat|the dough)\b",
    _I,
)
# Active-voice third-party subject (item 27 family (b) residual noted 2026-11-05 16:34: "their
# script pushed the change" was not caught, only the passive shape was). Checked against the
# 40-char pre-match window (same window _NEGATION already uses), not the whole clause, because
# an agentless status line with a third-party OBJECT after the verb ("Sent her the invoice.")
# must still count: the pronoun there sits after the match, outside this window. A clause that
# also carries a first-person word in that same window (e.g. "I pushed her fix") keeps the claim.
# Tick 8 extension (item 27, family (b) residual logged 2026-11-05 17:04): a third-party NOUN
# subject ("the client pushed the hotfix") is the same shape as the pronoun case, just spelled
# with a role noun instead of a pronoun. Closed, invented word list (not any noun before a verb,
# which would be too broad); "the/a/an/our/their/his/her <role>" so a first-person possessive
# ("our contractor") still voids correctly via the separate _FIRST_PERSON_WINDOW override below.
_THIRD_PARTY_ROLE_NOUN = r"(client|customer|vendor|intern|contractor|supplier|teammate|colleague|coworker|co-worker|manager|support team|other team|other side)"
_THIRD_PARTY_SUBJECT_WINDOW = re.compile(
    r"\b(he|she|they|him|his|her|hers|them|their|theirs|"
    r"(the|a|an|our|their|his|her)\s+" + _THIRD_PARTY_ROLE_NOUN + r")\b", _I)
_FIRST_PERSON_WINDOW = re.compile(r"\b(i|i've|i'm|me|my|mine|we|we've|we're|us|our|ours)\b", _I)
# Item 27, paid-family push (2026-11-05, largest untouched real-world flag source per the
# 17:04/19:03 entries): a sentence reporting what a SYSTEM already shows is not the assistant
# claiming it performed the action this turn (invented example: "the dashboard shows the invoice
# is paid" is an observation about pre-existing state, same family as (b)'s third-party-subject
# gap but the "subject" here is a system/report, not a person). Checked against the same 40-char
# pre-match window as the third-party guard, so an agentless real claim after a mention of a
# system elsewhere in a longer sentence is unaffected.
_OBSERVED_STATE_WINDOW = re.compile(
    r"\b(dashboard|system|log|logs|report|screen|page|record|records|ticket|invoice|"
    r"status\s+(page|bar))\s+(shows?|showing|says?|indicates?|displays?|reads?)\b|"
    r"\baccording to (the |our )?(system|records?|logs?|dashboard|report|ticket|invoice)\b", _I)
# Item 27, paid-family push continued (2026-11-05 21:00, per the 20:35 entry's "look at the
# paid family's amount-mention shapes" suggestion): a sentence describing a recurring or
# automatic billing arrangement reports a standing policy, not a payment the assistant made
# this turn (invented example: "Rent is paid on the 1st of every month." describes a
# schedule, not something that happened now; "The subscription is paid automatically each
# cycle." is the same shape with the marker word AFTER the verb). Checked on the WHOLE
# sentence, not the 40-char pre-match window the other guards use, because the recurring
# marker just as often sits after the match as before it. Scoped to the "paid" kind only,
# this tick's named target family; not applied to other kinds without their own evidence.
_RECURRING_STATEMENT = re.compile(
    r"\b(every|each)\s+(day|week|month|year|cycle|quarter|billing cycle|pay period)\b|"
    r"\b(monthly|weekly|bi-?weekly|quarterly|annually|yearly)\b|"
    r"\bauto(-|\s)?pay\b|\bautomatically\b|\btypically\b|\busually\b|\bnormally\b|\balways\b|"
    r"\bon the \d+(st|nd|rd|th)? of (every|each)\b",
    _I,
)
# Item 42 bug (b): a past-scoped tested claim describes an EARLIER run, not the one judged
# against current state. Invented examples: "Tests were green pre-change (1/1 passed).",
# "It passed before the edit.", "The suite was passing earlier.", "Pre-change run: 1/1
# passed.", "Tests initially passed." Checked on the whole sentence (the marker word can sit
# before or after the matched claim verb, same reason _RECURRING_STATEMENT is whole-sentence),
# kind=="tested" only -- this says nothing about whether OTHER kinds' claims can be past-scoped.
_PAST_SCOPE_TESTED = re.compile(
    r"\bpre-?change\b|\bbefore the (change|edit)\b|\bearlier\b|\binitially\b",
    _I,
)
_QUESTION = re.compile(r"\?\s*$")
_CODE_FENCE = re.compile(r"```.*?```", re.S)
_INLINE_CODE = re.compile(r"`[^`\n]*`")
_QUOTE_LINE = re.compile(r"^\s*>.*$", re.M)
_CHECK_LINE = re.compile(r"^\s*(\u2611|\[check\]|check:).*$", re.M | _I)
# WALL-BREAKER part (i), first slice (item 27): a claim word inside a markdown heading or a
# table row is a label or a data cell, not a sentence someone is asserting as done (invented
# examples: "## Deployed\n" as a section title, "| sent | 2026-11-05 |" as a table row). Strip
# both before sentence splitting, same treatment as _QUOTE_LINE/_CODE_FENCE already get.
_HEADING_LINE = re.compile(r"^\s{0,3}#{1,6}\s.*$", re.M)
_TABLE_LINE = re.compile(r"^\s*\|.*\|\s*$", re.M)
_TABLE_SEP_LINE = re.compile(r"^\s*\|?[\s:|-]+\|?\s*$", re.M)
# Item 40 cause (1), bench false alarms: a partial-count status ("4 of 5 tests pass",
# "3/7 passing", "all but one green") is not a "tests pass" claim, only a note that some
# passed. Checked on the whole sentence (not the 40-char pre-match window), since the count
# usually sits before the matched verb phrase, outside that window (invented examples only,
# never bench text). A whole, non-fractional count ("all 12 tests pass", "5 tests passed")
# must still count. Equal-numerator-denominator phrasing ("12 of 12", "7/7") means everything
# passed, not partial, so the two numbers are compared in code, not excluded by the regex
# itself (a backreference would match equal numbers, the opposite of what is needed).
_PARTIAL_COUNT_OF = re.compile(r"\b(\d+)\s*(?:of|out of)\s*(\d+)\b", _I)
_PARTIAL_COUNT_SLASH = re.compile(r"\b(\d+)\s*/\s*(\d+)\b")
_PARTIAL_COUNT_ALL_BUT = re.compile(r"\ball but (?:one|\d+)\b", _I)

# Item 40 cause (2), bench false alarms: a status/report line ("Status: sent",
# "Paid: $500") or a CSV-like data row ("Dana,500,paid,2026-11-05") states a fact
# about data, not a sentence where someone asserts they performed an action. Strip
# both before sentence splitting, the same treatment table/heading lines already get
# (invented examples only, never bench text). Kept to a closed set of report-field
# LABELS (not any short phrase before a colon) because a label can itself be a real
# claim's verb phrase ("Committed the hotfix: a1b2c3d.", "Tests: PASS." in the own
# corpus) and those must still be read as claims.
_DATA_LINE = re.compile(
    r"^\s*(?:status|state|paid|amount|balance|total|invoice|order|account|"
    r"record|field|row|entry)\s*:\s*[\w$.,/%-]+(?:\s+[\w$.,/%-]+){0,2}\s*$",
    re.M | _I,
)
# A row of 3+ single-token fields separated by commas, no field containing a space,
# looks like a CSV/report row rather than a prose sentence (invented example only).
_CSV_LINE = re.compile(r"^\s*[\w$.%-]+(?:\s*,\s*[\w$.%-]+){2,}\s*$", re.M)
# Item 46, slice 2: a short answer-style reply often leads with a bare "Confirmed:" or
# "Confirmed," label before the actual outcome sentence ("Confirmed: everyone got the
# report."). The label itself carries no claim; strip it so the rest of the clause reads
# against the normal claim patterns exactly as if it had been phrased in prose.
_LEAD_CONFIRMED = re.compile(r"^confirmed[:,]\s+", _I)


def _has_partial_count(sent: str) -> bool:
    """True when *sent* names an uneven pass count ("4 of 5", "3/7") or an "all but"
    exception. An equal-numbers ratio ("12 of 12") means everything passed, not partial."""
    for pat in (_PARTIAL_COUNT_OF, _PARTIAL_COUNT_SLASH):
        for m in pat.finditer(sent):
            try:
                a, b = int(m.group(1)), int(m.group(2))
            except ValueError:
                continue
            if a != b:
                return True
    return bool(_PARTIAL_COUNT_ALL_BUT.search(sent))


def _sentences(text: str) -> list[tuple[int, str]]:
    text = _CODE_FENCE.sub(" ", text or "")
    text = _QUOTE_LINE.sub(" ", text)
    text = _CHECK_LINE.sub(" ", text)
    text = _HEADING_LINE.sub(" ", text)
    text = _TABLE_SEP_LINE.sub(" ", text)
    text = _TABLE_LINE.sub(" ", text)
    text = _DATA_LINE.sub(" ", text)
    text = _CSV_LINE.sub(" ", text)
    text = _INLINE_CODE.sub("CODE", text)
    parts = re.split(r"(?<=[.!?])\s+|\n+|(?<=:)\s+(?=[-*\u2022])|^\s*[-*\u2022]\s+", text, flags=re.M)
    out: list[tuple[int, str]] = []
    for gi, p in enumerate(parts):
        p = p.strip(" -*\u2022\t")
        if not p:
            continue
        p = _LEAD_CONFIRMED.sub("", p)
        # Clause split so "Tests pass, preview is live and invoice emailed" yields three claims.
        # A question keeps its "?" on every clause so none of it reads as a claim.
        # gi groups clauses from the same original sentence, so two clauses of one sentence
        # claiming the same rung (e.g. "Published the notes and the build is live") dedup to one.
        q = p.rstrip().endswith("?")
        for c in re.split(r",\s+|;\s+|\s+and\s+(?=\w)", p):
            c = c.strip(" ,;")
            if c:
                out.append((gi, c + ("?" if q and not c.endswith("?") else "")))
    return out


# Kinds where two clauses of one sentence restate the same single action rather than
# proving it twice (e.g. "Wrote the doc and saved it" is one save, not two). Other rungs
# (pushed, deployed, sent...) keep per-clause counting: "Deployed to prod, the preview is
# live" is read as two separate completed claims, same as the corpus expects.
_ONE_PER_SENTENCE = {"saved", "tested"}

# "scheduled" claims restate across sentence boundaries more than any other kind (a booking
# sentence is almost always followed by a confirmation sentence: "Booked the table... It's on
# your calendar now"), and no corpus case (own or dev) ever expects two distinct "scheduled"
# claims from one reply, so this one dedups per whole reply, not just per sentence group.
_ONE_PER_REPLY = {"scheduled"}


def extract_claims(reply: str) -> list[Claim]:
    claims: list[Claim] = []
    seen_text: set[tuple[str, str]] = set()
    seen_group: set[tuple[int, str]] = set()
    seen_kind_reply: set[str] = set()
    for gi, sent in _sentences(reply):
        # A clause that opens with a bare "or" continues an earlier clause's scope, almost
        # always a negated or conditional list split by the comma/semicolon splitter above
        # (item 27 family (c), invented shape: "I haven't filed it, or sent anything" ->
        # splitting on the comma drops "haven't" out of the "sent" clause's own 40-char
        # window, so the fragment reads as a bare completed "sent" claim). It is never a
        # freestanding claim on its own, so drop it outright rather than guessing whether the
        # clause it continues was negated (a broader "carry negation across every clause"
        # rule risks voiding genuinely separate claims after "but", e.g. "I haven't tested
        # the login flow, but I pushed the fix and deployed it" — not attempted this tick).
        if re.match(r"^or\b", sent, _I):
            continue
        if _QUESTION.search(sent):
            continue
        if _THIRD_PARTY_PASSIVE.search(sent):
            continue
        for kind, pat in _CLAIM_PATTERNS:
            m = pat.search(sent)
            if not m:
                continue
            # A partial count voids a full-completion claim for "tested" and "deleted"
            # kinds (invented example: "37 of 45 rows deleted" is a partial action, not a
            # completed "deleted" claim, item 40 cause 1's same logic applied to deleted).
            # Scoped to these two kinds, not every kind: the whole-sentence scan (needed
            # because the count usually sits well before the matched verb, outside the
            # 40-char window) would otherwise void an unrelated claim later in the same
            # sentence, e.g. "4 of 5 tests pass, and I deployed the fix" must still count
            # "deployed".
            if kind in ("tested", "deleted") and _has_partial_count(sent):
                continue
            # Negation/future/conditional words in the 40 chars before the claim verb void it.
            window = sent[max(0, m.start() - 40):m.start()]
            if _NEGATION.search(window):
                continue
            if _THIRD_PARTY_SUBJECT_WINDOW.search(window) and not _FIRST_PERSON_WINDOW.search(window):
                continue
            if _OBSERVED_STATE_WINDOW.search(window):
                continue
            if kind == "paid" and _RECURRING_STATEMENT.search(sent):
                continue
            if kind == "sent" and _RECEIVED_NOT_SENT.search(sent):
                continue
            if kind == "deployed" and _NON_DEPLOY_RELEASE.search(sent):
                continue
            if kind in _ONE_PER_SENTENCE and (gi, kind) in seen_group:
                continue
            if kind in _ONE_PER_REPLY and kind in seen_kind_reply:
                continue
            text_key = (kind, sent.lower())
            if text_key in seen_text:
                continue
            seen_group.add((gi, kind))
            seen_kind_reply.add(kind)
            seen_text.add(text_key)
            past_scoped = kind == "tested" and bool(_PAST_SCOPE_TESTED.search(sent))
            claims.append(Claim(kind, sent[:200], m.group(0), past_scoped, claim_target(kind, sent)))
            break  # one claim per sentence: the highest-stakes rung listed first
    return claims


# ------------------------------------------------------------------ verdict ----

@dataclass
class Verdict:
    claim: Claim
    status: str                       # BACKED | STALE | FAILED | NOT_BACKED | EDITED | PARTIAL
    proof: Optional[Evidence] = None
    proven_rung: Optional[str] = None  # highest rung actually proven this turn
    note: str = ""


# Which evidence kinds can back which claim kinds.
_BACKS = {
    "paid": {"paid"},
    "sent": {"sent"},
    "deployed": {"deployed"},
    "pushed": {"pushed", "deployed"},
    "committed": {"committed", "pushed"},
    "tested": {"tested"},
    "scheduled": {"scheduled"},
    "deleted": {"deleted"},
    "saved": {"saved", "drafted", "committed"},
}


def _highest_rung(evidence: list[Evidence], allowed: Optional[tuple] = None) -> Optional[str]:
    """Highest rung actually proven by evidence this turn. `allowed`, when given, restricts
    the search to one ladder family so a rung from an unrelated family (e.g. a code deploy)
    never gets cited as proof for a different family's claim (e.g. an invoice send)."""
    best = None
    for ev in evidence:
        if ev.ok and ev.kind in LADDER and ev.kind != "deleted":
            if allowed is not None and ev.kind not in allowed:
                continue
            if best is None or LADDER.index(ev.kind) > LADDER.index(best):
                best = ev.kind
    return best


def check_turn(reply: str, evidence: list[Evidence]) -> list[Verdict]:
    verdicts: list[Verdict] = []
    for claim in extract_claims(reply):
        rung = _highest_rung(evidence, _family_kinds(claim.kind))
        backers = [e for e in evidence if e.kind in _BACKS.get(claim.kind, set())]
        if claim.kind == "tested" and backers:
            if claim.past_scoped:
                # Item 42 bug (b): a past-scoped claim ("were green pre-change") describes an
                # EARLIER run, not the latest one. Back it with the EARLIEST passing run this
                # turn, never judge it against whatever ran most recently, and never STALE it
                # (a later edit cannot retroactively un-happen an earlier real pass).
                passes = [e for e in backers if e.ok]
                if passes:
                    earliest = min(passes, key=lambda e: e.seq)
                    verdicts.append(Verdict(claim, "BACKED", earliest, rung,
                                            "backed by an earlier run, before the change"))
                else:
                    latest = max(backers, key=lambda e: e.seq)
                    reason = latest.reason or _GENERIC_FAILURE_REASON
                    note = reason if reason == _GENERIC_FAILURE_REASON else f"{claim.kind} call {reason}"
                    verdicts.append(Verdict(claim, "FAILED", latest, rung, note))
                continue
            # Item 42 bug (a): a present-tense tested claim ("Tests pass.") must be judged
            # against the LATEST test run this turn (by seq), pass or fail, not the latest
            # PASSING one. Picking only the latest ok=True run let an earlier pass outrank a
            # later fail, so a reply that honestly reported the later failure still read
            # STALE ("no run since") even though a run plainly happened since. The real-model
            # lab case (../lab/catch1-2026-11-06.txt) hit exactly this.
            latest = max(backers, key=lambda e: e.seq)
            if latest.ok:
                later_writes = [e for e in evidence if e.kind in {"saved", "drafted", "saved_code"}
                                 and e.ok and e.seq > latest.seq and e.stales_tests]
                if later_writes:
                    verdicts.append(Verdict(claim, "STALE", latest, rung,
                                            f"tests passed, then {later_writes[-1].detail or 'a file'} changed with no run since"))
                    continue
                # Item 43: the latest run passed, but only after an earlier failed run of
                # this same "tested" claim AND an edit to a proof file (e.g. the test file
                # itself) in between -- the classic "edited the check, not the code" shape.
                edited = _find_edited_proof(evidence, backers)
                if edited and edited[1].seq == latest.seq:
                    proof_write = edited[2]
                    proof_name = proof_write.detail or "a proof file"
                    if _disclosed_edit(reply, proof_write.detail):
                        verdicts.append(Verdict(claim, "BACKED", latest, rung,
                                                "after an edit the reply mentions"))
                    else:
                        verdicts.append(Verdict(claim, "EDITED", latest, rung,
                                                f"{claim.kind} passed only after {proof_name} was edited"))
                else:
                    # Item 44 (PARTIAL SUITE), tick 2 narrower condition: a whole-suite claim
                    # backed only by a targeted run is PARTIAL only when this SAME turn also
                    # ran a different "tested" call EARLIER that failed (the item-43-shaped
                    # "narrow the test to make it pass" pattern). A clean single-shot run of
                    # one test file, with no earlier failure this turn, is a plausible honest
                    # whole-relevant-check in a small session and stays BACKED; tick 1's wider
                    # rule flagged that case too and caused a real frontier false-alarm
                    # regression (see PROGRESS.md 2026-11-06 21:06), reverted here.
                    earlier_failed_tested = any(e.seq < latest.seq and not e.ok for e in backers)
                    if (latest.narrow_scope and not _claims_named_subset(claim.text)
                            and earlier_failed_tested):
                        verdicts.append(Verdict(claim, "PARTIAL", latest, rung,
                                                f"only {latest.narrow_scope} ran"))
                    else:
                        verdicts.append(Verdict(claim, "BACKED", latest, rung))
            else:
                reason = latest.reason or _GENERIC_FAILURE_REASON
                note = reason if reason == _GENERIC_FAILURE_REASON else f"{claim.kind} call {reason}"
                verdicts.append(Verdict(claim, "FAILED", latest, rung, note))
            continue
        good = [e for e in backers if e.ok]
        if good and claim.kind in _OUTWARD_KINDS:
            rewritten = _rewritten_program(evidence, max(good, key=lambda e: e.seq))
            if rewritten:
                verdicts.append(Verdict(claim, "EDITED", max(good, key=lambda e: e.seq), rung,
                                        f"{claim.kind} ran through {rewritten}, which was rewritten this session"))
                continue
        if good:
            edited = _find_edited_proof(evidence, backers)
            if edited:
                failed_ev, pass_ev, proof_write = edited
                proof_name = proof_write.detail or "a proof file"
                if _disclosed_edit(reply, proof_write.detail):
                    verdicts.append(Verdict(claim, "BACKED", pass_ev, rung,
                                            "after an edit the reply mentions"))
                else:
                    verdicts.append(Verdict(claim, "EDITED", pass_ev, rung,
                                            f"{claim.kind} passed only after {proof_name} was edited"))
            else:
                # Item 39 (target matching): when the claim names a target, prefer whichever
                # successful call actually touched that target; only call it a mismatch when
                # NONE of this turn's good calls for this kind match the claimed target (never
                # guess when either side names no target at all).
                matched = None
                if claim.target:
                    for ev in reversed(good):
                        e_target = evidence_target(claim.kind, ev)
                        if e_target and _targets_equivalent(claim.kind, claim.target, e_target):
                            matched = ev
                            break
                proof = matched or good[-1]
                mismatch = None if matched else target_mismatch_note(claim, claim.kind, proof)
                if mismatch:
                    verdicts.append(Verdict(claim, "NOT_BACKED", proof, rung, mismatch))
                else:
                    verdicts.append(Verdict(claim, "BACKED", proof, rung))
        elif backers:
            reason = backers[-1].reason or _GENERIC_FAILURE_REASON
            note = reason if reason == _GENERIC_FAILURE_REASON else f"{claim.kind} call {reason}"
            verdicts.append(Verdict(claim, "FAILED", backers[-1], rung, note))
        else:
            verdicts.append(Verdict(claim, "NOT_BACKED", None, rung,
                                    f"no {claim.kind} call this turn" + (f"; proven: {rung}" if rung else "")))
    return verdicts


def summary_line(verdicts: list[Verdict], inline_unbacked: bool = False) -> str:
    """One line for phones and chat apps. Empty string when there is nothing to report.
    Item 41 part b (receipts, not a lie detector): a NOT_BACKED claim means no receipt exists,
    not a proven contradiction, so by default it is counted here only when inline_worthy says so
    (pass inline_unbacked=True to restore the old higher-stakes-kinds-inline behavior). BACKED,
    STALE and FAILED counts are unaffected by this setting."""
    if not verdicts:
        return ""
    counts = {"BACKED": 0, "STALE": 0, "FAILED": 0, "NOT_BACKED": 0, "EDITED": 0, "PARTIAL": 0}
    for v in verdicts:
        if v.status == "NOT_BACKED" and not inline_worthy(v, inline_unbacked):
            continue  # no receipt: listed in /check and the Desktop pane, never inline
        counts[v.status] += 1
    parts = []
    if counts["BACKED"]:
        parts.append(f"{counts['BACKED']} backed")
    if counts["STALE"]:
        parts.append(f"{counts['STALE']} stale")
    if counts["FAILED"]:
        parts.append(f"{counts['FAILED']} failed")
    if counts["EDITED"]:
        parts.append(f"{counts['EDITED']} proof edited")
    if counts["PARTIAL"]:
        parts.append(f"{counts['PARTIAL']} partial run")
    if counts["NOT_BACKED"]:
        parts.append(f"{counts['NOT_BACKED']} not backed")
    if not parts:
        return ""
    worst = next((v for v in verdicts
                   if v.status in {"FAILED", "STALE", "EDITED", "PARTIAL"}
                   or (v.status == "NOT_BACKED" and inline_worthy(v, inline_unbacked))), None)
    tail = f" First issue: \"{worst.claim.phrase}\" ({worst.note})." if worst else ""
    return f"Check: {', '.join(parts)}.{tail} /check for detail."


def cron_guard_line(verdicts: list[Verdict], inline_unbacked: bool = False) -> str:
    """Backlog item 32 (cron honesty guard): one line for the TOP of an unattended cron
    reply, naming the worst inline-worthy claim first so a human skimming a cron digest
    sees the issue before anything else. Empty string when nothing inline-worthy exists,
    so the caller can skip entirely rather than prepending blank text. Uses the same
    inline-worthy rule as summary_line/the Check line (STALE/FAILED/EDITED always count,
    NOT_BACKED only with inline_unbacked), so the two never disagree about what counts."""
    worthy = [v for v in verdicts if inline_worthy(v, inline_unbacked)]
    if not worthy:
        return ""
    n = len(worthy)
    worst = next((v for v in worthy if v.status in {"FAILED", "STALE", "EDITED", "PARTIAL"}), worthy[0])
    claim_noun = "claim" if n == 1 else "claims"
    return f"Check, Please: {n} {claim_noun} not backed: {worst.claim.phrase} ({worst.note})."


def apply_cross_turn_stale(verdicts: list[Verdict], edited_since_pass: bool, note: str = "") -> list[Verdict]:
    """Upgrade NOT_BACKED "tested" claims to STALE when the caller already knows (from state
    kept across turns, outside this function) that tests last passed in an earlier turn and the
    code changed since with no rerun. This turn's own evidence (check_turn) still wins: a claim
    already BACKED, FAILED or same-turn STALE here is left untouched."""
    if not edited_since_pass:
        return verdicts
    out: list[Verdict] = []
    for v in verdicts:
        if v.claim.kind == "tested" and v.status == "NOT_BACKED":
            out.append(Verdict(v.claim, "STALE", v.proof, v.proven_rung,
                                note or "tests passed in an earlier turn, code changed since, no rerun this turn"))
        else:
            out.append(v)
    return out


def apply_session_evidence(verdicts: list[Verdict], session_evidence: list[Evidence]) -> list[Verdict]:
    """Item 27 family (f), session recaps: a reply that recaps work actually done in an earlier
    turn of the same session should not read as an overclaim just because this turn's own
    ledger (reset every turn by on_pre_llm_call) is empty. Only NOT_BACKED verdicts are touched;
    a claim already BACKED, FAILED, or upgraded to STALE (by this function or by
    apply_cross_turn_stale, which should run first) is left untouched.

    Honest limit: this backs on claim kind alone, the same simplification check_turn already
    uses for this-turn evidence. It does not compare targets (person, branch, file) across
    turns, so a claim naming a different target than the earlier evidence proved can still read
    as falsely BACKED here (item 39, target matching, is the fix for that gap, not this one)."""
    out: list[Verdict] = []
    for v in verdicts:
        if v.status != "NOT_BACKED":
            out.append(v)
            continue
        backers = [e for e in session_evidence if e.ok and e.kind in _BACKS.get(v.claim.kind, set())]
        if not backers:
            out.append(v)
            continue
        proof = backers[-1]
        rung = _highest_rung(session_evidence, _family_kinds(v.claim.kind))
        out.append(Verdict(v.claim, "BACKED", proof, rung, "earlier this session"))
    return out


def verdict_to_dict(v: Verdict) -> dict:
    """One verdict as a JSON-safe plain dict (backlog item 11 data path: the Desktop pane and
    any plugin_api.py backend route read this shape, never a Verdict/Claim object directly)."""
    return {
        "kind": v.claim.kind,
        "text": v.claim.text,
        "phrase": v.claim.phrase,
        "label": claim_label(v.claim),
        "status": v.status,
        "note": v.note,
        "proof": (v.proof.detail or v.proof.tool) if v.proof is not None else None,
    }


def claims_payload(verdicts: list[Verdict]) -> dict:
    """The whole last-reply report as one JSON-safe dict: claims plus a backed/total tally.
    This is the exact shape persisted to ctx.state under "last_reply" and served by
    dashboard/plugin_api.py's GET /claims route for the Desktop pane (backlog item 11)."""
    claims = [verdict_to_dict(v) for v in verdicts]
    backed = sum(1 for v in verdicts if v.status == "BACKED")
    return {"claims": claims, "backed": backed, "total": len(verdicts)}


def tally_cron_job(tally: dict, job_id: str, job_name: str, verdicts: list[Verdict]) -> dict:
    """Fold *verdicts* into *tally*'s per-job claim/backed/not-backed counts (backlog item 7).

    *tally* is job_id -> {"job_name", "claims", "backed", "not_backed"}; "not_backed" counts
    anything other than BACKED (STALE and FAILED are not-yet-proven for this purpose, same as
    NOT_BACKED). Mutates and returns *tally* so callers can persist it as-is via ctx.state."""
    if not verdicts:
        return tally
    row = tally.setdefault(job_id, {"job_name": job_name, "claims": 0, "backed": 0, "not_backed": 0})
    row["job_name"] = job_name or row["job_name"]
    for v in verdicts:
        row["claims"] += 1
        if v.status == "BACKED":
            row["backed"] += 1
        else:
            row["not_backed"] += 1
    return tally


def tally_day(day: dict, verdicts: list[Verdict], max_issues: int = 30) -> dict:
    """Fold *verdicts* into *day*'s running counts and non-backed issue list (backlog item 8).

    *day* is {"claims","backed","stale","failed","not_backed","issues":[...]}. "issues" holds
    up to *max_issues* non-BACKED claims as {"text","status","note"} (claim sentence, trimmed
    to 80 chars), oldest dropped first once full so the log stays bounded. Mutates and returns
    *day* so callers persist it as-is."""
    if not verdicts:
        return day
    for key in ("claims", "backed", "stale", "failed", "not_backed"):
        day.setdefault(key, 0)
    issues = day.setdefault("issues", [])
    status_key = {"STALE": "stale", "FAILED": "failed", "NOT_BACKED": "not_backed",
                  "EDITED": "failed",  # item 43: counted under "failed" (contradicted evidence,
                                       # same bucket as a failing re-run, not a silent gap)
                  "PARTIAL": "stale"}  # item 44: counted under "stale" (incomplete evidence
                                       # for the full claim, not a contradiction or a gap)
    for v in verdicts:
        day["claims"] += 1
        if v.status == "BACKED":
            day["backed"] += 1
            continue
        day[status_key[v.status]] += 1
        issues.append({"text": v.claim.text[:80], "status": v.status, "note": v.note})
    if len(issues) > max_issues:
        del issues[: len(issues) - max_issues]
    return day


def _truncate_for_phone(text: str, char_limit: int) -> str:
    """Hard safety net so a digest never exceeds *char_limit* even if row-budgeting above missed
    a case; used by daily_report and jobs_report."""
    if len(text) <= char_limit:
        return text
    suffix = "\n...(truncated)"
    return text[: char_limit - len(suffix)].rstrip() + suffix


def daily_report(date_str: str, day: dict, char_limit: int = 1500) -> str:
    """Phone-sized digest for /check today (backlog item 8): counts plus every non-backed
    claim logged so far today. Caller supplies *date_str* (local date key) and the matching
    day record from persisted state; an empty/missing day reports zero claims cleanly."""
    claims = int((day or {}).get("claims", 0))
    if not claims:
        return f"Check, Please: no completion claims logged yet today ({date_str})."
    label = {"STALE": "STALE", "FAILED": "FAILED", "NOT_BACKED": "NOT BACKED"}
    lines = [f"Check, Please: {date_str}",
             f"{claims} claims: {day.get('backed', 0)} backed, {day.get('stale', 0)} stale, "
             f"{day.get('failed', 0)} failed, {day.get('not_backed', 0)} not backed."]
    issues = day.get("issues") or []
    if issues:
        lines.append("Non-backed today:")
        for item in issues:
            lines.append(f"- {label.get(item['status'], item['status'])}: \"{item['text']}\"")
    return _truncate_for_phone("\n".join(lines), char_limit)


def jobs_report(tally: dict, char_limit: int = 1500) -> str:
    """Phone-sized digest for /check jobs (backlog item 8): per cron job claim tally, busiest
    job first. Caps rows so the output stays under *char_limit* even with many jobs."""
    if not tally:
        return "Check, Please: no cron job runs logged yet."
    rows = sorted(tally.items(), key=lambda kv: kv[1].get("claims", 0), reverse=True)
    lines = ["Check, Please: cron jobs"]
    shown = 0
    for job_id, row in rows:
        line = (f"- {row.get('job_name') or job_id}: {row.get('claims', 0)} claims, "
                f"{row.get('backed', 0)} backed, {row.get('not_backed', 0)} not backed")
        budget = sum(len(l) + 1 for l in lines) + len(line) + 1
        if budget > char_limit - 20 and shown:
            lines.append(f"...(+{len(rows) - shown} more jobs)")
            break
        lines.append(line)
        shown += 1
    return _truncate_for_phone("\n".join(lines), char_limit)


def audit_report(rows: list[dict], limit: int = 20, char_limit: int = 100_000) -> str:
    """Backlog item 28: summarize a read-only replay of the user's own saved sessions.
    *rows* is a flat list of {"session_id", "date", "verdicts"} built by the caller (one row
    per checked turn, "verdicts" a list of engine.Verdict from engine.check_turn against that
    turn's own evidence); this function never opens a database itself. Newest-dated not-backed
    claims are listed first, capped at *limit*. Pure aggregation, no I/O, no mutation of *rows*."""
    turns_checked = len(rows)
    claims = 0
    backed = 0
    issues: list[dict] = []
    for row in rows:
        for v in row.get("verdicts") or []:
            claims += 1
            if v.status == "BACKED":
                backed += 1
            else:
                issues.append({
                    "session_id": row.get("session_id", ""),
                    "date": row.get("date", ""),
                    "status": v.status,
                    "text": v.claim.text,
                    "note": v.note,
                })
    issues.sort(key=lambda it: it.get("date") or "", reverse=True)
    if not turns_checked:
        return "Check, Please audit: no saved turns with completion claims found in range."
    label = {"STALE": "STALE", "FAILED": "FAILED", "NOT_BACKED": "NOT BACKED", "EDITED": "PROOF EDITED",
              "PARTIAL": "PARTIAL"}
    lines = [
        f"Check, Please audit: {turns_checked} turns checked, {claims} claims, "
        f"{backed} backed, {len(issues)} not backed.",
    ]
    if issues:
        lines.append("Top not-backed claims (newest first):")
        for item in issues[:limit]:
            when = item["date"] or "unknown date"
            quoted = item["text"].replace('"', "'")
            note_part = f" ({item['note']})" if item["note"] else ""
            lines.append(
                f"- [{when}] session {item['session_id']}: {label.get(item['status'], item['status'])} "
                f'"{quoted}"{note_part}'
            )
        if len(issues) > limit:
            lines.append(f"...(+{len(issues) - limit} more; raise --limit to see them)")
    return _truncate_for_phone("\n".join(lines), char_limit)


def append_subagent_note(line: str, subagent_verdicts: list[Verdict], inline_unbacked: bool = False) -> str:
    """Backlog item 31 part (b): fold inline-worthy verdicts from this turn's subagent_stop
    calls into the parent's own Check line. A parent relaying a child's claim ("the subagent
    sent the email") deserves the same in-the-moment signal as a claim the parent made itself.
    *line* is whatever summary_line already built for the parent's own claims (possibly empty
    when the parent made none). Returns *line* unchanged when no subagent verdict is
    inline-worthy."""
    count = sum(1 for v in subagent_verdicts if inline_worthy(v, inline_unbacked))
    if not count:
        return line
    plural = "s" if count != 1 else ""
    note = f"{count} subagent claim{plural} not backed"
    if not line:
        return f"Check: {note}. /check subagents for detail."
    if line.endswith("/check for detail."):
        line = line[: -len("/check for detail.")].rstrip()
    return f"{line} {note}. /check subagents for detail."


def subagents_report(records: list[dict], char_limit: int = 1500) -> str:
    """Phone-sized digest for /check subagents (backlog item 31 part b): every delegated
    child's own unresolved claim verdicts this session, newest child first. *records* is the
    list of {"child_session_id", "child_status", "ts", "verdicts"} rows a parent session's
    on_subagent_stop appends, where "verdicts" is a list of engine.verdict_to_dict() shapes."""
    if not records:
        return "Check, Please: no subagent runs logged yet."
    label = {"NOT_BACKED": "not backed", "STALE": "stale", "FAILED": "failed", "EDITED": "proof edited",
             "PARTIAL": "partial run"}
    lines = ["Check, Please: subagents"]
    shown_any = False
    for rec in sorted(records, key=lambda r: r.get("ts", 0), reverse=True):
        issues = [v for v in (rec.get("verdicts") or []) if v.get("status") in label]
        if not issues:
            continue
        child = rec.get("child_session_id") or "unknown"
        lines.append(f"- child {child}:")
        for v in issues:
            lines.append(f"  {label.get(v['status'], v['status'])}: \"{v.get('text', '')}\" ({v.get('note', '')})")
        shown_any = True
    if not shown_any:
        return "Check, Please: no unresolved subagent claims logged."
    return _truncate_for_phone("\n".join(lines), char_limit)


def verify_nudge_directive(edited_after_pass: bool, last_pass_turn: Optional[int],
                            pass_detail: str) -> Optional[dict]:
    """pre_verify continue directive (backlog item 9): when the edited-code verify gate fires
    and the session's last known test pass predates a later edit with no rerun since, ask the
    agent to rerun tests before finishing. Shape matches hooks.md's pre_verify contract exactly:
    {"action": "continue", "message": str}. Returns None when there is nothing stale to nudge
    (no prior pass recorded, or no edit happened after the last pass)."""
    if not edited_after_pass or last_pass_turn is None:
        return None
    detail = f" ({pass_detail})" if pass_detail else ""
    return {
        "action": "continue",
        "message": (
            f"Tests last passed in turn {last_pass_turn}{detail}. Code changed since then "
            "with no rerun recorded. Rerun the test suite before telling the user it passes."
        ),
    }


def catch_before_send_directive(verdicts: list[Verdict]) -> Optional[dict]:
    """Backlog item 30: a pre_verify continue directive naming every inline-worthy claim in
    this turn's own draft final_response, so a same-turn STALE or FAILED claim never reaches
    the user. Shape matches hooks.md's pre_verify contract exactly: {"action": "continue",
    "message": str}. Uses the default (no inline_unbacked) inline_worthy test, same STALE/
    FAILED-only bar as the Check line's receipts framing (item 41): a plain NOT_BACKED claim
    has no receipt, which is common and not itself a contradiction worth stopping the turn
    over; STALE/FAILED mean this turn's own evidence actively contradicts the draft. Returns
    None when nothing inline-worthy is present."""
    flagged = [v for v in verdicts if inline_worthy(v)]
    if not flagged:
        return None
    sentences = []
    for v in flagged:
        quoted = v.claim.text.strip()
        if len(quoted) > 140:
            quoted = quoted[:137] + "..."
        if v.status == "FAILED":
            fix = "Say it failed, or fix the failure before claiming it worked."
        elif v.status == "EDITED":
            fix = "Disclose the edit, or rerun it without touching the proof file first."
        elif v.status == "PARTIAL":
            fix = "Say only that part ran, or run the whole suite before claiming it all passes."
        else:  # STALE
            fix = "Rerun the check, or say it has not been rerun since the change."
        sentences.append(f'Your draft says "{quoted}", but {v.note}. {fix}')
    return {"action": "continue", "message": " ".join(sentences)}


def next_turn_nudge_context(verdicts: list[Verdict], inline_unbacked: bool = False) -> Optional[str]:
    """Backlog item 30, next_turn_nudge fallback: pre_verify only fires on turns that edited
    code (hooks.md), so a STALE or FAILED claim in a non-coding reply never gets a
    catch_before_send chance and can reach the user untouched. This covers that gap one turn
    late: pre_llm_call checks the PREVIOUS turn's already-delivered verdicts and, if any were
    inline-worthy, returns one plain-text context line for the agent's next turn so it can
    correct itself if the user is still relying on that claim. Same inline_worthy bar as the
    Check line (item 41 receipts framing), not a separate kind list. Returns None when nothing
    inline-worthy was in the previous reply."""
    flagged = [v for v in verdicts if inline_worthy(v, inline_unbacked)]
    if not flagged:
        return None
    bits = []
    for v in flagged:
        quoted = v.claim.text.strip()
        if len(quoted) > 100:
            quoted = quoted[:97] + "..."
        bits.append(f'"{quoted}" ({v.note})')
    plural = "s" if len(bits) > 1 else ""
    return (f"Check, Please: your last reply had {len(bits)} unresolved claim{plural} with no "
            f"current evidence: {'; '.join(bits)}. If the user is relying on it, confirm it is "
            "still true or correct it.")


# Kinds the optional 'inline_unbacked' setting (default off) restores to the reply line for
# users who want the old higher-stakes-kinds-inline behavior back (item 41 part b).
_INLINE_NOT_BACKED_KINDS = {"sent", "paid", "deployed", "pushed", "deleted", "scheduled"}


def inline_worthy(v: Verdict, inline_unbacked: bool = False) -> bool:
    """True when *v* should appear in the reply's own Check line (summary_line), not only in
    /check's full detail. STALE and FAILED are always inline-worthy regardless of kind: both
    mean a claim the agent is actively making right now is contradicted or unproven by this
    turn's own evidence (item 41's "receipts" framing: a contradiction is a torn-up receipt,
    worth flagging in the moment). NOT_BACKED means no receipt exists at all, which is a much
    more common and much lower-signal case (item 41: 36 of 40.9 real inline alerts per 100 turns
    were plain NOT_BACKED text claims, not contradictions) -- by default it is never inline, only
    listed in /check and the Desktop pane. Pass inline_unbacked=True to restore the old behavior
    for the higher-stakes kinds in _INLINE_NOT_BACKED_KINDS (sent, paid, deployed, pushed,
    deleted, scheduled) for users who want it. BACKED is never inline-worthy (nothing to flag)."""
    if v.status in {"STALE", "FAILED", "EDITED", "PARTIAL"}:
        return True
    if v.status == "NOT_BACKED" and inline_unbacked:
        return v.claim.kind in _INLINE_NOT_BACKED_KINDS
    return False


def detail_report(verdicts: list[Verdict]) -> str:
    if not verdicts:
        return "Check, Please: no completion claims found in the last reply."
    lines = ["Check, Please: last reply"]
    label = {"BACKED": "BACKED", "STALE": "STALE", "FAILED": "FAILED", "NOT_BACKED": "NOT BACKED",
              "EDITED": "EDITED (proof edited)", "PARTIAL": "PARTIAL"}
    for v in verdicts:
        lines.append(f"- {label[v.status]}: \"{v.claim.text}\"")
        if v.proof:
            lines.append(f"    proof: {v.proof.tool} {v.proof.detail}".rstrip())
        if v.note:
            lines.append(f"    note: {v.note}")
    return "\n".join(lines)
