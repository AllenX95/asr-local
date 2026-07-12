import path from 'node:path'
import { describe, expect, it } from 'vitest'
import { resolveRuntimePaths } from '../electron/runtimePaths.js'

describe('resolveRuntimePaths', () => {
  it('uses installed application data for real-data hot debugging', () => {
    const result = resolveRuntimePaths({
      isPackaged: false,
      appDir: 'E:/repo/apps/desktop-electron/dist-electron',
      resourcesPath: 'E:/installed/resources',
      userDataDir: 'C:/Users/test/AppData/Roaming/ASR Local',
      documentsDir: 'C:/Users/test/Documents',
      env: {
        ASR_LOCAL_DEBUG_DATA_PROFILE: 'real',
        ASR_LOCAL_PROJECT_ROOT: 'E:/repo',
        ASR_LOCAL_PYTHON: 'E:/repo/apps/desktop-electron/runtime/python/python.exe',
      },
      pathExists: () => true,
    })
    expect(result.configDir).toBe(path.normalize('C:/Users/test/AppData/Roaming/ASR Local/config'))
    expect(result.stateDir).toBe(path.normalize('C:/Users/test/AppData/Roaming/ASR Local/workflow'))
    expect(result.outputsDir).toBe(path.normalize('C:/Users/test/Documents/ASR Local/outputs'))
    expect(result.logsDir).toBe(path.normalize('C:/Users/test/AppData/Roaming/ASR Local/logs'))
  })

  it('keeps isolated debugging under the requested root', () => {
    const result = resolveRuntimePaths({
      isPackaged: false,
      appDir: 'E:/repo/apps/desktop-electron/dist-electron',
      resourcesPath: '',
      userDataDir: 'C:/real',
      documentsDir: 'C:/docs',
      env: { ASR_LOCAL_PROJECT_ROOT: 'E:/repo', ASR_LOCAL_DEBUG_DATA_PROFILE: 'isolated', ASR_LOCAL_DEBUG_DATA_ROOT: 'E:/repo/tmp/electron-debug' },
      pathExists: (candidate) => candidate.endsWith('python.exe'),
    })
    expect(result.configDir).toBe(path.normalize('E:/repo/tmp/electron-debug/config'))
    expect(result.stateDir).toBe(path.normalize('E:/repo/tmp/electron-debug/workflow'))
    expect(result.outputsDir).toBe(path.normalize('E:/repo/tmp/electron-debug/outputs'))
    expect(result.logsDir).toBe(path.normalize('E:/repo/tmp/electron-debug/logs'))
  })
})
