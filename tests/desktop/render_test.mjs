// node --import ./register.mjs render_test.mjs <fixtures.json>  -> prints rendered HTML of the page
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'

globalThis.__MANDATE_FIXTURES__ = JSON.parse(readFileSync(process.argv[2], 'utf8'))
const plugin = (await import('../../desktop/plugin.js')).default
const req = createRequire(process.env.MANDATE_REACT_DIR + '/x.js')
const { renderToStaticMarkup } = req('react-dom/server')
const contributions = []
const disposers = []
plugin.register({
  rest: async p => globalThis.__MANDATE_FIXTURES__[p],
  onDispose: fn => disposers.push(fn),
  registerMany: cs => { contributions.push(...cs); return () => {} },
  register: c => { contributions.push(c); return () => {} }
})
const page = contributions.find(c => c.area === 'routes')
const palette = contributions.find(c => c.area === 'palette')
palette.data.run()
const { navigated } = await import('./sdk_stub.mjs')
const html = renderToStaticMarkup(page.render())
process.stdout.write(JSON.stringify({ id: plugin.id, areas: contributions.map(c => c.area), navigated, disposers: disposers.length, html }))
