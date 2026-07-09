use anyhow::{bail, Context, Result};
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct SummaryTemplate {
    pub name: String,
    pub prompt: String,
}

#[derive(Debug, Default, Serialize, Deserialize)]
struct SummaryTemplateFile {
    #[serde(default)]
    templates: Vec<SummaryTemplate>,
}

pub fn load_templates(project_root: &Path) -> Result<Vec<SummaryTemplate>> {
    let path = templates_path(project_root);
    if !path.exists() {
        return Ok(Vec::new());
    }

    let content = std::fs::read_to_string(&path)
        .with_context(|| format!("failed to read {}", path.display()))?;
    let file: SummaryTemplateFile =
        toml::from_str(&content).with_context(|| format!("failed to parse {}", path.display()))?;

    Ok(file.templates)
}

pub fn upsert_template(
    project_root: &Path,
    name: &str,
    prompt: &str,
) -> Result<Vec<SummaryTemplate>> {
    let normalized_name = name.trim();
    if normalized_name.is_empty() {
        bail!("template name is empty");
    }

    let mut templates = load_templates(project_root)?;
    let normalized_prompt = prompt.trim();

    if let Some(existing) = templates
        .iter_mut()
        .find(|template| template.name.eq_ignore_ascii_case(normalized_name))
    {
        existing.name = normalized_name.to_string();
        existing.prompt = normalized_prompt.to_string();
    } else {
        templates.push(SummaryTemplate {
            name: normalized_name.to_string(),
            prompt: normalized_prompt.to_string(),
        });
    }

    templates.sort_by(|left, right| left.name.to_lowercase().cmp(&right.name.to_lowercase()));
    save_templates(project_root, &templates)?;
    Ok(templates)
}

pub fn delete_template(project_root: &Path, name: &str) -> Result<Vec<SummaryTemplate>> {
    let normalized_name = name.trim();
    let mut templates = load_templates(project_root)?;
    templates.retain(|template| !template.name.eq_ignore_ascii_case(normalized_name));
    save_templates(project_root, &templates)?;
    Ok(templates)
}

pub fn templates_path(project_root: &Path) -> PathBuf {
    project_root
        .join("config")
        .join("summary_templates.toml")
}

fn save_templates(project_root: &Path, templates: &[SummaryTemplate]) -> Result<()> {
    let path = templates_path(project_root);
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)
            .with_context(|| format!("failed to create {}", parent.display()))?;
    }

    let file = SummaryTemplateFile {
        templates: templates.to_vec(),
    };
    let content = toml::to_string_pretty(&file).context("failed to serialize summary templates")?;
    std::fs::write(&path, content)
        .with_context(|| format!("failed to write {}", path.display()))?;

    Ok(())
}
