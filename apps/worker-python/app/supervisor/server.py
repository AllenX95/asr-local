from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from typing import Any

from app.ipc.v2 import ProtocolError, decode_request, encode_event, encode_response
from app.config import project_root
from app.workflow.registry import WorkflowRegistry
from app.workflow.supervisor import WorkflowSupervisor
from app.workflow.secrets import EphemeralSecretBroker, SecretRequest


class BrokerSecretProvider:
    """Bridges a just-in-time secret request to the desktop over contract v2."""

    def __init__(self) -> None:
        self.broker = EphemeralSecretBroker()
        self.on_request = None
        self.on_granted = None
        self._pending: dict[str, asyncio.Future[str]] = {}

    async def provide(self, *, workflow_id: str, attempt_id: str, profile: dict[str, Any], purpose: str) -> str:
        request = self.broker.request(workflow_id=workflow_id, attempt_id=attempt_id, profile=profile, purpose=purpose)
        future = asyncio.get_running_loop().create_future()
        self._pending[request.secret_request_id] = future
        if self.on_request is not None:
            await self.on_request({**request.as_event_data(), "workflow_id": request.workflow_id, "attempt_id": request.attempt_id})
        try:
            return await future
        finally:
            self._pending.pop(request.secret_request_id, None)
            self.broker.revoke(request.secret_request_id)

    async def grant(self, params: dict[str, Any]) -> dict[str, Any]:
        if params.get("lease_scope") != "attempt":
            raise ValueError("CREDENTIAL_REJECTED: unsupported lease scope")
        secret = self.broker.provide(
            secret_request_id=params["secret_request_id"],
            workflow_id=params["workflow_id"],
            attempt_id=params["expected_attempt_id"],
            profile_id=params["profile_id"],
            profile_version=params["profile_version"],
            credential_ref=params["credential_ref"],
            purpose=params["purpose"],
            provider_binding_sha256=params["provider_binding_sha256"],
            secret=params["secret"],
        )
        future = self._pending.get(params["secret_request_id"])
        if future is None:
            raise ValueError("CREDENTIAL_REJECTED: secret request is not pending")
        if not future.done():
            future.set_result(secret)
        if self.on_granted is not None:
            await self.on_granted(params["workflow_id"], params["expected_attempt_id"])
        return {"accepted": True, "secret_request_id": params["secret_request_id"]}


