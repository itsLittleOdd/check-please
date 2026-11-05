# Check, Please

Your agent says "Tests pass, preview is live, invoice emailed to Dana."

Did it?

Check, Please pairs every "done" claim in a Hermes reply with the tool call that proves it, and adds one line under the reply:

```
Check: 1 backed, 1 stale, 1 not backed. First issue: "emailed" (no sent call this turn; proven: deployed). /check for detail.
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
    note: no sent call this turn; proven: deployed
```

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

`line`: `all` (default) adds the line under every reply that makes claims, `issues` only when something is not backed, `off` keeps it to `/check`.

## What it touches

- Hooks: `pre_llm_call`, `post_tool_call` (observe only) and `transform_llm_output` (appends one line; streaming stays on).
- No network calls, no model calls, no shell commands, no credentials, no background processes, no telemetry.
- Keeps the current turn's tool evidence in memory only.
- Never blocks, approves or edits a tool call.

## Develop

```
python3 -m unittest discover -s tests
hermes plugins doctor . --ci
hermes plugins validate .
```

License: MIT.
