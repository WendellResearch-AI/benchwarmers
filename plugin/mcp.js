#!/usr/bin/env node
// The plugin's MCP server: `benchwarmers mcp`. From a checkout of the repo (a
// local marketplace, or the clone `/plugin marketplace add` makes) the
// launcher beside this folder; otherwise the npm package through npx. Node is
// the command in .mcp.json, not npx: on native Windows npx is a .cmd file,
// which only starts through a shell.
'use strict';

const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');

const local = path.join(__dirname, '..', 'bin', 'benchwarmers.js');
const [cmd, args] = fs.existsSync(local)
  ? [process.execPath, [local, 'mcp']]
  : ['npx', ['-y', '@wendellresearch-ai/benchwarmers', 'mcp']];
const child = spawn(cmd, args, { stdio: 'inherit', shell: process.platform === 'win32' && cmd === 'npx' });
child.on('error', (e) => { console.error('benchwarmers mcp: ' + e.message); process.exit(1); });
child.on('exit', (code) => process.exit(code === null ? 1 : code));
