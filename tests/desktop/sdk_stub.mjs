// Minimal stand-in for @hermes/plugin-sdk so plugin.js can be rendered under node in tests.
export const ROUTES_AREA = 'routes'
export const SIDEBAR_NAV_AREA = 'sidebar.nav'
export const PALETTE_AREA = 'palette'
export const navigated = []
export const host = { navigate: p => navigated.push(p) }
export function useQuery({ queryKey }) {
  const fx = globalThis.__MANDATE_FIXTURES__
  const key = queryKey[1] === 'jobs' ? '/jobs' : `/jobs/${encodeURIComponent(queryKey[2])}`
  return key in fx ? { data: fx[key] } : { error: new Error('404') }
}