class V2StdioServer:
    def __init__(self, *, pipeline_mode: str = "auto") -> None:
        self.registry = WorkflowRegistry(project_root() / "outputs" / ".workflow" / "registry.sqlite3")
        self.secret_provider = BrokerSecretProvider()
        self.requested_pipeline_mode = pipeline_mode
        self.pipeline_mode = resolve_pipeline_mode(pipeline_mode)
        self.startup_error: dict[str, Any] | None = None
        if self.pipeline_mode == "production":
            try:
                self.supervisor = self._production_supervisor()
            except (ImportError, OSError) as exc:
                self.startup_error = _production_startup_error(exc)
                self.supervisor = WorkflowSupervisor(self.registry, event_sink=self._emit_event)
        else:
            self.supervisor = WorkflowSupervisor(self.registry, event_sink=self._emit_event)
        self.secret_provider.on_request = self.supervisor.mark_waiting_for_secret
        self.secret_provider.on_granted = self.supervisor.mark_secret_granted
        self.handshaken = False
        self.stopping = False
        self._stdout_lock = asyncio.Lock()
        self._defer_events = False
        self._deferred_events: list[dict[str, Any]] = []

    def _production_supervisor(self) -> WorkflowSupervisor:
        """Load native adapters only after production mode is selected.

        The v2 protocol must remain available in auto/fake mode on machines
        that do not have the optional inference stack installed. Keeping these
        imports behind the mode boundary also lets explicit production errors
        be returned as protocol responses instead of killing stdout at import
        time.
        """
        from app.pipeline.cloud_asr import CloudAsrTranscriber
        from app.pipeline.legacy_v2 import LegacyQwenPyannoteTranscriber
        from app.pipeline.moss_v2 import MossTranscriber
        from app.pipeline.router import ProfileRoutingTranscriber
        from app.summary.openai_compatible import OpenAICompatibleSummaryGenerator

        return WorkflowSupervisor(
            self.registry,
            transcriber=ProfileRoutingTranscriber(
                moss=MossTranscriber(),
                cloud=CloudAsrTranscriber(secret_provider=self.secret_provider),
                legacy=LegacyQwenPyannoteTranscriber(),
            ),
            summary_generator=OpenAICompatibleSummaryGenerator(secret_provider=self.secret_provider),
            event_sink=self._emit_event,
        )

    async def run(self) -> int:
        while not self.stopping:
            raw_line = await asyncio.to_thread(sys.stdin.buffer.readline)
            if not raw_line:
                break
            if not raw_line.strip():
                continue
            await self._handle_line(raw_line)
        if self.supervisor._started:
            await self.supervisor.shutdown(interrupt=True)
        self.registry.close()
        return 0

    async def _handle_line(self, raw_line: bytes) -> None:
        request_id = "unknown"
        operation_id: str | None = None
        try:
            message = decode_request(raw_line)
            request_id = message.get("request_id", request_id)
            operation_id = message.get("operation_id")
            if self.startup_error is not None:
                await self._respond(request_id, ok=False, error=self.startup_error, operation_id=operation_id)
                return
            if not self.handshaken:
                if message["method"] != "runtime.hello":
                    raise ProtocolError("HANDSHAKE_REQUIRED", "runtime.hello must be the first request.", [], {})
                self.handshaken = True
                await self._respond(
                    request_id,
                    ok=True,
                    result={
                        "selected_version": 2,
                        "worker_instance_id": "worker-v2-instance",
                        "store_instance_id": "workflow-store-v2",
                        "runtime_version": "0.2.0",
                        "capabilities": capabilities(
                            requested_pipeline_mode=self.requested_pipeline_mode,
                            resolved_pipeline_mode=self.pipeline_mode,
                        ),
                    },
                )
                return
            self._defer_events = True
            result = await self._dispatch(message)
            await self._respond(request_id, ok=True, result=result, operation_id=operation_id)
            await self._flush_deferred_events()
        except ProtocolError as exc:
            await self._respond(request_id, ok=False, error=exc.as_error(), operation_id=operation_id)
            await self._flush_deferred_events()
        except Exception as exc:  # pragma: no cover - exercised through integration smoke
            await self._respond(
                request_id,
                ok=False,
                error={
                    "code": _error_code(exc),
                    "message": str(exc),
                    "retryable": False,
                    "field_errors": [],
                    "details": {},
                    "diagnostic_id": "diag-v2-server",
                },
                operation_id=operation_id,
            )
            await self._flush_deferred_events()

    async def _dispatch(self, message: dict[str, Any]) -> dict[str, Any]:
        method = message["method"]
        params = message["params"]
        if method == "runtime.hello":
            return {"selected_version": 2}
        if method == "runtime.capabilities":
            return capabilities()
        if method == "prompt.preview":
            return _prompt_preview(params)
        if method == "workflow.submit":
            return await self.supervisor.submit(params["draft"], operation_id=message["operation_id"])
        if method == "workflow.list":
            statuses = set(params.get("statuses", [])) or None
            return {"items": await self.supervisor.list(statuses), "next_cursor": None}
        if method == "workflow.get":
            snapshot = await self.supervisor.get(params["workflow_id"])
            return {"snapshot": snapshot, "timeline": self.supervisor.registry.timeline(params["workflow_id"], params.get("timeline_limit", 200)), "attempt_history": []}
        if method == "workflow.clear":
            return await self.supervisor.clear(params, operation_id=message["operation_id"])
        if method == "workflow.control":
            return await self.supervisor.control(params, operation_id=message["operation_id"])
        if method == "workflow.retry":
            return await self.supervisor.retry(params, operation_id=message["operation_id"])
        if method == "artifact.register_revision":
            return await self.supervisor.register_revision(params, operation_id=message["operation_id"])
        if method == "secret.provide":
            return await self.secret_provider.grant(params)
        if method == "runtime.shutdown":
            active_workflow_ids = [item["workflow_id"] for item in self.supervisor.registry.active_snapshots()]
            await self.supervisor.shutdown(interrupt=params.get("mode") == "interrupt")
            self.stopping = True
            return {"state": "interrupting" if params.get("mode") == "interrupt" else "draining", "active_workflow_ids": active_workflow_ids}
        raise ValueError(f"UNSUPPORTED_METHOD: {method}")

    async def _respond(self, request_id: str, *, ok: bool, result: Any = None, error: dict[str, Any] | None = None, operation_id: str | None = None) -> None:
        raw = encode_response(request_id, ok=ok, result=result, error=error, operation_id=operation_id)
        async with self._stdout_lock:
            sys.stdout.buffer.write(raw)
            sys.stdout.buffer.flush()

    async def _emit_event(self, payload: dict[str, Any]) -> None:
        if self._defer_events:
            self._deferred_events.append(payload)
            return
        raw = encode_event(payload)
        async with self._stdout_lock:
            sys.stdout.buffer.write(raw)
            sys.stdout.buffer.flush()

    async def _flush_deferred_events(self) -> None:
        self._defer_events = False
        pending = self._deferred_events
        self._deferred_events = []
        for payload in pending:
            await self._emit_event(payload)


