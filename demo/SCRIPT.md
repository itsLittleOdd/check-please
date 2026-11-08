# Check, Please demo script

Shot numbers and timings below match Wren's storyboard ("Check, Please: 83 seconds",
`../wren/IDEAS.md` section 3). This file maps each shot that shows plugin output to the
exact tool calls and the exact text our code produces, so the owner can film the real thing
in one take instead of reading a script aloud.

All output below is a real, dated run, not hand-typed: `tests/demo_hermes.py` drives the
same three turns through a real `PluginManager` (temp `HERMES_HOME`, real
`discover_and_load`, real `invoke_hook` calls), the same path a live Hermes turn uses.
Run it yourself to reproduce:

```
PY=$(ls ~/.hermes/installs/*/environments/*/venv/bin/python | head -1)
"$PY" tests/demo_hermes.py
```

Last run: 2026-11-08, commit 942fabf (HEAD at the time of this script).

Invented names only: "Dana" is a placeholder client, not a real person.

## Turn 1 (shot 3 setup, off-camera): tests run and pass

Tool calls:
- `terminal`: `python3 -m unittest discover -s tests` -> exit 0, "OK (24 tests)"

Reply text: "Tests pass."

Exact Check line appended to the reply:
```
Check: 1 backed. /check for detail.
```

## Turn 2 (shot 3 setup, off-camera): the file is edited, no rerun

Tool calls:
- `write_file`: path `pricing.py`

Reply text: "Updated the pricing function." (makes no claim itself; this is the edit
that makes turn 3's "tests pass" claim stale)

## Turn 3 (shots 1, 3, 4, 5, 6): deploy runs, invoice is drafted and saved, nothing sends it

Tool calls:
- `terminal`: `vercel deploy --prod` -> exit 0, "https://preview-dana.vercel.app"
- `write_file`: path `drafts/invoice-dana.md`

Reply text (shot 1, the Discord ping the owner sees on the bar top):
```
All set. Tests pass, preview is live, invoice emailed to Dana.
```

Exact Check line transform_llm_output appends to that same reply (shot 7, "same check
on your phone"):
```
Check: 1 backed, 1 stale. First issue: "Tests pass" (tests passed in turn 1 (python3 -m unittest discover -s tests), code changed since, no rerun this turn). /check for detail.
```

This is a receipts line, not a lie detector. A claim with no matching call at all (the
invoice "emailed" line below) is never shown inline by default; it goes in `/check` and
in the Desktop pane. The `inline_unbacked` setting flips that for a user who wants it
inline. The storyboard's shot 7 line ("1 not sent") does not match the real engine
output: the inline line names only the stale test claim, and the "invoice emailed" line
shows up as NOT BACKED in `/check` (shot 6) and in the Desktop pane, not inline. Film
the real line above, not the scripted one.

## Shot 4: "Tests pass" line detail (amber, STALE)

Exact `/check` row for this claim (from the full detail report below):
```
- STALE: "Tests pass"
    note: tests passed in turn 1 (python3 -m unittest discover -s tests), code changed since, no rerun this turn
```

Storyboard asks for exact timestamps ("Passed 2:14 PM... edited 2:16 PM"); the plugin
does not store wall-clock times today, only turn numbers and tool/command text. Either
film with turn numbers in the voiceover ("passed in turn 1, no rerun since") or treat
timestamped evidence as a later backlog item, not something to fake in the demo.

## Shot 5: "preview is live" line detail (green, BACKED)

Exact `/check` row:
```
- BACKED: "preview is live"
    proof: terminal vercel deploy --prod
```

## Shot 6: "Invoice emailed" line detail (red, NOT BACKED)

Exact `/check` row:
```
- NOT BACKED: "invoice emailed to Dana."
    note: no sent call this turn; proven: drafted
```

Storyboard asks for "Proven: saved"; the real ladder-family fix (backlog item 18/19)
now reports "drafted" for a freshly-written invoice path (saved and drafted are both in
the document family, drafted is the file's actual rung on its first write). Say "drafted"
on camera, not "saved" — that line was corrected in README.md during item 18 and this
script should not reintroduce the old wording.

## Full `/check` detail report (shots 4-6 combined, what the Desktop pane and `/check`
both read from)

```
Check, Please: last reply
- STALE: "Tests pass"
    note: tests passed in turn 1 (python3 -m unittest discover -s tests), code changed since, no rerun this turn
- BACKED: "preview is live"
    proof: terminal vercel deploy --prod
- NOT BACKED: "invoice emailed to Dana."
    note: no sent call this turn; proven: drafted
```

## Shots not covered here

- Shot 2 ("Is it?"), shot 8 (approve-and-send flow), shot 9 (overnight cron digest with
  a bounce), and shot 10 (title card) are not reproduced by `tests/demo_hermes.py`. Shot
  8 needs a real approved send call (an external send still needs the owner's own
  review and go-ahead to film for real); shot 9's "1 contradicted ... bounced" scenario
  is not something the current engine detects (it has no delivery-receipt/bounce
  evidence kind) — do not film shot 9 as scripted until that gap is closed or the line
  is rewritten to match what the plugin actually reports today.
- The Desktop pane's visual rendering (shots 3-6's actual on-screen pane) is still
  unverified in a real Electron window (backlog item 10, open). This script gives the
  exact text the pane would show once that smoke test exists; it is not a screenshot.
