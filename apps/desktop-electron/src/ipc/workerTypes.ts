export type ViewKey = 'workflow' | 'markdown' | 'history' | 'settings';
export type LocalAsrModelKey = 'qwen3_asr_1_7b' | 'moss_transcribe_diarize';
export type PipelineProfile = 'pyannote_qwen3_asr' | 'pyannote_moss_asr' | 'cloud_asr';

export interface AsrCloudProfile {
  id?: string;
  version?: number;
  name: string;
  base_url: string;
  model: string;
  api_key: string;
}

export interface AsrProfilesState {
  profiles: AsrCloudProfile[];
  last_profile: string | null;
}

export interface SessionLogPaths {
  directory: string;
  desktop_log_path: string;
  worker_log_path: string;
  stdio_log_path: string;
}

export interface AppInfo {
  project_root: string;
  outputs_dir: string;
  legacy_desktop_dir: string;
  worker_dir: string;
  contract_version: string;
  logs: SessionLogPaths | null;
}

export interface TextFile {
  path: string;
  content: string;
  size_bytes: number;
  modified_ms: number;
}

export interface SavedFile {
  path: string;
  size_bytes: number;
  modified_ms: number;
}

export interface ModelsConfig {
  project_root: string;
  config_path: string;
  raw: {
    model_root: string;
    active_local_asr_model: LocalAsrModelKey;
    qwen3_asr_1_7b: LocalModelConfig;
    moss_transcribe_diarize: LocalModelConfig;
    pyannote_speaker_diarization: LocalModelConfig;
  };
  active_local_asr_model: LocalAsrModelKey;
  qwen_path: string;
  moss_path: string;
  pyannote_path: string;
  qwen_exists: boolean;
  moss_exists: boolean;
  pyannote_exists: boolean;
}

export interface LocalModelConfig {
  path: string;
  required: boolean;
  description: string;
}

export interface SummaryProfile {
  id?: string;
  version?: number;
  name: string;
  base_url: string;
  model: string;
  api_key: string;
}

export interface SummaryProfilesState {
  profiles: SummaryProfile[];
  last_profile: string | null;
}

export interface SummaryTemplate {
  id?: string;
  version?: number;
  name: string;
  prompt: string;
}

export interface WorkflowSummaryProfile {
  id: string;
  version: number;
  name: string;
  base_url: string;
  model: string;
  auth_mode: 'none' | 'bearer';
  provider_binding_sha256: string;
}

export interface WorkflowSummaryTemplate {
  id: string;
  version: number;
  name: string;
  prompt: string;
}

export interface WorkflowCatalogs {
  summary_profiles: WorkflowSummaryProfile[];
  summary_templates: WorkflowSummaryTemplate[];
}

export interface SummaryRequest {
  base_url: string;
  api_key: string;
  model: string;
  prompt: string;
  transcript_markdown: string;
}

export interface HistoryItem {
  id: string;
  kind: string;
  title: string;
  path: string;
  companion_json_path: string | null;
  modified_ms: number;
  size_bytes: number;
}
