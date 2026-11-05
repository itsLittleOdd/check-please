# Catalog PR: Check, Please

STATUS: DRAFT. Not opened. No GitHub repo has been created, no remote added,
no PR filed. This file is text only, prepared for Justin to use when the repo
is public and the gates below are confirmed at the real pinned commit.

## Title

Add check-please (community, tools): pairs a "done" claim with the tool call
that proves it

## What this plugin does

Hermes agents sometimes say "done," "sent," "deployed," or "tests pass" without
the tool call to back it up. Check, Please reads each reply against the turn's
real tool calls (terminal, write_file, patch, send_message, execute_code,
cronjob_manage, MCP and connector tools) and appends one line: which claims are
backed, stale (passed earlier, then the code changed with no rerun), or not
backed. A `/check` command gives a detail report, a per-cron-job tally, and a
daily digest. Deterministic: no extra model call, no network call, stdlib only
on the engine side.

## Capabilities declared

- Hooks: pre_llm_call, post_tool_call, transform_llm_output, pre_verify
- No tools, no middleware, no env vars required
- Desktop pane (SDK surface only: panes area + status-bar chip), data served
  through ctx.state / the plugin's own dashboard route, never a renderer file
  read

## Disclosure (rule 13)

- No network calls. No reads outside the plugin's own ctx.state-backed data.
- No shell commands run by the plugin itself (it reads tool call records
  Hermes already produced; it does not execute anything).
- No credentials read, stored, or required.
- Desktop pane polls the plugin's own `/claims` route every 4 seconds while a
  session is focused; no push/socket channel yet.

## Honest limits (also in known_issues and README)

- Heuristic lexicon, English-only. Can miss claims phrased outside the patterns
  it knows, and can miss evidence hidden in a runtime-built string or a webhook
  body it cannot read.
- Held-out benchmark (150 hand-written replies by an author with no access to
  the code): precision 0.905, recall 0.738. Two kinds (tested, scheduled) are
  below a 0.70 per-kind recall floor as of this PR; see the repo's
  PROGRESS.md for the open item and the numbers behind it. Own 140+-row test
  corpus scores 1.000/1.000, which proves pattern coverage of that corpus, not
  real-world recall.
- Desktop pane has no proof-on-click yet and has never run inside a real
  Electron Desktop window; it has only been checked by reading the source and
  a 5-test suite plus a documented empty state.

## Before filing for real

- [ ] Public repo exists under Justin's GitHub account (Justin's step, not the
  loop's)
- [ ] 40-char sha pinned to the exact reviewed commit
- [ ] `capabilities:` block re-checked against `plugin.yaml` at that exact sha
- [ ] `hermes plugins validate . --install-deps` run clean at that sha
- [ ] version in catalog entry matches plugin.yaml version at that sha
