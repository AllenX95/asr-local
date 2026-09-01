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

  it('builds into staging and validates bundled ffmpeg before activation', () => {
    const fastPackageScript = readFileSync(resolve(projectDir, '../../scripts/dev/package_electron_fast.ps1'), 'utf-8');
    const runtimeBuildScript = readFileSync(resolve(projectDir, '../../scripts/build/build_python_runtime.ps1'), 'utf-8');

    expect(fastPackageScript).toContain('release-electron-staging');
    expect(fastPackageScript).toContain('Assert-PackagedRuntime $stagedAppDir');
    expect(fastPackageScript).toContain('Assert-TargetAppStopped');
    expect(fastPackageScript.indexOf('Assert-PackagedRuntime $stagedAppDir'))
      .toBeLessThan(fastPackageScript.indexOf('Move-SafeDirectoryAtomic $stagedAppDir $targetAppDir'));
    expect(runtimeBuildScript).toContain('imageio_ffmpeg.get_ffmpeg_exe()');
  });

  it('cleans main output, excludes test sources, and packages runtime dependencies once', () => {
    const packageJson = JSON.parse(readFileSync(resolve(projectDir, 'package.json'), 'utf-8'));
    const electronTsconfig = JSON.parse(readFileSync(resolve(projectDir, 'tsconfig.electron.json'), 'utf-8'));
    const cleanScript = readFileSync(resolve(projectDir, 'electron/cleanMainBuild.cjs'), 'utf-8');

    expect(packageJson.scripts?.['electron:compile']).toContain('node electron/cleanMainBuild.cjs');
    expect(cleanScript).toContain("path.join(projectDir, 'dist-electron')");
    expect(electronTsconfig.exclude).toContain('electron/**/*.spec.ts');
    expect(packageJson.build?.files).toEqual([
      'dist/**/*',
      'dist-electron/**/*',
      'package.json'
    ]);
  });
});
