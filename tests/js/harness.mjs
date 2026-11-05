// Tiny single-pass renderer: enough to drive desktop/plugin.js's two functional components
// through one real execution (not a mock of the output), reusing the hook-cell wiring from
// stubs/react.mjs. Not a React reimplementation: no diffing, no fiber tree, no batching.
// Each call to render(element) walks the element graph and, for function components, gives
// them a fresh-or-reused hook cell keyed by component identity so repeated renders (used to
// observe state after an effect settles) see the same state slots a real re-render would.

import { __setCell } from './stubs/react.mjs'

const cells = new WeakMap()

function cellFor(type) {
  let cell = cells.get(type)
  if (!cell) {
    cell = { state: {}, effectDeps: {}, cleanups: {}, cursor: 0, onChange: () => {} }
    cells.set(type, cell)
  }
  cell.cursor = 0
  return cell
}

export function render(element) {
  if (element == null || typeof element === 'string' || typeof element === 'number') return element
  if (Array.isArray(element)) return element.map(render)
  if (typeof element.type === 'function') {
    const cell = cellFor(element.type)
    __setCell(cell)
    const out = element.type(element.props)
    __setCell(null)
    return render(out)
  }
  const { children, ...rest } = element.props || {}
  return { type: element.type, props: rest, children: render(children) }
}

// Call after render() when the component called useEffect with an async ctx.rest(...).then(...):
// the effect body ran synchronously, but the promise resolution lands on a later microtask.
// This flushes microtasks so a second render() picks up the state the .then()/.catch() set.
export async function settle() {
  await Promise.resolve()
  await Promise.resolve()
}
