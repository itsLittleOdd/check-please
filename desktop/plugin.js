// Check, Please — Desktop pane + status-bar chip.
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

const VERDICT_STYLES = {
  BACKED: { label: 'BACKED', color: 'var(--ui-positive, #2e7d32)' },
  STALE: { label: 'STALE', color: 'var(--ui-caution, #b8860b)' },
  NOT_BACKED: { label: 'NOT BACKED', color: 'var(--ui-danger, #c62828)' }
}

function verdictStyle(verdict) {
  return VERDICT_STYLES[verdict] || VERDICT_STYLES.NOT_BACKED
}

function ClaimRow({ claim }) {
  // claim.status matches engine.claims_payload()'s key exactly (BACKED | STALE | NOT_BACKED |
  // FAILED); FAILED falls through to the NOT_BACKED style via verdictStyle's default.
  const style = verdictStyle(claim.status)
  return jsxs('div', {
    className: 'flex items-center justify-between gap-2 border-b border-(--ui-border) px-2 py-1.5 text-sm',
    children: [
      // min-w-0 is required alongside truncate in a flex row: without it flex items default to
      // min-width:auto and the ellipsis never kicks in, so long claim text overflows the 280px
      // pane at narrow widths instead of truncating (item 12 finding, fixed here).
      jsx('span', { className: 'min-w-0 flex-1 truncate', children: claim.text }),
      jsx('span', {
        style: { color: style.color },
        className: 'shrink-0 text-xs font-medium',
        // Label text is the signal, not just color (item 12: color is never the only cue).
        children: style.label
      })
    ]
  })
}

function CheckPane({ ctx }) {
  const { claims } = useClaims(ctx)

  return jsxs('div', {
    className: 'flex h-full flex-col overflow-y-auto p-2 text-sm',
    children: [
      jsx('div', { className: 'px-2 pb-1 font-medium', children: 'Check, Please' }),
      claims.length === 0
        ? jsx('div', {
            className: 'px-2 py-3 text-(--ui-text-tertiary)',
            children: 'No claims checked yet this session.'
          })
        : jsx('div', { children: claims.map((c, i) => jsx(ClaimRow, { key: `${c.kind}-${i}`, claim: c })) })
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
