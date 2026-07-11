use crate::config;
use crate::workflow_v2_client;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use tauri::AppHandle;

type CommandResult<T> = Result<T, String>;

#[tauri::command]
pub fn workflow_v2_capabilities(app: AppHandle) -> CommandResult<Value> {
    workflow_v2_client::request(app, &config::project_root(), "runtime.capabilities", None, json!({})).map_err(|e| e.to_string())
}

#[tauri::command]
pub fn workflow_v2_catalogs() -> CommandResult<Value> {
    let root = config::project_root();
    let profiles = crate::summary_profiles::load_profiles(&root)
        .map_err(|e| e.to_string())?
        .profiles
        .into_iter()
        .map(|profile| {
            let profile_id = if profile.id.is_empty() { format!("summary-profile-{}", profile.name) } else { profile.id.clone() };
            let auth_mode = if profile.api_key.trim().is_empty() { "none" } else { "bearer" };
            json!({
                "id": profile_id,
                "version": profile.version,
                "name": profile.name,
                "base_url": profile.base_url,
                "model": profile.model,
                "auth_mode": auth_mode,
                "provider_binding_sha256": provider_binding_digest(&profile_id, profile.version, &profile.base_url, &profile.model, auth_mode),
            })
        })
        .collect::<Vec<_>>();
    let templates = crate::summary_templates::load_templates(&root)
        .map_err(|e| e.to_string())?
        .into_iter()
        .map(|template| {
            json!({
                "id": if template.id.is_empty() { format!("summary-template-{}", template.name) } else { template.id },
                "version": template.version,
                "name": template.name,
                "prompt": template.prompt,
            })
        })
        .collect::<Vec<_>>();
    Ok(json!({ "summary_profiles": profiles, "summary_templates": templates }))
}

#[tauri::command]
pub fn workflow_v2_prompt_preview(app: AppHandle, input: Value) -> CommandResult<Value> {
    workflow_v2_client::request(app, &config::project_root(), "prompt.preview", None, input).map_err(|e| e.to_string())
}

#[tauri::command]
pub fn workflow_v2_submit(app: AppHandle, operation_id: String, draft: Value) -> CommandResult<Value> {
    workflow_v2_client::request(app, &config::project_root(), "workflow.submit", Some(&operation_id), json!({"draft": draft})).map_err(|e| e.to_string())
}

#[tauri::command]
pub fn workflow_v2_list(app: AppHandle, statuses: Option<Vec<String>>) -> CommandResult<Value> {
    workflow_v2_client::request(app, &config::project_root(), "workflow.list", None, json!({"statuses": statuses.unwrap_or_default(), "cursor": null, "limit": 100})).map_err(|e| e.to_string())
}

#[tauri::command]
pub fn workflow_v2_get(app: AppHandle, workflow_id: String, timeline_limit: Option<u32>) -> CommandResult<Value> {
    workflow_v2_client::request(app, &config::project_root(), "workflow.get", None, json!({"workflow_id": workflow_id, "timeline_limit": timeline_limit.unwrap_or(200)})).map_err(|e| e.to_string())
}

#[tauri::command]
pub fn workflow_v2_clear(app: AppHandle, operation_id: String, workflow_id: String) -> CommandResult<Value> {
    workflow_v2_client::request(
        app,
        &config::project_root(),
        "workflow.clear",
        Some(&operation_id),
        json!({"workflow_id": workflow_id}),
    )
    .map_err(|e| e.to_string())
}

#[tauri::command]
pub fn workflow_v2_control(app: AppHandle, operation_id: String, workflow_id: String, expected_attempt_id: String, action: String) -> CommandResult<Value> {
    workflow_v2_client::request(app, &config::project_root(), "workflow.control", Some(&operation_id), json!({"workflow_id": workflow_id, "expected_attempt_id": expected_attempt_id, "action": action})).map_err(|e| e.to_string())
}

#[tauri::command]
pub fn workflow_v2_retry(app: AppHandle, operation_id: String, workflow_id: String, expected_attempt_id: String, expected_sequence: u64, from_stage: String, input_artifact_id: Option<String>) -> CommandResult<Value> {
    workflow_v2_client::request(app, &config::project_root(), "workflow.retry", Some(&operation_id), json!({"workflow_id": workflow_id, "expected_attempt_id": expected_attempt_id, "expected_sequence": expected_sequence, "from_stage": from_stage, "input_artifact_id": input_artifact_id})).map_err(|e| e.to_string())
}

