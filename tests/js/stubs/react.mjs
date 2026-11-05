// Minimal stand-in for 'react' hooks, enough to drive one functional-component render pass
// in a test harness. Not a reimplementation of React: no fiber, no scheduling, no batching.
// Hook state lives per-component-invocation on the call stack set up by render() in
// tests/js/stubs/harness.mjs, matching the classic "hooks array indexed by call order" model.

let currentCell = null // set by harness.mjs around each component call

export function __setCell(cell) {
  currentCell = cell
}

export function useState(initial) {
  const cell = currentCell
  const i = cell.cursor++
  if (!(i in cell.state)) cell.state[i] = typeof initial === 'function' ? initial() : initial
  const setState = (next) => {
    const value = typeof next === 'function' ? next(cell.state[i]) : next
    cell.state[i] = value
    cell.onChange()
  }
  return [cell.state[i], setState]
}

export function useEffect(fn, deps) {
  const cell = currentCell
  const i = cell.cursor++
  const prevDeps = cell.effectDeps[i]
  const changed = !prevDeps || !deps || deps.length !== prevDeps.length ||
    deps.some((d, idx) => d !== prevDeps[idx])
  if (changed) {
    if (cell.cleanups[i]) cell.cleanups[i]()
    cell.cleanups[i] = fn() || null
  }
  cell.effectDeps[i] = deps
}
