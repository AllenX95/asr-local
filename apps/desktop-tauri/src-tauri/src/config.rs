use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct ModelsConfig {
    pub model_root: String,
    pub qwen3_asr_1_7b: LocalModelConfig,
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
    pub qwen_path: PathBuf,
    pub pyannote_path: PathBuf,
    pub qwen_exists: bool,
    pub pyannote_exists: bool,
}

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

    let qwen_path = resolve_path(&project_root, &raw.qwen3_asr_1_7b.path);
    let pyannote_path = resolve_path(&project_root, &raw.pyannote_speaker_diarization.path);

    Ok(ResolvedModelsConfig {
        project_root,
        config_path,
        qwen_exists: qwen_path.exists(),
        pyannote_exists: pyannote_path.exists(),
        qwen_path,
        pyannote_path,
        raw,
    })
}

pub fn save_model_paths(
    model_root: String,
    qwen_path: String,
    pyannote_path: String,
) -> Result<ResolvedModelsConfig> {
    let config_path = config_path();
    let mut raw = if config_path.exists() {
        let content = std::fs::read_to_string(&config_path)
            .with_context(|| format!("failed to read {}", config_path.display()))?;
        toml::from_str::<ModelsConfig>(&content).context("failed to parse config/models.toml")?
    } else {
        ModelsConfig {
            model_root: "models".to_string(),
            qwen3_asr_1_7b: LocalModelConfig {
                path: "models/Qwen/Qwen3-ASR-1.7B".to_string(),
                required: true,
                description: "Manual local path for the downloaded Qwen3-ASR-1.7B model directory."
                    .to_string(),
            },
            pyannote_speaker_diarization: LocalModelConfig {
                path: "models/pyannote/speaker-diarization-community-1".to_string(),
                required: true,
                description:
                    "Manual local path for the downloaded pyannote speaker diarization model directory."
                        .to_string(),
            },
        }
    };

    raw.model_root = normalize_config_path(model_root);
    raw.qwen3_asr_1_7b.path = normalize_config_path(qwen_path);
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
