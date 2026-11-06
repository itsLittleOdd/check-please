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


_TEST_CMD = re.compile(
    r"\b(pytest|py\.test|python3?\s+-m\s+(pytest|unittest)|npm\s+(run\s+)?test|pnpm\s+(run\s+)?test|yarn\s+test|"
    r"bun\s+test|go\s+test|cargo\s+test|jest|vitest|mocha|rspec|phpunit|make\s+(test|check)|tox|nox|ctest)\b", _I)
# Real checks we don't otherwise recognize as test evidence (item 27 family (e)): a script or
# flag whose own name says it's a check, not a fixed test-runner name. Scoped to command/code
# text only (never claim text), so a false match here just over-credits evidence, which is the
# direction this item wants; still word-bounded to avoid "checkout"/"doctorate"-style substrings.
_CHECK_CMD = re.compile(
    r"\b(check|verify|validate|lint|doctor|smoke|probe|assert)\b|--check\b|\bpy_compile\b|\btsc\b|"
    r"\bbash\s+-n\b|\bsh\s+-n\b|\bzsh\s+-n\b|\bnode\s+--check\b", _I)
_COMMIT_CMD = re.compile(r"\bgit\s+commit\b", _I)
_PUSH_CMD = re.compile(r"\bgit\s+push\b|\bgh\s+pr\s+(create|merge)\b", _I)
_DEPLOY_CMD = re.compile(
    r"\bvercel\b[^\n;|&]*(--prod\b|\bdeploy\b|\bpromote\b)|^\s*vercel\s*$|\bnetlify\s+deploy\b|\bwrangler\s+(deploy|publish|pages\s+deploy)\b|"
    r"\bfly(ctl)?\s+deploy\b|\brailway\s+up\b|\bheroku\b[^\n]*\b(deploy|releases)\b|\bgit\s+push\s+heroku\b|"
    r"\bnpm\s+publish\b|\bpnpm\s+publish\b|\btwine\s+upload\b|\buv\s+publish\b|\bpoetry\s+publish\b|\bgh\s+release\s+create\b|"
    r"\bkubectl\s+(apply|rollout)\b|\bhelm\s+(install|upgrade)\b|\bfirebase\s+deploy\b|\bsupabase\s+functions\s+deploy\b", _I)
_SEND_CMD = re.compile(
    r"\bsendmail\b|\bmailx?\s+-s\b|\bhimalaya\b[^\n]*\bsend\b|\bgws\b[^\n]*\bsend\b|api\.resend\.com|smtplib|\bmsmtp\b|"
    r"discord(app)?\.com/api/(v\d+/)?(webhooks|channels/\d+/messages)|hooks\.slack\.com|slack\.com/api/chat\.post|"
    r"api\.telegram\.org/bot\S*/send|\bhermes\s+send\b|\bimsg\s+send\b|osascript[^\n]*\bMessages\b[^\n]*\bsend\b|"
    r"\bxurl\s+(post|reply|dm)\b|api\.(twitter|x)\.com/2/(tweets|dm)|api\.twilio\.com|api\.sendgrid\.com|api\.postmarkapp\.com", _I)
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
_SCHEDULE_CMD = re.compile(r"\bhermes\s+cron\s+(create|add|edit)\b|\bcrontab\s+-[el]?\b|\blaunchctl\s+(load|bootstrap)\b", _I)

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
    if data:
        if data.get("success") is False or data.get("ok") is False:
            return False
        err = data.get("error")
        if err not in (None, "", False, [], {}):
            return False
        code = data.get("exit_code", data.get("returncode", data.get("exitCode")))
        if code is not None:
            try:
                return int(code) == 0
            except (TypeError, ValueError):
                return False
        if str(data.get("status", "")).lower() in {"error", "failed", "blocked", "denied"}:
            return False
        return True
    text = str(result or "")
    if re.search(r"\b(traceback|error:|permission denied|command not found|exit code [1-9])", text, _I):
        return False
    return True


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
    if _CHECK_CMD.search(text):
        return "tested"
    if _DELETE_SCRIPT_CMD.search(text):
        return "deleted"
    return None


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
    kinds = [k for k in (_scan_ladder(part) for part in cmd.split("&&")) if k]
    if len(kinds) < 2:
        return None
    return kinds[0]


