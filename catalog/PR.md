# Catalog PR: Check, Please

STATUS: READY FOR JUSTIN'S REVIEW. Not opened. Opening it means forking
NousResearch/hermes-agent under itsLittleOdd, adding one file
(plugin-catalog/check-please.yaml, the copy in this folder) on a branch, and
using the text below as the PR body. Arlo opens it only after Justin approves
this exact text.

Checks at the pinned commit PIN_SHA (Arlo, 2026-11-09 evening):
- `scripts/validate_plugin_catalog.py` (Hermes origin/main 4f6ebb9afe): OK, 1 file valid.
- Fresh public clone at the pinned sha, `hermes plugins validate <clone> --install-deps` (Hermes 0.23.0): Validation passed.
- Clean-home install with no GitHub login: `hermes plugins install itsLittleOdd/check-please --enable` installs, enables, adds no line to an honest "tests pass", flags a stale one.

---

## Title

Add check-please (community, tools): pairs a "done" claim with the tool call that proves it

## Body

**What it does**

Agents say "done", "sent", "deployed" or "tests pass" in the reply, and you usually can't see the 20 commands behind it. Check, Please reads the reply against the turn's real tool calls and adds one line under it when a claim doesn't hold up:

- **stale**: the tests passed, then the code changed and nothing reran them
- **failed**: the matching call ran but errored, bounced or was blocked
- **proof edited**: the check passed only after the agent changed the thing it was being checked by (a test, a hook, an approval record), or the "send" ran through a program the agent rewrote this session

An honest reply gets no line by default (`line: issues`). It never edits the agent's words. `/check` shows the receipt for every claim: which call backs it, or why not. A Desktop pane and status-bar chip show the same for the focused session. On coding turns it can send the agent back once to recheck a contradicted claim before the reply is delivered (`catch_before_send`), and on an unattended cron run it puts the worst flagged claim at the top of the report (`cron_guard`).

The match is plain code: no second model, no network.

Demo (under 90 s): [X POST LINK]

**Hermes surfaces used**

- Hooks: `post_tool_call` (observe), `subagent_stop` (observe), `pre_llm_call` (observe, plus an optional one-line context note), `transform_llm_output` (appends one line), `pre_verify` (observe, plus an optional continue directive)
- `ctx.register_command("check")`: `/check`, `/check today`, `/check jobs`, `/check subagents`
- `ctx.register_cli_command`: `hermes check-please audit` (read-only replay of stored sessions)
- Plugin state (`ctx.state`) for the session record, cross-turn checks and the cron tally, all size-capped
- `ctx.current_cron_execution()` to tag a cron run's claims with its job
- Desktop SDK: one pane and one status-bar chip, reading through the plugin's own dashboard route (`GET /claims`, `GET /jobs`)
- No tools, no middleware, no env vars, no credentials

**Disclosures (rule 13)**

- No network calls, no model calls, no shell commands, no background processes, no telemetry.
- Reads only the tool calls and replies Hermes passes to its hooks.
- Stores, in its own plugin state file (owner-readable, capped and trimmed): per session, the newest successful call of each kind (tool name plus the command's first line, up to 150 characters, or the file path); flagged claim sentences; per day for 14 days, claim counts and up to 30 non-backed claim sentences.
- `catch_before_send` (default on) can add one agent turn on a coding turn; `next_turn_nudge` (default on) can add one line to the next turn's context. Both are settings, and both cost tokens when they fire.
- The Desktop pane polls the plugin's own route every 4 seconds while a session is focused.

**Testing**

- Unit tests in plain `python3` and in the Hermes venv: both OK (the 7 expected failures are recorded known gaps).
- `hermes plugins validate . --install-deps`: passed at the pinned sha.
- Results are dated and sourced in the README. In short: 0 lines added to 32 honest replies in a sealed test written after the plugin was frozen; on about 3,300 real past turns it adds a line to about 2.4 replies per 100. Part of the earlier test bench was used for tuning, so catch counts there are not real-world rates.

**Limits** (also in `known_issues`)

English-only heuristic patterns; it can't see webhook bodies, runtime-built strings, or writes made inside a script body. A claim with no receipt is common and often honest, so it stays in `/check` by default. Developed and tested on macOS; Linux and Windows untested.

**Non-affiliation**

Submitted by Justin Whalen (Whalen Ventures), the maintainer. Not affiliated with Nous Research. Built for the Hermes Agent Plugin Hackathon.
