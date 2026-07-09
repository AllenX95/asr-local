export const WORKER_EVENT_NAME = 'worker-event';

export type ViewKey = 'workbench' | 'markdown' | 'summary' | 'history' | 'settings';

export interface ReplacementRule {
  wrong: string;
  correct: string;
}

export type AsrBackend = 'local' | 'cloud';

export interface AsrCloudProfile {
  name: string;
  base_url: string;
  model: string;
  api_key: string;
}

export interface AsrProfilesState {
  profiles: AsrCloudProfile[];
  last_profile: string | null;
}

export interface RunJobRequest {
  job_id: string;
  source_path: string;
  output_dir: string;
  output_file_name: string;
  asr_backend: AsrBackend;
  cloud_asr_profile: AsrCloudProfile | null;
  language_mode: string;
  fixed_language: string | null;
  enable_speaker_diarization: boolean;
  context_text: string;
  terms: string[];
  replacements: ReplacementRule[];
  keep_fillers: boolean;
  auto_punctuation: boolean;
}

export interface SubmitJobResponse {
  job_id: string;
  lane_id: number;
  queued_ahead: number;
}

export interface JobResult {
  worker_lane: number;
  md_path: string;
  transcript_json_path: string;
  job_json_path: string;
  job_dir: string;
  source_path: string;
  segments: number;
  speakers: number;
  total_ms: number;
  detected_languages: string[];
  asr_backend: string;
  asr_profile_name: string | null;
  asr_model: string;
}

export interface WorkerUiEvent {
  event: 'queued' | 'progress' | 'completed' | 'failed';
  job_id: string;
  lane_id: number;
  source_path: string;
  stage: string;
  progress: number;
  detail: string;
  processed_ms: number;
  total_ms: number;
  payload: Record<string, unknown>;
  result: JobResult | null;
  error: string | null;
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
    qwen3_asr_1_7b: LocalModelConfig;
    pyannote_speaker_diarization: LocalModelConfig;
  };
  qwen_path: string;
  pyannote_path: string;
  qwen_exists: boolean;
  pyannote_exists: boolean;
}

export interface LocalModelConfig {
  path: string;
  required: boolean;
  description: string;
}

export interface SummaryProfile {
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
  name: string;
  prompt: string;
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
