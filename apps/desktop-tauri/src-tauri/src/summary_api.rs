use anyhow::{anyhow, bail, Context, Result};
use reqwest::blocking::Client;
use reqwest::header::{AUTHORIZATION, CONTENT_TYPE};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::time::Duration;

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct SummaryRequest {
    pub base_url: String,
    pub api_key: String,
    pub model: String,
    pub prompt: String,
    pub transcript_markdown: String,
}

pub fn generate_summary(request: &SummaryRequest) -> Result<String> {
    let url = chat_completions_url(&request.base_url)?;
    let client = Client::builder()
        .timeout(Duration::from_secs(600))
        .build()
        .context("failed to create summary API client")?;

    let payload = json!({
        "model": request.model.trim(),
        "messages": [
            {
                "role": "system",
                "content": "You summarize transcripts into clean Markdown. Follow the user prompt exactly and return Markdown only."
            },
            {
                "role": "user",
                "content": format!(
                    "# Summary Instructions\n{}\n\n# Transcript Markdown\n{}",
                    request.prompt.trim(),
                    request.transcript_markdown
                )
            }
        ]
    });

    let mut builder = client.post(url).header(CONTENT_TYPE, "application/json");
    if !request.api_key.trim().is_empty() {
        builder = builder.header(
            AUTHORIZATION,
            format!("Bearer {}", request.api_key.trim()),
        );
    }

    let response = builder
        .json(&payload)
        .send()
        .context("failed to send summary request")?;
    let status = response.status();
    let body = response
        .text()
        .context("failed to read summary response body")?;

    if !status.is_success() {
        bail!("summary API returned {}: {}", status, body);
    }

    let value: Value =
        serde_json::from_str(&body).context("failed to parse summary response as JSON")?;
    extract_message_content(&value).ok_or_else(|| {
        anyhow!("summary API response did not contain choices[0].message.content")
    })
}

fn chat_completions_url(base_url: &str) -> Result<String> {
    let trimmed = base_url.trim();
    if trimmed.is_empty() {
        bail!("summary API base URL is empty");
    }

    if trimmed.ends_with("/chat/completions") {
        return Ok(trimmed.to_string());
    }

    Ok(format!("{}/chat/completions", trimmed.trim_end_matches('/')))
}

fn extract_message_content(value: &Value) -> Option<String> {
    let content = value
        .get("choices")?
        .as_array()?
        .first()?
        .get("message")?
        .get("content")?;

    if let Some(text) = content.as_str() {
        return Some(text.trim().to_string());
    }

    let parts = content.as_array()?;
    let mut output = String::new();
    for part in parts {
        if part.get("type").and_then(Value::as_str) == Some("text") {
            if let Some(text) = part.get("text").and_then(Value::as_str) {
                if !output.is_empty() {
                    output.push('\n');
                }
                output.push_str(text.trim());
            }
        }
    }

    if output.trim().is_empty() {
        None
    } else {
        Some(output)
    }
}
