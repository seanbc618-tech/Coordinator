#!/usr/bin/env node
// Bundles src/entry.tsx into a single self-contained dist/entry.js.
// Adapted from Hermes Agent (MIT) by Nous Research.
// See THIRD_PARTY_NOTICES.md for attribution.
import { build } from 'esbuild'
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs'
import { createHash } from 'node:crypto'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const root = resolve(here, '..')
const distDir = resolve(root, 'dist')
const out = resolve(distDir, 'entry.js')

mkdirSync(distDir, { recursive: true })

// Resolve Ink internals (e.g. instances registry) not listed in package exports.
const resolveInkInternals = {
  name: 'resolve-ink-internals',
  setup(b) {
    b.onResolve({ filter: /^ink\/build\// }, args => ({
      path: resolve(root, 'node_modules', args.path),
    }))
  },
}

// Stub out react-devtools-core — only used in Ink dev mode.
const stubDevtools = {
  name: 'stub-react-devtools-core',
  setup(b) {
    b.onResolve({ filter: /^react-devtools-core$/ }, args => ({
      path: args.path,
      namespace: 'stub-devtools'
    }))
    b.onLoad({ filter: /.*/, namespace: 'stub-devtools' }, () => ({
      contents: 'export default { initialize() {}, connectToDevTools() {} }',
      loader: 'js'
    }))
  }
}

await build({
  entryPoints: [resolve(root, 'src/entry.tsx')],
  bundle: true,
  platform: 'node',
  format: 'esm',
  target: 'node20',
  outfile: out,
  sourcemap: true,
  jsx: 'automatic',
  jsxImportSource: 'react',
  plugins: [resolveInkInternals, stubDevtools],
  banner: {
    js: "import { createRequire as __cr } from 'node:module'; const require = __cr(import.meta.url);"
  },
  logLevel: 'info'
})

// Strip shebang — the launcher always invokes as `node dist/entry.js`.
const body = readFileSync(out, 'utf8')
if (body.startsWith('#!')) {
  writeFileSync(out, body.slice(body.indexOf('\n') + 1))
}

// Generate build hash from bundle content.
const bundleContent = readFileSync(out, 'utf8')
const buildHash = createHash('sha256').update(bundleContent).digest('hex').slice(0, 16)

// Write manifest.json with protocol version and build hash.
const manifest = {
  protocol_version: 1,
  build_hash: buildHash,
  bundle: 'entry.js',
  source_map: 'entry.js.map',
  built_at: new Date().toISOString(),
}
writeFileSync(resolve(distDir, 'manifest.json'), JSON.stringify(manifest, null, 2) + '\n')

// Copy the production bundle into Python package data for wheel installs.
const packageBundleDir = resolve(root, '..', 'src', 'local_cli_coordinator', 'tui_bundle')
mkdirSync(packageBundleDir, { recursive: true })

const sourceMapPolicy = {
  ship_source_maps: true,
  bundle_source_map: 'entry.js.map',
}
writeFileSync(
  resolve(packageBundleDir, 'source_map_policy.json'),
  JSON.stringify(sourceMapPolicy, null, 2) + '\n',
)

const packageArtifacts = [
  [resolve(distDir, 'entry.js'), 'entry.js'],
  [resolve(distDir, 'manifest.json'), 'manifest.json'],
  [resolve(distDir, 'entry.js.map'), 'entry.js.map'],
  [resolve(root, '..', 'THIRD_PARTY_NOTICES.md'), 'THIRD_PARTY_NOTICES.md'],
]

for (const [source, name] of packageArtifacts) {
  writeFileSync(resolve(packageBundleDir, name), readFileSync(source))
}

console.log(`built ${out}`)
console.log(`manifest: protocol_version=1, build_hash=${buildHash}`)
console.log(`packaged bundle data in ${packageBundleDir}`)
