# Catalog PR: Check, Please

STATUS: DRAFT. Not opened. No GitHub repo has been created, no remote added,
no PR filed. This file is text only, prepared for Justin to use when the repo
is public and the gates below are confirmed at the real pinned commit.

## Title

Add check-please (community, tools): pairs a "done" claim with the tool call
that proves it

## What it does

Hermes agents sometimes say "done," "sent," "deployed," or "tests pass"
without the tool call to back it up. Check, Please reads each reply against
the turn's real tool calls (terminal, write_file, patch, send_message,
execute_code, cronjob_manage, browser_exec, delegate_task, MCP and connector
tools) and appends one line: which claims are backed, stale (passed earlier,
then the code changed with no rerun), or not backed. A `/check` command gives
a detail report, a per-cron-job tally, and a daily digest. A Desktop pane
shows the same verdicts as line items for the focused session. Deterministic:
no extra model call, no network call, stdlib only on the engine side.

## Public surfaces only

Every API the plugin uses, nothing else:

- Hooks: `pre_llm_call`, `post_tool_call`, `transform_llm_output`, `pre_verify`
- `ctx.state` (file-backed, plugin-scoped) for the session ledger, cross-turn
  staleness record, per-cron-job tally, and daily digest log, all with
  explicit size caps and trim logic
- `ctx.current_cron_execution()` to tag a turn's verdicts with the active
  cron job, when present
- `ctx.register_command()` for `/check` (`today`, `jobs`, or last-reply)
- Desktop SDK only: an area pane (`panes`, right, 280px) and a status-bar
  chip (`STATUSBAR_AREAS.right`), both reading data through the plugin's own
  `dashboard/plugin_api.py` route (`GET /claims`, `GET /jobs`) via
  `hermes_cli.plugins_state.PluginState`, never a renderer file read
- No tools provided, no middleware, no env vars required

## Disclosures

- No network calls. No reads outside the plugin's own `ctx.state`-backed data.
- No shell commands run by the plugin itself; it only reads tool call records
  Hermes already produced, it does not execute anything.
- No credentials read, stored, or required.
- Desktop pane polls the plugin's own `/claims` route every 4 seconds while a
  session is focused; no push/socket channel yet.
- Every stored structure (ledger, cross-turn record, cron tally, daily log)
  has an explicit cap and trims the oldest entries; none grows unbounded.

## Changes Made

- New standalone plugin: `engine.py` (claim extraction, evidence
  classification, verdicts, ladder-family scoping), `__init__.py` (4 hooks,
  `/check` command, ctx.state wiring), `plugin.yaml` (manifest, hook
  declarations, `verify_nudge` config), `desktop/plugin.js` (pane + chip),
  `dashboard/manifest.json` + `dashboard/plugin_api.py` (claims/jobs routes).
- `subdir: ""`, `requires_hermes: ">=0.22.6"`, `tier: community`,
  `category: tools`, version `0.3.0` (matches `plugin.yaml`).
- No changes to Hermes core. No binaries shipped.

## How to Test

Run from the plugin repo root at the exact sha this PR pins:

1. `scripts/validate_plugin_catalog.py catalog/check-please.yaml` (Hermes
   checkout, read-only use): PASS except the two allowed remaining TODOs
   (sha, image), confirmed 2026-11-05 13:32 EST.
2. `python3 -m unittest discover -s tests`: 91 ran, 85 pass + 6 skip (skips
   are env-gated: no fastapi/ruamel.yaml in plain python3), 0 fail.
3. Hermes venv: `"$PY" -m unittest discover -s tests`: 94/94 pass, 0 skip
   (confirmed 2026-11-05 13:32 EST).
4. `hermes plugins doctor . --ci`: OK, manifest check-please 0.3.0, 4 hooks.
5. `hermes plugins validate . --install-deps`: "Validation passed.", zero
   warning lines.
6. Isolated-install smoke (`tests/install_smoke.sh`): PASS, installed and
   enabled `check-please@e27fd5f` in a throwaway `HERMES_HOME` under
   `~/.hermes/cache/scratch`, doctor OK there, real `~/.hermes` confirmed
   untouched, temp dir confirmed deleted after. Run 2026-11-05 13:32 EST.
7. Held-out score (`holdout/score.py`, independent 150-row set, author has no
   repo access): precision 0.908, recall 0.768 (run 2026-11-05 13:32 EST).
   Two kinds below a 0.70 per-kind recall floor: scheduled (0.67), tested
   (0.46); see the repo's PROGRESS.md for the open item and the phrasing
   families still unsolved.

## Honest limits (also in known_issues and README)

- Heuristic lexicon, English-only. Can miss claims phrased outside the
  patterns it knows, and can miss evidence hidden in a runtime-built string,
  a webhook body, or fenced/inline code it cannot parse as text.
- Held-out recall (0.768) is below the 0.80 target; tested (0.46) and
  scheduled (0.67) are the weak kinds. Own 160+-row test corpus scores near
  1.000, which proves pattern coverage of that corpus, not real-world recall.
- Desktop pane has no proof-on-click yet and has never run inside a real
  Electron Desktop window; it has only been checked by reading the source,
  a Node-driven unit-level render harness, and a documented empty state.

## Owner submission and non-affiliation

Submitted by Justin Whalen (Whalen Ventures), maintainer of this plugin. Not
affiliated with and does not speak for Nous Research or the Hermes Agent
project. Built for the Hermes Agent Plugin Hackathon, entries due 2026-11-11.

## Before filing for real

- [ ] Public repo exists under Justin's GitHub account (Justin's step, not
  the loop's)
- [ ] 40-char sha pinned to the exact reviewed commit
- [ ] `capabilities:` block re-checked against `plugin.yaml` at that exact sha
- [ ] `hermes plugins validate . --install-deps` run clean at that sha
- [ ] version in catalog entry matches plugin.yaml version at that sha
