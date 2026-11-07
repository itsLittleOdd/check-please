# Check, Please

Your agent says "Tests pass, preview is live, invoice emailed to Dana."

Did it?

Check, Please pairs every "done" claim in a Hermes reply with the tool call that proves it, and adds one line under the reply:

```
Check: 1 backed, 1 stale, 1 not backed. First issue: "emailed" (no sent call this turn; proven: saved). /check for detail.
```

Type `/check` for the full breakdown:

```
Check, Please: last reply
- STALE: "Tests pass"
    proof: terminal pytest -q
    note: tests passed, then pricing.py changed with no run since
- BACKED: "preview is live"
    proof: terminal vercel deploy
- NOT BACKED: "invoice emailed to Dana"
    note: no sent call this turn; proven: saved
```

## Screenshots

_Not yet captured (no Electron host available on this build box). Placeholders for the catalog listing:_

- `screenshots/check-line.png`: a Hermes chat reply with the `Check:` line under it (one backed, one stale, one not backed).
- `screenshots/check-detail.png`: the `/check` command output showing the full breakdown with proof lines.
- `screenshots/desktop-pane.png`: the Desktop pane docked right, showing claim rows with BACKED/STALE/NOT BACKED labels.
- `screenshots/status-chip.png`: the status-bar chip for the focused session.

## Limits, stated honestly

- **Heuristic lexicon, not understanding.** Claims are matched by regex patterns over the reply text (verbs like "deployed", "pushed", "tested", "paid"). It has no model call and no semantic understanding of the sentence. A claim phrased in a way the lexicon has never seen will read as NOT BACKED even if it is true, and an unusual sentence shape can still slip past a negation check.
- **English only.** Every pattern, negation word and alias is English. Any other language passes through unrecognized (treated as having no claims, not flagged as an error).
- **What it cannot see.** It only sees this turn's tool calls and the reply text; it cannot see outside the terminal/write_file/patch/send_message/execute_code/browser_exec/cronjob/MCP/connector shapes it was built against, cannot see inside a webhook payload or a push hidden behind a runtime-built string, and treats `delegate_task` as no evidence on principle (a child agent's work is not this turn's proof).
- **Held-out accuracy.** On an independent 150-row held-out set written without access to this repo, precision is 0.905 and recall is 0.738 (`score.py`, checked 2026-11-05 10:33 EST). Tested (recall 0.35) and scheduled (recall 0.58) are the weakest categories; overall recall is still short of where we want it. The project's own development corpus (166 rows, written by this loop) is not reported here because a test suite scoring itself is not evidence of real-world accuracy; it only shows the code covers every pattern that corpus was built to test.
- **No proof-on-click yet.** The Desktop pane shows verdicts and labels but clicking a row to see the underlying proof call is not built yet.
- **Desktop pane not yet run in a real Desktop window.** The pane and chip pass a Node-based harness that executes the real `desktop/plugin.js` logic outside a browser, plus `doctor`/`validate`'s desktop surface check. Neither has run inside an actual Hermes Desktop/Electron window on this build machine, so real CSS layout, plugin-SDK subscription timing and docking behavior are unverified.

## Why a plugin and not a prompt

A prompt asks the model that made the claim to grade its own homework. Check, Please reads the tool log through Hermes hooks, outside the model, and the match is plain code. No second model, no extra tokens, no network.

## The state ladder

Saved is not sent. A claim only counts at the rung the tool log proves:

drafted, saved, tested, committed, pushed, deployed, scheduled, sent, paid

## Verdicts

- **BACKED**: a matching tool call ran this turn and succeeded.
- **STALE**: tests passed, then code changed and nothing re-ran them.
- **FAILED**: the matching call ran but errored, bounced or was blocked.
- **NOT BACKED**: no matching call this turn.

## Install

```
hermes plugins install check-please
```

## Settings

`line`: `all` (default) adds the line under every reply that makes claims, `issues` only when something is stale, failed, or (with `inline_unbacked` on) a high-stakes not-backed claim, `off` keeps it to `/check`.

