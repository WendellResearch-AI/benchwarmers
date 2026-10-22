#!/usr/bin/env node
// Benchwarmers from npm: a launcher for the Python app this package carries.
// It finds Python 3.11 or later, keeps your data in your user profile (the
// folder scripts/init.py picks, or BENCHWARMERS_HOME), sets that folder up on
// the first run, then starts the dashboard. The app itself is the Python
// standard library only; nothing is installed beside it.
'use strict';

const { spawn, spawnSync } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');

// The same folder as scripts/init.py's default_folder(), so a checkout and an
// npm install on one machine find the same data.
function defaultHome() {
  if (process.platform === 'win32') return path.join(process.env.LOCALAPPDATA || os.homedir(), 'benchwarmers');
  if (process.platform === 'darwin') return path.join(os.homedir(), 'Library', 'Application Support', 'benchwarmers');
  return path.join(process.env.XDG_DATA_HOME || path.join(os.homedir(), '.local', 'share'), 'benchwarmers');
}

function usage() {
  return [
    'Usage: benchwarmers [command]',
    '',
    '  serve        the dashboard, with the gateway at /v1: http://localhost:8122 (the default)',
    '  gateway      the gateway alone: http://127.0.0.1:8123/v1',
    '  init [...]   set up a data folder, optionally with a first endpoint (init --help)',
    '  mcp          an MCP server on stdio for agents, a client of a running Benchwarmers (BENCHWARMERS_URL)',
    '  --version    the version',
    '',
    'Your data lives in BENCHWARMERS_HOME, else ' + defaultHome() + '.',
    'BENCH_PORT changes the port. Docs: https://github.com/WendellResearch-AI/benchwarmers',
  ].join('\n');
}

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

function main(argv) {
  const command = argv[0] && !argv[0].startsWith('-') ? argv[0] : 'serve';
  if (argv.includes('--version') || argv.includes('-v')) {
    console.log(fs.readFileSync(path.join(ROOT, 'VERSION'), 'utf8').trim());
    return 0;
  }
  if (command !== 'init' && (argv.includes('--help') || argv.includes('-h'))) {
    console.log(usage());
    return 0;
  }
  const scripts = { serve: 'serve.py', gateway: 'gateway.py', mcp: 'mcp_server.py', init: path.join('scripts', 'init.py') };
  if (!scripts[command]) {
    console.error('benchwarmers: no command "' + command + '"\n\n' + usage());
    return 2;
  }
  const python = findPython();
  if (!python) {
    console.error('benchwarmers needs Python 3.11 or later on PATH (https://www.python.org/downloads/).');
    return 1;
  }
  const home = (process.env.BENCHWARMERS_HOME || '').trim() || defaultHome();
  // No bytecode beside the code: an npm folder may not be writable, and an
  // upgrade replaces it anyway.
  const env = Object.assign({}, process.env, {
    BENCHWARMERS_HOME: home, BENCHWARMERS_LAUNCHER: 'npm', PYTHONDONTWRITEBYTECODE: '1', PYTHONUTF8: '1' });
  const rest = command === argv[0] ? argv.slice(1) : argv;
  const py = (script, args) => python[1].concat([path.join(ROOT, script)], args);

  // The first run makes the data folder from examples/ (init.py never writes
  // over a file already there, and with BENCHWARMERS_HOME set it leaves the
  // package folder alone). Not for mcp: it keeps no data, and its stdout
  // carries protocol messages only.
  if (command !== 'init' && command !== 'mcp' && !fs.existsSync(home)) {
    console.log('First run: making your data folder at ' + home);
    const r = spawnSync(python[0], py(scripts.init, [home]), { stdio: 'inherit', env, cwd: ROOT });
    if (r.status !== 0) return r.status || 1;
  }
  const child = spawn(python[0], py(scripts[command], rest), { stdio: 'inherit', env, cwd: ROOT });
  for (const sig of ['SIGINT', 'SIGTERM']) process.on(sig, () => child.kill(sig));
  child.on('exit', (code, signal) => process.exit(code === null ? (signal ? 1 : 0) : code));
  return null;
}

const code = main(process.argv.slice(2));
if (code !== null) process.exit(code);