#[tauri::command]
pub fn workflow_v2_register_revision(app: AppHandle, operation_id: String, params: Value) -> CommandResult<Value> {
    workflow_v2_client::request(app, &config::project_root(), "artifact.register_revision", Some(&operation_id), params).map_err(|e| e.to_string())
}

/// Resolve the credential inside the native host and send only the ephemeral
/// grant over the v2 pipe. The renderer receives no plaintext secret.
#[tauri::command]
pub fn workflow_v2_provide_secret(
    app: AppHandle,
    workflow_id: String,
    expected_attempt_id: String,
    request_data: Value,
) -> CommandResult<Value> {
    let purpose = request_data
        .get("purpose")
        .and_then(Value::as_str)
        .ok_or_else(|| "secret request purpose is missing".to_string())?;
    let profile_id = request_data
        .get("profile_id")
        .and_then(Value::as_str)
        .ok_or_else(|| "secret request profile_id is missing".to_string())?;
    let profile_version = request_data
        .get("profile_version")
        .and_then(Value::as_u64)
        .ok_or_else(|| "secret request profile_version is missing".to_string())? as u32;
    let root = config::project_root();
    let secret = match purpose {
        "summary_api" => {
            let profile = crate::summary_profiles::load_profiles(&root)
                .map_err(|e| e.to_string())?
                .profiles
                .into_iter()
                .find(|profile| profile.id == profile_id && profile.version == profile_version)
                .ok_or_else(|| "CREDENTIAL_REJECTED: summary profile snapshot not found".to_string())?;
            if profile.api_key.trim().is_empty() {
                return Err("CREDENTIAL_REJECTED: summary profile has no bearer credential".to_string());
            }
            let expected_binding = provider_binding_digest(&profile.id, profile.version, &profile.base_url, &profile.model, "bearer");
            if request_data.get("provider_binding_sha256").and_then(Value::as_str) != Some(expected_binding.as_str()) {
                return Err("CREDENTIAL_REJECTED: provider binding does not match the submitted profile snapshot".to_string());
            }
            profile.api_key
        }
        "cloud_asr" => {
            let profile = crate::asr_profiles::load_profiles(&root)
                .map_err(|e| e.to_string())?
                .profiles
                .into_iter()
                .find(|profile| profile.id == profile_id && profile.version == profile_version)
                .ok_or_else(|| "CREDENTIAL_REJECTED: cloud ASR profile snapshot not found".to_string())?;
            if profile.api_key.trim().is_empty() {
                return Err("CREDENTIAL_REJECTED: cloud ASR profile has no bearer credential".to_string());
            }
            let expected_binding = provider_binding_digest(&profile.id, profile.version, &profile.base_url, &profile.model, "bearer");
            if request_data.get("provider_binding_sha256").and_then(Value::as_str) != Some(expected_binding.as_str()) {
                return Err("CREDENTIAL_REJECTED: provider binding does not match the submitted cloud profile snapshot".to_string());
            }
            profile.api_key
        }
        other => return Err(format!("CREDENTIAL_REJECTED: unsupported purpose {other}")),
    };
    let mut params = request_data;
    params["workflow_id"] = json!(workflow_id);
    params["expected_attempt_id"] = json!(expected_attempt_id);
    params["secret"] = json!(secret);
    params["lease_scope"] = json!("attempt");
    workflow_v2_client::request(app, &root, "secret.provide", None, params).map_err(|e| e.to_string())
}

fn provider_binding_digest(profile_id: &str, version: u32, base_url: &str, model: &str, auth_mode: &str) -> String {
    let canonical = format!("{profile_id}\n{version}\n{base_url}\n{model}\n{auth_mode}");
    format!("{:x}", Sha256::digest(canonical.as_bytes()))
}

#[tauri::command]
pub fn workflow_v2_shutdown(app: AppHandle) -> CommandResult<Value> {
    workflow_v2_client::shutdown(app, &config::project_root()).map_err(|e| e.to_string())
}
