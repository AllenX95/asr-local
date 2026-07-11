use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct ModelsConfig {
    #[serde(default = "default_model_root")]
    pub model_root: String,
    #[serde(default = "default_active_local_asr_model")]
    pub active_local_asr_model: String,
    #[serde(default = "default_qwen_config")]
    pub qwen3_asr_1_7b: LocalModelConfig,
    #[serde(default = "default_moss_config")]
    pub moss_transcribe_diarize: LocalModelConfig,
    #[serde(default = "default_pyannote_config")]
    pub pyannote_speaker_diarization: LocalModelConfig,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct LocalModelConfig {
    pub path: String,
    pub required: bool,
    pub description: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct ResolvedModelsConfig {
    pub project_root: PathBuf,
    pub config_path: PathBuf,
    pub raw: ModelsConfig,
    pub active_local_asr_model: String,
    pub qwen_path: PathBuf,
    pub moss_path: PathBuf,
    pub pyannote_path: PathBuf,
    pub qwen_exists: bool,
    pub moss_exists: bool,
    pub pyannote_exists: bool,
}

const LOCAL_ASR_MODEL_KEYS: &[&str] = &["qwen3_asr_1_7b", "moss_transcribe_diarize"];

pub fn project_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .ancestors()
        .nth(3)
        .expect("project root should exist")
        .to_path_buf()
}

pub fn config_path() -> PathBuf {
    project_root().join("config").join("models.toml")
}

pub fn load_models_config() -> Result<ResolvedModelsConfig> {
    let project_root = project_root();
    let config_path = config_path();
    let content = std::fs::read_to_string(&config_path)
        .with_context(|| format!("failed to read {}", config_path.display()))?;

    let raw: ModelsConfig =
        toml::from_str(&content).context("failed to parse config/models.toml")?;
    validate_local_asr_model(&raw.active_local_asr_model)?;

    let qwen_path = resolve_path(&project_root, &raw.qwen3_asr_1_7b.path);
    let moss_path = resolve_path(&project_root, &raw.moss_transcribe_diarize.path);
    let pyannote_path = resolve_path(&project_root, &raw.pyannote_speaker_diarization.path);

    Ok(ResolvedModelsConfig {
        project_root,
        config_path,
        active_local_asr_model: raw.active_local_asr_model.clone(),
        qwen_exists: qwen_path.exists(),
        moss_exists: moss_path.exists(),
        pyannote_exists: pyannote_path.exists(),
        qwen_path,
        moss_path,
        pyannote_path,
        raw,
    })
}

pub fn save_model_paths(
    model_root: String,
    active_local_asr_model: Option<String>,
    qwen_path: String,
    moss_path: Option<String>,
    pyannote_path: String,
) -> Result<ResolvedModelsConfig> {
    let config_path = config_path();
    let mut raw = if config_path.exists() {
        let content = std::fs::read_to_string(&config_path)
            .with_context(|| format!("failed to read {}", config_path.display()))?;
        toml::from_str::<ModelsConfig>(&content).context("failed to parse config/models.toml")?
    } else {
        ModelsConfig {
            model_root: default_model_root(),
            active_local_asr_model: default_active_local_asr_model(),
            qwen3_asr_1_7b: default_qwen_config(),
            moss_transcribe_diarize: default_moss_config(),
            pyannote_speaker_diarization: default_pyannote_config(),
        }
    };

    raw.model_root = normalize_config_path(model_root);
    if let Some(active_local_asr_model) = active_local_asr_model {
        raw.active_local_asr_model = normalize_local_asr_model(active_local_asr_model)?;
    }
    raw.qwen3_asr_1_7b.path = normalize_config_path(qwen_path);
    if let Some(moss_path) = moss_path {
        raw.moss_transcribe_diarize.path = normalize_config_path(moss_path);
    }
    raw.pyannote_speaker_diarization.path = normalize_config_path(pyannote_path);

    if let Some(parent) = config_path.parent() {
        std::fs::create_dir_all(parent)
            .with_context(|| format!("failed to create {}", parent.display()))?;
    }
    let content = toml::to_string_pretty(&raw).context("failed to serialize config/models.toml")?;
    std::fs::write(&config_path, content)
        .with_context(|| format!("failed to write {}", config_path.display()))?;

    load_models_config()
}

pub fn resolve_path(project_root: &Path, value: &str) -> PathBuf {
    let path = PathBuf::from(value);
    if path.is_absolute() {
        path
    } else {
        project_root.join(path)
    }
}

fn normalize_config_path(value: String) -> String {
    value.trim().replace('\\', "/")
}

fn normalize_local_asr_model(value: String) -> Result<String> {
    let normalized = value.trim().to_string();
    validate_local_asr_model(&normalized)?;
    Ok(normalized)
}

fn validate_local_asr_model(value: &str) -> Result<()> {
    if LOCAL_ASR_MODEL_KEYS.contains(&value) {
        return Ok(());
    }
    anyhow::bail!(
        "unsupported active_local_asr_model: {}. Supported values: {}",
        value,
        LOCAL_ASR_MODEL_KEYS.join(", ")
    );
}

fn default_model_root() -> String {
    "models".to_string()
}

fn default_active_local_asr_model() -> String {
    "moss_transcribe_diarize".to_string()
}

fn default_qwen_config() -> LocalModelConfig {
    LocalModelConfig {
        path: "models/Qwen/Qwen3-ASR-1.7B".to_string(),
        required: true,
        description: "Manual local path for the downloaded Qwen3-ASR-1.7B model directory."
            .to_string(),
    }
}

fn default_moss_config() -> LocalModelConfig {
    LocalModelConfig {
        path: "models/OpenMOSS-Team/MOSS-Transcribe-Diarize".to_string(),
        required: false,
        description: "Manual local path for the downloaded MOSS-Transcribe-Diarize model directory."
            .to_string(),
    }
}

fn default_pyannote_config() -> LocalModelConfig {
    LocalModelConfig {
        path: "models/pyannote/speaker-diarization-community-1".to_string(),
        required: true,
        description:
            "Manual local path for the downloaded pyannote speaker diarization model directory."
                .to_string(),
    }
}
