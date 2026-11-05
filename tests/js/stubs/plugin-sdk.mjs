// Minimal stand-in for '@hermes/plugin-sdk', scoped to exactly what desktop/plugin.js imports:
// host, haptic, useValue, STATUSBAR_AREAS. Test code can read/replace host.state.focusedSessionId
// and spy on haptic/host.notify calls; see tests/js/stubs/harness.mjs for how useValue is wired
// to the fake hook cell so it participates in re-renders like a real SDK value subscription would.

export const STATUSBAR_AREAS = { right: 'statusbar-right', left: 'statusbar-left' }

export const haptic = (...args) => { haptic.calls.push(args) }
haptic.calls = []

export const host = {
  state: { focusedSessionId: null },
  notify: (...args) => { host.notify.calls.push(args) }
}
host.notify.calls = []

// useValue mirrors the SDK's "subscribe to a host value" hook. For this harness it just reads
// the current value each render; it does not implement the real subscription/update channel.
export function useValue(getterOrValue) {
  return typeof getterOrValue === 'function' ? getterOrValue() : getterOrValue
}
