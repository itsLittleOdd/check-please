// Smoke test for desktop/plugin.js: imports the REAL plugin file (not a copy) through a
// module.register() loader that redirects react / react/jsx-runtime / @hermes/plugin-sdk to
// local stubs (tests/js/stubs/), then drives real render passes with a minimal hook harness
// (tests/js/harness.mjs) by calling each registered spec's own render() function — the exact
// callback Hermes Desktop would call, not a reimplementation of it.
//
// This is not a substitute for an Electron smoke test (no real DOM, no CSS layout, no real
// plugin-sdk subscription semantics) — it is the next best thing available on a host with no
// Electron: it actually executes useClaims/CheckPane/CheckChip/ClaimRow. A test like this
// would have caught the item-11 claim.verdict/claim.status field-name mismatch (found by
// manual code read, before this file existed) because the rendered label falls back to the
// wrong default when the field name is wrong.
//
// Run: node tests/js/test_plugin.mjs   (exit 0 on pass, prints FAIL lines and exits 1 on any
// assertion failure; no test framework needed, stdlib + Node builtins only, no npm install).

import { register } from 'node:module'
import assert from 'node:assert/strict'
register('./loader.mjs', import.meta.url)

const { render, settle } = await import('./harness.mjs')
const sdk = await import('./stubs/plugin-sdk.mjs')
const plugin = (await import('../../desktop/plugin.js')).default

let failures = 0
function check(name, fn) {
  try {
    fn()
    console.log(`PASS ${name}`)
  } catch (err) {
    failures++
    console.log(`FAIL ${name}: ${err.stack || err.message}`)
  }
}
async function checkAsync(name, fn) {
  try {
    await fn()
    console.log(`PASS ${name}`)
  } catch (err) {
    failures++
    console.log(`FAIL ${name}: ${err.stack || err.message}`)
  }
}

function collect(tree, type, out = []) {
  if (!tree) return out
  if (Array.isArray(tree)) {
    for (const t of tree) collect(t, type, out)
    return out
  }
  if (tree.type === type) out.push(tree)
  if (tree.children) collect(tree.children, type, out)
  return out
}

function textOf(tree) {
  if (tree == null) return ''
  if (typeof tree === 'string' || typeof tree === 'number') return String(tree)
  if (Array.isArray(tree)) return tree.map(textOf).join('')
  return textOf(tree.children)
}

function register_(ctx) {
  const registered = []
  const fakeCtx = { ...ctx, register: (spec) => registered.push(spec) }
  plugin.register(fakeCtx)
  return registered
}

check('register() wires a pane and a status-bar chip with expected ids/areas', () => {
  const registered = register_({})
  assert.equal(registered.length, 2)
  const pane = registered.find((r) => r.id === 'pane')
  const chip = registered.find((r) => r.id === 'chip')
  assert.ok(pane, 'pane not registered')
  assert.equal(pane.area, 'panes')
  assert.equal(pane.data.placement, 'right')
  assert.ok(chip, 'chip not registered')
  assert.equal(chip.area, 'statusbar-right')
})

await checkAsync('pane renders the honest empty state with no focused session', async () => {
  sdk.host.state.focusedSessionId = null
  const ctx = { rest: async () => { throw new Error('ctx.rest must not be called with no session') } }
  const registered = register_(ctx)
  const pane = registered.find((r) => r.id === 'pane')
  const tree = render(pane.render())
  assert.match(textOf(tree), /No claims checked yet this session/)
})

await checkAsync('pane renders real claims, status text drives the label, not just color', async () => {
  sdk.host.state.focusedSessionId = 'sess-1'
  const payload = {
    claims: [
      { kind: 'tested', text: 'ran the tests, all green', status: 'BACKED' },
      { kind: 'sent', text: 'invoice emailed to Dana', status: 'NOT_BACKED' },
      { kind: 'tested', text: 'tests pass (two turns ago)', status: 'STALE' }
    ],
    backed: 1,
    total: 3
  }
  const ctx = { rest: async () => payload }
  const registered = register_(ctx)
  const pane = registered.find((r) => r.id === 'pane')
  const chip = registered.find((r) => r.id === 'chip')

  // First render triggers the effect (ctx.rest called, promise pending); settle flushes
  // the microtasks the .then()/.catch() run on, then a second render picks up the state.
  render(pane.render())
  await settle()
  const tree = render(pane.render())
  const rows = collect(tree, 'div').filter((d) => (d.props.className || '').includes('justify-between'))
  assert.equal(rows.length, 3, `expected 3 claim rows, got ${rows.length}`)
  const labels = rows.map((r) => {
    const spans = collect(r, 'span')
    return textOf(spans[1])
  })
  assert.deepEqual(labels, ['BACKED', 'NOT BACKED', 'STALE'])

  render(chip.render())
  await settle()
  const chipTree = render(chip.render())
  assert.equal(textOf(chipTree), 'check: 1/3')
})

await checkAsync('ctx.rest rejecting falls back to the honest empty state, not a crash', async () => {
  sdk.host.state.focusedSessionId = 'sess-2'
  const ctx = { rest: async () => { throw new Error('backend half off') } }
  const registered = register_(ctx)
  const pane = registered.find((r) => r.id === 'pane')
  render(pane.render())
  await settle()
  const tree = render(pane.render())
  assert.match(textOf(tree), /No claims checked yet this session/)
})

await checkAsync('chip click fires haptic + host.notify with the real counts, not a crash', async () => {
  sdk.host.state.focusedSessionId = 'sess-3'
  sdk.haptic.calls.length = 0
  sdk.host.notify.calls.length = 0
  const ctx = { rest: async () => ({ claims: [{ kind: 'sent', text: 'x', status: 'BACKED' }], backed: 1, total: 1 }) }
  const registered = register_(ctx)
  const chip = registered.find((r) => r.id === 'chip')
  render(chip.render())
  await settle()
  const btn = render(chip.render())
  assert.equal(btn.type, 'button')
  btn.props.onClick()
  assert.equal(sdk.haptic.calls.length, 1)
  assert.equal(sdk.host.notify.calls.length, 1)
  assert.match(sdk.host.notify.calls[0][0].message, /1\/1 backed/)
})

await checkAsync('an unrecognized verdict status (e.g. FAILED) falls back to NOT BACKED, never throws', async () => {
  sdk.host.state.focusedSessionId = 'sess-4'
  const ctx = { rest: async () => ({ claims: [{ kind: 'tested', text: 'build failed', status: 'FAILED' }], backed: 0, total: 1 }) }
  const registered = register_(ctx)
  const pane = registered.find((r) => r.id === 'pane')
  render(pane.render())
  await settle()
  const tree = render(pane.render())
  assert.match(textOf(tree), /NOT BACKED/)
})

// useClaims' setInterval(load, POLL_MS) is never cleared here (no real unmount happens in this
// harness), so the event loop would otherwise stay alive forever; exit explicitly instead of
// letting that look like a hang.
if (failures > 0) {
  console.log(`\n${failures} FAILURE(S)`)
  process.exit(1)
}
console.log('\nAll desktop/plugin.js smoke checks passed.')
process.exit(0)
