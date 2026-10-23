#!/usr/bin/env node
// Benchwarmers from npm: finds Python 3.11 or later and hands over to
// launcher.py, the same launcher a pip install runs, which knows the commands,
// the help and the first run. The app itself is the Python standard library
// only; nothing is installed beside it.
'use strict';

const { spawn, spawnSync } = require('child_process');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');

// The first Python that is 3.11 or later: `py -3` on Windows, then python3, python.
function findPython() {
  const tries = process.platform === 'win32'
    ? [['py', ['-3']], ['python', []], ['python3', []]]
    : [['python3', []], ['python', []]];
  for (const [cmd, pre] of tries) {
    const r = spawnSync(cmd, pre.concat(['-c', 'import sys; print("%d %d" % sys.version_info[:2])']), { encoding: 'utf8' });
    if (r.status !== 0 || !r.stdout) continue;
    const [major, minor] = r.stdout.trim().split(' ').map(Number);
    if (major === 3 && minor >= 11) return [cmd, pre];
  }
  return null;
}

const python = findPython();
if (!python) {
  console.error('benchwarmers needs Python 3.11 or later on PATH (https://www.python.org/downloads/).');
  process.exit(1);
}
// `bench` and `benchwarmers` are the same program; usage names the one typed.
const env = Object.assign({}, process.env, {
  BENCHWARMERS_LAUNCHER: 'npm', BENCHWARMERS_PROG: path.basename(process.argv[1] || 'benchwarmers').replace(/\.js$/, ''),
  PYTHONDONTWRITEBYTECODE: '1', PYTHONUTF8: '1' });
const child = spawn(python[0], python[1].concat([path.join(ROOT, 'launcher.py')], process.argv.slice(2)),
  { stdio: 'inherit', env });
for (const sig of ['SIGINT', 'SIGTERM']) process.on(sig, () => child.kill(sig));
child.on('exit', (code, signal) => process.exit(code === null ? (signal ? 1 : 0) : code));
