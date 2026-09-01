'use strict';

const fs = require('node:fs');
const path = require('node:path');

const projectDir = path.resolve(__dirname, '..');
const outputDir = path.join(projectDir, 'dist-electron');

fs.rmSync(outputDir, { recursive: true, force: true });
