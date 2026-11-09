"""Replay one recorded Hermes session through Check, Please, on screen.

Lives under tests/ (not shipped plugin code), like tests/demo_hermes.py. Reads a session that
a real agent already ran from a profile's state.db (read-only), then feeds the recorded tool
calls and the recorded final reply through the real Hermes hook path (PluginManager.
discover_and_load, invoke_hook) in a temp HERMES_HOME. Nothing is re-run: no model, no tools.
The words, commands and results on screen are the ones the agent produced; the only change is
display-only shortening of long local paths.

Prints the user's request, each tool call with its real result, the agent's real reply with
the Check line the plugin appends today, then the /check receipt for that turn.

Usage (Hermes dev venv):
  PY=$(ls ~/.hermes/installs/*/environments/*/venv/bin/python | head -1)
  "$PY" tests/replay_session.py --db ~/.hermes/profiles/cp-bench/state.db --session <id> \
      [--root <sandbox dir to show as a short name>] [--label <short name>] [--pace 0.35]

Tool status reconstruction: state.db does not store the status the live hook saw. A result
with "Blocked in this environment" is replayed as status "blocked", any other non-empty
"error" as "error", everything else as "success".
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import textwrap
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HERMES_AGENT_HOME = Path(os.environ.get("HERMES_AGENT_HOME", str(Path.home() / ".hermes" / "hermes-agent")))

DIM, BOLD, RED, GREEN, YELLOW, CYAN, RESET = ("\033[2m", "\033[1m", "\033[31m", "\033[32m",
                                              "\033[33m", "\033[36m", "\033[0m")


def load_session(db: Path, session_id: str):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = con.execute("select role, content, tool_calls, tool_call_id, timestamp from messages "
                       "where session_id=? order by id", (session_id,)).fetchall()
    con.close()
    user, reply, started = None, None, None
    calls: dict[str, dict] = {}
    order: list[str] = []
    for role, content, tool_calls, tool_call_id, ts in rows:
        if role == "user" and user is None:
            user, started = content, ts
        elif role == "assistant" and tool_calls:
            for t in json.loads(tool_calls):
                f = t.get("function") or {}
                cid = t.get("id") or t.get("call_id")
                args = f.get("arguments")
                try:
                    args = json.loads(args) if isinstance(args, str) else args
                except ValueError:
                    pass
                calls[cid] = {"name": f.get("name"), "args": args, "result": None}
                order.append(cid)
        elif role == "tool" and tool_call_id in calls:
            calls[tool_call_id]["result"] = content
        elif role == "assistant" and content:
            reply = content
    # The stored reply already carries the Check line the plugin added live (an older build).
    # Strip it so today's plugin starts from the agent's own words.
    if reply:
        head, sep, tail = reply.rstrip().rpartition("\n\n")
        if sep and tail.startswith("Check:") and tail.endswith("/check for detail."):
            reply = head
    return user, [calls[c] for c in order], reply, started


def status_of(result) -> str:
    try:
        data = json.loads(result) if isinstance(result, str) else (result or {})
    except ValueError:
        return "success"
    err = data.get("error") if isinstance(data, dict) else None
    if err and "Blocked in this environment" in str(err):
        return "blocked"
    return "error" if err else "success"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--session", required=True)
    ap.add_argument("--root", default="")
    ap.add_argument("--label", default="")
    ap.add_argument("--pace", type=float, default=0.35)
    ap.add_argument("--header", default="")
    ap.add_argument("--rich", type=int, default=0, help="render the reply's markdown at this width")
    ap.add_argument("--width", type=int, default=92, help="max width of one tool-call line")
    ap.add_argument("--wrap", type=int, default=100, help="word-wrap width for the header and request")
    a = ap.parse_args()

    def show(s: str) -> str:
        s = str(s or "")
        if a.root:
            s = s.replace(a.root, a.label or "~/project")
        return s.replace(str(Path.home()), "~")

    sys.path.insert(0, str(HERMES_AGENT_HOME))
    from hermes_cli import plugins  # read-only use, same seam as tests/demo_hermes.py

    user, calls, reply, started = load_session(Path(a.db).expanduser(), a.session)
    if not reply:
        print("session has no final reply", file=sys.stderr)
        return 1

    home = Path(tempfile.mkdtemp(prefix="check-please-replay-"))
    old_home = os.environ.get("HERMES_HOME")
    try:
        pdir = home / "plugins" / "check-please"
        pdir.mkdir(parents=True)
        for name in ("plugin.yaml", "__init__.py", "engine.py"):
            shutil.copy(REPO_ROOT / name, pdir / name)
        (home / "config.yaml").write_text(
            "plugins:\n  enabled:\n    - check-please\n  entries:\n    check-please:\n"
            "      settings:\n        line: issues\n", encoding="utf-8")
        os.environ["HERMES_HOME"] = str(home)
        plugins._reset_plugin_managers_for_tests()
        mgr = plugins.get_plugin_manager()
        mgr.discover_and_load(force=True)
        sid = "replay-" + a.session[-8:]

        if a.header:
            print(f"{DIM}{textwrap.fill(a.header, a.wrap)}{RESET}\n")
            time.sleep(a.pace * 6)
        you = textwrap.fill("You: " + show(user or ""), a.wrap)
        print(f"{BOLD}You:{RESET}{you[4:]}\n")
        time.sleep(a.pace * 6)
        mgr.invoke_hook("pre_llm_call", session_id=sid, turn_id="turn-1")
        for i, c in enumerate(calls, 1):
            st = status_of(c["result"])
            args = c["args"]
            what = args.get("command") or args.get("path") if isinstance(args, dict) else args
            first = show(str(what or "")).strip().splitlines()[0] if what else ""
            mark = f"{GREEN}ok{RESET}" if st == "success" else f"{RED}{st}{RESET}"
            try:
                rd = json.loads(c["result"]) if isinstance(c["result"], str) else {}
                ec = rd.get("exit_code") if isinstance(rd, dict) else None
            except ValueError:
                ec = None
            if ec not in (None, 0) and st == "success":
                mark = f"{RED}exit {ec}{RESET}"
            print(f"{DIM}{i:>2}{RESET} {CYAN}{c['name']:<9}{RESET} {textwrap.shorten(first, a.width, placeholder=' ...')}  {mark}")
            mgr.invoke_hook("post_tool_call", tool_name=c["name"], args=args, result=c["result"],
                            status=st, session_id=sid)
            time.sleep(a.pace)
        out = mgr.invoke_hook("transform_llm_output", response_text=reply, session_id=sid)
        text = next((r for r in out if isinstance(r, str)), reply)
        body, _, line = text.rpartition("\n\n") if text != reply else (text, "", "")
        print(f"\n{BOLD}Hermes:{RESET}")
        if a.rich:
            from rich.console import Console
            from rich.markdown import Markdown
            Console(width=a.rich, force_terminal=True).print(Markdown(show(body)))
            print()
        else:
            print(f"{show(body)}\n")
        time.sleep(a.pace * 4)
        if line:
            print(f"{YELLOW}{BOLD}{show(line)}{RESET}\n")
        time.sleep(a.pace * 8)
        entry = mgr._plugin_commands.get("check") or {}
        if entry.get("handler"):
            print(f"{BOLD}> /check{RESET}")  # what the user types next
            time.sleep(a.pace * 3)
            print(show(entry["handler"](raw_args="", session_id=sid)))
        return 0
    finally:
        plugins._reset_plugin_managers_for_tests()
        if old_home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = old_home
        shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
