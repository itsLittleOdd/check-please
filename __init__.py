"""Check, Please: Hermes plugin registration.

Hooks (all observe-only except the cosmetic reply transform):
  pre_llm_call          -> start a fresh evidence ledger for this session's turn
  post_tool_call        -> record what each tool call proves (engine.classify_call)
  transform_llm_output  -> append one "Check:" line under replies that make completion claims
Slash command:
  /check                -> full per-claim detail for the last checked reply
  /check today          -> counts plus every non-backed claim logged today
  /check jobs           -> per cron job claim tally, busiest job first

No network, no model calls, no shell. Never blocks, approves or edits a tool call.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

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


def _save_cron_tally(tally: dict) -> None:
    ctx = _ctx_box.get("ctx")
    if ctx is not None:
        try:
            ctx.state.set("cron_tally", tally)
            return
        except Exception:
            logger.debug("check-please cron tally write failed", exc_info=True)
    _CRON_TALLY_MEM.clear()
    _CRON_TALLY_MEM.update(tally)


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


def on_pre_llm_call(session_id: Any = None, turn_id: Any = None, **kwargs: Any) -> None:
    try:
        sid = _session(session_id)
        _reset_turn(sid, turn_id)
        rec = _cross_record(sid)
        rec["turn_num"] += 1
        _save_cross_record(sid, rec)
    except Exception:  # never break a turn
        logger.debug("check-please pre_llm_call failed", exc_info=True)
    return None


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
        if ev.ok and ev.kind == "tested":
            rec = _cross_record(sid)
            rec["last_pass_turn"] = rec["turn_num"]
            rec["edited_after_pass"] = False
            rec["pass_detail"] = ev.detail or ev.tool
            _save_cross_record(sid, rec)
        elif ev.ok and ev.kind in {"saved", "drafted"}:
            rec = _cross_record(sid)
            if rec["last_pass_turn"] is not None and rec["turn_num"] > rec["last_pass_turn"]:
                rec["edited_after_pass"] = True
                _save_cross_record(sid, rec)
    except Exception:
        logger.debug("check-please post_tool_call failed", exc_info=True)
    return None


def on_transform_llm_output(response_text: str = "", session_id: Any = None, **kwargs: Any):
    """Return the reply with a check line appended, or None to leave it untouched."""
    try:
        mode = str(_settings.get("line") or "all").lower()
        sid = _session(session_id)
        with _LOCK:
            evidence = list(_LEDGER.get(sid, {}).get("evidence", []))
        verdicts = engine.check_turn(response_text or "", evidence)
        rec = _cross_record(sid)
        if rec["edited_after_pass"] and rec["last_pass_turn"] is not None:
            note = (f"tests passed in turn {rec['last_pass_turn']} ({rec['pass_detail']}), "
                    "code changed since, no rerun this turn")
            verdicts = engine.apply_cross_turn_stale(verdicts, True, note)
        run = _current_cron()
        if run is not None and verdicts:
            with _LOCK:
                tally = _load_cron_tally()
                engine.tally_cron_job(tally, str(run.job_id), str(run.job_name), verdicts)
                _save_cron_tally(tally)
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
        if mode == "off" or not verdicts:
            return None
        if mode == "issues" and all(v.status == "BACKED" for v in verdicts):
            return None
        line = engine.summary_line(verdicts)
        return f"{response_text.rstrip()}\n\n{line}" if line else None
    except Exception:
        logger.debug("check-please transform failed", exc_info=True)
        return None


def on_pre_verify(session_id: Any = None, coding: bool = False, attempt: int = 0,
                   changed_paths: Any = None, **kwargs: Any) -> Any:
    """Backlog item 9: nudge the agent to rerun tests if the verify gate fires on edited code
    while the session's last known test pass predates a later edit with no rerun since.
    Scoped to coding sessions, one-shot per turn (idempotent on attempt), off entirely when
    the 'verify_nudge' config is disabled."""
    try:
        if not bool(_settings.get("verify_nudge", True)):
            return None
        if attempt or not coding:
            return None
        sid = _session(session_id)
        rec = _cross_record(sid)
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
        sid = _session(session_id) or _LAST_SESSION[0]
        with _LOCK:
            verdicts = _LAST.get(sid) or _LAST.get(_LAST_SESSION[0]) or []
        return engine.detail_report(verdicts)
    except Exception as exc:
        return f"Check, Please could not build the report: {exc}"


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
    ctx.register_hook("pre_llm_call", on_pre_llm_call)
    ctx.register_hook("post_tool_call", on_post_tool_call)
    ctx.register_hook("transform_llm_output", on_transform_llm_output)
    ctx.register_hook("pre_verify", on_pre_verify)
    ctx.register_command("check", handle_check,
                          description="Show which claims the tool log backs: last reply, "
                                       "'today' for the day's tally, 'jobs' for per-cron-job tally")
