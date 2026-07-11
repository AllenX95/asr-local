from __future__ import annotations

import copy
import json
from typing import Any

from .canonical import normalize_json
from .errors import ProtocolError

MAX_MESSAGE_BYTES = 1024 * 1024
PROTOCOL = "asr-local-workflow"
VERSION = 2
PERSISTENT_OPERATION_METHODS = {
    "workflow.submit",
    "workflow.control",
    "workflow.retry",
    "workflow.clear",
    "artifact.register_revision",
}
KNOWN_METHODS = {
    "runtime.hello",
    "runtime.capabilities",
    "prompt.preview",
    "workflow.submit",
    "workflow.list",
    "workflow.get",
    "workflow.control",
    "workflow.retry",
    "workflow.clear",
    "artifact.register_revision",
    "secret.provide",
    "runtime.shutdown",
}
PURPOSES = {"summary_api", "cloud_asr"}
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


def _error(message: str, field: str | None = None) -> ProtocolError:
    return ProtocolError(
        "INVALID_REQUEST",
        message,
        [{"field": field, "message": message}] if field else [],
        {},
    )


def _require_keys(value: dict[str, Any], keys: set[str], label: str) -> None:
    missing = sorted(keys - value.keys())
    if missing:
        raise _error(f"{label} is missing required fields: {', '.join(missing)}.", label)


