import { readFileSync, statSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { gzipSync } from 'node:zlib';

/**
 * Computes the homepage's required JS dependency set from the production Vite
 * manifest and reports it against the budgets in FRONTEND_IMPLEMENTATION_PLAN.md.
 * Run `npm --prefix frontend run build` first, then `node frontend/scripts/bundle-budget.mjs`.
 */

const scriptDir = dirname(fileURLToPath(import.meta.url));
const frontendDir = resolve(scriptDir, '..');
const clientDir = join(frontendDir, '.svelte-kit', 'output', 'client');
const manifestPath = join(clientDir, '.vite', 'manifest.json');

const BUDGETS = {
  homepageGzipBytes: 102 * 1024,
  mainCssGzipBytes: 12.3 * 1024,
  oglGzipBytes: 39.4 * 1024,
  oglReduction: 0.2
};

function fail(message) {
  process.stderr.write(`bundle-budget: ${message}\n`);
  process.exitCode = 1;
}

let manifest;
try {
  manifest = JSON.parse(readFileSync(manifestPath, 'utf8'));
} catch {
  fail(`manifest not found at ${manifestPath}; run "npm --prefix frontend run build" first.`);
  process.exit(1);
}

function statSize(relativeFile) {
  const absolute = join(clientDir, relativeFile);
  try {
    const info = statSync(absolute);
    const raw = readFileSync(absolute);
    return { raw: info.size, gzip: gzipSync(raw).length };
  } catch {
    return { raw: 0, gzip: 0 };
  }
}

function isEntryNamed(expectedSrcSuffix, expectedName) {
  return (key, value) =>
    Boolean(value.isEntry) &&
    (value.name === expectedName || String(value.src || '').endsWith(expectedSrcSuffix));
}

// Homepage = the bootstrap app entry, the start entry, the root layout (nodes/0)
// and the root page (nodes/2). Everything they import statically is required.
const homepageRoots = Object.entries(manifest).filter(([key, value]) => {
  if (!value.isEntry) return false;
  return (
    key === 'node_modules/@sveltejs/kit/src/runtime/client/entry.js' ||
    value.name === 'entry/app' ||
    value.name === 'nodes/0' ||
    value.name === 'nodes/2'
  );
});

const homepageFiles = new Map();
const visited = new Set();

function collectStatic(key, value) {
  if (visited.has(key)) return;
  visited.add(key);
  const file = value.file;
  if (file) homepageFiles.set(file, statSize(file));
  for (const importKey of value.imports || []) {
    const imported = manifest[importKey];
    if (imported) collectStatic(importKey, imported);
  }
}

for (const [key, value] of homepageRoots) collectStatic(key, value);

function sum(sizes) {
  return sizes.reduce(
    (acc, size) => ({ raw: acc.raw + size.raw, gzip: acc.gzip + size.gzip }),
    { raw: 0, gzip: 0 }
  );
}

function formatBytes(bytes) {
  return `${bytes.toLocaleString('en-US')} B`;
}

function checkBudget(label, actual, budget, { higherIsBetter = false } = {}) {
  const pass = higherIsBetter ? actual >= budget : actual <= budget;
  const status = pass ? 'PASS' : 'FAIL';
  const direction = higherIsBetter
    ? `target >= ${formatBytes(budget)}`
    : `budget <= ${formatBytes(budget)}`;
  return { label, actual, budget, pass, status, direction };
}

const homepageTotal = sum([...homepageFiles.values()]);
const chunkSizes = Object.values(manifest)
  .filter((value) => value.isDynamicEntry)
  .map((value) => ({ name: value.name, file: value.file, ...statSize(value.file) }));

const oglChunk = chunkSizes.find((chunk) => chunk.name === 'oglAdapter');
const pageNode = Object.values(manifest).find((value) => value.name === 'nodes/2');
const pageStaticImport = (pageNode?.imports || [])
  .map((key) => manifest[key])
  .find((value) => value && value.isDynamicEntry);
const mainChunkName = pageStaticImport?.name;
const cssFiles = new Set();
for (const value of Object.values(manifest)) {
  for (const css of value.css || []) cssFiles.add(css);
}
const cssTotal = sum([...cssFiles].map((file) => statSize(file)));

const checks = [
  checkBudget('Homepage JS (gzip)', homepageTotal.gzip, BUDGETS.homepageGzipBytes),
  checkBudget('Main CSS (gzip)', cssTotal.gzip, BUDGETS.mainCssGzipBytes)
];

if (oglChunk) {
  checks.push(
    checkBudget('OGL chunk (gzip)', oglChunk.gzip, Math.round(BUDGETS.oglGzipBytes * (1 - BUDGETS.oglReduction)))
  );
}

process.stdout.write('Bundle budget report\n');
process.stdout.write('====================\n');
process.stdout.write(`Manifest: ${manifestPath}\n\n`);

process.stdout.write(`Homepage required JS: ${homepageFiles.size} files, ${formatBytes(homepageTotal.raw)} raw, ${formatBytes(homepageTotal.gzip)} gzip\n`);
for (const file of [...homepageFiles.keys()].sort()) {
  process.stdout.write(`  - ${file} (${formatBytes(homepageFiles.get(file).gzip)} gzip)\n`);
}

process.stdout.write('\nDynamic subpackages:\n');
for (const chunk of chunkSizes.sort((a, b) => b.gzip - a.gzip)) {
  process.stdout.write(`  - ${chunk.name || '(unnamed)'}: ${formatBytes(chunk.raw)} raw, ${formatBytes(chunk.gzip)} gzip\n`);
}

if (mainChunkName) {
  const mainChunk = chunkSizes.find((chunk) => chunk.name === mainChunkName);
  if (mainChunk) {
    process.stdout.write(`\nWorkspace main chunk: ${formatBytes(mainChunk.raw)} raw, ${formatBytes(mainChunk.gzip)} gzip\n`);
  }
}
if (cssTotal.raw) {
  process.stdout.write(`Main CSS: ${formatBytes(cssTotal.raw)} raw, ${formatBytes(cssTotal.gzip)} gzip\n`);
}

process.stdout.write('\nBudget checks:\n');
for (const check of checks) {
  process.stdout.write(`  [${check.status}] ${check.label}: ${formatBytes(check.actual)} (${check.direction})\n`);
}

if (checks.some((check) => !check.pass)) {
  process.exitCode = 1;
}
