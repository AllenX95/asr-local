'use strict';

const fs = require('node:fs');
const path = require('node:path');

const projectDir = path.resolve(__dirname, '..');
const source = path.join(projectDir, 'node_modules', '@iarna', 'toml');
const destination = path.join(projectDir, 'dist-electron', 'vendor', 'toml');

fs.rmSync(destination, { recursive: true, force: true });
fs.mkdirSync(path.dirname(destination), { recursive: true });
fs.cpSync(source, destination, { recursive: true });