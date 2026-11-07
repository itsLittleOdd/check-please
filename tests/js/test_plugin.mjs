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
  // Item 29(d): worst verdict first (NOT BACKED, FAILED, STALE, BACKED), not backend order.
  assert.deepEqual(labels, ['NOT BACKED', 'STALE', 'BACKED'])

  render(chip.render())
  await settle()
  const chipTree = render(chip.render())
  assert.equal(textOf(chipTree), 'check: 1/3')
})

await checkAsync('pane header tally reads good-first, like a receipt total (item 29c)', async () => {
  sdk.host.state.focusedSessionId = 'sess-5'
  const payload = {
    claims: [
      { kind: 'tested', text: 'ran the tests, all green', status: 'BACKED' },
      { kind: 'saved', text: 'notes saved', status: 'BACKED' },
      { kind: 'tested', text: 'tests pass (two turns ago)', status: 'STALE' },
      { kind: 'tested', text: 'build is clean', status: 'STALE' },
      { kind: 'sent', text: 'invoice emailed to Dana', status: 'NOT_BACKED' }
    ],
    backed: 2,
    total: 5
  }
  const ctx = { rest: async () => payload }
  const registered = register_(ctx)
  const pane = registered.find((r) => r.id === 'pane')
  render(pane.render())
  await settle()
  const tree = render(pane.render())
  assert.match(textOf(tree), /2 backed · 2 stale · 1 not backed/)
})