def _reject_unknown(value: dict[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise _error(f"{label} contains unknown fields: {', '.join(unknown)}.", label)


def _string(value: Any, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise _error(f"{field} must be a non-empty string.", field)
    return value


def normalize_workflow_draft(draft: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(draft, dict):
        raise _error("draft must be an object.", "draft")
    normalized = copy.deepcopy(draft)
    _validate_draft_shape(normalized)

    prompt = normalized["transcription"]["prompt_input"]
    seen: set[str] = set()
    hotwords: list[str] = []
    for item in prompt["hotwords"]:
        word = _string(item, "transcription.prompt_input.hotwords").strip()
        key = word.casefold()
        if key not in seen:
            seen.add(key)
            hotwords.append(word)
    prompt["hotwords"] = hotwords

    replacements = normalized["transcription"]["postprocess"]["replacements"]
    for index, rule in enumerate(replacements):
        rule["wrong"] = _string(rule["wrong"], f"replacements[{index}].wrong").strip()
        rule["correct"] = _string(rule["correct"], f"replacements[{index}].correct").strip()

    return normalized


def _validate_draft_shape(draft: dict[str, Any]) -> None:
    _reject_unknown(draft, {"draft_version", "display_name", "source", "transcription", "summary", "output"}, "draft")
    _require_keys(draft, {"draft_version", "display_name", "source", "transcription", "summary", "output"}, "draft")
    if draft["draft_version"] != 2:
        raise _error("draft_version must be 2.", "draft.draft_version")
    _string(draft["display_name"], "draft.display_name")

    source = draft["source"]
    if not isinstance(source, dict):
        raise _error("source must be an object.", "draft.source")
    _reject_unknown(source, {"path"}, "draft.source")
    _require_keys(source, {"path"}, "draft.source")
    _string(source["path"], "draft.source.path")

    transcription = draft["transcription"]
    if not isinstance(transcription, dict):
        raise _error("transcription must be an object.", "draft.transcription")
    _reject_unknown(
        transcription,
        {"pipeline_profile", "pipeline_profile_version", "device_policy", "language", "prompt_input", "postprocess", "cloud_profile", "audio"},
        "draft.transcription",
    )
    _require_keys(
        transcription,
        {"pipeline_profile", "pipeline_profile_version", "device_policy", "language", "prompt_input", "postprocess", "cloud_profile"},
        "draft.transcription",
    )
    if transcription["pipeline_profile"] not in {"moss_transcribe_diarize", "qwen3_asr_with_pyannote", "cloud_asr"}:
        raise _error("Unsupported pipeline profile.", "draft.transcription.pipeline_profile")
    if not isinstance(transcription["pipeline_profile_version"], int) or transcription["pipeline_profile_version"] < 1:
        raise _error("pipeline_profile_version must be a positive integer.", "draft.transcription.pipeline_profile_version")
    if transcription["device_policy"] not in {"auto", "cpu", "cuda"}:
        raise _error("Unsupported device policy.", "draft.transcription.device_policy")

    audio = transcription.get("audio")
    if audio is None:
        transcription["audio"] = {"channel_strategy": "mixdown"}
    else:
        if not isinstance(audio, dict):
            raise _error("audio must be an object.", "draft.transcription.audio")
        _reject_unknown(audio, {"channel_strategy"}, "audio")
        _require_keys(audio, {"channel_strategy"}, "audio")
        if audio["channel_strategy"] not in {"mixdown", "split_stereo"}:
            raise _error("Unsupported audio channel strategy.", "draft.transcription.audio.channel_strategy")

    language = transcription["language"]
    if not isinstance(language, dict) or set(language) != {"mode", "value"}:
        raise _error("language must contain mode and value.", "draft.transcription.language")
    if language["mode"] not in {"auto", "fixed"}:
        raise _error("Unsupported language mode.", "draft.transcription.language.mode")
    if language["mode"] == "fixed" and not isinstance(language["value"], str):
        raise _error("Fixed language requires a string value.", "draft.transcription.language.value")

    prompt = transcription["prompt_input"]
    if not isinstance(prompt, dict):
        raise _error("prompt_input must be an object.", "draft.transcription.prompt_input")
    _reject_unknown(prompt, {"recording_background", "hotwords", "extra_instruction"}, "prompt_input")
    _require_keys(prompt, {"recording_background", "hotwords", "extra_instruction"}, "prompt_input")
    if not isinstance(prompt["recording_background"], str) or len(prompt["recording_background"]) > 4000:
        raise _error("recording_background is invalid.", "prompt_input.recording_background")
    if not isinstance(prompt["hotwords"], list) or len(prompt["hotwords"]) > 200:
        raise _error("hotwords must contain at most 200 items.", "prompt_input.hotwords")
    if not isinstance(prompt["extra_instruction"], str) or len(prompt["extra_instruction"]) > 1000:
        raise _error("extra_instruction is invalid.", "prompt_input.extra_instruction")

    postprocess = transcription["postprocess"]
    if not isinstance(postprocess, dict):
        raise _error("postprocess must be an object.", "draft.transcription.postprocess")
    _reject_unknown(postprocess, {"replacements", "keep_fillers", "auto_punctuation"}, "postprocess")
    _require_keys(postprocess, {"replacements", "keep_fillers", "auto_punctuation"}, "postprocess")
    if not isinstance(postprocess["replacements"], list) or len(postprocess["replacements"]) > 500:
        raise _error("replacements must contain at most 500 items.", "postprocess.replacements")
    for index, rule in enumerate(postprocess["replacements"]):
        if not isinstance(rule, dict) or set(rule) != {"wrong", "correct"}:
            raise _error("replacement must contain wrong and correct.", f"postprocess.replacements[{index}]")
    if not isinstance(postprocess["keep_fillers"], bool) or not isinstance(postprocess["auto_punctuation"], bool):
        raise _error("postprocess flags must be boolean.", "postprocess")

    cloud_profile = transcription["cloud_profile"]
    if transcription["pipeline_profile"] == "cloud_asr" and cloud_profile is None:
        raise _error("cloud_profile is required for cloud_asr.", "transcription.cloud_profile")
    if cloud_profile is not None:
        _validate_provider(cloud_profile, "transcription.cloud_profile")
    elif transcription["pipeline_profile"] != "cloud_asr":
        transcription["cloud_profile"] = None

    _validate_provider(draft["summary"], "summary")
    output = draft["output"]
    if not isinstance(output, dict):
        raise _error("output must be an object.", "draft.output")
    _reject_unknown(output, {"directory", "base_name", "collision_policy"}, "output")
    _require_keys(output, {"directory", "base_name", "collision_policy"}, "output")
    _string(output["directory"], "output.directory")
    _string(output["base_name"], "output.base_name")
    if output["collision_policy"] not in {"reject", "unique_suffix"}:
        raise _error("Unsupported collision policy.", "output.collision_policy")


def _validate_provider(provider: Any, field: str) -> None:
    if not isinstance(provider, dict):
        raise _error("provider must be an object.", field)
    required = {"profile_id", "profile_version", "base_url", "auth_mode", "model", "credential_ref", "provider_binding_sha256"}
    allowed = required
    if field == "summary":
        allowed = required | {"model_source", "template", "context_strategy", "input_token_budget", "max_output_tokens"}
    _reject_unknown(provider, allowed, field)
    _require_keys(provider, allowed if field == "summary" else required, field)
    _string(provider["profile_id"], f"{field}.profile_id")
    if not isinstance(provider["profile_version"], int) or provider["profile_version"] < 1:
        raise _error("profile_version must be a positive integer.", f"{field}.profile_version")
    _string(provider["base_url"], f"{field}.base_url")
    if provider["auth_mode"] not in {"none", "bearer"}:
        raise _error("Unsupported auth_mode.", f"{field}.auth_mode")
    _string(provider["model"], f"{field}.model")
    _string(provider["provider_binding_sha256"], f"{field}.provider_binding_sha256")
    if provider["auth_mode"] == "none" and provider["credential_ref"] is not None:
        raise _error("auth_mode=none requires credential_ref=null.", f"{field}.credential_ref")
    if provider["auth_mode"] == "bearer":
        _string(provider["credential_ref"], f"{field}.credential_ref")
    if field == "summary":
        if provider["model_source"] not in {"profile_default", "recipe_override"}:
            raise _error("Unsupported model_source.", "summary.model_source")
        template = provider["template"]
        if not isinstance(template, dict) or set(template) != {"id", "version", "name", "prompt_snapshot"}:
            raise _error("summary.template is invalid.", "summary.template")
        _string(template["id"], "summary.template.id")
        _string(template["name"], "summary.template.name")
        _string(template["prompt_snapshot"], "summary.template.prompt_snapshot")
        if not isinstance(template["version"], int) or template["version"] < 1:
            raise _error("summary.template.version must be positive.", "summary.template.version")
        if provider["context_strategy"] not in {"auto", "single_pass", "hierarchical"}:
            raise _error("Unsupported context strategy.", "summary.context_strategy")
        for name in ("input_token_budget", "max_output_tokens"):
            if not isinstance(provider[name], int) or provider[name] < 1:
                raise _error(f"{name} must be a positive integer.", f"summary.{name}")


def validate_envelope(message: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(message, dict):
        raise _error("Envelope must be a JSON object.")
    kind = message.get("kind")
    allowed = {"protocol", "protocol_version", "kind", "request_id", "operation_id", "method", "params", "ok", "result", "error", "event", "payload"}
    _reject_unknown(message, allowed, "envelope")
    _require_keys(message, {"protocol", "protocol_version", "kind"}, "envelope")
    if message["protocol"] != PROTOCOL or message["protocol_version"] != VERSION:
        raise _error("Unsupported protocol version.", "protocol_version")
    if kind not in {"request", "response", "event"}:
        raise _error("Unsupported envelope kind.", "kind")
    if kind == "request":
        _require_keys(message, {"request_id", "method", "params"}, "request")
        _string(message["request_id"], "request_id")
        method = _string(message["method"], "method")
        if method not in KNOWN_METHODS:
            raise ProtocolError("UNSUPPORTED_METHOD", f"Unsupported method: {method}.", [], {})
        if not isinstance(message["params"], dict):
            raise _error("params must be an object.", "params")
        if method in PERSISTENT_OPERATION_METHODS and not message.get("operation_id"):
            raise _error("operation_id is required for this method.", "operation_id")
        if method == "secret.provide" and "operation_id" in message:
            raise _error("secret.provide must not carry operation_id.", "operation_id")
        if method == "runtime.shutdown" and "operation_id" in message:
            raise _error("runtime.shutdown must not carry operation_id.", "operation_id")
        if method == "workflow.submit":
            params = message["params"]
            _require_keys(params, {"draft"}, "workflow.submit.params")
            normalized = normalize_workflow_draft(params["draft"])
            message = copy.deepcopy(message)
            message["params"]["draft"] = normalized
        elif method == "workflow.control":
            _validate_control_params(message["params"])
        elif method == "workflow.retry":
            _validate_retry_params(message["params"])
        elif method == "workflow.clear":
            _validate_clear_params(message["params"])
        elif method == "artifact.register_revision":
            _validate_revision_params(message["params"])
        elif method == "secret.provide":
            _validate_secret_params(message["params"])
    elif kind == "response":
        _require_keys(message, {"request_id", "ok"}, "response")
        _string(message["request_id"], "request_id")
        if not isinstance(message["ok"], bool):
            raise _error("ok must be boolean.", "ok")
        if message["ok"] and "error" in message:
            raise _error("successful response cannot contain error.", "error")
        if not message["ok"] and "result" in message:
            raise _error("failed response cannot contain result.", "result")
    else:
        _require_keys(message, {"event", "payload"}, "event")
        if message["event"] != "workflow.event" or not isinstance(message["payload"], dict):
            raise _error("Invalid workflow event envelope.", "payload")
        _validate_event_payload(message["payload"])
    return normalize_json(message)


def _validate_event_payload(payload: dict[str, Any]) -> None:
    required = {"workflow_id", "attempt_id", "sequence", "occurred_at", "type", "stage", "data", "state"}
    _require_keys(payload, required, "workflow.event.payload")
    _string(payload["workflow_id"], "workflow.event.payload.workflow_id")
    _string(payload["attempt_id"], "workflow.event.payload.attempt_id")
    if not isinstance(payload["sequence"], int) or payload["sequence"] < 1:
        raise _error("sequence must be positive.", "workflow.event.payload.sequence")
    _string(payload["occurred_at"], "workflow.event.payload.occurred_at")
    _string(payload["type"], "workflow.event.payload.type")
    if not isinstance(payload["data"], dict) or not isinstance(payload["state"], dict):
        raise _error("event data and state must be objects.", "workflow.event.payload")
    state_required = {"snapshot_version", "workflow_id", "sequence", "spec", "status", "stage", "attempt", "progress", "control", "runtime_plan", "artifacts", "recovery", "last_error", "timestamps"}
    _require_keys(payload["state"], state_required, "workflow.event.payload.state")
    if payload["workflow_id"] != payload["state"]["workflow_id"]:
        raise _error("payload workflow_id must equal state.workflow_id.", "workflow.event.payload.workflow_id")
    if payload["sequence"] != payload["state"]["sequence"]:
        raise _error("payload sequence must equal state.sequence.", "workflow.event.payload.sequence")
    if payload["stage"] != payload["state"]["stage"]:
        raise _error("payload stage must equal state.stage.", "workflow.event.payload.stage")
    attempt = payload["state"]["attempt"]
    if not isinstance(attempt, dict) or payload["attempt_id"] != attempt.get("attempt_id"):
        raise _error("payload attempt_id must equal state.attempt.attempt_id.", "workflow.event.payload.attempt_id")
    if payload["state"]["status"] not in STATUSES:
        raise _error("Unsupported workflow status.", "workflow.event.payload.state.status")


def _validate_control_params(params: dict[str, Any]) -> None:
    allowed = {"workflow_id", "expected_attempt_id", "action"}
    _reject_unknown(params, allowed, "workflow.control.params")
    _require_keys(params, allowed, "workflow.control.params")
    _string(params["workflow_id"], "workflow.control.params.workflow_id")
    _string(params["expected_attempt_id"], "workflow.control.params.expected_attempt_id")
    if params["action"] not in {"pause", "resume", "cancel"}:
        raise _error("Unsupported control action.", "workflow.control.params.action")


def _validate_retry_params(params: dict[str, Any]) -> None:
    allowed = {"workflow_id", "expected_attempt_id", "expected_sequence", "from_stage", "input_artifact_id"}
    _reject_unknown(params, allowed, "workflow.retry.params")
    _require_keys(params, {"workflow_id", "expected_attempt_id", "expected_sequence", "from_stage"}, "workflow.retry.params")
    _string(params["workflow_id"], "workflow.retry.params.workflow_id")
    _string(params["expected_attempt_id"], "workflow.retry.params.expected_attempt_id")
    if not isinstance(params["expected_sequence"], int) or params["expected_sequence"] < 1:
        raise _error("expected_sequence must be positive.", "workflow.retry.params.expected_sequence")
    if params["from_stage"] not in {"auto", "transcribing", "summarizing", "writing_final"}:
        raise _error("Unsupported retry stage.", "workflow.retry.params.from_stage")


def _validate_clear_params(params: dict[str, Any]) -> None:
    allowed = {"workflow_id"}
    _reject_unknown(params, allowed, "workflow.clear.params")
    _require_keys(params, allowed, "workflow.clear.params")
    _string(params["workflow_id"], "workflow.clear.params.workflow_id")


def _validate_revision_params(params: dict[str, Any]) -> None:
    allowed = {"workflow_id", "expected_attempt_id", "expected_sequence", "source_artifact_id", "kind", "staged_path", "size_bytes", "sha256"}
    _reject_unknown(params, allowed, "artifact.register_revision.params")
    _require_keys(params, allowed, "artifact.register_revision.params")
    for name in ("workflow_id", "expected_attempt_id", "source_artifact_id", "staged_path", "sha256"):
        _string(params[name], f"artifact.register_revision.params.{name}")
    if not isinstance(params["expected_sequence"], int) or params["expected_sequence"] < 1:
        raise _error("expected_sequence must be positive.", "artifact.register_revision.params.expected_sequence")
    if params["kind"] not in {"transcript_markdown", "final_summary_markdown"}:
        raise _error("Unsupported artifact revision kind.", "artifact.register_revision.params.kind")
    if not isinstance(params["size_bytes"], int) or params["size_bytes"] < 0:
        raise _error("size_bytes must be non-negative.", "artifact.register_revision.params.size_bytes")


def _validate_secret_params(params: dict[str, Any]) -> None:
    allowed = {"workflow_id", "expected_attempt_id", "secret_request_id", "profile_id", "profile_version", "credential_ref", "purpose", "provider_binding_sha256", "secret", "lease_scope"}
    _reject_unknown(params, allowed, "secret.provide.params")
    _require_keys(params, allowed, "secret.provide.params")
    for name in ("workflow_id", "expected_attempt_id", "secret_request_id", "profile_id", "provider_binding_sha256", "secret", "lease_scope"):
        _string(params[name], f"secret.provide.params.{name}")
    if not isinstance(params["profile_version"], int) or params["profile_version"] < 1:
        raise _error("profile_version must be positive.", "secret.provide.params.profile_version")
    _string(params["credential_ref"], "secret.provide.params.credential_ref")
    if params["purpose"] not in PURPOSES:
        raise _error("Unsupported secret purpose.", "secret.provide.params.purpose")


def decode_request(line: bytes | str) -> dict[str, Any]:
    raw = line.encode("utf-8") if isinstance(line, str) else line
    if len(raw) > MAX_MESSAGE_BYTES:
        raise ProtocolError("INVALID_REQUEST", "Message exceeds the 1 MiB limit.", [], {})
    try:
        text = raw.decode("utf-8", errors="strict")
        value = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError("INVALID_REQUEST", f"Invalid UTF-8 JSON message: {exc}.", [], {}) from exc
    return validate_envelope(value)


def encode_response(request_id: str, *, ok: bool, result: Any = None, error: dict[str, Any] | None = None, operation_id: str | None = None) -> bytes:
    response: dict[str, Any] = {"protocol": PROTOCOL, "protocol_version": VERSION, "kind": "response", "request_id": request_id, "ok": ok}
    if operation_id is not None:
        response["operation_id"] = operation_id
    if ok:
        response["result"] = result if result is not None else {}
    else:
        response["error"] = error or {}
    validate_envelope(response)
    return (json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def encode_event(payload: dict[str, Any]) -> bytes:
    event = {"protocol": PROTOCOL, "protocol_version": VERSION, "kind": "event", "event": "workflow.event", "payload": payload}
    validate_envelope(event)
    return (json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
