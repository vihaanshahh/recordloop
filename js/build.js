#!/usr/bin/env node
/**
 * Simple build script — bundles core.js into a UMD script tag version.
 * No dependencies needed.
 */
const fs = require('fs')
const path = require('path')

const src = fs.readFileSync(path.join(__dirname, 'src/core.js'), 'utf8')

// Strip ES module syntax, wrap in IIFE
const umd = `// RecordLoop v0.1.0 — https://github.com/vihaanshahh/recordloop
// Drop this script into any page to capture interactions.
;(function(root) {
${src
  .replace(/^export\s+default\s+\w+$/gm, '')
  .replace(/^export\s+/gm, '')
  .replace(/^import\s+.*$/gm, '')
}
  root.RecordLoop = RecordLoop;
})(typeof window !== 'undefined' ? window : globalThis);
`

fs.mkdirSync(path.join(__dirname, 'dist'), { recursive: true })
fs.writeFileSync(path.join(__dirname, 'dist/recordloop.js'), umd)
console.log('Built dist/recordloop.js')
