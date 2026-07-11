use crate::asr_profiles::{self, AsrCloudProfile, AsrProfilesState};
use crate::config::{self, ResolvedModelsConfig};
use crate::history::{self, HistoryItem};
use crate::session_log::{self, SessionLogPaths};
use crate::summary_api::{self, SummaryRequest};
use crate::summary_profiles::{self, SummaryProfile, SummaryProfilesState};
use crate::summary_templates::{self, SummaryTemplate};
use crate::worker_client;
use crate::worker_contract::{RunJobRequest, SubmitJobResponse, WORKER_CONTRACT_VERSION};
use serde::Serialize;
use serde_json::Value;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::UNIX_EPOCH;
use tauri::AppHandle;

const SUPPORTED_AUDIO_EXTENSIONS: &[&str] = &[
    "wav", "mp3", "mp2", "m4a", "m4b", "aac", "flac", "ogg", "oga", "opus", "wma", "webm",
    "mp4", "m4v", "3gp", "3g2", "amr", "aiff", "aif", "caf", "mka", "mkv", "mov", "ac3",
    "eac3", "ape",
];

type CommandResult<T> = Result<T, String>;

#[derive(Debug, Clone, Serialize)]
pub struct AppInfo {
    pub project_root: PathBuf,
    pub outputs_dir: PathBuf,
    pub legacy_desktop_dir: PathBuf,
    pub worker_dir: PathBuf,
    pub contract_version: String,
    pub logs: Option<SessionLogPaths>,
}

#[derive(Debug, Clone, Serialize)]
pub struct TextFile {
    pub path: PathBuf,
    pub content: String,
    pub size_bytes: u64,
    pub modified_ms: u128,
}

#[derive(Debug, Clone, Serialize)]
pub struct SavedFile {
    pub path: PathBuf,
    pub size_bytes: u64,
    pub modified_ms: u128,
}

#[tauri::command]
pub fn get_app_info() -> AppInfo {
    let project_root = config::project_root();
    AppInfo {
        outputs_dir: project_root.join("outputs"),
        legacy_desktop_dir: project_root.join("apps").join("desktop-rust"),
        worker_dir: project_root.join("apps").join("worker-python"),
        project_root,
        contract_version: WORKER_CONTRACT_VERSION.to_string(),
        logs: session_log::paths().cloned(),
    }
}

#[tauri::command]
pub fn select_audio_file() -> Option<PathBuf> {
    rfd::FileDialog::new()
        .add_filter("Audio", SUPPORTED_AUDIO_EXTENSIONS)
        .pick_file()
}

#[tauri::command]
pub fn select_markdown_file() -> Option<PathBuf> {
    rfd::FileDialog::new()
        .add_filter("Markdown", &["md", "markdown"])
        .pick_file()
}

#[tauri::command]
pub fn select_output_dir() -> Option<PathBuf> {
    rfd::FileDialog::new().pick_folder()
}

#[tauri::command]
pub fn read_text_file(path: String) -> CommandResult<TextFile> {
    let path = PathBuf::from(path);
    let metadata = fs::metadata(&path)
        .map_err(|error| format!("failed to read metadata for {}: {error}", path.display()))?;
    let content = fs::read_to_string(&path)
        .map_err(|error| format!("failed to read {}: {error}", path.display()))?;

    Ok(TextFile {
        path,
        content,
        size_bytes: metadata.len(),
        modified_ms: modified_ms(&metadata),
    })
}

#[tauri::command]
pub fn save_text_file(path: String, content: String) -> CommandResult<SavedFile> {
    let path = PathBuf::from(path);
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .map_err(|error| format!("failed to create {}: {error}", parent.display()))?;
    }

    fs::write(&path, content.as_bytes())
        .map_err(|error| format!("failed to write {}: {error}", path.display()))?;
    let metadata = fs::metadata(&path)
        .map_err(|error| format!("failed to read metadata for {}: {error}", path.display()))?;

    Ok(SavedFile {
        path,
        size_bytes: metadata.len(),
        modified_ms: modified_ms(&metadata),
    })
}

#[tauri::command]
pub fn open_path(path: String) -> CommandResult<()> {
    let path = PathBuf::from(path);
    if !path.exists() {
        return Err(format!("path does not exist: {}", path.display()));
    }

    open_path_native(&path)
        .map_err(|error| format!("failed to open {}: {error}", path.display()))
}

#[tauri::command]
pub fn load_models_config() -> CommandResult<ResolvedModelsConfig> {
    config::load_models_config().map_err(format_anyhow)
}

#[tauri::command]
pub fn save_model_paths(
    model_root: String,
    active_local_asr_model: Option<String>,
    qwen_path: String,
    moss_path: Option<String>,
    pyannote_path: String,
) -> CommandResult<ResolvedModelsConfig> {
    config::save_model_paths(
        model_root,
        active_local_asr_model,
        qwen_path,
        moss_path,
        pyannote_path,
    )
    .map_err(format_anyhow)
}

