//! Contract v2 types are intentionally isolated from the existing v1 lane
//! contract. Production commands/client wiring is deferred until Phase 5.

use serde::{Deserialize, Serialize};
use serde_json::Value;

pub const WORKFLOW_PROTOCOL_V2: &str = "asr-local-workflow";
pub const WORKFLOW_PROTOCOL_VERSION_V2: u8 = 2;

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct WorkflowV2Request {
    pub protocol: String,
    pub protocol_version: u8,
    pub kind: String,
    pub request_id: String,
    #[serde(default)]
    pub operation_id: Option<String>,
    pub method: String,
    pub params: Value,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct WorkflowV2Response {
    pub protocol: String,
    pub protocol_version: u8,
    pub kind: String,
    pub request_id: String,
    #[serde(default)]
    pub operation_id: Option<String>,
    pub ok: bool,
    #[serde(default)]
    pub result: Option<Value>,
    #[serde(default)]
    pub error: Option<WorkflowV2Error>,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct WorkflowV2EventEnvelope {
    pub protocol: String,
    pub protocol_version: u8,
    pub kind: String,
    pub event: String,
    pub payload: WorkflowV2EventPayload,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct WorkflowV2EventPayload {
    pub workflow_id: String,
    pub attempt_id: String,
    pub sequence: u64,
    pub occurred_at: String,
    #[serde(default)]
    pub caused_by_operation_id: Option<String>,
    #[serde(rename = "type")]
    pub event_type: String,
    pub stage: Option<String>,
    pub data: Value,
    pub state: WorkflowSnapshot,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct WorkflowSnapshot {
    pub snapshot_version: u8,
    pub workflow_id: String,
    pub sequence: u64,
    pub spec: Value,
    pub status: WorkflowStatus,
    pub stage: Option<String>,
    pub attempt: WorkflowAttempt,
    pub progress: Value,
    pub control: Value,
    pub runtime_plan: Option<Value>,
    pub artifacts: Vec<Value>,
    pub recovery: Value,
    pub last_error: Option<WorkflowV2Error>,
    pub timestamps: Value,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct WorkflowAttempt {
    pub attempt_id: String,
    pub number: u32,
    pub stage_attempts: Value,
}

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum WorkflowStatus {
    Queued,
    Running,
    Paused,
    WaitingForSecret,
    Completed,
    Failed,
    Cancelled,
    Interrupted,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct WorkflowV2Error {
    pub code: String,
    pub message: String,
    pub retryable: bool,
    pub field_errors: Vec<Value>,
    pub details: Value,
    pub diagnostic_id: String,
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::fs;
    use std::path::PathBuf;

    fn fixture(name: &str) -> String {
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
        fs::read_to_string(root.join("../../../contracts/workflow-v2/fixtures").join(name))
            .expect("workflow v2 fixture must exist")
    }

    #[test]
    fn request_fixtures_round_trip() {
        for name in [
            "workflow-submit.request.json",
            "workflow-control.request.json",
            "workflow-retry.request.json",
            "artifact-register-revision.request.json",
        ] {
            let request: WorkflowV2Request = serde_json::from_str(&fixture(name)).unwrap();
            assert_eq!(request.protocol, WORKFLOW_PROTOCOL_V2);
            assert_eq!(request.protocol_version, WORKFLOW_PROTOCOL_VERSION_V2);
            assert_eq!(request.kind, "request");
            assert!(!request.request_id.is_empty());
        }
    }

    #[test]
    fn event_fixtures_require_full_snapshot_state() {
        for name in [
            "workflow-progress.event.json",
            "workflow-summary-credentials-required.event.json",
            "workflow-cloud-asr-credentials-required.event.json",
        ] {
            let event: WorkflowV2EventEnvelope = serde_json::from_str(&fixture(name)).unwrap();
            assert_eq!(event.payload.workflow_id, event.payload.state.workflow_id);
            assert_eq!(event.payload.sequence, event.payload.state.sequence);
            assert_eq!(event.payload.attempt_id, event.payload.state.attempt.attempt_id);
        }
    }

    #[test]
    fn unknown_request_fields_are_rejected() {
        let mut value: serde_json::Value = serde_json::from_str(&fixture("workflow-control.request.json")).unwrap();
        value["unexpected"] = json!(true);
        assert!(serde_json::from_value::<WorkflowV2Request>(value).is_err());
    }
}
