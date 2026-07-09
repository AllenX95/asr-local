use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::path::PathBuf;

pub const WORKER_CONTRACT_VERSION: &str = "worker-contract-v1";
pub const WORKER_EVENT_NAME: &str = "worker-event";

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ReplacementRule {
    pub wrong: String,
    pub correct: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AsrCloudProfile {
    pub name: String,
    pub base_url: String,
    pub model: String,
    #[serde(default)]
    pub api_key: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RunJobRequest {
    pub job_id: String,
    pub source_path: PathBuf,
    pub output_dir: PathBuf,
    pub output_file_name: String,
    #[serde(default = "default_asr_backend")]
    pub asr_backend: String,
    #[serde(default)]
    pub cloud_asr_profile: Option<AsrCloudProfile>,
    pub language_mode: String,
    pub fixed_language: Option<String>,
    pub enable_speaker_diarization: bool,
    pub context_text: String,
    pub terms: Vec<String>,
    pub replacements: Vec<ReplacementRule>,
    pub keep_fillers: bool,
    pub auto_punctuation: bool,
}

#[derive(Debug, Clone, Serialize)]
pub struct SubmitJobResponse {
    pub job_id: String,
    pub lane_id: usize,
    pub queued_ahead: usize,
}

#[derive(Debug, Clone)]
pub struct JobProgress {
    pub worker_lane: usize,
    pub stage: String,
    pub progress: f32,
    pub detail: String,
    pub processed_ms: u64,
    pub total_ms: u64,
    pub payload: Value,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct JobResult {
    #[serde(default)]
    pub worker_lane: usize,
    pub md_path: PathBuf,
    pub transcript_json_path: PathBuf,
    pub job_json_path: PathBuf,
    pub job_dir: PathBuf,
    pub source_path: PathBuf,
    pub segments: usize,
    pub speakers: usize,
    pub total_ms: u64,
    pub detected_languages: Vec<String>,
    #[serde(default)]
    pub asr_backend: String,
    #[serde(default)]
    pub asr_profile_name: Option<String>,
    #[serde(default)]
    pub asr_model: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct WorkerUiEvent {
    pub event: String,
    pub job_id: String,
    pub lane_id: usize,
    pub source_path: String,
    pub stage: String,
    pub progress: f32,
    pub detail: String,
    pub processed_ms: u64,
    pub total_ms: u64,
    pub payload: Value,
    pub result: Option<JobResult>,
    pub error: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct WorkerEnvelope {
    #[serde(rename = "type")]
    pub kind: String,
    #[serde(default)]
    pub payload: Value,
}

fn default_asr_backend() -> String {
    "local".to_string()
}
