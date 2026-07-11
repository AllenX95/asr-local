from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any


STATUSES = {
    "queued",
    "running",
    "paused",
    "waiting_for_secret",
    "completed",
    "failed",
    "cancelled",
    "interrupted",
}
RETRY_STAGES = {"auto", "transcribing", "summarizing", "writing_final"}


class WorkflowStateError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RetryDecision:
    snapshot: dict[str, Any]
    from_stage: str


def create_initial_snapshot(
    workflow_id: str,
    attempt_id: str,
    spec: dict[str, Any],
    *,
    created_at: str,
) -> dict[str, Any]:
    if not workflow_id or not attempt_id:
        raise WorkflowStateError("workflow_id and attempt_id are required")
    return {
        "snapshot_version": 2,
        "workflow_id": workflow_id,
        "sequence": 1,
        "spec": deepcopy(spec),
        "status": "queued",
        "stage": "queued",
        "attempt": {
            "attempt_id": attempt_id,
            "number": 1,
            "stage_attempts": {"transcription": 0, "summary": 0, "writing_final": 0},
        },
        "progress": {"stage_ratio": 0.0, "overall_ratio": 0.0, "queue_position": None},
        "control": {"pending_action": None},
        "runtime_plan": None,
        "artifacts": [],
        "recovery": {"recommended_retry_stage": None, "interrupted_attempt_id": None, "input_artifact_id": None},
        "last_error": None,
        "timestamps": {
            "created_at": created_at,
            "updated_at": created_at,
            "started_at": None,
            "completed_at": None,
        },
    }


def mark_interrupted(snapshot: dict[str, Any], *, recommended_retry_stage: str, updated_at: str) -> dict[str, Any]:
    if snapshot.get("status") not in {"running", "waiting_for_secret", "paused", "queued"}:
        raise WorkflowStateError("only an active workflow can be marked interrupted")
    if recommended_retry_stage not in {"transcribing", "summarizing", "writing_final"}:
        raise WorkflowStateError("invalid recommended retry stage")
    result = deepcopy(snapshot)
    result["sequence"] += 1
    result["status"] = "interrupted"
    result["recovery"] = {
        "recommended_retry_stage": recommended_retry_stage,
        "interrupted_attempt_id": result["attempt"]["attempt_id"],
        "input_artifact_id": None,
    }
    result["timestamps"]["updated_at"] = updated_at
    return result


def retry_snapshot(
    snapshot: dict[str, Any],
    *,
    expected_attempt_id: str,
    expected_sequence: int,
    from_stage: str,
    new_attempt_id: str,
    updated_at: str,
    input_artifact_id: str | None = None,
) -> RetryDecision:
    if snapshot.get("status") not in {"failed", "completed", "interrupted"}:
        raise WorkflowStateError("workflow retry requires failed, completed or interrupted status")
    if snapshot.get("attempt", {}).get("attempt_id") != expected_attempt_id:
        raise WorkflowStateError("STALE_ATTEMPT")
    if snapshot.get("sequence") != expected_sequence:
        raise WorkflowStateError("SEQUENCE_CONFLICT")
    if from_stage not in RETRY_STAGES:
        raise WorkflowStateError("invalid retry stage")
    selected_stage = from_stage
    if from_stage == "auto":
        selected_stage = snapshot.get("recovery", {}).get("recommended_retry_stage") or _infer_retry_stage(snapshot)
    if selected_stage not in {"transcribing", "summarizing", "writing_final"}:
        raise WorkflowStateError("unable to infer retry stage")
    if selected_stage == "summarizing" and input_artifact_id is not None:
        selected = next((item for item in snapshot.get("artifacts", []) if item.get("artifact_id") == input_artifact_id), None)
        if selected is None or selected.get("kind") not in {"transcript_markdown", "transcript_json"} or selected.get("stale"):
            raise WorkflowStateError("input artifact does not belong to workflow")

    result = deepcopy(snapshot)
    result["sequence"] += 1
    result["status"] = "queued"
    result["stage"] = "queued"
    result["attempt"] = {
        "attempt_id": new_attempt_id,
        "number": int(snapshot["attempt"]["number"]) + 1,
        "stage_attempts": {"transcription": 0, "summary": 0, "writing_final": 0},
    }
    result["progress"] = {"stage_ratio": 0.0, "overall_ratio": 0.0, "queue_position": None}
    result["control"] = {"pending_action": None}
    result["runtime_plan"] = None
    result["last_error"] = None
    result["recovery"] = {
        "recommended_retry_stage": selected_stage,
        "interrupted_attempt_id": None,
        "input_artifact_id": input_artifact_id,
    }
    result["timestamps"]["updated_at"] = updated_at
    return RetryDecision(result, selected_stage)


def apply_event(snapshot: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    workflow_id = event.get("workflow_id")
    attempt_id = event.get("attempt_id")
    sequence = event.get("sequence")
    state = event.get("state")
    if workflow_id != snapshot.get("workflow_id"):
        raise WorkflowStateError("WORKFLOW_ID_MISMATCH")
    if not isinstance(sequence, int) or not isinstance(state, dict):
        raise WorkflowStateError("MALFORMED_EVENT")
    if sequence <= snapshot.get("sequence", 0):
        return deepcopy(snapshot)
    if attempt_id != state.get("attempt", {}).get("attempt_id"):
        raise WorkflowStateError("EVENT_ATTEMPT_MISMATCH")
    if attempt_id != snapshot.get("attempt", {}).get("attempt_id"):
        return deepcopy(snapshot)
    if workflow_id != state.get("workflow_id") or sequence != state.get("sequence") or event.get("stage") != state.get("stage"):
        raise WorkflowStateError("EVENT_STATE_MISMATCH")
    if state.get("status") not in STATUSES:
        raise WorkflowStateError("UNKNOWN_STATUS")
    return deepcopy(state)


def _infer_retry_stage(snapshot: dict[str, Any]) -> str:
    artifacts = snapshot.get("artifacts", [])
    if any(item.get("kind") == "summary_checkpoint_json" and not item.get("stale") for item in artifacts):
        return "writing_final"
    if any(item.get("kind") in {"transcript_markdown", "transcript_json"} and not item.get("stale") for item in artifacts):
        return "summarizing"
    return "transcribing"
