// Check, Please: Desktop pane + status-bar chip.
// SDK surface only: @hermes/plugin-sdk, react, react/jsx-runtime. No app DOM reach, no raw bridge.
//
// Data path (backlog item 11): ctx.rest('/claims?session_id=...') reaches this package's own
// dashboard/plugin_api.py route, which reads the plugin's ctx.state ("last_reply", written every
// turn by __init__.py's on_transform_llm_output) through hermes_cli.plugins_state.PluginState.
// No file reads from the renderer, no reach into another plugin's data.
import { host, haptic, useValue, STATUSBAR_AREAS } from '@hermes/plugin-sdk'
import { useEffect, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const EMPTY = { claims: [], backed: 0, total: 0 }
const POLL_MS = 4000 // ctx.socket has no server-push side here yet; poll is the documented fallback.

// Shared poll: both the pane and the chip want the same focused-session payload, so one hook
// (used by each component) avoids two independent ctx.rest calls firing every tick.
function useClaims(ctx) {
  const sessionId = useValue(host.state.focusedSessionId)
  const [data, setData] = useState(EMPTY)

  useEffect(() => {
    if (!sessionId) {
      setData(EMPTY)
      return
    }
    let live = true
    const load = () => {
      ctx.rest(`/claims?session_id=${encodeURIComponent(sessionId)}`)
        .then(result => { if (live) setData(result && typeof result === 'object' ? result : EMPTY) })
        .catch(() => { if (live) setData(EMPTY) }) // backend half off/unavailable: honest empty state, not a crash
    }
    load()
    const id = setInterval(load, POLL_MS)
    return () => { live = false; clearInterval(id) }
  }, [ctx, sessionId])

  return data
}

// Fable 5.2 review: FAILED, EDITED and PARTIAL had no entry here, so each rendered as
// "NOT BACKED", the one label the README defines as "no receipt found, often not an overclaim
// at all". A claim the tool log contradicts (the call failed, or passed only after the agent
// edited the check) looked identical to a claim with no receipt, and the tally read
// "1 not backed · 1 not backed". Same words engine.summary_line uses in the reply line.
const VERDICT_STYLES = {
  BACKED: { label: 'BACKED', color: 'var(--ui-positive, #2e7d32)' },
  STALE: { label: 'STALE', color: 'var(--ui-caution, #b8860b)' },
  NOT_BACKED: { label: 'NOT BACKED', color: 'var(--ui-danger, #c62828)' },
  FAILED: { label: 'FAILED', color: 'var(--ui-danger, #c62828)' },
  EDITED: { label: 'PROOF EDITED', color: 'var(--ui-danger, #c62828)' },
  PARTIAL: { label: 'PARTIAL RUN', color: 'var(--ui-caution, #b8860b)' }
}

function verdictStyle(verdict) {
  return VERDICT_STYLES[verdict] || VERDICT_STYLES.NOT_BACKED
}

// Item 29(d): worst verdict first, per the backlog's own named order. Any status outside
// that list (EDITED, PARTIAL: items 43/44) ranks between STALE and BACKED: still worth a
// look, but not literally the spec's top two. Array.sort is stable in Node, so claims tied
// on rank keep the backend's own order.
const ROW_ORDER = ['NOT_BACKED', 'FAILED', 'STALE', 'BACKED']
function rowRank(status) {
  const i = ROW_ORDER.indexOf(status)
  return i === -1 ? 2.5 : i
}
function sortedByRank(claims) {
  return claims.slice().sort((a, b) => rowRank(a.status) - rowRank(b.status))
}

// Item 29(b): two STALE rows backed by the same stale run (same claim.proof, the backend's
// own evidence-detail string) are one row with a count, not a duplicate line each. Scoped to
// STALE only, per the backlog's own wording ("two STALE rows for the same test run"), and only
// when both sides actually name a proof string. Two STALE claims with no proof info at all
// are never merged, since there is nothing to confirm they share the same run.
function dedupeSameRunStale(claims) {
  const merged = []
  const indexByKey = new Map()
  for (const c of claims) {
    if (c.status === 'STALE' && c.proof) {
      const key = `STALE|${c.proof}`
      if (indexByKey.has(key)) {
        merged[indexByKey.get(key)].count += 1
        continue
      }
      indexByKey.set(key, merged.length)
      merged.push({ ...c, count: 1 })
      continue
    }
    merged.push({ ...c, count: 1 })
  }
  return merged
}

// Item 29(c): a header tally like a receipt total ("2 backed · 2 stale · 1 not backed"),
// good-first per the backlog's own example order; any other status (EDITED, PARTIAL) is
// appended after, lowest status label text, never color alone (same item-12 rule as rows).
const TALLY_ORDER = ['BACKED', 'STALE', 'NOT_BACKED']
function tallyText(claims) {
  const counts = {}
  for (const c of claims) counts[c.status] = (counts[c.status] || 0) + 1
  const parts = []
  for (const key of TALLY_ORDER) {
    if (counts[key]) parts.push(`${counts[key]} ${verdictStyle(key).label.toLowerCase()}`)
  }
  for (const key of Object.keys(counts)) {
    if (!TALLY_ORDER.includes(key)) parts.push(`${counts[key]} ${verdictStyle(key).label.toLowerCase()}`)
  }
  return parts.join(' \u00b7 ')
}

// Item 29(e): the row's own expand detail text. We only have what the backend's
// verdict_to_dict() already sends (claim.proof, claim.note); no relative-time field exists
// in that payload today, so this reports the proof string or the flagged reason honestly
// rather than inventing a timestamp the backend doesn't send.
// Item 29(a): the row now shows a short label, not the raw mid-sentence fragment, so the
// full claim.text (the actual sentence) goes here first, ahead of proof/reason.
// Fable 5.2 review: a flagged row that HAS a proof string (every STALE, EDITED, PARTIAL and
// FAILED row does: the proof is the call that was contradicted) used to expand to
// "Proof: pytest -q" and nothing else, which reads as "proven". The reason comes first now;
// the call it was judged against follows.
function rowDetailText(claim) {
  const parts = [claim.text]
  const flagged = claim.status !== 'BACKED'
  if (flagged && claim.note) parts.push(`Why flagged: ${claim.note}`)
  if (claim.proof) parts.push(`${flagged ? 'Judged against' : 'Proof'}: ${claim.proof}`)
  else if (!claim.note) parts.push('No proof recorded.')
  else if (!flagged) parts.push(claim.note)
  return parts.join(' \u2014 ')
}

function ClaimRow({ claim, expanded, onToggle }) {
  // claim.status matches engine.claims_payload()'s key exactly (BACKED | STALE | NOT_BACKED |
  // FAILED | EDITED | PARTIAL); a status this build has never heard of falls through to the
  // NOT_BACKED style via verdictStyle's default.
  const style = verdictStyle(claim.status)
  // Item 29(b): a deduped same-run STALE row carries claim.count > 1; show it next to the
  // label (text, never color alone, same item-12 rule the base label already follows).
  const label = claim.count > 1 ? `${style.label} \u00d7${claim.count}` : style.label
  return jsxs('div', {
    className: 'border-b border-(--ui-border) px-2 py-1.5 text-sm',
    children: [
      // Item 29(e): click or Enter/Space expands the row. role+tabIndex make it keyboard
      // reachable (item 12's keyboard-focus rule); aria-expanded is the accessible state,
      // never color or layout alone.
      jsxs('div', {
        role: 'button',
        tabIndex: 0,
        'aria-expanded': expanded,
        className: 'flex items-center justify-between gap-2 cursor-pointer',
        onClick: onToggle,
        onKeyDown: (e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            if (typeof e.preventDefault === 'function') e.preventDefault()
            onToggle()
          }
        },
        children: [
          // min-w-0 is required alongside truncate in a flex row: without it flex items default
          // to min-width:auto and the ellipsis never kicks in, so long claim text overflows the
          // 280px pane at narrow widths instead of truncating (item 12 finding, fixed here).
          // Item 29(a): a short built label (engine.claim_label, e.g. "Tests ran clean",
          // "invoice.md saved"), not the raw mid-sentence fragment; the full sentence is one
          // click/Enter away via rowDetailText. Falls back to claim.text if an older backend
          // build has not sent "label" yet, so a stale server never renders a blank row.
          jsx('span', {
            className: 'min-w-0 flex-1 truncate',
            title: claim.text,
            children: claim.label || claim.text
          }),
          jsx('span', {
            style: { color: style.color },
            className: 'shrink-0 text-xs font-medium',
            // Label text is the signal, not just color (item 12: color is never the only cue).
            children: label
          })
        ]
      }),
      expanded
        ? jsx('div', {
            className: 'px-0 pt-1 text-xs text-(--ui-text-tertiary)',
            children: rowDetailText(claim)
          })
        : null
    ]
  })
}

