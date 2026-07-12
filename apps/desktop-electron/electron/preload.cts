const { contextBridge, ipcRenderer } = require('electron') as typeof import('electron')

const DESKTOP_INVOKE_CHANNEL = 'asr-local:desktop:invoke'
const WORKFLOW_EVENT_CHANNEL = 'asr-local:workflow:event'
const RUNTIME_STATUS_EVENT_CHANNEL = 'asr-local:runtime:status'
const allowedCommands = new Set([
  'get_app_info', 'select_audio_file', 'select_markdown_file', 'select_output_dir', 'read_text_file', 'save_text_file', 'open_path',
  'load_models_config', 'save_model_paths', 'load_asr_profiles', 'save_asr_profile', 'delete_asr_profile',
  'load_summary_profiles', 'save_summary_profile', 'delete_summary_profile', 'load_summary_templates',
  'save_summary_template', 'delete_summary_template', 'list_history_items',
  'worker_health_check',
  'workflow_v2_capabilities', 'workflow_v2_catalogs', 'workflow_v2_prompt_preview', 'workflow_v2_submit', 'workflow_v2_list', 'workflow_v2_get',
  'workflow_v2_clear', 'workflow_v2_control', 'workflow_v2_retry', 'workflow_v2_register_revision', 'workflow_v2_shutdown',
])

contextBridge.exposeInMainWorld('asrLocal', {
  invoke(command: string, args?: Record<string, unknown>) {
    if (!allowedCommands.has(command)) return Promise.reject(new Error(`Unsupported desktop command: ${command}`))
    return ipcRenderer.invoke(DESKTOP_INVOKE_CHANNEL, command, args)
  },
  onWorkflowEvent(handler: (payload: unknown) => void) {
    const listener = (_event: Electron.IpcRendererEvent, payload: unknown) => handler(payload)
    ipcRenderer.on(WORKFLOW_EVENT_CHANNEL, listener)
    return () => ipcRenderer.removeListener(WORKFLOW_EVENT_CHANNEL, listener)
  },
  onRuntimeStatus(handler: (payload: unknown) => void) {
    const listener = (_event: Electron.IpcRendererEvent, payload: unknown) => handler(payload)
    ipcRenderer.on(RUNTIME_STATUS_EVENT_CHANNEL, listener)
    return () => ipcRenderer.removeListener(RUNTIME_STATUS_EVENT_CHANNEL, listener)
  },
})
