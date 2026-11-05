"""Check, Please core engine. Pure stdlib, no Hermes imports, no model calls.

Two halves:
  1. classify_call(): turn one tool call (name, args, result, status) into evidence:
     which rung of the state ladder it proves, and whether it succeeded.
  2. extract_claims(): find past-tense "done" claims in the agent's final reply.
check_turn() pairs each claim with the evidence recorded during the same turn.

State ladder (low to high): drafted < saved < tested < committed < pushed < deployed < sent < paid.
A claim only counts at the rung the tool log proves.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional

LADDER = ["drafted", "saved", "tested", "committed", "pushed", "deployed", "scheduled", "sent", "paid", "deleted"]

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
_SCHEDULE_CMD = re.compile(r"\bhermes\s+cron\s+(create|add|edit)\b|\bcrontab\s+-[el]?\b|\blaunchctl\s+(load|bootstrap)\b", _I)

_SEND_TOOL = re.compile(r"(^|_|\.|-)(send|post|reply|tweet|email|mail|message|dm|publish_post|create_post|chat_post)", _I)
_WRITE_TOOLS = {"write_file", "patch", "edit_file", "str_replace", "create_file", "apply_patch", "file_write", "replace_in_file"}
_DRAFT_TOOL = re.compile(r"draft", _I)
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


def classify_call(tool_name: str, args: Any, result: Any = None, status: Optional[str] = None,
                  seq: int = 0, ts: float = 0.0) -> Optional[Evidence]:
    """Return the evidence a tool call provides, or None when it proves nothing on the ladder."""
    name = (tool_name or "").strip()
    low = name.lower()
    a = _as_dict(args)
    ok = call_succeeded(result, status)

    if low in {"terminal", "shell", "bash", "run_command", "execute_command"}:
        cmd = str(a.get("command") or a.get("cmd") or "")
        short = cmd.strip().splitlines()[0][:160] if cmd.strip() else ""
        # Order matters: the highest-stakes rung wins for a compound command.
        for kind, pat in (("paid", _PAY_CMD), ("sent", _SEND_CMD), ("deployed", _DEPLOY_CMD),
                          ("pushed", _PUSH_CMD), ("committed", _COMMIT_CMD), ("scheduled", _SCHEDULE_CMD),
                          ("tested", _TEST_CMD), ("deleted", _DELETE_CMD)):
            if pat.search(cmd):
                return Evidence(kind, ok, name, short, seq, ts)
        return None

    if low in _WRITE_TOOLS:
        path = str(a.get("path") or a.get("file_path") or a.get("filename") or "")
        kind = "drafted" if re.search(r"(^|/)drafts?/|draft", path, _I) else "saved"
        return Evidence(kind, ok, name, path[:160], seq, ts)

    if low in {"cronjob", "cron", "schedule", "cronjob_manage"}:
        action = str(a.get("action") or "")
        if action.lower() in {"create", "add", "update", "edit", "resume", ""}:
            return Evidence("scheduled", ok, name, str(a.get("name") or a.get("schedule") or "")[:160], seq, ts)
        return None

    # Messaging/MCP/connector tools. Read-only verbs never count as a send.
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
    ("paid", r"\b(paid|charged|payment (went through|succeeded|processed|collected))\b"),
    ("sent", r"\b(sent|emailed|e-mailed|texted|messaged|dm'?d|replied to|posted (it|this|that|the|a|an|to|in|on)\b|"
             r"notified|delivered (it|the|to)|it'?s been sent|has been sent|have been sent|went out|is out the door)"),
    ("deployed", r"\b(deployed|is (now )?live|went live|now live|shipped to prod(uction)?|published|released|rolled out)\b"),
    ("pushed", r"\b(pushed|merged|opened (a|the) (pr|pull request))\b"),
    ("committed", r"\b(committed|made (a|the) commit)\b"),
    ("tested", r"\b(tests? (now )?(pass|passed|passing|are passing|all pass|green|succeeded)|all (tests )?green|"
               r"test suite (passes|passed|is green)|verified (it )?works|all \d+ tests pass(ed)?|\d+ (of \d+ )?tests? pass(ed|ing)?)\b"),
    ("scheduled", r"\b(scheduled|set up (a|the) (cron|job|reminder)|created (a|the) cron)\b"),
    ("deleted", r"\b(deleted|removed (the|it|them|those|these|that)|cleaned up)\b"),
    ("saved", r"\b(saved|wrote (the|it|a|an|to)|written to|created (the|a|an) (file|draft|doc|document)|updated (the )?file)\b"),
]]

_NEGATION = re.compile(
    r"\b(not|n't|never|nothing|no|none|no longer|haven'?t|hasn'?t|didn'?t|won'?t|will|would|could|should|can|cannot|can'?t|"
    r"ready to|about to|going to|plan to|want to|need to|needs to|if you|once you|after you|before|"
    r"draft|unless|until|try to|tried to|attempted|failed to|unable)\b", _I)
_QUESTION = re.compile(r"\?\s*$")
_CODE_FENCE = re.compile(r"```.*?```", re.S)
_INLINE_CODE = re.compile(r"`[^`\n]*`")
_QUOTE_LINE = re.compile(r"^\s*>.*$", re.M)
_CHECK_LINE = re.compile(r"^\s*(\u2611|\[check\]|check:).*$", re.M | _I)


def _sentences(text: str) -> list[str]:
    text = _CODE_FENCE.sub(" ", text or "")
    text = _QUOTE_LINE.sub(" ", text)
    text = _CHECK_LINE.sub(" ", text)
    text = _INLINE_CODE.sub("CODE", text)
    parts = re.split(r"(?<=[.!?])\s+|\n+|(?<=:)\s+(?=[-*\u2022])|^\s*[-*\u2022]\s+", text, flags=re.M)
    out: list[str] = []
    for p in parts:
        p = p.strip(" -*\u2022\t")
        if not p:
            continue
        # Clause split so "Tests pass, preview is live and invoice emailed" yields three claims.
        # A question keeps its "?" on every clause so none of it reads as a claim.
        q = p.rstrip().endswith("?")
        for c in re.split(r",\s+|;\s+|\s+and\s+(?=\w)", p):
            c = c.strip(" ,;")
            if c:
                out.append(c + ("?" if q and not c.endswith("?") else ""))
    return out


def extract_claims(reply: str) -> list[Claim]:
    claims: list[Claim] = []
    seen: set[tuple[str, str]] = set()
    for sent in _sentences(reply):
        if _QUESTION.search(sent):
            continue
        for kind, pat in _CLAIM_PATTERNS:
            m = pat.search(sent)
            if not m:
                continue
            # Negation/future/conditional words in the 40 chars before the claim verb void it.
            window = sent[max(0, m.start() - 40):m.start()]
            if _NEGATION.search(window):
                continue
            key = (kind, sent.lower())
            if key in seen:
                continue
            seen.add(key)
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


def _highest_rung(evidence: list[Evidence]) -> Optional[str]:
    best = None
    for ev in evidence:
        if ev.ok and ev.kind in LADDER and ev.kind != "deleted":
            if best is None or LADDER.index(ev.kind) > LADDER.index(best):
                best = ev.kind
    return best


def check_turn(reply: str, evidence: list[Evidence]) -> list[Verdict]:
    verdicts: list[Verdict] = []
    rung = _highest_rung(evidence)
    for claim in extract_claims(reply):
        backers = [e for e in evidence if e.kind in _BACKS.get(claim.kind, set())]
        good = [e for e in backers if e.ok]
        if good:
            proof = good[-1]
            if claim.kind == "tested":
                later_writes = [e for e in evidence if e.kind in {"saved", "drafted"} and e.ok and e.seq > proof.seq]
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