function CheckPane({ ctx }) {
  const { claims } = useClaims(ctx)
  // Item 29(e): expand state lives HERE, one level up from the rows, not inside ClaimRow
  // itself. The harness's hook cell is keyed by component function identity, not by element
  // instance, so every ClaimRow render in the same pass would otherwise share one state slot;
  // lifting the state avoids that trap and is also just the correct React pattern for a list.
  const [expandedKeys, setExpandedKeys] = useState(() => new Set())
  const toggleKey = (key) => {
    setExpandedKeys((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  return jsxs('div', {
    className: 'flex h-full flex-col overflow-y-auto p-2 text-sm',
    children: [
      jsx('div', { className: 'px-2 pb-1 font-medium', children: 'Check, Please' }),
      claims.length === 0
        ? jsx('div', {
            className: 'px-2 py-3 text-(--ui-text-tertiary)',
            children: 'No claims checked yet this session.'
          })
        // Item 29(f): the guest-check look from the storyboard, line items, then a dotted
        // rule, then the tally read like a receipt total (bottom, not top). Text labels
        // still carry the status (item 12: color is never the only signal), so this is a
        // layout/order change only, never a new signal.
        : jsxs('div', {
            className: 'receipt',
            children: [
              // Item 29(d): worst verdict first, so the thing that needs attention is on top.
              // Item 29(b): dedupe same-run STALE rows into one row with a count before sorting.
              jsx('div', {
                className: 'receipt-lines',
                children: sortedByRank(dedupeSameRunStale(claims)).map((c, i) => {
                  const key = `${c.kind}-${c.text}-${i}`
                  return jsx(ClaimRow, {
                    key,
                    claim: c,
                    expanded: expandedKeys.has(key),
                    onToggle: () => toggleKey(key)
                  })
                })
              }),
              // Item 29(c): header-turned-footer tally, like a receipt total: a dashed rule
              // above it (border-dashed, never relying on spacing alone at narrow widths),
              // bold weight so it still reads as "the total" with no color involved.
              jsx('div', {
                className: 'receipt-total mt-1 border-t border-dashed border-(--ui-border) px-2 pt-1.5 text-xs font-medium',
                children: tallyText(claims)
              })
            ]
          })
    ]
  })
}

function CheckChip({ ctx }) {
  const { backed, total } = useClaims(ctx)
  return jsx('button', {
    type: 'button',
    className: 'px-1.5 text-[0.6875rem] text-(--ui-text-tertiary)',
    onClick: () => {
      haptic('tap')
      host.notify({
        kind: 'info',
        message: total === 0
          ? 'Check, Please: nothing checked yet this session.'
          : `Check, Please: ${backed}/${total} backed`
      })
    },
    children: total === 0 ? 'check: --' : `check: ${backed}/${total}`
  })
}

export default {
  id: 'check-please',
  name: 'Check, Please',
  register(ctx) {
    ctx.register({
      id: 'pane',
      area: 'panes',
      title: 'Check, Please',
      data: { placement: 'right', width: '280px' },
      render: () => jsx(CheckPane, { ctx })
    })
    ctx.register({
      id: 'chip',
      area: STATUSBAR_AREAS.right,
      order: 130,
      render: () => jsx(CheckChip, { ctx })
    })
  }
}
