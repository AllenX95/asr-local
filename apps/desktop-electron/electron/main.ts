import { app, BrowserWindow, dialog, ipcMain, shell } from 'electron'
import { copyFile, mkdir, readFile, stat, writeFile } from 'node:fs/promises'
import { existsSync, statSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { ALLOWED_COMMANDS, DESKTOP_INVOKE_CHANNEL, WORKFLOW_EVENT_CHANNEL } from './channels.js'
import { WorkflowRuntimeClient } from './workflowRuntimeClient.js'
import { HostServices } from './hostServices.js'

const appDir = path.dirname(fileURLToPath(import.meta.url))
const desktopDir = path.resolve(appDir, '..')
const projectRoot = process.env.ASR_LOCAL_PROJECT_ROOT ?? (app.isPackaged ? path.join(process.resourcesPath, 'runtime-root') : path.resolve(desktopDir, '..', '..'))
const userDataDir = app.getPath('userData')
if (app.isPackaged) {
  process.env.ASR_LOCAL_STATE_DIR ??= path.join(userDataDir, 'workflow')
  process.env.ASR_LOCAL_CONFIG_DIR ??= path.join(userDataDir, 'config')
}
const workerDir = path.join(projectRoot, 'apps', 'worker-python')
const packagedPython = path.join(projectRoot, 'runtime', 'python', 'python.exe')
const developmentPython = path.join(workerDir, '.venv', 'Scripts', 'python.exe')
const pythonExecutable = process.env.ASR_LOCAL_PYTHON ?? (existsSync(packagedPython) ? packagedPython : developmentPython)
const outputsDir = app.isPackaged ? path.join(app.getPath('documents'), 'ASR Local', 'outputs') : path.join(projectRoot, 'outputs')
if (!process.env.ASR_LOCAL_V2_PIPELINE_MODE) process.env.ASR_LOCAL_V2_PIPELINE_MODE = app.isPackaged ? 'production' : 'auto'
const runtime = new WorkflowRuntimeClient(projectRoot)
const host = new HostServices(projectRoot, process.env.ASR_LOCAL_CONFIG_DIR, outputsDir, process.env.ASR_LOCAL_LEGACY_CONFIG_DIR, pythonExecutable, process.env.ASR_LOCAL_LEGACY_OUTPUTS_DIR)
let mainWindow: BrowserWindow | null = null
let quitting = false
const grantedPaths = new Set<string>()

function grantPath(target: string): string { const resolved = path.resolve(target); grantedPaths.add(resolved); return resolved }
function assertAllowedPath(target: string): string {
  const resolved = path.resolve(target)
  const roots = [projectRoot, app.getPath('userData'), outputsDir, ...grantedPaths]
  if (!roots.some((root) => resolved === root || resolved.startsWith(`${root}${path.sep}`))) throw new Error(`Path is outside approved locations: ${resolved}`)
  return resolved
}

function assertTrustedSender(event: Electron.IpcMainInvokeEvent): void {
  if (!mainWindow || event.sender !== mainWindow.webContents || event.senderFrame !== mainWindow.webContents.mainFrame) {
    throw new Error('Rejected IPC call from an untrusted sender')
  }
}

function requireString(args: Record<string, unknown>, key: string): string {
  const value = args[key]
  if (typeof value !== 'string') throw new Error(`${key} must be a string`)
  return value
}

async function invoke(command: string, args: Record<string, unknown>): Promise<unknown> {
  switch (command) {
    case 'get_app_info': return { project_root: projectRoot, outputs_dir: outputsDir, legacy_desktop_dir: desktopDir, worker_dir: workerDir, contract_version: 'workflow-contract-v2', logs: null }
    case 'select_audio_file': {
      const result = await dialog.showOpenDialog(mainWindow!, { properties: ['openFile'], filters: [{ name: 'Audio and video', extensions: ['wav', 'mp3', 'm4a', 'aac', 'flac', 'ogg', 'opus', 'mp4', 'mov', 'mkv', 'webm'] }] })
      return result.canceled || !result.filePaths[0] ? null : grantPath(result.filePaths[0])
    }
    case 'select_markdown_file': {
      const result = await dialog.showOpenDialog(mainWindow!, { properties: ['openFile'], filters: [{ name: 'Markdown', extensions: ['md', 'markdown', 'txt'] }] })
      return result.canceled || !result.filePaths[0] ? null : grantPath(result.filePaths[0])
    }
    case 'select_output_dir': {
      const result = await dialog.showOpenDialog(mainWindow!, { properties: ['openDirectory', 'createDirectory'] })
      return result.canceled || !result.filePaths[0] ? null : grantPath(result.filePaths[0])
    }
    case 'read_text_file': {
      const filePath = assertAllowedPath(requireString(args, 'path')); const info = await stat(filePath); return { path: filePath, content: await readFile(filePath, 'utf8'), size_bytes: info.size, modified_ms: info.mtimeMs }
    }
    case 'save_text_file': {
      const filePath = assertAllowedPath(requireString(args, 'path')); const content = requireString(args, 'content')
      await mkdir(path.dirname(filePath), { recursive: true }); await writeFile(filePath, content, 'utf8'); const info = await stat(filePath); return { path: filePath, size_bytes: info.size, modified_ms: info.mtimeMs }
    }
    case 'open_path': {
      const target = assertAllowedPath(requireString(args, 'path')); if (!existsSync(target)) throw new Error(`path does not exist: ${target}`)
      if (statSync(target).isFile()) shell.showItemInFolder(target)
      else { const error = await shell.openPath(target); if (error) throw new Error(error) }
      return null
    }
    case 'load_models_config': return host.loadModelsConfig()
    case 'save_model_paths': return host.saveModelPaths(args)
    case 'load_asr_profiles': return host.loadProfiles('asr')
    case 'save_asr_profile': return host.saveProfile('asr', args.profile as Record<string, unknown>)
    case 'delete_asr_profile': return host.deleteProfile('asr', requireString(args, 'name'))
    case 'load_summary_profiles': return host.loadProfiles('summary')
    case 'save_summary_profile': return host.saveProfile('summary', args.profile as Record<string, unknown>)
    case 'delete_summary_profile': return host.deleteProfile('summary', requireString(args, 'name'))
    case 'load_summary_templates': return host.loadTemplates()
    case 'save_summary_template': return host.saveTemplate(requireString(args, 'name'), requireString(args, 'prompt'))
    case 'delete_summary_template': return host.deleteTemplate(requireString(args, 'name'))
    case 'list_history_items': return host.history(args.limit)
    case 'worker_health_check': return runtime.request('runtime.capabilities', {})
    case 'workflow_v2_capabilities': return runtime.request('runtime.capabilities', {})
    case 'workflow_v2_catalogs': return host.catalogs()
    case 'workflow_v2_prompt_preview': return runtime.request('prompt.preview', args.input as Record<string, unknown>)
    case 'workflow_v2_submit': {
      const draft = args.draft as Record<string, any>
      assertAllowedPath(String(draft.source?.path ?? ''))
      assertAllowedPath(String(draft.output?.directory ?? ''))
      return runtime.request('workflow.submit', { draft: await host.trustedWorkflowDraft(draft) }, requireString(args, 'operationId'))
    }
    case 'workflow_v2_list': return runtime.request('workflow.list', { statuses: args.statuses ?? [], cursor: null, limit: 100 })
    case 'workflow_v2_get': return runtime.request('workflow.get', { workflow_id: requireString(args, 'workflowId'), timeline_limit: args.timelineLimit ?? 200 })
    case 'workflow_v2_clear': return runtime.request('workflow.clear', { workflow_id: requireString(args, 'workflowId') }, requireString(args, 'operationId'))
    case 'workflow_v2_control': return runtime.request('workflow.control', { workflow_id: requireString(args, 'workflowId'), expected_attempt_id: requireString(args, 'expectedAttemptId'), action: requireString(args, 'action') }, requireString(args, 'operationId'))
    case 'workflow_v2_retry': return runtime.request('workflow.retry', { workflow_id: requireString(args, 'workflowId'), expected_attempt_id: requireString(args, 'expectedAttemptId'), expected_sequence: args.expectedSequence, from_stage: requireString(args, 'fromStage'), input_artifact_id: args.inputArtifactId ?? null }, requireString(args, 'operationId'))
    case 'workflow_v2_register_revision': return runtime.request('artifact.register_revision', args.params as Record<string, unknown>, requireString(args, 'operationId'))
    case 'workflow_v2_shutdown': await runtime.shutdown(); return { state: 'stopped', active_workflow_ids: [] }
    default: throw new Error(`Unsupported desktop command: ${command}`)
  }
}

async function createWindow(): Promise<void> {
  await host.initialize()
  const stateDir = process.env.ASR_LOCAL_STATE_DIR
  const legacyOutputs = process.env.ASR_LOCAL_LEGACY_OUTPUTS_DIR
  if (stateDir && legacyOutputs) {
    const source = path.join(legacyOutputs, '.workflow', 'registry.sqlite3')
    const target = path.join(stateDir, 'registry.sqlite3')
    if (!existsSync(target) && existsSync(source)) {
      await mkdir(stateDir, { recursive: true })
      await copyFile(source, target)
    }
  }
  mainWindow = new BrowserWindow({
    width: 1360, height: 900, minWidth: 1100, minHeight: 720, show: false,
    webPreferences: { preload: path.join(appDir, 'preload.cjs'), contextIsolation: true, nodeIntegration: false, sandbox: true },
  })
  mainWindow.maximize()
  mainWindow.once('ready-to-show', () => mainWindow?.show())
  mainWindow.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
  mainWindow.webContents.on('will-navigate', (event, url) => {
    const rendererUrl = process.env.ELECTRON_RENDERER_URL
    if (rendererUrl ? !url.startsWith(rendererUrl) : !url.startsWith('file:')) event.preventDefault()
  })
  const rendererUrl = process.env.ELECTRON_RENDERER_URL
  if (rendererUrl) await mainWindow.loadURL(rendererUrl)
  else await mainWindow.loadFile(path.join(desktopDir, 'dist', 'index.html'))
}

ipcMain.handle(DESKTOP_INVOKE_CHANNEL, async (event, command: unknown, args: unknown) => {
  assertTrustedSender(event)
  if (typeof command !== 'string' || !ALLOWED_COMMANDS.has(command)) throw new Error('Unsupported desktop command')
  if (args !== undefined && (typeof args !== 'object' || args === null || Array.isArray(args))) throw new Error('Desktop command args must be an object')
  return invoke(command, (args ?? {}) as Record<string, unknown>)
})

runtime.on('workflow-event', (payload: any) => {
  mainWindow?.webContents.send(WORKFLOW_EVENT_CHANNEL, payload)
  if (payload?.type !== 'credentials_required' || !payload?.data) return
  void host.credentialGrant(payload.data).then(({ secret }) => runtime.request('secret.provide', {
    ...payload.data,
    workflow_id: payload.workflow_id,
    expected_attempt_id: payload.attempt_id,
    secret,
    lease_scope: 'attempt',
  })).catch((error) => console.error('Credential grant rejected:', error))
})
runtime.on('protocol-error', (error) => console.error(error))
runtime.on('error', (error) => console.error(error))

app.whenReady().then(createWindow)
app.on('window-all-closed', () => app.quit())
app.on('before-quit', (event) => {
  if (quitting) return
  event.preventDefault(); quitting = true
  void runtime.shutdown().finally(() => app.exit(0))
})
