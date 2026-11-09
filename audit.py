"""Read-only session history audit for Check, Please (backlog item 28).

``hermes check-please audit`` replays the user's own saved sessions, read-only, through the
same engine every live turn already uses, and prints a summary: how many turns had completion
claims, how many were backed, and the newest not-backed claims with session id and date.

Nothing here writes to state.db, sends anything, or leaves the local machine. ``hermes_state``
is only imported inside ``run_audit()`` (lazy) so this module still collects fine under plain
``python3 -m unittest`` on a host with no Hermes install; only the real CLI invocation reaches
that import.
"""
from __future__ import annotations

import json
from typing import Any, Iterable, Iterator

try:  # package context (real Hermes plugin load, same style as __init__.py)
    from . import engine
except ImportError:  # flat sys.path import (plain `python3 -m unittest`, same as tests/test_engine.py)
    import engine  # type: ignore[no-redef]


def _unwrap_bridge_call(name: Any, args: Any) -> tuple:
    """Fable 5.2 review: a deferred tool reached through Hermes' Tool Search bridge is saved in
    the session store as a call to `tool_call` with {"calls": [{"name", "arguments"}]}. The
    live post_tool_call hook sees the real tool (model_tools unwraps the bridge before any
    hook fires); a replay of the store did not, so a send or a cron job made through the
    bridge read as no evidence and its claim as NOT BACKED. A single-entry bridge call is one
    dispatch with one result, so it is replayed as the tool it named. A multi-entry
    (connector batch) call has one combined result and is left alone."""
    if name != "tool_call" or not isinstance(args, dict):
        return name, args
    entries = args.get("calls")
    if not isinstance(entries, list) or len(entries) != 1 or not isinstance(entries[0], dict):
        return name, args
    inner_args = entries[0].get("arguments")
    if isinstance(inner_args, str):
        try:
            inner_args = json.loads(inner_args)
        except ValueError:
            pass
    return str(entries[0].get("name") or name), inner_args


def _iter_turns(messages: Iterable[dict]) -> Iterator[tuple[dict, list]]:
    """Yield (final assistant message, evidence list) per turn, same grouping rule as the
    proven ``replay/score_replay.py``: a user message starts a turn; an assistant message with
    tool_calls records pending calls; a tool message fills in that call's result; an assistant
    message with no tool_calls and real content ends the turn and is the one checked."""
    calls: dict[Any, list] = {}
    order: list[Any] = []
    active = False
    for msg in messages:
        role = msg.get("role")
        if role == "user":
            calls, order, active = {}, [], True
            continue
        if not active:
            continue
        tool_calls = msg.get("tool_calls")
        if role == "assistant" and tool_calls:
            for c in tool_calls:
                fn = c.get("function") or {}
                cid = c.get("id") or c.get("call_id")
                calls[cid] = [fn.get("name") or c.get("name") or "", fn.get("arguments"), None]
                order.append(cid)
        elif role == "tool" and msg.get("tool_call_id") in calls:
            calls[msg["tool_call_id"]][2] = msg.get("content")
        elif role == "assistant" and not tool_calls and msg.get("content"):
            ev = []
            for i, cid in enumerate(order, 1):
                name, args, result = calls.get(cid, ("", None, None))
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        pass
                name, args = _unwrap_bridge_call(name, args)
                e = engine.classify_call(name, args, result, None, seq=i)
                if e:
                    ev.append(e)
            yield msg, ev
            calls, order = {}, []


_DATE_KEYS = ("created_at", "timestamp", "ts", "updated_at")


def _message_date(msg: dict) -> str:
    for key in _DATE_KEYS:
        value = msg.get(key)
        if value:
            return str(value)
    return ""


def audit_session(session_id: str, messages: Iterable[dict]) -> list[dict]:
    """Per-turn verdict rows for one session's own saved messages. Read-only: never calls
    anything that mutates the session. Returns only turns that made at least one claim."""
    rows: list[dict] = []
    for msg, ev in _iter_turns(messages):
        verdicts = engine.check_turn(str(msg.get("content") or ""), ev)
        if verdicts:
            rows.append({"session_id": session_id, "date": _message_date(msg), "verdicts": verdicts})
    return rows


def _session_id(row: dict) -> str:
    return str(row.get("id") or row.get("session_id") or "")


def _session_last_active(row: dict) -> Any:
    for key in ("last_active", "_effective_last_active", "updated_at", "created_at"):
        if row.get(key) is not None:
            return row[key]
    return None


def run_audit(db_path, *, since_days: "int | None" = None, limit: int = 20,
               session_scan_limit: int = 500) -> str:
    """Open *db_path* read-only via ``hermes_state.SessionDB`` (documented session store),
    replay up to *session_scan_limit* of the most recently active sessions through the engine,
    and return the printed summary. Never writes to the database. Any read failure (missing
    file, locked db, unexpected row shape) is reported in the text, never raised to the CLI."""
    import time
    from hermes_state import SessionDB

    db = SessionDB(db_path, read_only=True)
    try:
        try:
            sessions = db.list_sessions_rich(limit=session_scan_limit, order_by_last_active=True)
        except Exception as exc:
            return f"Check, Please audit could not list sessions in {db_path}: {exc}"
        cutoff = time.time() - since_days * 86400 if since_days else None
        rows: list[dict] = []
        for s in sessions:
            sid = _session_id(s)
            if not sid:
                continue
            if cutoff is not None:
                last_active = _session_last_active(s)
                if isinstance(last_active, (int, float)) and last_active < cutoff:
                    continue
            try:
                messages = db.get_messages(sid)
            except Exception:
                continue
            rows.extend(audit_session(sid, messages))
        return engine.audit_report(rows, limit=limit)
    finally:
        try:
            db.close()
        except Exception:
            pass
