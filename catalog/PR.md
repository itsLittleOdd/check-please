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

- Hooks: `pre_llm_call`, `post_tool_call`, `subagent_stop`, `transform_llm_output`,
  `pre_verify`
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
  classification, verdicts, ladder-family scoping), `__init__.py` (5 hooks,
  `/check` command, ctx.state wiring), `audit.py` (the `check-please audit`
  CLI command, read-only session replay), `plugin.yaml` (manifest, hook
  declarations, settings), `desktop/plugin.js` (pane + chip, claim labels,
  merged stale rows, worst-first order, expand on click or keyboard),
  `dashboard/manifest.json` + `dashboard/plugin_api.py` (claims/jobs routes).
- `subdir: ""`, `requires_hermes: ">=0.22.6"`, `tier: community`,
  `category: tools`, version `0.4.0` (matches `plugin.yaml`).
- No changes to Hermes core. No binaries shipped.

## How to Test

Run from the plugin repo root at the exact sha this PR pins:

1. `scripts/validate_plugin_catalog.py catalog/check-please.yaml` (Hermes
   checkout, read-only use): PASS except the remaining TODOs (repo, sha,
   image); the validator flags sha and image as format errors today (the
   repo field is a syntactically valid placeholder URL under a not-yet-
   created account, so it does not itself raise a validator error), confirmed
   2026-11-08 13:02 EST.
2. `python3 -m unittest discover -s tests`: 560 ran, 552 pass + 8 skip (skips
   are env-gated: no fastapi/ruamel.yaml in plain python3), 0 fail.
3. Hermes venv: `"$PY" -m unittest discover -s tests`: 569 ran, 567 pass + 2
   skip, 0 fail (confirmed 2026-11-08 13:02 EST).
4. `hermes plugins doctor . --ci`: OK, manifest check-please 0.4.0, 5 hooks.
5. `hermes plugins validate . --install-deps`: "Validation passed.", zero
   warning lines.
6. Isolated-install smoke (`tests/install_smoke.sh`): PASS, installed and
   enabled `check-please` at the commit this PR pins in a throwaway
   `HERMES_HOME` under `~/.hermes/cache/scratch`, doctor OK there, real
   `~/.hermes` confirmed untouched, temp dir confirmed deleted after.
7. Held-out score: see the README's Results section for the dated numbers.
   Running `holdout/score.py` directly is Arlo-only (sealed set); item 64 in
   the build loop owns publishing those numbers.

## Honest limits (also in known_issues and README)

- Heuristic lexicon, English-only. Can miss claims phrased outside the
  patterns it knows, and can miss evidence hidden in a runtime-built string,
  a webhook body, or fenced/inline code it cannot parse as text.
- Held-out precision and recall, and which claim kinds are weakest, are
  dated and sourced in the README's Results section, not restated here.
  Own test corpus scores near perfect, which proves pattern coverage of
  that corpus, not real-world accuracy.
- A delegated sub-agent's claims are judged only by that child's own tool
  calls, kept separate from the parent session's evidence.
- Desktop pane and chip pass a Node-driven render harness plus the doctor
  desktop check. Expand on click or keyboard shows the proof. Not yet run
  inside a real Electron Desktop window on the build machine.

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
