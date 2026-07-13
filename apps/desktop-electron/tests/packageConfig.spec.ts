import { existsSync, readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const projectDir = resolve(dirname(fileURLToPath(import.meta.url)), '..');

describe('Electron packaging configuration', () => {
  it('points electronDist at the installed Windows distribution', () => {
    const packageJson = JSON.parse(readFileSync(resolve(projectDir, 'package.json'), 'utf-8'));
    const electronDist = packageJson.build?.electronDist;
    const installedElectron = JSON.parse(readFileSync(resolve(projectDir, 'node_modules/electron/package.json'), 'utf-8'));

    expect(electronDist).toBe('node_modules/electron/dist');
    const distributionVersion = readFileSync(resolve(projectDir, electronDist, 'version'), 'utf-8').trim();
    expect(existsSync(resolve(projectDir, electronDist, 'electron.exe'))).toBe(true);
    expect(distributionVersion).toBe(installedElectron.version);
  });
});