await checkAsync('pane rows sort worst-first across all four named statuses (item 29d)', async () => {
  sdk.host.state.focusedSessionId = 'sess-6'
  const payload = {
    claims: [
      { kind: 'tested', text: 'c1', status: 'BACKED' },
      { kind: 'tested', text: 'c2', status: 'STALE' },
      { kind: 'tested', text: 'c3', status: 'FAILED' },
      { kind: 'sent', text: 'c4', status: 'NOT_BACKED' }
    ],
    backed: 1,
    total: 4
  }
  const ctx = { rest: async () => payload }
  const registered = register_(ctx)
  const pane = registered.find((r) => r.id === 'pane')
  render(pane.render())
  await settle()
  const tree = render(pane.render())
  const rows = collect(tree, 'div').filter((d) => (d.props.className || '').includes('justify-between'))
  const texts = rows.map((r) => textOf(collect(r, 'span')[0]))
  assert.deepEqual(texts, ['c4', 'c3', 'c2', 'c1'])
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

await checkAsync('two same-run STALE rows merge into one row with a count (item 29b)', async () => {
  sdk.host.state.focusedSessionId = 'sess-7'
  const payload = {
    claims: [
      { kind: 'tested', text: 'ran the tests, all green', status: 'BACKED', proof: 'python3 -m unittest -q' },
      { kind: 'tested', text: 'tests pass (first mention)', status: 'STALE', proof: 'unittest -q (3 turns ago)' },
      { kind: 'tested', text: 'suite is green (second mention, same run)', status: 'STALE', proof: 'unittest -q (3 turns ago)' },
      { kind: 'deployed', text: 'staging deploy', status: 'STALE', proof: null }
    ],
    backed: 1,
    total: 4
  }
  const ctx = { rest: async () => payload }
  const registered = register_(ctx)
  const pane = registered.find((r) => r.id === 'pane')
  render(pane.render())
  await settle()
  const tree = render(pane.render())
  const rows = collect(tree, 'div').filter((d) => (d.props.className || '').includes('justify-between'))
  // 4 claims in, 3 rows out: the two matching-proof STALE claims merged into one.
  assert.equal(rows.length, 3, `expected 3 rows after dedupe, got ${rows.length}`)
  const merged = rows.find((r) => textOf(collect(r, 'span')[0]).includes('first mention'))
  assert.ok(merged, 'merged row not found')
  assert.match(textOf(collect(merged, 'span')[1]), /STALE \u00d72/)
  // The no-proof STALE claim never merges with anything (nothing to confirm a shared run).
  const unmergedStale = rows.find((r) => textOf(collect(r, 'span')[0]).includes('staging deploy'))
  assert.ok(unmergedStale, 'unmerged no-proof STALE row not found')
  assert.equal(textOf(collect(unmergedStale, 'span')[1]), 'STALE')
  // The header tally still counts every individual claim, not deduped rows.
  assert.match(textOf(tree), /1 backed · 3 stale/)
})

await checkAsync('STALE rows with different proof strings never merge (item 29b)', async () => {
  sdk.host.state.focusedSessionId = 'sess-8'
  const payload = {
    claims: [
      { kind: 'tested', text: 'tests pass (run A)', status: 'STALE', proof: 'unittest run A' },
      { kind: 'tested', text: 'tests pass (run B)', status: 'STALE', proof: 'unittest run B' }
    ],
    backed: 0,
    total: 2
  }
  const ctx = { rest: async () => payload }
  const registered = register_(ctx)
  const pane = registered.find((r) => r.id === 'pane')
  render(pane.render())
  await settle()
  const tree = render(pane.render())
  const rows = collect(tree, 'div').filter((d) => (d.props.className || '').includes('justify-between'))
  assert.equal(rows.length, 2, `expected 2 distinct rows, got ${rows.length}`)
})

await checkAsync('clicking a row expands it to show the proof string (item 29e)', async () => {
  sdk.host.state.focusedSessionId = 'sess-9'
  const payload = {
    claims: [
      { kind: 'tested', text: 'tests pass (two turns ago)', status: 'STALE', proof: 'python3 -m unittest -q (3 turns ago)', note: 'no run since the edit' }
    ],
    backed: 0,
    total: 1
  }
  const ctx = { rest: async () => payload }
  const registered = register_(ctx)
  const pane = registered.find((r) => r.id === 'pane')
  render(pane.render())
  await settle()
  let tree = render(pane.render())
  // Collapsed by default: the proof text is not in the tree yet.
  assert.ok(!textOf(tree).includes('python3 -m unittest -q'), 'expand detail should not show before any click')
  const rowButtons = collect(tree, 'div').filter((d) => d.props.role === 'button')
  assert.equal(rowButtons.length, 1)
  assert.equal(rowButtons[0].props['aria-expanded'], false)
  rowButtons[0].props.onClick()
  tree = render(pane.render())
  assert.match(textOf(tree), /Proof: python3 -m unittest -q \(3 turns ago\)/)
  const reopenedButtons = collect(tree, 'div').filter((d) => d.props.role === 'button')
  assert.equal(reopenedButtons[0].props['aria-expanded'], true)
  // Clicking again collapses it.
  reopenedButtons[0].props.onClick()
  tree = render(pane.render())
  assert.ok(!textOf(tree).includes('Proof: python3'), 'second click should collapse the row')
})

await checkAsync('row shows the built label when the backend sends one, full sentence on expand (item 29a)', async () => {
  sdk.host.state.focusedSessionId = 'sess-9b'
  const payload = {
    claims: [
      { kind: 'tested', text: 'ran the tests, all green across the whole suite', label: 'Tests ran clean', status: 'BACKED' }
    ],
    backed: 1,
    total: 1
  }
  const ctx = { rest: async () => payload }
  const registered = register_(ctx)
  const pane = registered.find((r) => r.id === 'pane')
  render(pane.render())
  await settle()
  let tree = render(pane.render())
  // Collapsed: the short label shows, not the raw mid-sentence fragment.
  assert.match(textOf(tree), /Tests ran clean/)
  assert.ok(!textOf(tree).includes('ran the tests, all green'), 'collapsed row should show the label, not the raw sentence')
  const rowButtons = collect(tree, 'div').filter((d) => d.props.role === 'button')
  rowButtons[0].props.onClick()
  tree = render(pane.render())
  // Expanded: the full sentence is now visible too.
  assert.match(textOf(tree), /ran the tests, all green across the whole suite/)
})

await checkAsync('row falls back to claim.text when the backend has no label yet (item 29a)', async () => {
  sdk.host.state.focusedSessionId = 'sess-9c'
  const payload = {
    claims: [
      { kind: 'tested', text: 'ran the tests, all green', status: 'BACKED' }
    ],
    backed: 1,
    total: 1
  }
  const ctx = { rest: async () => payload }
  const registered = register_(ctx)
  const pane = registered.find((r) => r.id === 'pane')
  render(pane.render())
  await settle()
  const tree = render(pane.render())
  assert.match(textOf(tree), /ran the tests, all green/, 'no label field should still render the raw sentence, never a blank row')
})

await checkAsync('Enter on a row expands it to show the flagged reason when there is no proof (item 29e)', async () => {
  sdk.host.state.focusedSessionId = 'sess-10'
  const payload = {
    claims: [
      { kind: 'sent', text: 'invoice emailed to Dana', status: 'NOT_BACKED', proof: null, note: 'no send call ran' }
    ],
    backed: 0,
    total: 1
  }
  const ctx = { rest: async () => payload }
  const registered = register_(ctx)
  const pane = registered.find((r) => r.id === 'pane')
  render(pane.render())
  await settle()
  let tree = render(pane.render())
  const rowButtons = collect(tree, 'div').filter((d) => d.props.role === 'button')
  assert.equal(rowButtons.length, 1)
  rowButtons[0].props.onKeyDown({ key: 'Enter', preventDefault: () => {} })
  tree = render(pane.render())
  assert.match(textOf(tree), /Why flagged: no send call ran/)
  // A keypress other than Enter/Space must not toggle anything.
  const reopenedButtons = collect(tree, 'div').filter((d) => d.props.role === 'button')
  reopenedButtons[0].props.onKeyDown({ key: 'a', preventDefault: () => {} })
  tree = render(pane.render())
  assert.match(textOf(tree), /Why flagged: no send call ran/, 'an unrelated key must not collapse the row')
})

await checkAsync('two rows expand independently, proving state is not shared across rows (item 29e)', async () => {
  sdk.host.state.focusedSessionId = 'sess-11'
  const payload = {
    claims: [
      { kind: 'tested', text: 'row one', status: 'STALE', proof: 'proof one' },
      { kind: 'sent', text: 'row two', status: 'NOT_BACKED', proof: 'proof two' }
    ],
    backed: 0,
    total: 2
  }
  const ctx = { rest: async () => payload }
  const registered = register_(ctx)
  const pane = registered.find((r) => r.id === 'pane')
  render(pane.render())
  await settle()
  let tree = render(pane.render())
  let rowButtons = collect(tree, 'div').filter((d) => d.props.role === 'button')
  assert.equal(rowButtons.length, 2)
  // sortedByRank (item 29d) may reorder rows by status, so find "row one" by its own text
  // rather than assuming array position.
  const rowOneButton = rowButtons.find((b) => textOf(b).includes('row one'))
  assert.ok(rowOneButton, 'row one button not found')
  rowOneButton.props.onClick()
  tree = render(pane.render())
  assert.match(textOf(tree), /Proof: proof one/)
  assert.ok(!textOf(tree).includes('Proof: proof two'), 'row two must stay collapsed when only row one was clicked')
})

await checkAsync('receipt layout (item 29f): rows then a dashed rule then the total, not header-first', async () => {
  sdk.host.state.focusedSessionId = 'sess-12'
  const payload = {
    claims: [
      { kind: 'tested', text: 'ran the tests, all green', status: 'BACKED' },
      { kind: 'sent', text: 'invoice emailed to Dana', status: 'NOT_BACKED' }
    ],
    backed: 1,
    total: 2
  }
  const ctx = { rest: async () => payload }
  const registered = register_(ctx)
  const pane = registered.find((r) => r.id === 'pane')
  render(pane.render())
  await settle()
  const tree = render(pane.render())
  const divs = collect(tree, 'div')
  const linesDiv = divs.find((d) => (d.props.className || '').includes('receipt-lines'))
  const totalDiv = divs.find((d) => (d.props.className || '').includes('receipt-total'))
  assert.ok(linesDiv, 'receipt-lines wrapper not found')
  assert.ok(totalDiv, 'receipt-total wrapper not found')
  // The dotted rule above the total: a dashed border, never relying on spacing alone.
  assert.match(totalDiv.props.className, /border-dashed/)
  assert.match(totalDiv.props.className, /border-t\b/)
  // Order: line items render before the total, like reading down a receipt to the bottom line.
  const flatTexts = collect(tree, 'div').map((d) => d === linesDiv ? '[LINES]' : d === totalDiv ? '[TOTAL]' : null).filter(Boolean)
  assert.deepEqual(flatTexts, ['[LINES]', '[TOTAL]'])
  // The total line still reads with text, not color alone (item 12).
  assert.match(textOf(totalDiv), /1 backed · 1 not backed/)
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
