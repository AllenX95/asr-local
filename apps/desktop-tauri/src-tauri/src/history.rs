use anyhow::{Context, Result};
use serde::Serialize;
use std::fs;
use std::path::{Path, PathBuf};
use std::time::UNIX_EPOCH;

#[derive(Debug, Clone, Serialize)]
pub struct HistoryItem {
    pub id: String,
    pub kind: String,
    pub title: String,
    pub path: PathBuf,
    pub companion_json_path: Option<PathBuf>,
    pub modified_ms: u128,
    pub size_bytes: u64,
}

pub fn list_history_items(project_root: &Path, limit: usize) -> Result<Vec<HistoryItem>> {
    let outputs = project_root.join("outputs");
    if !outputs.exists() {
        return Ok(Vec::new());
    }

    let mut items = Vec::new();
    scan_markdown_outputs(&outputs, &mut items)?;
    items.sort_by(|left, right| right.modified_ms.cmp(&left.modified_ms));
    items.truncate(limit);
    Ok(items)
}

fn scan_markdown_outputs(dir: &Path, items: &mut Vec<HistoryItem>) -> Result<()> {
    for entry in fs::read_dir(dir).with_context(|| format!("failed to read {}", dir.display()))? {
        let entry = entry?;
        let path = entry.path();
        let metadata = entry.metadata()?;

        if metadata.is_dir() {
            if should_skip_directory(&path) {
                continue;
            }
            scan_markdown_outputs(&path, items)?;
            continue;
        }

        if path.extension().and_then(|value| value.to_str()) != Some("md") {
            continue;
        }

        let title = path
            .file_name()
            .and_then(|value| value.to_str())
            .unwrap_or("markdown")
            .to_string();
        let kind = if title.ends_with(".summary.md") {
            "summary"
        } else if title.ends_with(".draft.md") {
            "draft"
        } else if title.ends_with(".transcript.md") {
            "transcript"
        } else {
            "markdown"
        }
        .to_string();
        let companion_json_path = companion_json_path(&path);
        let modified_ms = metadata
            .modified()
            .ok()
            .and_then(|value| value.duration_since(UNIX_EPOCH).ok())
            .map(|value| value.as_millis())
            .unwrap_or_default();

        items.push(HistoryItem {
            id: path.display().to_string(),
            kind,
            title,
            path,
            companion_json_path: companion_json_path.filter(|value| value.exists()),
            modified_ms,
            size_bytes: metadata.len(),
        });
    }
    Ok(())
}

fn should_skip_directory(path: &Path) -> bool {
    let file_name = path.file_name().and_then(|value| value.to_str()).unwrap_or("");
    matches!(
        file_name,
        ".jobs" | "logs" | "webview2-data" | "node_modules" | "target"
    ) || file_name.starts_with("cargo-target-")
}

fn companion_json_path(path: &Path) -> Option<PathBuf> {
    let file_name = path.file_name()?.to_str()?;
    if file_name.ends_with(".transcript.md") {
        return Some(path.with_file_name(file_name.replace(".transcript.md", ".transcript.json")));
    }
    if file_name.ends_with(".summary.md") {
        return Some(path.with_file_name(file_name.replace(".summary.md", ".summary.json")));
    }
    None
}

#[cfg(test)]
mod tests {
    use super::should_skip_directory;
    use std::path::Path;

    #[test]
    fn skips_internal_and_build_directories() {
        for directory in [
            ".jobs",
            "logs",
            "webview2-data",
            "node_modules",
            "target",
            "cargo-target-codex-check",
        ] {
            assert!(should_skip_directory(Path::new(directory)), "{directory}");
        }

        assert!(!should_skip_directory(Path::new("customer-interviews")));
    }
}
