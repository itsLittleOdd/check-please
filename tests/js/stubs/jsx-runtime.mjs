// Minimal stand-in for 'react/jsx-runtime'. Elements are plain descriptors, same shape real
// React produces: { type, props }. No key/ref handling needed for this plugin's tree.

export function jsx(type, props) {
  return { type, props }
}

export function jsxs(type, props) {
  return { type, props }
}
