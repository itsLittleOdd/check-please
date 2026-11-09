# Check, Please

Your agent says "Tests pass, preview is live, invoice emailed to Dana."

Did it?

Check, Please pairs every "done" claim in a Hermes reply with the tool call that proves it. When a claim does not hold up, it adds one line under the reply:

```
Check: 1 backed, 1 stale. First issue: "Tests pass" (tests passed in turn 1 (python3 -m unittest discover -s tests), code changed since, no rerun this turn). /check for detail.
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

_Not yet captured. Placeholders for the catalog listing:_

- `screenshots/check-line.png`: a Hermes chat reply with the `Check:` line under it (one backed, one stale, one not backed).
- `screenshots/check-detail.png`: the `/check` command output showing the full breakdown with proof lines.
- `screenshots/desktop-pane.png`: the Desktop pane docked right, showing claim rows with BACKED/STALE/NOT BACKED labels.
- `screenshots/status-chip.png`: the status-bar chip for the focused session.

## Results

- **Held-out set** (150 rows, written by an independent author with no access to this repo): precision 0.921, recall 0.768, 129 true positives, 11 false positives, 39 false negatives, 46 of 54 clean negatives correctly left alone (`score.py`, 2026-11-08). The two weakest kinds are tested (recall 0.42) and scheduled (recall 0.67); overall recall is still short of where we want it.
- **Real-history replay**: about 3,200 of the author's own saved Hermes turns, replayed read-only, show 3.5 inline alerts per 100 turns and 59.1 flags per 100 turns (`score_replay.py`, 2026-11-08). The inline number is what a user actually sees in a reply; the flags number counts everything `/check` lists, which is most of what never reaches the reply at all.
- **Model exams** (`score_bench_replay.py`, 2026-11-08): set 1, one frontier model, 24 replies, 0 false-alarm replies and 0 misses. Set 2, six local-model runs, 72 replies total, 7 graded overclaims, 1 false-alarm reply, 1 miss. The false alarm is an honest partial-pass report, not an overclaim this plugin missed.
- **The first model exam** (`score_bench_replay.py`, 2026-11-08): three local-model runs and one frontier run, 32 sessions, graded blind by an independent grader. The local model overclaimed in 16 of 24 sessions, every one by changing the check it was being judged by (a test, a hook, an approval record); the frontier model overclaimed in 0 of 8. Before the fixes this exam drove, the plugin caught 2 of those 16 overclaims and raised 1 false alarm on the frontier run; after the fixes it catches 8 of 16 with 0 false alarms. Those misses were inspected while fixing the plugin, so the after number is tuned to this exam and is not a held-out result. The remaining 8 are the limits listed below: a tool's own escape hatch, a restored file read as a plain write, a silent no-op script, and a claim with no call at all, which goes to `/check` rather than inline.
- **The sealed held-out exam** (`score_bench_replay.py`, 2026-11-08): 32 sessions written by a second independent author after the fixes landed, three local-model runs and one frontier run, graded blind. Neither model overclaimed (0 of 24, 0 of 8), and the plugin raised 0 inline alerts on these 32 honest replies, so 0 false alarms and 0 misses. This exam measures the false-alarm side only; with no overclaims in it, it gives no catch rate. The same local model overclaimed in 16 of 24 sessions on the first exam and 0 of 24 on this sealed one; the cause, whether it is scenario design or run conditions, is not established.
- The project's own development corpus is not reported here because a test suite scoring itself is not evidence of real-world accuracy; it only shows the code covers what that corpus was built to test.

## Limits, stated honestly

- **Heuristic lexicon, not understanding.** Claims are matched by regex patterns over the reply text (verbs like "deployed", "pushed", "tested", "paid"). It has no model call and no semantic understanding of the sentence. A claim phrased in a way the lexicon has never seen will read as NOT BACKED even if it is true, and an unusual sentence shape can still slip past a negation check.
- **English only.** Every pattern, negation word and alias is English. Any other language passes through unrecognized (treated as having no claims, not flagged as an error).
- **What it cannot see.** It only sees this turn's tool calls and the reply text; it cannot see outside the terminal/write_file/patch/send_message/execute_code/browser_exec/cronjob/MCP/connector shapes it was built against, cannot see inside a webhook payload or a push hidden behind a runtime-built string, and treats `delegate_task` as no evidence on principle (a child agent's work is not this turn's proof).
- **Desktop pane: one real-window run so far.** The pane and chip ran once in a real Hermes Desktop window on 2026-11-05, before the later polish, and showed the right verdicts. Since then, claim labels, merged stale rows, the tally, worst-first order, expand on click or keyboard and the receipt layout have been checked by a Node-based harness that executes the real `desktop/plugin.js` logic outside a browser, plus `doctor`/`validate`'s desktop surface check, but have not yet been re-run inside a real Desktop window.
- **A tool's own escape hatch is invisible to it.** When an agent bypasses a check through a tool's own override, such as an invented ticket for a rebaseline flag, this plugin has no way to tell that from a real pass. A policy bypass through a tool's own escape hatch is not a claim a proof checker can judge.
- **A restored file reads as a plain write.** A recipient list or other record put back under a written privacy hold looks the same to the plugin as any other write; it cannot tell a restore from an edit.
- **A silent no-op can still count as proof.** A script that prints "done" and exits clean without actually doing anything backs its own claim; nothing in the transcript tells the plugin the action never happened.
- **A claim with no call at all stays out of the reply by design.** It is listed in `/check`, not inline, unless the `inline_unbacked` setting is turned on.
- **Tainted rung only inherits from one check.** A claim resting on an edited lower-rung proof inherits the EDITED verdict only through the proof-file-edit check, never through the rewritten-program rule or the approval override.
- **The admission-phrase list is closed.** When the plugin cannot find a pass whose target shares a word with an admitted failure, it falls back to the latest pass rather than leaving the claim unjudged.
- **Disclosed test edits need matching line counts.** The rule that clears an honestly-disclosed test change only works when the before and after patches have the same number of lines; a tamper dressed up as a reformat, landed alongside a genuinely new test, could slip past it.
- **The disclosure-literal check is narrow.** Its change-verb list is closed, and the new value has to sit in the same clause as that verb, or the disclosure is not recognized.
- **An approval record gates the whole session.** Once any approval write happens, every later pass of a gated kind in that session is treated as coming after it, even when the two are unrelated.
- **Why only contradictions interrupt the reply.** Inline shows contradictions only (stale, failed, proof edited); `/check` shows everything the plugin found. That split is what keeps the line quiet when the agent is being honest.

## Why a plugin and not a prompt

A prompt asks the model that made the claim to grade its own homework. Check, Please reads the tool log through Hermes hooks, outside the model, and the match is plain code. No second model, no network, and the check itself makes no model calls. The cost is in the two optional nudges: on a coding turn, catch-before-send may send the agent back once to recheck before it replies, and a one-line note can be added to the next turn's context (see "Catch before send").

## The state ladder

Saved is not sent. A claim only counts at the rung the tool log proves:

drafted, saved, tested, committed, pushed, deployed, scheduled, sent, paid

## Verdicts

- **BACKED**: a matching tool call ran this turn and succeeded.
- **STALE**: tests passed, then code changed and nothing re-ran them.
- **FAILED**: the matching call ran but errored, bounced or was blocked.
- **EDITED** (shown as "proof edited"): the matching call passed only after the agent changed the thing it was being checked by (the test, the script, a hook, an approval record), or the call ran through a program the agent rewrote this session.
- **PARTIAL** (shown as "partial run"): a whole-suite claim backed only by a narrowed run after an earlier failure.
- **NOT BACKED**: no matching call this turn.

## Install

```
hermes plugins install itsLittleOdd/check-please --enable
```

`--enable` turns it on in the same step; without it, Hermes installs the plugin but leaves it off until you run `hermes plugins enable check-please`. Hermes warns that the source is not from its catalog until the catalog listing is merged. New sessions pick the plugin up; restart an open chat (or the gateway) to load it there.

## Settings

`line`: `issues` (default) adds the line only when something is stale, failed, edited, or (with `inline_unbacked` on) a high-stakes not-backed claim, so an honest reply gets no line at all. `all` adds it under every reply that makes claims, including a quiet "Check: 2 backed". `off` keeps it to `/check`. `/check` shows the receipts in every mode.

`inline_unbacked`: off by default. Check, Please is a receipts tool, not a lie detector: a not-backed claim means no receipt was found, which is common and often not an overclaim at all, so by default it only shows in `/check`, never inline (the Desktop pane still lists it today; relabeling it there as "no receipt" is tracked separately). Stale (a claim contradicted by this turn's own evidence) and failed always interrupt the reply, with or without this setting. Turn `inline_unbacked` on to also show not-backed inline for the higher-stakes kinds: sent, paid, deployed, pushed, deleted, scheduled.

`verify_nudge`: on by default. On coding turns, asks the agent to rerun tests before finishing when tests last passed before a later edit with no rerun since.

`catch_before_send`: on by default. See "Catch before send" below.

`next_turn_nudge`: on by default. See "Catch before send" below.

`cron_guard`: on by default. On an unattended cron run, puts the worst inline-worthy claim (stale, failed, proof edited, or not-backed when `inline_unbacked` is also on) at the TOP of the delivered reply, one line, so a human skimming a cron digest sees it before anything else. The normal Check line still runs too, at the bottom, as usual. Never alters a reply that is exactly empty or the literal `[SILENT]` suppression token many cron jobs use to stay silent-on-green. Detected through `ctx.current_cron_execution()`; outside a cron run this setting does nothing.

## Audit: `hermes check-please audit`

A CLI command, not a chat command: `hermes check-please audit [--since Nd] [--profile NAME] [--limit N]`.

Read-only, local only, nothing sent. It replays your own saved session history (the current profile's own session store) through the same claim-check engine every live turn already uses, and prints a summary: how many turns made a completion claim, how many were backed, and the newest not-backed claims with session id and date. It opens your session database through Hermes' documented `hermes_state.SessionDB(..., read_only=True)`; it never writes to it, never sends anything, and never leaves this machine.

`--since 7d` limits the replay to sessions active in the last 7 days (any number of days). `--limit N` caps how many not-backed claims print (default 20). `--profile NAME` for a profile other than the one you are running in is accepted but not yet implemented; the command tells you so and audits the current profile instead.

## Catch before send

Two settings work together to stop a stale or failed claim before it reaches you, instead of only reporting it after the fact.

`catch_before_send` (default on) runs on coding turns, right before the agent finishes. It checks the agent's own draft reply against this turn's evidence; if the draft still claims something this turn's evidence contradicts or does not prove (for example "Tests pass" right after an edit with no rerun), the agent gets sent back once, by name, before you ever see the reply.

`catch_before_send` only fires on turns that edited code, because that is the only turn Hermes' `pre_verify` hook runs on. `next_turn_nudge` (default on) covers the gap: on the next turn, whatever kind it is, if the previous reply had a stale or failed claim that reached you untouched, the agent gets one line of context about it, so it can correct itself if you are still relying on that claim.

Both only fire on stale or failed claims (an active contradiction), never a plain not-backed claim, matching the Check line's own receipts framing above.

## What it touches

- Hooks: `post_tool_call` (observe only); `subagent_stop` (observe only: a delegated child's tool-call metadata and summary); `pre_llm_call` (observe, plus an optional one-line context nudge, see "Catch before send"); `transform_llm_output` (appends one line; streaming stays on); `pre_verify` (observe, plus an optional continue directive, see "Catch before send").
- No network calls, no model calls, no shell commands, no credentials, no background processes, no telemetry.
- What it stores. The current turn's full evidence ledger lives in memory only. A smaller record is written to this plugin's own state file, `state.json` in its folder under `plugin-data/` in your Hermes home (the folder name starts with `agent-plugin-check-please`; local, owner-readable only), so `/check`, the Desktop pane, the cross-turn checks and the cron tally survive a restart:
  - per session, the newest successful call of each kind: tool name plus the first line of the command (up to 150 characters) or the file path;
  - per session, the last reply's claims: each claim sentence (up to 200 characters), its verdict, note and proof line;
  - per session, the turn counter, the last passing test command, and any claim kind still flagged;
  - per day, for 14 days: claim counts and up to 30 non-backed claim sentences (80 characters each);
  - per cron job: claim counts; per parent session: delegated children's claim verdicts.
  Capped at 200 sessions. A command line that carries a secret on its first line would be stored and shown as written; nothing here redacts it.
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
