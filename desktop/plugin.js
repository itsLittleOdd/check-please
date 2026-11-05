// Check, Please — Desktop pane + status-bar chip.
// SDK surface only: @hermes/plugin-sdk, react, react/jsx-runtime. No app DOM reach, no raw bridge.
//
// Data note (backlog item 11): this pane has no wired data source yet. It renders
// an honest empty state rather than inventing claim rows. The documented route
// (plugin API or ctx.state) lands in item 11; this slice is the pane shell only.
import { host, haptic, STATUSBAR_AREAS } from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'

const VERDICT_STYLES = {
  BACKED: { label: 'BACKED', color: 'var(--ui-positive, #2e7d32)' },
  STALE: { label: 'STALE', color: 'var(--ui-caution, #b8860b)' },
  NOT_BACKED: { label: 'NOT BACKED', color: 'var(--ui-danger, #c62828)' }
}

function verdictStyle(verdict) {
  return VERDICT_STYLES[verdict] || VERDICT_STYLES.NOT_BACKED
}

function ClaimRow({ claim }) {
  const style = verdictStyle(claim.verdict)
  return jsxs('div', {
    className: 'flex items-center justify-between gap-2 border-b border-(--ui-border) px-2 py-1.5 text-sm',
    children: [
      jsx('span', { className: 'truncate', children: claim.text }),
      jsx('span', {
        style: { color: style.color },
        className: 'shrink-0 text-xs font-medium',
        // Label text is the signal, not just color (item 12: color is never the only cue).
        children: style.label
      })
    ]
  })
}

function CheckPane() {
  // Placeholder empty state until item 11 wires a real data route.
  const claims = []

  return jsxs('div', {
    className: 'flex h-full flex-col overflow-y-auto p-2 text-sm',
    children: [
      jsx('div', { className: 'px-2 pb-1 font-medium', children: 'Check, Please' }),
      claims.length === 0
        ? jsx('div', {
            className: 'px-2 py-3 text-(--ui-text-tertiary)',
            children: 'No claims checked yet this session.'
          })
        : jsx('div', { children: claims.map(c => jsx(ClaimRow, { key: c.id, claim: c })) })
    ]
  })
}

function CheckChip() {
  // No wired tally yet (item 11); shows a neutral placeholder rather than a fake count.
  const backed = 0
  const total = 0
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
      render: () => jsx(CheckPane, {})
    })
    ctx.register({
      id: 'chip',
      area: STATUSBAR_AREAS.right,
      order: 130,
      render: () => jsx(CheckChip, {})
    })
  }
}
