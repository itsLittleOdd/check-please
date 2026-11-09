# Changelog

All notable changes to Check, Please are logged here. Versions follow the
project's own pace, not strict semver, while the plugin is pre-catalog.

## 0.4.2 - 2026-11-09

A correctness pass from an independent review by Claude Fable 5.2, measured on the
same gates as every other change: no new false alarms on the graded bench, holdout
unchanged, about a third fewer inline alerts on real history.

- Fixed: `/check` only ever shows the asking chat's own claims. On a gateway it
  could fall back to another chat's last reply.
- Fixed: the README now says what the plugin stores on disk (`state.json`, a
  14-day claim log) and lists every hook and verdict.
- Fixed: a flagged claim stays flagged when the agent repeats it next turn with
  nothing new.
- Fixed: chained commands (`pytest && git push`) credit only the stages that ran;
  a chain that died at the tests is a failed test run.
- Fixed: a reply that states the failing run's own failure count is reporting it,
  not overclaiming.
- Fixed: read-only commands (grep, cat, echo) are never test runs; a `>` inside
  quotes or a heredoc is not a file write; V4A patches name their files.
- Fixed: target matching no longer calls true claims mismatches ("Pushed to
  `main`", "Emailed Dana" with dana.smith@).
- Fixed: the Desktop pane labels FAILED, PROOF EDITED and PARTIAL RUN, and a
  flagged row shows why.
- Fixed: session evidence keeps one row per kind, so the state file stays small;
  only the plugin's own lines are skipped when reading claims; `audit` unwraps
  Tool Search bridge calls.
- Six known gaps are recorded as expected-failure tests (dry runs, writes made
  inside a script body, and others) for after the hackathon.

## 0.4.1 - 2026-11-09

- The check line now defaults to `issues`: an honest reply gets no line, and a
  stale, failed or edited claim still gets one. `line: all` brings back the quiet
  "Check: N backed" line under every reply with claims. `/check` is unchanged.
- Fixed: an honest "1 failed, 1 passed" report no longer reads as a stale pass;
  a patched file's diff is no longer read as failure output; failed-call reasons
  read in plain words; `/check` quotes claims without markdown markers.

## 0.4.0 - 2026-11-08

Receipts, not a lie detector: a new verdict family, catch-before-send, subagents,
cron honesty, and a long real-world false-alarm hardening pass (Day 5 to the freeze).

- Added two new verdicts. EDITED ("proof edited") fires when a check passed only
  after the agent changed the test, script, hook, gate data file, or approval
  record it was being checked by; a claim resting on a lower-rung claim that is
  itself EDITED inherits EDITED. Disclosing the edit in the reply clears it to
  BACKED with a note. PARTIAL fires when a whole-suite claim is backed only by a
  narrowed run (a single file, `-k`, a module) after an earlier failure in the
  same turn; a clean single-shot targeted run with no earlier failure stays
  BACKED.
- Settled the surface policy: the plugin pitches itself as receipts, not a lie
  detector. STALE, FAILED and EDITED verdicts go inline by default; a claim with
  no backing call at all is listed in `/check` only, not inline, unless the new
  `inline_unbacked` setting turns that back on for sent, paid, deployed, pushed,
  deleted and scheduled claims.
- Added catch before send: `pre_verify` now checks the draft reply itself against
  this turn's own evidence, not only cross-turn staleness, and returns a plain
  two-sentence directive naming the claim and the fix before a stale or failed
  claim reaches the user. A `next_turn_nudge` fallback covers non-coding turns
  that `pre_verify` does not fire on. Both are settings, default on.
- Added the subagent claim check: a parent session's relayed claims about a
  child's work are now backed only by what that child actually did, never mixed
  across children; `/check subagents` lists any unresolved child claim.
- Added the cron honesty guard: an unattended cron reply gets its worst
  inline-worthy claim prepended as a one-line check at the top of the delivered
  output. A `[SILENT]` or empty reply is never touched by this, by
  `transform_llm_output`, or by either nudge.
- Added failure-result detection: a tool call that ran but failed (nonzero exit,
  a failure count above zero, a traceback, an HTTP 4xx/5xx status, "0 passed")
  now backs nothing and the matching claim reads FAILED with a short, specific
  reason instead of a generic one.
- Added target matching: a claim naming a person, branch, environment or file is
  compared against the backing call's own target; a mismatch reads NOT BACKED
  with the actual target named ("deployed to staging, not prod").
- Added `check-please audit`, a CLI command that replays your own saved session
  history read-only and prints a summary of claims checked, backed and the top
  not-backed ones.
- Finished the Desktop pane: short claim labels instead of mid-sentence
  fragments, repeated STALE rows for the same run merged into one row with a
  count, a header tally, worst-verdict-first ordering, click or keyboard expand
  to see the proof or the flagged reason, and a receipt-style layout with a
  dashed rule above the total.
- Spent most of this cycle cutting false alarms against real saved Hermes turns
  and a small overclaim bench: adjective and noun uses that are not an action
  (paid plans, saved passwords, scheduled jobs), third-party subjects and
  reported speech, data and report lines, mentions inside quotes or code, chained
  shell commands (`&&`, `;`, `||`, a single pipe), partial test-pass counts, a
  weak failure-text marker no longer overriding an explicit zero exit code plus
  an explicit pass summary, an honest same-reply admission of a different
  failure no longer flipping an unrelated claim to FAILED, and an agent's own
  new test (written before it ever failed) no longer reading as tampering while
  a renamed or rewritten existing assertion still does.
- Widened evidence coverage: a project's own deploy and mail tools by path, a
  shell write (`cp`, `sed -i`, redirects, `tee`) counting as an edit just like a
  file write, a `crontab -l` read never backing a schedule claim, a check
  program named plainly `ci` now counting as tested evidence, and chain-credit
  rules so a failed second stage after a real first-stage pass does not
  overclaim and a real failure is not missed either.
- Widened claim extraction for answer-style replies: a contracted "it's live",
  a leading "Confirmed:" label, a coordinated "pushed and live" sentence now
  reading as both a pushed and a deployed claim, verification language
  ("passed all checks", "is verified and restorable", "CI pipeline is green"),
  and an approval record written by hand counting the same way an edited test
  does.
- Honest limits, stated plainly rather than fixed: the lexicon is closed and
  English only; a bypass performed through a tool's own escape hatch, a
  recipient list restored under a written hold, and a script that prints "done"
  and changes nothing all leave no signal this plugin can judge.

## 0.3.0 - 2026-11-05

Desktop pane and the data path behind it (Day 3 to 4 work).

- Added `desktop/plugin.js`: an area pane (claims as line items, BACKED/STALE/NOT BACKED
  labels, never color alone) and a status-bar chip with the session tally.
- Added `dashboard/plugin_api.py` and `dashboard/manifest.json`: `GET /claims` and
  `GET /jobs` routes backed by `ctx.state` through `PluginState`, so the pane reads the
  same record the hooks write, not a file read from the renderer.
- Added `engine.verdict_to_dict` / `claims_payload()` for a stable JSON shape between
  the hooks and the pane.
- Accessibility pass: fixed a real narrow-width truncation bug (missing `min-w-0`/`flex-1`
  in a flex row). Verified by source read, not a live Electron render; no Electron host
  is available on this build machine.
- Added `demo/run_demo.py`: drives the 3-turn storyboard through a real PluginManager in
  a temp HERMES_HOME and prints the actual Check line and `/check` report.
- Found and logged, not yet fixed: `proven_rung` picks the turn's single highest evidence
  kind globally instead of per claim family (Discovered item 18).
- README gained Screenshots (named placeholders, none captured) and a Limits section
  stated honestly (heuristic lexicon, English only, held-out numbers, no proof-on-click).
- Drafted `catalog/check-please.yaml` and `catalog/PR.md`. Not submitted; repo and sha
  left as TODO because no public repo exists yet.
- Added `tests/test_performance.py`: per-hook median under 2ms on a 200-call turn,
  1000-input fuzz with zero exceptions, and bounded-memory checks for every trimmed
  state structure.

## 0.2.0 - 2026-11-05

Trust and depth work (Day 1 to 3): real-hook proof, a labeled corpus, cross-turn memory,
cron awareness, and the `/check` command family. Items 1 through 9 of the backlog.

- Proved the three original hooks fire inside a real Hermes turn through PluginManager
  (`tests/test_hermes_integration.py`), run under Hermes' own venv.
- Built a 167-row labeled corpus (`tests/corpus/replies.jsonl`) and `tests/measure.py`;
  own-corpus precision/recall reached 1.000/1.000 after the precision push (item 3).
- Extended evidence coverage to Hermes' real tool families: `execute_code`, `browser_exec`,
  `delegate_task` (no evidence), MCP and connector naming.
- Confirmed `call_succeeded` against the actual `post_tool_call` result shapes for
  terminal, write_file, patch, and send_message, with source-cited tests.
- Added cross-turn stale detection (tests pass, then an edit, then no rerun), cron-run
  tagging and a per-job tally, and `/check`, `/check today`, `/check jobs`.
- Added a `pre_verify` nudge that asks the agent to rerun tests before claiming they pass,
  scoped to coding sessions, with a config switch to disable it.
- Built an independent 150-row held-out set (not written by this loop) and a read-only
  `score.py`. Held-out best as of this release: precision 0.905, recall 0.738. Recall is
  below the 0.85 target the loop is chasing (item 4b), concentrated in the `tested` and
  `scheduled` claim kinds; escalated to Justin on 2026-11-05 00:33 after three failed
  attempts at closing the gap with lexical pattern additions. This gap is an honest,
  open limit of this release, not fixed in 0.2.0 or 0.3.0.

## 0.1.0 - 2026-11-04

First working version, built by hand (not the loop), to seed real work.

- `engine.py`: claim extraction, evidence classification, verdict computation.
- `__init__.py`: `pre_llm_call`, `post_tool_call`, `transform_llm_output` hooks and
  the `/check` command (last-reply report only).
- `plugin.yaml`, `README.md`, `LICENSE`.
- 18/18 unit tests passing. `hermes plugins doctor . --ci` OK. `hermes plugins validate .`
  passed, security scan clean.