def classify_call(tool_name: str, args: Any, result: Any = None, status: Optional[str] = None,
                  seq: int = 0, ts: float = 0.0) -> Optional[Evidence]:
    """Return the evidence a tool call provides, or None when it proves nothing on the ladder."""
    name = (tool_name or "").strip()
    low = name.lower()
    a = _as_dict(args)
    ok = call_succeeded(result, status)

    if low in _NO_EVIDENCE_TOOLS:
        # A child agent's work is not this parent turn's evidence: the plugin only sees the
        # parent session's ledger, and the child may still be running or may have failed.
        return None

    if low in {"terminal", "shell", "bash", "run_command", "execute_command"}:
        cmd = str(a.get("command") or a.get("cmd") or "")
        short = cmd.strip().splitlines()[0][:160] if cmd.strip() else ""
        kind = _scan_ladder(cmd)
        if kind is None and ok and _DELETE_OUTPUT.search(str(result or "")):
            kind = "deleted"
        if not ok and "&&" in cmd:
            earliest = _earliest_chain_kind(cmd)
            if earliest:
                return Evidence(earliest, True, name, short, seq, ts)
        return Evidence(kind, ok, name, short, seq, ts) if kind else None

    if low in _CODE_TEXT_TOOLS:
        # execute_code runs Python directly; browser_exec runs Python against browser helpers.
        # Neither has a fixed verb, so scan the code body for the same ladder patterns. A plain
        # form fill/click/navigate call matches nothing here, which is the point: a form submit
        # is not a send unless the code actually posts somewhere (webhook URL, mail API, etc.).
        code = str(a.get("code") or a.get("script") or "")
        short = code.strip().splitlines()[0][:160] if code.strip() else ""
        kind = _scan_ladder(code)
        if kind is None and ok and _DELETE_OUTPUT.search(str(result or "")):
            kind = "deleted"
        return Evidence(kind, ok, name, short, seq, ts) if kind else None

    if low in _WRITE_TOOLS:
        path = str(a.get("path") or a.get("file_path") or a.get("filename") or "")
        if re.search(r"(^|/)drafts?/|draft", path, _I):
            kind = "drafted"
        elif _is_code_path(path):
            kind = "saved_code"
        else:
            kind = "saved"
        return Evidence(kind, ok, name, path[:160], seq, ts)

    if low in {"cronjob", "cron", "schedule", "cronjob_manage"}:
        action = str(a.get("action") or "")
        if action.lower() in {"create", "add", "update", "edit", "resume", ""}:
            return Evidence("scheduled", ok, name, str(a.get("name") or a.get("schedule") or "")[:160], seq, ts)
        return None

    # Messaging/MCP/connector tools (mcp__<server>__<tool>, connectors__<connector>__<tool>) and
    # bare names like send_message. Read-only verbs never count as a send.
    tail = re.split(r"__|\.", low)[-1]
    if _DRAFT_TOOL.search(tail):
        return Evidence("drafted", ok, name, _target_hint(a), seq, ts)
    if _READONLY_HINT.search(tail):
        return None
    if _SEND_TOOL.search(tail) or low in {"send_message", "message", "imessage"}:
        return Evidence("sent", ok, name, _target_hint(a), seq, ts)
    return None


def _target_hint(a: dict) -> str:
    for key in ("to", "recipient", "recipients", "channel", "chat_id", "target", "email", "thread"):
        if a.get(key):
            return f"{key}={str(a[key])[:80]}"
    return ""


# ------------------------------------------------------------------ claims ----

@dataclass
class Claim:
    kind: str          # ladder rung the sentence claims
    text: str          # the sentence (trimmed)
    phrase: str        # the matched words