def capabilities(*, requested_pipeline_mode: str = "auto", resolved_pipeline_mode: str | None = None) -> dict[str, Any]:
    return {
        "methods": [
            "runtime.capabilities",
            "prompt.preview",
            "workflow.submit",
            "workflow.list",
            "workflow.get",
            "workflow.clear",
            "workflow.control",
            "workflow.retry",
            "artifact.register_revision",
            "secret.provide",
            "runtime.shutdown",
        ],
        "pipeline_profiles": ["moss_transcribe_diarize", "qwen3_asr_with_pyannote"],
        "max_inflight_workflows": 3,
        "event_recovery": "snapshot_reconcile",
        "secret_transport": "ephemeral_grant",
        "max_message_bytes": 1048576,
        "pipeline_mode": {
            "requested": requested_pipeline_mode,
            "resolved": resolved_pipeline_mode or resolve_pipeline_mode(requested_pipeline_mode),
        },
    }


def resolve_pipeline_mode(requested: str) -> str:
    """Resolve the safe v2 default without hiding an explicit operator choice.

    ``auto`` selects production only when the configured MOSS model and its
    native runtime dependencies are present. A missing optional dependency or
    model keeps the desktop usable for UI/recovery testing, while explicit
    ``production`` still fails loudly when the operator requests it.
    """
    if requested in {"fake", "production"}:
        return requested
    if requested != "auto":
        raise ValueError(f"unsupported pipeline mode: {requested}")
    try:
        from app.runtime.env import environment_snapshot

        snapshot = environment_snapshot()
        moss = snapshot["models"]["moss_transcribe_diarize"]
        optional = snapshot["optional_modules"]
        torch_runtime = snapshot.get("torch_runtime")
        torch_ready = (
            bool(torch_runtime.get("available"))
            if isinstance(torch_runtime, dict)
            else bool(optional.get("torch"))
        )
        native_ready = (
            bool(moss.get("exists"))
            and torch_ready
            and bool(optional.get("transformers"))
            and importlib.util.find_spec("soundfile") is not None
        )
    except Exception:
        native_ready = False
    return "production" if native_ready else "fake"


def _prompt_preview(params: dict[str, Any]) -> dict[str, Any]:
    prompt_input = params.get("prompt_input", {})
    parts = [
        "请将音频转写为文本，每一段需以起始时间戳和说话人编号（[S01]、[S02]、[S03]…）开头，"
        "正文为对应的语音内容，并在段末标注结束时间戳，以清晰标明该段语音范围。"
    ]
    if prompt_input.get("recording_background"):
        parts.append(f"录音背景：\n{prompt_input['recording_background']}")
    if prompt_input.get("hotwords"):
        parts.append("热词提示：" + "、".join(str(word) for word in prompt_input["hotwords"]))
    if prompt_input.get("extra_instruction"):
        parts.append(str(prompt_input["extra_instruction"]).strip())
    compiled_text = "\n\n".join(parts)
    import hashlib

    return {
        "compiler_id": "moss-prompt",
        "compiler_version": 1,
        "base_template_version": "openmoss-official-2026-07-09",
        "compiled_text": compiled_text,
        "sha256": hashlib.sha256(compiled_text.encode("utf-8")).hexdigest(),
        "warnings": [],
    }


def _error_code(error: Exception) -> str:
    message = str(error)
    for code in ("STALE_ATTEMPT", "SEQUENCE_CONFLICT", "INVALID_TRANSITION", "CONTROL_NOT_SUPPORTED", "WORKFLOW_NOT_TERMINAL", "NOT_FOUND", "CREDENTIAL_REJECTED", "CREDENTIAL_REQUIRED", "SOURCE_NOT_FOUND", "SOURCE_UNREADABLE", "SOURCE_CHANGED", "OUTPUT_CONFLICT", "SUMMARY_INPUT_TOO_LARGE", "SUMMARY_RESULT_UNKNOWN", "MODEL_SNAPSHOT_MISMATCH", "LEGACY_ADAPTER_UNAVAILABLE", "CLOUD_PROFILE_REQUIRED", "UNSUPPORTED_PIPELINE_PROFILE"):
        if code in message:
            return code
    return "INTERNAL"


def _production_startup_error(error: Exception) -> dict[str, Any]:
    dependency = getattr(error, "name", None) or "the native inference runtime"
    return {
        "code": "DEPENDENCY_MISSING" if isinstance(error, ImportError) else "RUNTIME_INIT_FAILED",
        "message": f"MOSS production runtime cannot start: {dependency}",
        "retryable": False,
        "field_errors": [],
        "details": {
            "dependency": dependency,
            "hint": "Install the worker's moss-native dependencies in apps/worker-python/.venv and restart the desktop app.",
        },
        "diagnostic_id": "diag-v2-missing-dependency",
    }


def run_v2_stdio(*, pipeline_mode: str = "auto") -> int:
    return asyncio.run(V2StdioServer(pipeline_mode=pipeline_mode).run())