`inline_unbacked`: off by default. Check, Please is a receipts tool, not a lie detector: a not-backed claim means no receipt was found, which is common and often not an overclaim at all, so by default it only shows in `/check`, never inline (the Desktop pane still lists it today; relabeling it there as "no receipt" is tracked separately). Stale (a claim contradicted by this turn's own evidence) and failed always interrupt the reply, with or without this setting. Turn `inline_unbacked` on to also show not-backed inline for the higher-stakes kinds: sent, paid, deployed, pushed, deleted, scheduled.

`verify_nudge`: on by default. On coding turns, asks the agent to rerun tests before finishing when tests last passed before a later edit with no rerun since.

`catch_before_send`: on by default. See "Catch before send" below.

`next_turn_nudge`: on by default. See "Catch before send" below.

`cron_guard`: on by default. On an unattended cron run, puts the worst inline-worthy claim (stale, failed, proof edited, or not-backed when `inline_unbacked` is also on) at the TOP of the delivered reply, one line, so a human skimming a cron digest sees it before anything else. The normal Check line still runs too, at the bottom, as usual. Never alters a reply that is exactly empty or the literal `[SILENT]` suppression token many cron jobs use to stay silent-on-green. Detected through `ctx.current_cron_execution()`; outside a cron run this setting does nothing.

## Catch before send

Two settings work together to stop a stale or failed claim before it reaches you, instead of only reporting it after the fact.

`catch_before_send` (default on) runs on coding turns, right before the agent finishes. It checks the agent's own draft reply against this turn's evidence; if the draft still claims something this turn's evidence contradicts or does not prove (for example "Tests pass" right after an edit with no rerun), the agent gets sent back once, by name, before you ever see the reply.

`catch_before_send` only fires on turns that edited code, because that is the only turn Hermes' `pre_verify` hook runs on. `next_turn_nudge` (default on) covers the gap: on the next turn, whatever kind it is, if the previous reply had a stale or failed claim that reached you untouched, the agent gets one line of context about it, so it can correct itself if you are still relying on that claim.

Both only fire on stale or failed claims (an active contradiction), never a plain not-backed claim, matching the Check line's own receipts framing above.

## What it touches

- Hooks: `post_tool_call` (observe only); `pre_llm_call` (observe, plus an optional one-line context nudge, see "Catch before send"); `transform_llm_output` (appends one line; streaming stays on); `pre_verify` (observe, plus an optional continue directive, see "Catch before send").
- No network calls, no model calls, no shell commands, no credentials, no background processes, no telemetry.
- Keeps the current turn's tool evidence in memory only.
- Never blocks, approves or edits a tool call.

## Develop

```
python3 -m unittest discover -s tests
hermes plugins doctor . --ci
hermes plugins validate . --install-deps
```

## Desktop pane data path

The Desktop pane (`desktop/plugin.js`) reads the focused session's claim history through
this package's own backend namespace, never by reading a file from the renderer or reaching
into another plugin's data:

1. `__init__.py`'s `on_transform_llm_output` writes every turn's verdicts to `ctx.state` under
   the key `last_reply`, keyed by session id, as `engine.claims_payload(verdicts)` (`{"claims":
   [...], "backed": int, "total": int}`).
2. `dashboard/plugin_api.py` exposes `GET /claims?session_id=<id>` and `GET /jobs`, mounted at
   `/api/plugins/check-please/`. It reads the same state through
   `hermes_cli.plugins_state.PluginState("check-please")`, the same class `ctx.state` wraps, so
   the backend route and the agent-side hooks always agree on one on-disk source.
3. `desktop/plugin.js` calls `ctx.rest('/claims?session_id=...')` on load and on a 4-second
   poll, addressed to `host.state.focusedSessionId`. An unknown session, or the backend half
   being disabled, returns the same empty shape the pane already renders as "no claims checked
   yet", never a guessed or stale row.

The backend half (`dashboard/plugin_api.py`) only works when this plugin's Python half is
enabled in `config.yaml`'s `plugins.enabled` list (see "The Python side" in the Desktop Plugin
SDK docs). A renderer-only enable of the desktop half does not import it.

License: MIT.
