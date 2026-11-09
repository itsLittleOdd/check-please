#!/usr/bin/env python3
"""Same-snapshot A/B replay of real session history through the plugin's real hooks.

Lives under tests/ (not shipped plugin code), like tests/replay_session.py. Added by the
Fable 5.2 review because the external replay scorer (replay/score_replay.py) has two limits:

1. It calls engine.classify_call + engine.check_turn once per turn, so it never runs what
   __init__.py adds on a live install: the cross-turn STALE upgrade, "earlier this session"
   backing, and contradictions carried across turns. Its inline_alerts_per_100_turns is
   therefore lower than the line rate a user actually sees.
2. The session stores it reads are live. Their turn count moved between two back-to-back runs
   during the review, so a before/after pair taken minutes apart is not comparable.

This script reads every turn ONCE (read-only) and feeds the identical turns to each repo given
on the command line, printing for each:
  ENGINE  the same computation score_replay.py does
  HOOKS   the same turns through on_pre_llm_call / on_post_tool_call /
          on_transform_llm_output with default settings (what a user would have seen),
          plus how many claims were flagged in one turn and then backed "earlier this
          session" in a later one with no new evidence

Aggregates only, by design: it never prints or writes reply text, commands or paths.
Usage: python3 tests/replay_hooks_ab.py <label>=<repo path> [<label>=<repo path> ...]
       python3 tests/replay_hooks_ab.py before=/path/to/old-checkout after=.
"""
import collections
import importlib.util
import io
import json
import os
import sqlite3
import sys
from contextlib import redirect_stdout

HOME = os.path.expanduser("~/.hermes")
DBS = [("default", f"{HOME}/state.db"), ("wren", f"{HOME}/profiles/wren/state.db"),
       ("hermes2", f"{HOME}/profiles/hermes2/state.db")]


def load(label, repo):
    name = f"_cp_ab_{label.replace('-', '_')}"
    repo = os.path.abspath(repo)
    spec = importlib.util.spec_from_file_location(name, os.path.join(repo, "__init__.py"),
                                                  submodule_search_locations=[repo])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def status_of(result):
    try:
        data = json.loads(result) if isinstance(result, str) else (result or {})
    except ValueError:
        return "ok"
    err = data.get("error") if isinstance(data, dict) else None
    return "error" if err else "ok"


def events():
    """Yield ('user'|'call'|'reply', session_key, payload) in stored order, each DB in one read."""
    for label, db in DBS:
        if not os.path.exists(db):
            continue
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        rows = con.execute("select session_id, role, content, tool_calls, tool_call_id from messages "
                           "where coalesce(active,1)=1 order by session_id, id").fetchall()
        con.close()
        cur_sid, pending, active = None, {}, False
        for sid, role, content, tool_calls, tcid in rows:
            key = f"{label}:{sid}"
            if key != cur_sid:
                cur_sid, pending, active = key, {}, False
            if role == "user":
                pending, active = {}, True
                yield "user", key, None
                continue
            if not active:
                continue
            if role == "assistant" and tool_calls:
                try:
                    for c in json.loads(tool_calls):
                        fn = c.get("function") or {}
                        args = fn.get("arguments")
                        try:
                            args = json.loads(args) if isinstance(args, str) else args
                        except ValueError:
                            pass
                        pending[c.get("id") or c.get("call_id")] = (fn.get("name") or c.get("name") or "", args)
                except (ValueError, TypeError, AttributeError):
                    pass
            elif role == "tool" and tcid in pending:
                name, args = pending[tcid]
                yield "call", key, (name, args, content)
            elif role == "assistant" and not tool_calls and content:
                yield "reply", key, content
                pending = {}


repos = [a.split("=", 1) for a in sys.argv[1:] if "=" in a]
if not repos:
    raise SystemExit(__doc__)
mods = {label: load(label, repo) for label, repo in repos}
tot = {label: collections.Counter() for label in mods}
ledger = {label: [] for label in mods}        # ENGINE path: this turn's evidence, like score_replay
tainted = {label: {} for label in mods}       # session -> kind -> last contradicted status
turn_no = collections.Counter()
for kind, key, payload in events():
    for label, mod in mods.items():
        eng, t = mod.engine, tot[label]
        if kind == "user":
            ledger[label] = []
            mod.on_pre_llm_call(session_id=key, turn_id=f"t{turn_no[key]}")
        elif kind == "call":
            name, args, result = payload
            with redirect_stdout(io.StringIO()):
                e = eng.classify_call(name, args, result, None, seq=len(ledger[label]) + 1)
            ledger[label].append(e)
            mod.on_post_tool_call(tool_name=name, args=args, result=result, session_id=key,
                                  status=status_of(result))
        else:
            ev = []
            for i, e in enumerate(ledger[label], 1):   # same seq numbering as score_replay
                if e is not None:
                    e.seq = i
                    ev.append(e)
            with redirect_stdout(io.StringIO()):
                vs = eng.check_turn(payload, ev)
            t["turns"] += 1
            t["claims"] += len(vs)
            for v in vs:
                t[v.status] += 1
                t["e_inline"] += int(bool(eng.inline_worthy(v)))
            out = mod.on_transform_llm_output(response_text=payload, session_id=key)
            hv = list(mod._LAST.get(key, []))
            t["h_line"] += int(isinstance(out, str))
            taint = tainted[label].setdefault(key, {})
            for v in hv:
                t["h_inline"] += int(bool(eng.inline_worthy(v)))
                t[f"h_{v.status}"] += 1
                note = v.note or ""
                if eng.inline_worthy(v):
                    t["h_carried"] += int("nothing has re-proved it since" in note)
                    t["h_xfail"] += int("nothing has passed since" in note)
                    t["h_xstale"] += int("no rerun this turn" in note)
                if v.status == "BACKED" and note == "earlier this session":
                    t["h_earlier"] += 1
                    t["h_laundered"] += int(v.claim.kind in taint)
                if v.status in ("STALE", "FAILED", "EDITED", "PARTIAL"):
                    taint[v.claim.kind] = v.status
                elif v.status == "BACKED" and note != "earlier this session":
                    taint.pop(v.claim.kind, None)
            ledger[label] = []
    if kind == "user":
        turn_no[key] += 1

for label, t in tot.items():
    n = t["turns"] or 1
    flags = t["STALE"] + t["FAILED"] + t["NOT_BACKED"]
    print(f"[{label}] ENGINE turns={t['turns']} claims={t['claims']} backed={t['BACKED']} "
          f"flags_per_100={100 * flags / n:.1f} inline_alerts_per_100_turns={100 * t['e_inline'] / n:.2f} "
          f"(inline={t['e_inline']}: stale={t['STALE']} failed={t['FAILED']} edited={t['EDITED']} partial={t['PARTIAL']})")
    print(f"[{label}] HOOKS  inline_alerts_per_100_turns={100 * t['h_inline'] / n:.2f} "
          f"replies_with_line_per_100={100 * t['h_line'] / n:.2f} "
          f"(inline={t['h_inline']}: stale={t['h_STALE']} failed={t['h_FAILED']} edited={t['h_EDITED']} "
          f"partial={t['h_PARTIAL']}; backed={t['h_BACKED']}, of which 'earlier this session'={t['h_earlier']})")
    print(f"[{label}] HOOKS  cross-turn: stale-upgrades={t['h_xstale']} failed-since={t['h_xfail']} "
          f"carried-contradictions={t['h_carried']} | laundered (flagged earlier, then BACKED 'earlier this "
          f"session' with no new evidence)={t['h_laundered']}")