#[tauri::command]
pub fn load_asr_profiles() -> CommandResult<AsrProfilesState> {
    asr_profiles::load_profiles(&config::project_root()).map_err(format_anyhow)
}

#[tauri::command]
pub fn save_asr_profile(profile: AsrCloudProfile) -> CommandResult<AsrProfilesState> {
    asr_profiles::upsert_profile(&config::project_root(), &profile).map_err(format_anyhow)
}

#[tauri::command]
pub fn delete_asr_profile(name: String) -> CommandResult<AsrProfilesState> {
    asr_profiles::delete_profile(&config::project_root(), &name).map_err(format_anyhow)
}

#[tauri::command]
pub fn worker_health_check() -> CommandResult<Value> {
    worker_client::health_check(&config::project_root()).map_err(format_anyhow)
}

#[tauri::command]
pub fn submit_job(app: AppHandle, request: RunJobRequest) -> CommandResult<SubmitJobResponse> {
    let asr_backend = request.asr_backend.trim().to_ascii_lowercase();
    if !matches!(asr_backend.as_str(), "local" | "cloud") {
        return Err(format!("unsupported ASR backend: {}", request.asr_backend));
    }
    if asr_backend == "cloud" {
        let profile = request
            .cloud_asr_profile
            .as_ref()
            .ok_or_else(|| "cloud ASR profile is required".to_string())?;
        if profile.base_url.trim().is_empty() {
            return Err("cloud ASR base URL is empty".to_string());
        }
        if profile.model.trim().is_empty() {
            return Err("cloud ASR model is empty".to_string());
        }
    }

    worker_client::submit_job(app, &config::project_root(), request).map_err(format_anyhow)
}

#[tauri::command]
pub fn pause_lane(lane_id: usize) -> CommandResult<()> {
    worker_client::pause_lane(lane_id).map_err(format_anyhow)
}

#[tauri::command]
pub fn resume_lane(lane_id: usize) -> CommandResult<()> {
    worker_client::resume_lane(lane_id).map_err(format_anyhow)
}

#[tauri::command]
pub fn terminate_lane(lane_id: usize) -> CommandResult<()> {
    worker_client::terminate_lane(lane_id).map_err(format_anyhow)
}

#[tauri::command]
pub fn load_summary_profiles() -> CommandResult<SummaryProfilesState> {
    summary_profiles::load_profiles(&config::project_root()).map_err(format_anyhow)
}

#[tauri::command]
pub fn save_summary_profile(profile: SummaryProfile) -> CommandResult<SummaryProfilesState> {
    summary_profiles::upsert_profile(&config::project_root(), &profile).map_err(format_anyhow)
}

#[tauri::command]
pub fn delete_summary_profile(name: String) -> CommandResult<SummaryProfilesState> {
    summary_profiles::delete_profile(&config::project_root(), &name).map_err(format_anyhow)
}

#[tauri::command]
pub fn load_summary_templates() -> CommandResult<Vec<SummaryTemplate>> {
    summary_templates::load_templates(&config::project_root()).map_err(format_anyhow)
}

#[tauri::command]
pub fn save_summary_template(name: String, prompt: String) -> CommandResult<Vec<SummaryTemplate>> {
    summary_templates::upsert_template(&config::project_root(), &name, &prompt)
        .map_err(format_anyhow)
}

#[tauri::command]
pub fn delete_summary_template(name: String) -> CommandResult<Vec<SummaryTemplate>> {
    summary_templates::delete_template(&config::project_root(), &name).map_err(format_anyhow)
}

#[tauri::command]
pub fn generate_summary(request: SummaryRequest) -> CommandResult<String> {
    summary_api::generate_summary(&request).map_err(format_anyhow)
}

#[tauri::command]
pub fn list_history_items(limit: Option<usize>) -> CommandResult<Vec<HistoryItem>> {
    history::list_history_items(&config::project_root(), limit.unwrap_or(100)).map_err(format_anyhow)
}

fn modified_ms(metadata: &fs::Metadata) -> u128 {
    metadata
        .modified()
        .ok()
        .and_then(|value| value.duration_since(UNIX_EPOCH).ok())
        .map(|value| value.as_millis())
        .unwrap_or_default()
}

fn format_anyhow(error: anyhow::Error) -> String {
    format!("{error:#}")
}

#[cfg(target_os = "windows")]
fn open_path_native(path: &Path) -> std::io::Result<()> {
    if path.is_file() {
        Command::new("explorer")
            .arg(format!("/select,{}", path.display()))
            .spawn()?;
    } else {
        Command::new("explorer").arg(path).spawn()?;
    }
    Ok(())
}

#[cfg(not(target_os = "windows"))]
fn open_path_native(path: &Path) -> std::io::Result<()> {
    Command::new("xdg-open").arg(path).spawn()?;
    Ok(())
}
