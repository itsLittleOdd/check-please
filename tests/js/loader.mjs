// Node module customization hook (module.register API) that redirects the three bare
// specifiers desktop/plugin.js imports to the local stubs in tests/js/stubs/, so the real
// plugin file can be imported unmodified in a plain Node process with no package.json,
// no node_modules and no network install.

const MAP = {
  'react': new URL('./stubs/react.mjs', import.meta.url).href,
  'react/jsx-runtime': new URL('./stubs/jsx-runtime.mjs', import.meta.url).href,
  '@hermes/plugin-sdk': new URL('./stubs/plugin-sdk.mjs', import.meta.url).href
}

export async function resolve(specifier, context, nextResolve) {
  if (specifier in MAP) {
    return { url: MAP[specifier], shortCircuit: true }
  }
  return nextResolve(specifier, context)
}