# (rung, pattern). Past tense / completed state only.
_CLAIM_PATTERNS: list[tuple[str, re.Pattern]] = [(k, re.compile(p, _I)) for k, p in [
    ("paid", r"\b(paid(?!\s+(attention|off|vacation|leave|time off|internship|plan|tier|feature|version|subscription|account|add[- ]on|service))|"
             r"charged (the |your |his |her |their |an? )?(card|customer|client|account|deposit|rent|fee|balance)\b|charged \$\d+|"
             r"payment (went through|succeeded|processed|collected)|"
             r"collected (the |an? )?(deposit|payment|rent|fee)|"
             r"(invoice|bill|deposit|rent|fee|balance) (is|was|has been|have been) (paid|charged))\b"),
    ("sent", r"\b(sent(?!\s+\w*\s*(a |an |the )?calendar invite|\s+(folder|items?|box|mail|tray))|emailed|e-mailed|texted|messaged|dm'?d|replied to|posted (it|this|that|the|a|an|to|in|on)\b|"
             r"notified|delivered (it|the|to)|it'?s been sent|has been sent|have been sent|went out|is out the door)"),
    ("deployed", r"\b(deployed(?!\s+(environment|version|branch|instance|url|preview|slot))|is (now )?live|went live|now live|shipped to prod(uction)?|"
                 r"published(?!\s+(the\s+)?release\s+notes?\b)|released|rolled out)\b"),
    ("pushed", r"\b(pushed(?!\s+(back|off|through|aside|for\b|myself|hard|notifications?\b|a notification\b|the button\b))|"
               r"merged(?=\b.{0,40}(\bpr\b|pull request|branch|into main|into master|the changes|the code|the feature))|"
               r"opened (a |an |the )?(pr\b|pull request))\b"),
    ("committed", r"\b(committed(?!\s+to\b)|made (a|the) commit)\b"),
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
               r"works? (as expected|correctly|now|fine|great)|worked (end to end|as expected|correctly|fine|great)|everything (looks|checks|passes|works)( (good|out|fine))?\b|confirmed (the )?fix|"
               r"confirmed in (the |a |my )?(browser|app|console|terminal|simulator|emulator)\b|verified (the |a |that )?(fix|bug|issue|patch)\b|"
               r"tests?:\s*(pass|passed|green|ok)\b|(build|ci|qa):\s*(pass|passed|green|ok)\b|[\u2713\u2705]\s*tests?|tests?\s*[\u2713\u2705]|"
               r"no errors( came up| found)?|ran clean|runs clean|it compiles|compiles cleanly|nothing broke|"
               r"no complaints( from (the )?linter)?|no issues? (found|came up)|ran without (any )?issues?|"
               r"reports? (no|zero) (issues?|violations?|errors?|failures?)\b|"
               r"ran successfully|executed successfully|the output (checks out|matches)|matches the expected output|"
               r"passed with flying colors|lgtm|looks (solid|good to go)|0 (issues|errors|failures)\b|0 failures\b|"
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
    ("deleted", r"\b(deleted|removed (the|it|them|those|these|that)|cleaned up)\b"),
    ("saved", r"\b(saved|wrote (the|it|a|an|to)|written to|created (the|a|an) (file|draft|doc|document)|updated (the )?file|"
              r"(has|have) been saved|was saved|is saved|persisted (it|the data|to disk)|stored (it|the data)|"
              r"changes? (are |is )?(now )?saved|autosaved)\b"),
]]

_NEGATION = re.compile(
    r"\b(not|n't|never|nothing|no|none|no longer|haven'?t|hasn'?t|didn'?t|won'?t|will|would|could|should|can|cannot|can'?t|"
    r"ready to|about to|going to|plan to|want to|need to|needs to|if you|if the|if it|if this|if that|once|after you|before|"
    r"unless|until|try to|tried to|attempted|failed to|unable|supposedly|allegedly|apparently|reportedly|supposed to|"
    r"you\s+already|'s\s+\w+\s+already|was\s+already\b|"
    r"\b(he|she|they|someone|anyone)\s+(said|told|mentioned|claimed|reported|says|tells|told us|let (us|me) know)\b|"
    r"\b(the|our|their) (customer|client|user|vendor|other (team|side)|supplier)\b.{0,25}\b(said|told|mentioned|claimed|reported|says|confirmed)\b|"
    r"draft(ing)?\s+(a|an|the|it|this|up)|to draft|will draft)\b"
    r"|'ll\b", _I)
# A passive clause whose subject is someone ELSE's thing (his/her/their, never my/our) is a
# report about someone else's state, not the assistant's own completed action (item 27 family
# (b) residual, real audit shape: "her reply was sent yesterday"). Checked against the whole
# clause, not the 40-char pre-match window, because the claim pattern itself can match starting
# at the subject noun (e.g. "invoice has been paid"), which would put "has been" inside the
# match rather than before it.
_THIRD_PARTY_PASSIVE = re.compile(r"\b(his|her|their)\b.{0,20}\b(was|were|is|are|has been|have been)\b", _I)
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
            claims.append(Claim(kind, sent[:200], m.group(0)))
            break  # one claim per sentence: the highest-stakes rung listed first
    return claims


