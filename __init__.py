"""Check, Please: Hermes plugin registration.

Hooks (all observe-only except the cosmetic reply transform):
  pre_llm_call          -> start a fresh evidence ledger for this session's turn
  post_tool_call        -> record what each tool call proves (engine.classify_call)
  transform_llm_output  -> append one "Check:" line under replies that make completion claims
Slash command:
  /check                -> full per-claim detail for the last checked reply
  /check today          -> counts plus every non-backed claim logged today
  /check jobs           -> per cron job claim tally, busiest job first
  /check subagents      -> each delegated child's own unresolved claim verdicts

No network, no model calls, no shell. Never blocks, approves or edits a tool call.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional

from . import engine

__version__ = "0.1.0"
logger = logging.getLogger("check_please")

_LOCK = threading.Lock()
_LEDGER: dict[str, dict[str, Any]] = {}   # session_id -> {"turn": str, "seq": int, "evidence": [Evidence]}
_LAST: dict[str, list] = {}               # session_id -> last verdicts
_LAST_SESSION: list[str] = [""]
_MAX_EVIDENCE = 500
_MAX_SESSIONS = 200

_settings: dict[str, Any] = {"line": "all"}   # all | issues | off

# Cross-turn "tests passed, then code changed, no rerun since" tracking (backlog item 6).
# Source of truth is ctx.state (file-backed JSON under plugin-data/, survives a module
# reload) once register(ctx) has run; _CROSS_MEM is only a fallback for unit tests that
# call the module functions directly without a real PluginContext.
_ctx_box: dict[str, Any] = {"ctx": None}
_CROSS_MEM: dict[str, dict] = {}

# Per-cron-job claim tally (backlog item 7): job_id -> {"job_name","claims","backed","not_backed"}.
# Same ctx.state-with-in-memory-fallback pattern as the cross-turn record above.
_CRON_TALLY_MEM: dict[str, dict] = {}
# Honest limit from items 7/16: this had no cap, unlike _MAX_SESSIONS/_MAX_DAYS, so a
# long-running install with many distinct cron jobs could grow it unbounded. Trimmed here
# (item 20), same oldest-inserted-first pattern as _save_last_reply, protecting the job_id
# being written this call so the tally never evicts the row it was just asked to update.
_MAX_CRON_JOBS = 200

# Per-local-day claim log for /check today (backlog item 8): date_key -> engine.tally_day() row.
# Same ctx.state-with-in-memory-fallback pattern; trimmed to _MAX_DAYS oldest-first.
_DAILY_MEM: dict[str, dict] = {}
_MAX_DAYS = 14

# Last-reply verdicts per session, persisted to ctx.state (backlog item 11 data path): the
# Desktop pane's dashboard/plugin_api.py route reads this exact key through the same
# hermes_cli.plugins_state.PluginState class ctx.state wraps, never a raw file read from the
# renderer and never this in-process module. _LAST (above) stays the in-memory copy /check
# itself reads; this is the durable, cross-process-readable twin of the same data.
_LAST_REPLY_MEM: dict[str, dict] = {}

# Session-wide evidence (item 27 family (f), session recaps): unlike _LEDGER (reset every turn
# by _reset_turn), this accumulates across the whole session so a reply recapping earlier-turn
# work (e.g. "I already sent that invoice.") can be backed by evidence from a prior turn, not
# just this one. Same ctx.state-with-in-memory-fallback pattern as the other records above,
# capped per session (oldest evidence dropped first) and across sessions (_MAX_SESSIONS).
_SESSION_EVIDENCE_MEM: dict[str, list[dict]] = {}
_MAX_SESSION_EVIDENCE = 200

# Subagent child claim log (backlog item 31 part b): parent session_id -> list of records
# {"child_session_id", "child_status", "ts", "verdicts"} where "verdicts" is a list of
# engine.verdict_to_dict() shapes, never the raw child_summary text. Same ctx.state-with-
# in-memory-fallback pattern as the other records above; capped per parent (oldest record
# dropped first) and across parent sessions (_MAX_SESSIONS trim on write).
_SUBAGENT_MEM: dict[str, list[dict]] = {}
_MAX_SUBAGENT_RECORDS_PER_PARENT = 20


def _session(sid: Any) -> str:
    return str(sid or "")


def _current_cron() -> Any:
    """ctx.current_cron_execution() if available, else None. Never raises (backlog item 7)."""
    ctx = _ctx_box.get("ctx")
    if ctx is None:
        return None
    try:
        return ctx.current_cron_execution()
    except Exception:
        logger.debug("check-please current_cron_execution failed", exc_info=True)
        return None


def _load_cron_tally() -> dict:
    ctx = _ctx_box.get("ctx")
    if ctx is not None:
        try:
            return dict(ctx.state.get("cron_tally", default={}) or {})
        except Exception:
            logger.debug("check-please cron tally read failed", exc_info=True)
    return dict(_CRON_TALLY_MEM)


def _save_cron_tally(tally: dict, keep: str | None = None) -> None:
    if len(tally) > _MAX_CRON_JOBS:
        over = len(tally) - _MAX_CRON_JOBS
        removed = 0
        for old in list(tally):
            if removed >= over:
                break
            if old == keep:
                continue
            tally.pop(old, None)
            removed += 1
    ctx = _ctx_box.get("ctx")
    if ctx is not None:
        try:
            ctx.state.set("cron_tally", tally)
            return
        except Exception:
            logger.debug("check-please cron tally write failed", exc_info=True)
    _CRON_TALLY_MEM.clear()
    _CRON_TALLY_MEM.update(tally)


def _load_subagent_log() -> dict:
    ctx = _ctx_box.get("ctx")
    if ctx is not None:
        try:
            return dict(ctx.state.get("subagent_log", default={}) or {})
        except Exception:
            logger.debug("check-please subagent log read failed", exc_info=True)
    return dict(_SUBAGENT_MEM)


def _save_subagent_log(data: dict) -> None:
    if len(data) > _MAX_SESSIONS:
        for old in list(data)[: len(data) - _MAX_SESSIONS]:
            data.pop(old, None)
    ctx = _ctx_box.get("ctx")
    if ctx is not None:
        try:
            ctx.state.set("subagent_log", data)
            return
        except Exception:
            logger.debug("check-please subagent log write failed", exc_info=True)
    _SUBAGENT_MEM.clear()
    _SUBAGENT_MEM.update(data)


def _today_key() -> str:
    """Local calendar date as YYYY-MM-DD; the key for the /check today log (backlog item 8)."""
    return time.strftime("%Y-%m-%d", time.localtime())


def _load_daily() -> dict:
    ctx = _ctx_box.get("ctx")
    if ctx is not None:
        try:
            return dict(ctx.state.get("daily_log", default={}) or {})
        except Exception:
            logger.debug("check-please daily log read failed", exc_info=True)
    return dict(_DAILY_MEM)


def _save_daily(daily: dict) -> None:
    if len(daily) > _MAX_DAYS:
        for old in sorted(daily)[: len(daily) - _MAX_DAYS]:
            daily.pop(old, None)
    ctx = _ctx_box.get("ctx")
    if ctx is not None:
        try:
            ctx.state.set("daily_log", daily)
            return
        except Exception:
            logger.debug("check-please daily log write failed", exc_info=True)
    _DAILY_MEM.clear()
    _DAILY_MEM.update(daily)


def _load_last_reply() -> dict:
    ctx = _ctx_box.get("ctx")
    if ctx is not None:
        try:
            return dict(ctx.state.get("last_reply", default={}) or {})
        except Exception:
            logger.debug("check-please last-reply read failed", exc_info=True)
    return dict(_LAST_REPLY_MEM)


def _save_last_reply(sid: str, payload: dict) -> None:
    with _LOCK:
        data = _load_last_reply()
        data[sid] = payload
        if len(data) > _MAX_SESSIONS:
            for old in list(data)[: len(data) - _MAX_SESSIONS]:
                if old != sid:
                    data.pop(old, None)
        ctx = _ctx_box.get("ctx")
        if ctx is not None:
            try:
                ctx.state.set("last_reply", data)
                return
            except Exception:
                logger.debug("check-please last-reply write failed", exc_info=True)
        _LAST_REPLY_MEM.clear()
        _LAST_REPLY_MEM.update(data)


def _reset_turn(sid: str, turn: Any = None) -> None:
    with _LOCK:
        _LEDGER[sid] = {"turn": str(turn or ""), "seq": 0, "evidence": []}
        if len(_LEDGER) > _MAX_SESSIONS:
            for old in list(_LEDGER)[: len(_LEDGER) - _MAX_SESSIONS]:
                _LEDGER.pop(old, None)


def _evidence_to_dict(ev: Any) -> dict:
    return {"kind": ev.kind, "ok": ev.ok, "tool": ev.tool, "detail": ev.detail, "seq": ev.seq, "ts": ev.ts}


def _evidence_from_dict(d: dict) -> Any:
    return engine.Evidence(kind=str(d.get("kind", "")), ok=bool(d.get("ok")), tool=str(d.get("tool", "")),
                            detail=str(d.get("detail", "")), seq=int(d.get("seq", 0) or 0),
                            ts=float(d.get("ts", 0.0) or 0.0))


def _load_session_evidence(sid: str) -> list:
    """All evidence recorded for this session across every turn so far (item 27 family (f)).
    Never raises; falls back to the in-memory store with no ctx."""
    ctx = _ctx_box.get("ctx")
    data: dict = {}
    if ctx is not None:
        try:
            data = dict(ctx.state.get("session_evidence", default={}) or {})
        except Exception:
            logger.debug("check-please session evidence read failed", exc_info=True)
            data = dict(_SESSION_EVIDENCE_MEM)
    else:
        data = dict(_SESSION_EVIDENCE_MEM)
    return [_evidence_from_dict(row) for row in data.get(sid, [])]


def _append_session_evidence(sid: str, ev: Any) -> None:
    with _LOCK:
        ctx = _ctx_box.get("ctx")
        if ctx is not None:
            try:
                data = dict(ctx.state.get("session_evidence", default={}) or {})
            except Exception:
                logger.debug("check-please session evidence read failed", exc_info=True)
                data = dict(_SESSION_EVIDENCE_MEM)
        else:
            data = dict(_SESSION_EVIDENCE_MEM)
        rows = list(data.get(sid, []))
        rows.append(_evidence_to_dict(ev))
        if len(rows) > _MAX_SESSION_EVIDENCE:
            del rows[: len(rows) - _MAX_SESSION_EVIDENCE]
        data[sid] = rows
        if len(data) > _MAX_SESSIONS:
            for old in list(data)[: len(data) - _MAX_SESSIONS]:
                if old != sid:
                    data.pop(old, None)
        if ctx is not None:
            try:
                ctx.state.set("session_evidence", data)
                return
            except Exception:
                logger.debug("check-please session evidence write failed", exc_info=True)
        _SESSION_EVIDENCE_MEM.clear()
        _SESSION_EVIDENCE_MEM.update(data)


def _load_cross() -> dict:
    ctx = _ctx_box.get("ctx")
    if ctx is not None:
        try:
            return dict(ctx.state.get("cross_turn", default={}) or {})
        except Exception:
            logger.debug("check-please cross-turn state read failed", exc_info=True)
    return dict(_CROSS_MEM)


def _save_cross(data: dict) -> None:
    ctx = _ctx_box.get("ctx")
    if ctx is not None:
        try:
            ctx.state.set("cross_turn", data)
            return
        except Exception:
            logger.debug("check-please cross-turn state write failed", exc_info=True)
    _CROSS_MEM.clear()
    _CROSS_MEM.update(data)


def _cross_record(sid: str) -> dict:
    rec = _load_cross().get(sid) or {}
    return {
        "turn_num": int(rec.get("turn_num", 0)),
        "last_pass_turn": rec.get("last_pass_turn"),
        "edited_after_pass": bool(rec.get("edited_after_pass", False)),
        "pass_detail": str(rec.get("pass_detail", "")),
    }


def _save_cross_record(sid: str, rec: dict) -> None:
    with _LOCK:
        data = _load_cross()
        data[sid] = rec
        if len(data) > _MAX_SESSIONS:
            for old in list(data)[: len(data) - _MAX_SESSIONS]:
                if old != sid:
                    data.pop(old, None)
        _save_cross(data)


def on_pre_llm_call(session_id: Any = None, turn_id: Any = None, **kwargs: Any) -> Any:
    """Starts a fresh evidence ledger for this turn, plus the backlog item 30 next_turn_nudge
    fallback: pre_verify (on_pre_verify) only runs its catch_before_send check on coding turns,
    so a STALE/FAILED claim in a non-coding reply reaches the user with nothing to catch it that
    same turn. This looks at the PREVIOUS turn's already-delivered verdicts (_LAST, set by
    on_transform_llm_output after the reply went out) one turn late and, if any were
    inline-worthy, returns a plain string for pre_llm_call's context-injection contract (hooks.md:
    pre_llm_call may return a string to inject ephemeral context into the current user message).
    Off entirely when next_turn_nudge is disabled; never raises."""
    nudge: Optional[str] = None
    try:
        sid = _session(session_id)
        if bool(_settings.get("next_turn_nudge", True)):
            with _LOCK:
                prev_verdicts = list(_LAST.get(sid, []))
            inline_unbacked = bool(_settings.get("inline_unbacked", False))
            nudge = engine.next_turn_nudge_context(prev_verdicts, inline_unbacked)
        _reset_turn(sid, turn_id)
        rec = _cross_record(sid)
        rec["turn_num"] += 1
        _save_cross_record(sid, rec)
    except Exception:  # never break a turn
        logger.debug("check-please pre_llm_call failed", exc_info=True)
        return None
    return nudge


def on_post_tool_call(tool_name: str = "", args: Any = None, result: Any = None, session_id: Any = None,
                      task_id: Any = None, status: Any = None, **kwargs: Any) -> None:
    try:
        sid = _session(session_id or task_id)
        with _LOCK:
            book = _LEDGER.setdefault(sid, {"turn": "", "seq": 0, "evidence": []})
            book["seq"] += 1
            seq = book["seq"]
        ev = engine.classify_call(tool_name, args, result, status, seq=seq, ts=time.time())
        if ev is None:
            return None
        with _LOCK:
            book["evidence"].append(ev)
            if len(book["evidence"]) > _MAX_EVIDENCE:
                del book["evidence"][: len(book["evidence"]) - _MAX_EVIDENCE]
        if ev.ok:
            # Item 27 family (f): only a successful call is worth remembering across turns;
            # a failed one has nothing to back a later recap with.
            _append_session_evidence(sid, ev)
        if ev.ok and ev.kind == "tested":
            rec = _cross_record(sid)
            rec["last_pass_turn"] = rec["turn_num"]
            rec["edited_after_pass"] = False
            rec["pass_detail"] = ev.detail or ev.tool
            _save_cross_record(sid, rec)
        elif ev.ok and ev.kind in {"saved", "drafted", "saved_code"} and ev.stales_tests:
            rec = _cross_record(sid)
            if rec["last_pass_turn"] is not None and rec["turn_num"] > rec["last_pass_turn"]:
                rec["edited_after_pass"] = True
                _save_cross_record(sid, rec)
    except Exception:
        logger.debug("check-please post_tool_call failed", exc_info=True)
    return None


def on_subagent_stop(parent_session_id: Any = None, child_session_id: Any = None,
                     child_summary: Any = None, child_status: Any = None,
                     tool_call_history: Any = None, **kwargs: Any) -> None:
    """Backlog item 31: a delegated child's own successful tool calls are real work, but the
    PARENT session's claims about them ("sent"/"pushed"/...) should only be backed by what the
    child actually did, not trusted on the child's self-reported summary alone.

    Part (a): classifies tool_call_history (metadata only, see engine.classify_subagent_entry)
    and appends it into the PARENT's own ledger, tagged via_subagent, so the parent's next
    transform_llm_output check sees it exactly like a call the parent made itself.

    Part (b): also checks child_summary against THIS CHILD's own evidence only (never mixed
    with the parent's or another child's), keeping those verdicts. This turn's child verdicts
    are folded into the parent's own Check line (engine.append_subagent_note) and logged,
    verdict-shape only (never the raw child_summary text), for /check subagents.
    """
    try:
        sid = _session(parent_session_id)
        if not sid:
            return None
        with _LOCK:
            book = _LEDGER.setdefault(sid, {"turn": "", "seq": 0, "evidence": []})
            seq_start = book["seq"]
        new_evidence = engine.subagent_evidence(tool_call_history, seq_start=seq_start, ts=time.time())
        if new_evidence:
            with _LOCK:
                book["seq"] = seq_start + len(new_evidence)
                book["evidence"].extend(new_evidence)
                if len(book["evidence"]) > _MAX_EVIDENCE:
                    del book["evidence"][: len(book["evidence"]) - _MAX_EVIDENCE]
            for ev in new_evidence:
                if ev.ok:
                    # Same reuse rule as a parent call (item 27 family f): only a successful
                    # child call is worth remembering for a later-turn recap.
                    _append_session_evidence(sid, ev)
        # Part (b): check the child's own summary against ONLY this child's own evidence,
        # so one honest child does not launder another's overclaim and vice versa.
        child_verdicts = engine.check_turn(str(child_summary or ""), new_evidence)
        if child_verdicts:
            with _LOCK:
                book.setdefault("subagent_verdicts", []).extend(child_verdicts)
            record = {
                "child_session_id": _session(child_session_id),
                "child_status": str(child_status or ""),
                "ts": time.time(),
                "verdicts": [engine.verdict_to_dict(v) for v in child_verdicts],
            }
            with _LOCK:
                log = _load_subagent_log()
                per_parent = log.setdefault(sid, [])
                per_parent.append(record)
                if len(per_parent) > _MAX_SUBAGENT_RECORDS_PER_PARENT:
                    del per_parent[: len(per_parent) - _MAX_SUBAGENT_RECORDS_PER_PARENT]
                _save_subagent_log(log)
    except Exception:
        logger.debug("check-please subagent_stop failed", exc_info=True)
    return None


def _is_silent(text: Any) -> bool:
    """Item 45: an empty reply or one starting with the cron suppression token is never
    touched. Cron delivery is skipped on "[SILENT]"; any added text would turn a silent run
    into a delivered message."""
    t = (text or "").strip() if isinstance(text, str) else ""
    return not t or t.startswith("[SILENT]")


def on_transform_llm_output(response_text: str = "", session_id: Any = None, **kwargs: Any):
    """Return the reply with a check line appended, or None to leave it untouched."""
    try:
        if _is_silent(response_text):
            return None
        mode = str(_settings.get("line") or "all").lower()
        sid = _session(session_id)
        with _LOCK:
            evidence = list(_LEDGER.get(sid, {}).get("evidence", []))
            subagent_verdicts = list(_LEDGER.get(sid, {}).get("subagent_verdicts", []))
        verdicts = engine.check_turn(response_text or "", evidence)
        rec = _cross_record(sid)
        if rec["edited_after_pass"] and rec["last_pass_turn"] is not None:
            note = (f"tests passed in turn {rec['last_pass_turn']} ({rec['pass_detail']}), "
                    "code changed since, no rerun this turn")
            verdicts = engine.apply_cross_turn_stale(verdicts, True, note)
        # Item 27 family (f): a reply recapping work actually done in an earlier turn of this
        # same session should not read as an overclaim just because this turn's own ledger is
        # empty. Runs after the cross-turn STALE upgrade above so a known stale "tests pass"
        # claim is never masked as freshly backed by an old pass.
        verdicts = engine.apply_session_evidence(verdicts, _load_session_evidence(sid))
        run = _current_cron()
        cron_guard_text = ""
        if run is not None and verdicts:
            with _LOCK:
                tally = _load_cron_tally()
                job_id = str(run.job_id)
                engine.tally_cron_job(tally, job_id, str(run.job_name), verdicts)
                _save_cron_tally(tally, keep=job_id)
            # Item 32 (cron honesty guard): an unattended run gets its worst inline-worthy
            # claim named at the TOP of the delivered reply, not just tallied for later.
            # A reply that is exactly the silent-suppression token or empty is never altered
            # (nothing is delivered to prepend onto, and a literal [SILENT] must stay exact
            # so the cron runner still suppresses delivery).
            if bool(_settings.get("cron_guard", True)) and (response_text or "").strip() not in ("", "[SILENT]"):
                cron_guard_text = engine.cron_guard_line(verdicts, bool(_settings.get("inline_unbacked", False)))
        if verdicts:
            with _LOCK:
                daily = _load_daily()
                key = _today_key()
                day = daily.setdefault(key, {})
                engine.tally_day(day, verdicts)
                _save_daily(daily)
        with _LOCK:
            _LAST[sid] = verdicts
            _LAST_SESSION[0] = sid
        _save_last_reply(sid, engine.claims_payload(verdicts))
        if mode == "off":
            bottom_line = ""
        elif not verdicts and not subagent_verdicts:
            bottom_line = ""
        else:
            inline_unbacked = bool(_settings.get("inline_unbacked", False))
            if mode == "issues" and not any(
                    engine.inline_worthy(v, inline_unbacked) for v in verdicts) and not any(
                    engine.inline_worthy(v, inline_unbacked) for v in subagent_verdicts):
                bottom_line = ""
            else:
                bottom_line = engine.summary_line(verdicts, inline_unbacked)
                bottom_line = engine.append_subagent_note(bottom_line, subagent_verdicts, inline_unbacked)
        if not cron_guard_text and not bottom_line:
            return None
        text = response_text.rstrip()
        if cron_guard_text:
            text = f"{cron_guard_text}\n\n{text}"
        if bottom_line:
            text = f"{text}\n\n{bottom_line}"
        return text
    except Exception:
        logger.debug("check-please transform failed", exc_info=True)
        return None


def on_pre_verify(session_id: Any = None, coding: bool = False, attempt: int = 0,
                   final_response: str = "", changed_paths: Any = None, **kwargs: Any) -> Any:
    """Backlog item 30 (catch before send) plus item 9 (verify nudge). Scoped to coding
    sessions, one-shot per turn (idempotent on attempt). Item 30 runs first: check this turn's
    own draft final_response against this turn's evidence (plus cross-turn stale and session-
    recap evidence, same pipeline on_transform_llm_output uses) and, if any inline-worthy
    STALE/FAILED claim is in the draft, send the agent back before the user ever sees it. Item
    9's generic 'rerun tests' nudge only fires when item 30 found nothing to catch, so the two
    never double-nudge in one turn. Each is off entirely when its own config is disabled."""
    try:
        if attempt or not coding or _is_silent(final_response):
            return None
        sid = _session(session_id)
        rec = _cross_record(sid)
        if bool(_settings.get("catch_before_send", True)):
            with _LOCK:
                evidence = list(_LEDGER.get(sid, {}).get("evidence", []))
            verdicts = engine.check_turn(final_response or "", evidence)
            if rec["edited_after_pass"] and rec["last_pass_turn"] is not None:
                note = (f"tests passed in turn {rec['last_pass_turn']} ({rec['pass_detail']}), "
                        "code changed since, no rerun this turn")
                verdicts = engine.apply_cross_turn_stale(verdicts, True, note)
            verdicts = engine.apply_session_evidence(verdicts, _load_session_evidence(sid))
            directive = engine.catch_before_send_directive(verdicts)
            if directive is not None:
                return directive
        if not bool(_settings.get("verify_nudge", True)):
            return None
        return engine.verify_nudge_directive(rec["edited_after_pass"], rec["last_pass_turn"], rec["pass_detail"])
    except Exception:
        logger.debug("check-please pre_verify failed", exc_info=True)
        return None


def handle_check(raw_args: str = "", session_id: Any = None, **kwargs: Any) -> str:
    try:
        arg = (raw_args or "").strip().split()[0].lower() if (raw_args or "").strip() else ""
        if arg == "today":
            with _LOCK:
                daily = _load_daily()
            key = _today_key()
            return engine.daily_report(key, daily.get(key, {}))
        if arg == "jobs":
            with _LOCK:
                tally = _load_cron_tally()
            return engine.jobs_report(tally)
        if arg == "subagents":
            with _LOCK:
                log = _load_subagent_log()
            sid = _session(session_id) or _LAST_SESSION[0]
            return engine.subagents_report(log.get(sid, []))
        sid = _session(session_id) or _LAST_SESSION[0]
        with _LOCK:
            verdicts = _LAST.get(sid) or _LAST.get(_LAST_SESSION[0]) or []
        return engine.detail_report(verdicts)
    except Exception as exc:
        return f"Check, Please could not build the report: {exc}"


def _audit_cli_setup(parser: Any) -> None:
    """Wire ``hermes check-please audit`` (backlog item 28). One subcommand today; more could
    join the same ``check-please`` namespace later without a new register_cli_command call."""
    subs = parser.add_subparsers(dest="check_please_command", required=False)
    p_audit = subs.add_parser("audit", help="Read-only replay of your own saved sessions through "
                                             "the engine: turns checked, claims, backed, top "
                                             "not-backed claims with session id and date.")
    p_audit.add_argument("--since", default=None,
                          help="Only sessions active in the last N days, e.g. 7d (default: no limit)")
    p_audit.add_argument("--profile", default=None,
                          help="Named profile to audit (default: the current profile's own home; "
                               "cross-profile audit is not yet built, see README)")
    p_audit.add_argument("--limit", type=int, default=20,
                          help="Max not-backed claims to print (default: 20)")
    parser.set_defaults(func=_audit_cli_dispatch)


def _audit_cli_dispatch(args: Any) -> int:
    import sys
    sub = getattr(args, "check_please_command", None)
    if sub != "audit":
        print("usage: hermes check-please audit [--since Nd] [--profile NAME] [--limit N]",
              file=sys.stderr)
        return 2
    return _audit_cli_run(args)


def _audit_cli_run(args: Any) -> int:
    """Handler for ``hermes check-please audit``. Read-only, local only, nothing sent. Resolves
    the current profile's own ``state.db`` via the documented ``hermes_constants.get_hermes_home``;
    ``--profile`` for a NAMED profile other than the current one is accepted on the command line
    but not yet implemented (reported plainly, not silently ignored) -- a follow-up slice."""
    import sys
    try:
        from . import audit as _audit
        import hermes_constants
    except Exception as exc:
        print(f"Check, Please audit needs a real Hermes install: {exc}", file=sys.stderr)
        return 1
    since_days = None
    if args.since:
        raw = str(args.since).strip().lower()
        digits = raw[:-1] if raw.endswith("d") else raw
        try:
            since_days = int(digits)
        except ValueError:
            print(f"--since expects a number of days like 7d, got {args.since!r}", file=sys.stderr)
            return 2
    if args.profile:
        print(f"--profile {args.profile!r} is not yet supported; auditing the current profile's "
              "own session history instead (see README's 'check-please audit' limits).",
              file=sys.stderr)
    db_path = hermes_constants.get_hermes_home() / "state.db"
    print(_audit.run_audit(db_path, since_days=since_days, limit=max(args.limit, 0)))
    return 0


def register(ctx) -> None:
    _ctx_box["ctx"] = ctx
    try:
        _settings["line"] = ctx.get_config("line", default="all")
    except Exception:
        pass
    try:
        _settings["verify_nudge"] = ctx.get_config("verify_nudge", default=True)
    except Exception:
        pass
    try:
        _settings["catch_before_send"] = ctx.get_config("catch_before_send", default=True)
    except Exception:
        pass
    try:
        _settings["next_turn_nudge"] = ctx.get_config("next_turn_nudge", default=True)
    except Exception:
        pass
    try:
        _settings["inline_unbacked"] = ctx.get_config("inline_unbacked", default=False)
    except Exception:
        pass
    try:
        _settings["cron_guard"] = ctx.get_config("cron_guard", default=True)
    except Exception:
        pass
    ctx.register_hook("pre_llm_call", on_pre_llm_call)
    ctx.register_hook("post_tool_call", on_post_tool_call)
    ctx.register_hook("subagent_stop", on_subagent_stop)
    ctx.register_hook("transform_llm_output", on_transform_llm_output)
    ctx.register_hook("pre_verify", on_pre_verify)
    ctx.register_command("check", handle_check,
                          description="Show which claims the tool log backs: last reply, "
                                       "'today' for the day's tally, 'jobs' for per-cron-job "
                                       "tally, 'subagents' for delegated children's own claims")
    ctx.register_cli_command(
        "check-please", help="Read-only audit of your own saved session history (backlog item 28)",
        setup_fn=_audit_cli_setup, handler_fn=_audit_cli_dispatch,
        description="hermes check-please audit [--since Nd] [--profile NAME] [--limit N]")
