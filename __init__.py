"""Check, Please: Hermes plugin registration.

Hooks (all observe-only except the cosmetic reply transform):
  pre_llm_call          -> start a fresh evidence ledger for this session's turn
  post_tool_call        -> record what each tool call proves (engine.classify_call)
  transform_llm_output  -> append one "Check:" line under replies that make completion claims
Slash command:
  /check                -> full per-claim detail for the last checked reply

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


def _session(sid: Any) -> str:
    return str(sid or "")


def _reset_turn(sid: str, turn: Any = None) -> None:
    with _LOCK:
        _LEDGER[sid] = {"turn": str(turn or ""), "seq": 0, "evidence": []}
        if len(_LEDGER) > _MAX_SESSIONS:
            for old in list(_LEDGER)[: len(_LEDGER) - _MAX_SESSIONS]:
                _LEDGER.pop(old, None)


def on_pre_llm_call(session_id: Any = None, turn_id: Any = None, **kwargs: Any) -> None:
    try:
        _reset_turn(_session(session_id), turn_id)
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
        with _LOCK:
            _LAST[sid] = verdicts
            _LAST_SESSION[0] = sid
        if mode == "off" or not verdicts:
            return None
        if mode == "issues" and all(v.status == "BACKED" for v in verdicts):
            return None
        line = engine.summary_line(verdicts)
        return f"{response_text.rstrip()}\n\n{line}" if line else None
    except Exception:
        logger.debug("check-please transform failed", exc_info=True)
        return None


def handle_check(raw_args: str = "", session_id: Any = None, **kwargs: Any) -> str:
    try:
        sid = _session(session_id) or _LAST_SESSION[0]
        with _LOCK:
            verdicts = _LAST.get(sid) or _LAST.get(_LAST_SESSION[0]) or []
        return engine.detail_report(verdicts)
    except Exception as exc:
        return f"Check, Please could not build the report: {exc}"


def register(ctx) -> None:
    try:
        _settings["line"] = ctx.get_config("line", default="all")
    except Exception:
        pass
    ctx.register_hook("pre_llm_call", on_pre_llm_call)
    ctx.register_hook("post_tool_call", on_post_tool_call)
    ctx.register_hook("transform_llm_output", on_transform_llm_output)
    ctx.register_command("check", handle_check, description="Show which claims in the last reply the tool log backs")