# ------------------------------------------------------------------ verdict ----

@dataclass
class Verdict:
    claim: Claim
    status: str                       # BACKED | STALE | FAILED | NOT_BACKED
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
        good = [e for e in backers if e.ok]
        if good:
            proof = good[-1]
            if claim.kind == "tested":
                later_writes = [e for e in evidence if e.kind in {"saved", "drafted", "saved_code"} and e.ok and e.seq > proof.seq]
                if later_writes:
                    verdicts.append(Verdict(claim, "STALE", proof, rung,
                                            f"tests passed, then {later_writes[-1].detail or 'a file'} changed with no run since"))
                    continue
            verdicts.append(Verdict(claim, "BACKED", proof, rung))
        elif backers:
            verdicts.append(Verdict(claim, "FAILED", backers[-1], rung, "the call ran but did not succeed"))
        else:
            verdicts.append(Verdict(claim, "NOT_BACKED", None, rung,
                                    f"no {claim.kind} call this turn" + (f"; proven: {rung}" if rung else "")))
    return verdicts


def summary_line(verdicts: list[Verdict]) -> str:
    """One line for phones and chat apps. Empty string when there is nothing to report."""
    if not verdicts:
        return ""
    counts = {"BACKED": 0, "STALE": 0, "FAILED": 0, "NOT_BACKED": 0}
    for v in verdicts:
        counts[v.status] += 1
    parts = []
    if counts["BACKED"]:
        parts.append(f"{counts['BACKED']} backed")
    if counts["STALE"]:
        parts.append(f"{counts['STALE']} stale")
    if counts["FAILED"]:
        parts.append(f"{counts['FAILED']} failed")
    if counts["NOT_BACKED"]:
        parts.append(f"{counts['NOT_BACKED']} not backed")
    worst = next((v for v in verdicts if v.status in {"NOT_BACKED", "FAILED", "STALE"}), None)
    tail = f" First issue: \"{worst.claim.phrase}\" ({worst.note})." if worst else ""
    return f"Check: {', '.join(parts)}.{tail} /check for detail."


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


def verdict_to_dict(v: Verdict) -> dict:
    """One verdict as a JSON-safe plain dict (backlog item 11 data path: the Desktop pane and
    any plugin_api.py backend route read this shape, never a Verdict/Claim object directly)."""
    return {
        "kind": v.claim.kind,
        "text": v.claim.text,
        "phrase": v.claim.phrase,
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
    status_key = {"STALE": "stale", "FAILED": "failed", "NOT_BACKED": "not_backed"}
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


# Kinds whose NOT_BACKED verdict is high-stakes enough to interrupt the reply itself (item 27
# surface policy, upgrade #4). Everything else still shows up in /check, just not inline.
_INLINE_NOT_BACKED_KINDS = {"sent", "paid", "deployed", "pushed", "deleted", "scheduled"}


def inline_worthy(v: Verdict) -> bool:
    """True when *v* should appear in the reply's own Check line (summary_line), not only in
    /check's full detail. STALE and FAILED are always inline-worthy regardless of kind: both
    mean a claim the agent is actively making right now is contradicted or unproven by this
    turn's own evidence. NOT_BACKED is inline-worthy only for the higher-stakes kinds in
    _INLINE_NOT_BACKED_KINDS (sent, paid, deployed, pushed, deleted, scheduled); the low-stakes
    kinds (saved, tested, committed) that drive most of item 27's real-world false alarms stay
    out of the reply and only surface in /check, where a user who wants the detail can look.
    BACKED is never inline-worthy (nothing to flag)."""
    if v.status in {"STALE", "FAILED"}:
        return True
    if v.status == "NOT_BACKED":
        return v.claim.kind in _INLINE_NOT_BACKED_KINDS
    return False


def detail_report(verdicts: list[Verdict]) -> str:
    if not verdicts:
        return "Check, Please: no completion claims found in the last reply."
    lines = ["Check, Please: last reply"]
    label = {"BACKED": "BACKED", "STALE": "STALE", "FAILED": "FAILED", "NOT_BACKED": "NOT BACKED"}
    for v in verdicts:
        lines.append(f"- {label[v.status]}: \"{v.claim.text}\"")
        if v.proof:
            lines.append(f"    proof: {v.proof.tool} {v.proof.detail}".rstrip())
        if v.note:
            lines.append(f"    note: {v.note}")
    return "\n".join(lines)
