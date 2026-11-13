import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const frontendDir = path.resolve(__dirname, '..');
const browsersDir = path.resolve(frontendDir, '.playwright-browsers');

const args = process.argv.slice(2);
const subcommand = args[0] ?? 'test';
const env = { ...process.env };

if (!env.PLAYWRIGHT_BROWSERS_PATH) {
  if (subcommand === 'install' || subcommand === 'install-deps' || existsSync(browsersDir)) {
    env.PLAYWRIGHT_BROWSERS_PATH = browsersDir;
  }
}

if (subcommand !== 'install' && subcommand !== 'install-deps') {
  const existingNoProxy = (env.NO_PROXY || env.no_proxy || '')
    .split(',')
    .map((entry) => entry.trim())
    .filter(Boolean);
  const mergedNoProxy = Array.from(new Set([...existingNoProxy, '127.0.0.1', 'localhost'])).join(',');
  env.NO_PROXY = mergedNoProxy;
  env.no_proxy = mergedNoProxy;
}

const localBin = path.resolve(
  frontendDir,
  'node_modules',
  '.bin',
  process.platform === 'win32' ? 'playwright.cmd' : 'playwright'
);
const command = existsSync(localBin) ? localBin : process.platform === 'win32' ? 'npx.cmd' : 'npx';
const commandArgs = existsSync(localBin) ? args : ['playwright', ...args];

const child = spawn(command, commandArgs, {
  cwd: frontendDir,
  env,
  stdio: 'inherit',
  shell: process.platform === 'win32',
});

child.on('exit', (code, signal) => {
  if (signal) {
    process.kill(process.pid, signal);
    return;
  }
  process.exit(code ?? 1);
});

child.on('error', (error) => {
  console.error(error);
  process.exit(1);
});
