import { pathToFileURL } from 'node:url'
const STUB = pathToFileURL(new URL('./sdk_stub.mjs', import.meta.url).pathname).href
export async function resolve(specifier, context, next) {
  if (specifier === '@hermes/plugin-sdk') return { url: STUB, shortCircuit: true }
  if (specifier === 'react' || specifier.startsWith('react/') || specifier.startsWith('react-dom')) {
    return next(specifier, { ...context, parentURL: pathToFileURL(process.env.MANDATE_REACT_DIR + '/x.mjs').href })
  }
  return next(specifier, context)
}
