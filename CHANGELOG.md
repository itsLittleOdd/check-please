# Changelog

All notable changes to Check, Please are logged here. Versions follow the
project's own pace, not strict semver, while the plugin is pre-catalog.

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
