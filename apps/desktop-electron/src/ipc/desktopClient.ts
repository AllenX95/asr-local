import type {
  AppInfo,
  AsrCloudProfile,
  AsrProfilesState,
  HistoryItem,
  LocalAsrModelKey,
  ModelsConfig,
  SavedFile,
  SummaryProfile,
  SummaryProfilesState,
  SummaryTemplate,
  WorkflowCatalogs,
  TextFile
} from './workerTypes';
import { electronBridge } from './desktopBridge';

function desktopOnly<T>(feature: string): Promise<T> {
  return Promise.reject(new Error(`${feature} 需要在 Electron 桌面应用中运行。`));
}

function invokeDesktop<T>(command: string, args?: Record<string, unknown>, fallback?: () => T | Promise<T>) {
  const electron = electronBridge();
  if (electron) {
    return electron.invoke<T>(command, args);
  }
  if (fallback) {
    return Promise.resolve(fallback());
  }
  return desktopOnly<T>(command);
}

export const api = {
  appInfo: () =>
    invokeDesktop<AppInfo>('get_app_info', undefined, () => ({
      project_root: '',
      outputs_dir: '',
      legacy_desktop_dir: '',
      worker_dir: '',
      contract_version: 'workflow-contract-v2',
      logs: null
    })),
  selectAudioFile: () => invokeDesktop<string | null>('select_audio_file', undefined, () => null),
  selectMarkdownFile: () => invokeDesktop<string | null>('select_markdown_file', undefined, () => null),
  selectOutputDir: () => invokeDesktop<string | null>('select_output_dir', undefined, () => null),
  readTextFile: (path: string) => invokeDesktop<TextFile>('read_text_file', { path }),
  saveTextFile: (path: string, content: string) =>
    invokeDesktop<SavedFile>('save_text_file', { path, content }),
  openPath: (path: string) => invokeDesktop<void>('open_path', { path }),
  loadModelsConfig: () =>
    invokeDesktop<ModelsConfig>('load_models_config', undefined, () => ({
      project_root: '',
      config_path: '',
      raw: {
        model_root: 'models',
        active_local_asr_model: 'moss_transcribe_diarize',
        qwen3_asr_1_7b: { path: 'models/Qwen/Qwen3-ASR-1.7B', required: true, description: '' },
        moss_transcribe_diarize: {
          path: 'models/OpenMOSS-Team/MOSS-Transcribe-Diarize',
          required: false,
          description: ''
        },
        pyannote_speaker_diarization: {
          path: 'models/pyannote/speaker-diarization-community-1',
          required: true,
          description: ''
        }
      },
      active_local_asr_model: 'moss_transcribe_diarize',
      qwen_path: '',
      moss_path: '',
      pyannote_path: '',
      qwen_exists: false,
      moss_exists: false,
      pyannote_exists: false
    })),
  saveModelPaths: (
    modelRoot: string,
    activeLocalAsrModel: LocalAsrModelKey,
    qwenPath: string,
    mossPath: string,
    pyannotePath: string
  ) =>
    invokeDesktop<ModelsConfig>('save_model_paths', {
      modelRoot,
      activeLocalAsrModel,
      qwenPath,
      mossPath,
      pyannotePath
    }),
  loadAsrProfiles: () =>
    invokeDesktop<AsrProfilesState>('load_asr_profiles', undefined, () => ({
      profiles: [],
      last_profile: null
    })),
  saveAsrProfile: (profile: AsrCloudProfile) =>
    invokeDesktop<AsrProfilesState>('save_asr_profile', { profile }),
  deleteAsrProfile: (name: string) =>
    invokeDesktop<AsrProfilesState>('delete_asr_profile', { name }),
  workerHealthCheck: () => invokeDesktop<Record<string, unknown>>('worker_health_check'),
  loadSummaryProfiles: () =>
    invokeDesktop<SummaryProfilesState>('load_summary_profiles', undefined, () => ({
      profiles: [],
      last_profile: null
    })),
  saveSummaryProfile: (profile: SummaryProfile) =>
    invokeDesktop<SummaryProfilesState>('save_summary_profile', { profile }),
  deleteSummaryProfile: (name: string) =>
    invokeDesktop<SummaryProfilesState>('delete_summary_profile', { name }),
  loadSummaryTemplates: () => invokeDesktop<SummaryTemplate[]>('load_summary_templates', undefined, () => []),
  loadWorkflowCatalogs: () => invokeDesktop<WorkflowCatalogs>('workflow_v2_catalogs', undefined, () => ({ summary_profiles: [], summary_templates: [] })),
  saveSummaryTemplate: (name: string, prompt: string) =>
    invokeDesktop<SummaryTemplate[]>('save_summary_template', { name, prompt }),
  deleteSummaryTemplate: (name: string) =>
    invokeDesktop<SummaryTemplate[]>('delete_summary_template', { name }),
  listHistoryItems: (limit = 100) => invokeDesktop<HistoryItem[]>('list_history_items', { limit }, () => [])
};
