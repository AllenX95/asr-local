import path from 'node:path'

export type RuntimePathEnvironment = Record<string, string | undefined>

export interface RuntimePathInput {
  isPackaged: boolean
  appDir: string
  resourcesPath: string
  userDataDir: string
  documentsDir: string
  env: RuntimePathEnvironment
  pathExists: (candidate: string) => boolean
}

export interface RuntimePaths {
  projectRoot: string
  desktopDir: string
  workerDir: string
  pythonExecutable: string
  configDir: string
  stateDir: string
  outputsDir: string
  legacyOutputsDir?: string
  logsDir: string
}

export function resolveRuntimePaths(input: RuntimePathInput): RuntimePaths {
  const desktopDir = path.resolve(input.appDir, '..')
  const projectRoot = path.resolve(input.env.ASR_LOCAL_PROJECT_ROOT || (input.isPackaged ? path.join(input.resourcesPath, 'runtime-root') : path.resolve(desktopDir, '..', '..')))
  const workerDir = path.join(projectRoot, 'apps', 'worker-python')
  const packagedPython = path.join(projectRoot, 'runtime', 'python', 'python.exe')
  const developmentPython = path.join(workerDir, '.venv', 'Scripts', 'python.exe')
  const pythonExecutable = input.env.ASR_LOCAL_PYTHON || (input.pathExists(packagedPython) ? packagedPython : developmentPython)
  const profile = input.env.ASR_LOCAL_DEBUG_DATA_PROFILE

  let configDir: string
  let stateDir: string
  let outputsDir: string
  let logsDir: string
  if (!input.isPackaged && profile === 'isolated') {
    const root = path.resolve(input.env.ASR_LOCAL_DEBUG_DATA_ROOT || path.join(projectRoot, 'tmp', 'electron-debug'))
    configDir = path.join(root, 'config')
    stateDir = path.join(root, 'workflow')
    outputsDir = path.join(root, 'outputs')
    logsDir = path.join(root, 'logs')
  } else if (input.isPackaged) {
    configDir = path.join(input.userDataDir, 'config')
    stateDir = path.join(input.userDataDir, 'workflow')
    outputsDir = path.join(input.documentsDir, 'ASR Local')
    logsDir = path.join(input.userDataDir, 'logs')
  } else if (profile === 'real') {
    configDir = path.join(input.userDataDir, 'config')
    stateDir = path.join(input.userDataDir, 'workflow')
    outputsDir = path.join(projectRoot, 'outputs')
    logsDir = path.join(input.userDataDir, 'logs')
  } else {
    configDir = path.join(projectRoot, 'config')
    stateDir = path.join(projectRoot, 'outputs', '.workflow')
    outputsDir = path.join(projectRoot, 'outputs')
    logsDir = path.join(projectRoot, 'outputs', 'logs')
  }

  const resolvedOutputsDir = path.resolve(input.env.ASR_LOCAL_OUTPUTS_DIR || outputsDir)
  const legacyOutputsCandidate = input.env.ASR_LOCAL_LEGACY_OUTPUTS_DIR || (input.isPackaged ? path.join(projectRoot, 'outputs') : undefined)
  const legacyOutputsDir = legacyOutputsCandidate ? path.resolve(legacyOutputsCandidate) : undefined

  return {
    projectRoot,
    desktopDir,
    workerDir,
    pythonExecutable: path.resolve(pythonExecutable),
    configDir: path.resolve(input.env.ASR_LOCAL_CONFIG_DIR || configDir),
    stateDir: path.resolve(input.env.ASR_LOCAL_STATE_DIR || stateDir),
    outputsDir: resolvedOutputsDir,
    legacyOutputsDir: legacyOutputsDir === resolvedOutputsDir ? undefined : legacyOutputsDir,
    logsDir: path.resolve(input.env.ASR_LOCAL_LOG_DIR || logsDir),
  }
}
