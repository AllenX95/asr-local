"""Shared v2 adapter for Pyannote-first local ASR backends.

The existing, well-tested segment/export implementation remains in
``job_runner`` for this first migration slice.  This adapter makes the
backend choice explicit per immutable workflow snapshot and forces every
local model through external Pyannote segmentation.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import threading
import time
from typing import Any

from app.models.manager import ModelManager
from app.pipeline.job_runner import run_job


class _NoopInferenceHook:
    """Compatibility hook for older tests; it never serializes execution."""

    def acquire(self) -> None:
        return None

    def release(self) -> None:
        return None


# Kept as a non-blocking compatibility seam for pre-dispatcher callers.  This
# is deliberately not a Semaphore and does not constrain CPU or CUDA work.
_LOCAL_INFERENCE_LANE = _NoopInferenceHook()


LOCAL_CLOSE_WAIT_SECONDS = 5.0


class ChunkedLocalTranscriber:
    backend_id = "pyannote_qwen3_asr"

    def __init__(
        self,
        *,
        batch_executor=None,
        execution_gate=None,
        close_wait_seconds: float = LOCAL_CLOSE_WAIT_SECONDS,
        close_wait_timeout_seconds: float | None = None,
    ) -> None:
        # The dispatcher owns the one shared CUDA Qwen model.  A missing
        # dispatcher is intentional for CPU workflows and legacy direct use.
        self.batch_executor = batch_executor
        self.execution_gate = execution_gate
        self.close_wait_seconds = max(
            0.0,
            float(
                close_wait_seconds
                if close_wait_timeout_seconds is None
                else close_wait_timeout_seconds
            ),
        )
        self._closed = False
        self._closing = False
        self._active_sync_calls = 0
        self._active_attempts: set[tuple[str, str]] = set()
        self._cancelled_attempts: set[tuple[str, str]] = set()
        # A call is registered before its daemon native runner is started.
        # The token, rather than a racy ``started`` flag, is the ownership
        # record used by the runner's finally block to debit active calls.
        self._active_call_tokens: dict[object, tuple[str, str]] = {}
        self._dispatcher_close_started = False
        self._dispatcher_closed = False
        self._deferred_dispatcher_close = False
        self._lifecycle = threading.Condition(threading.Lock())

    async def transcribe(self, spec: dict[str, Any], attempt_id: str, *, progress=None) -> dict[str, Any]:
        workflow_id = str(spec.get("workflow_id", "workflow-v2"))
        loop = asyncio.get_running_loop()
        token = self._register_native_call(workflow_id, attempt_id)
        future = loop.create_future()
        native_thread = threading.Thread(
            target=self._native_runner,
            args=(token, loop, future, spec, attempt_id, progress),
            name=f"local-asr-{workflow_id}-{attempt_id}",
            daemon=True,
        )
        try:
            native_thread.start()
        except BaseException:
            # No runner can execute its finally block if thread creation
            # itself fails.  This is the only exceptional reservation cleanup
            # path; normal cancellation never debits active calls.
            self._finish_native_call(token)
            raise
        return await future

    def _native_runner(self, token, loop, future, spec, attempt_id, progress) -> None:
        try:
            result = self._transcribe_sync(spec, attempt_id, progress)
        except BaseException as exc:
            self._deliver_future(loop, future, exception=exc)
        else:
            self._deliver_future(loop, future, result=result)
        finally:
            # Only the native runner owns the active-call decrement.  The
            # asyncio task can be cancelled while this synchronous call is
            # still executing and must not make close() race it.
            self._finish_native_call(token)

    @staticmethod
    def _deliver_future(loop, future, *, result=None, exception=None) -> None:
        def deliver() -> None:
            if future.done():
                return
            if exception is not None:
                future.set_exception(exception)
            else:
                future.set_result(result)

        try:
            loop.call_soon_threadsafe(deliver)
        except RuntimeError:
            # asyncio.run() may have closed the loop after the caller
            # cancelled the task.  The daemon runner still reaches its
            # lifecycle finally block; there is simply no Future to notify.
            return

    def _register_native_call(self, workflow_id: str, attempt_id: str):
        with self._lifecycle:
            if self._closing or self._closed:
                raise RuntimeError("LOCAL_TRANSCRIBER_CLOSED: no new attempts accepted")
            token = object()
            key = (str(workflow_id), str(attempt_id))
            self._active_call_tokens[token] = key
            self._active_sync_calls += 1
            self._active_attempts.add(key)
            return token

    def _finish_native_call(self, token) -> None:
        close_dispatcher = False
        with self._lifecycle:
            key = self._active_call_tokens.pop(token, None)
            if key is None:
                return
            self._active_sync_calls = max(0, self._active_sync_calls - 1)
            if not any(active_key == key for active_key in self._active_call_tokens.values()):
                self._active_attempts.discard(key)
                self._cancelled_attempts.discard(key)
            if (
                self._active_sync_calls == 0
                and self._closing
                and self._deferred_dispatcher_close
                and not self._dispatcher_close_started
            ):
                self._dispatcher_close_started = True
                close_dispatcher = True
            self._lifecycle.notify_all()
        if close_dispatcher:
            self._close_dispatcher_once()

    def _transcribe_sync(self, spec: dict[str, Any], attempt_id: str, progress=None) -> dict[str, Any]:
        workflow_id = str(spec.get("workflow_id", "workflow-v2"))
        runtime_plan = spec.get("runtime_plan", {})
        resolved_device = runtime_plan.get("resolved_device") if isinstance(runtime_plan, dict) else None
        dtype = runtime_plan.get("dtype") if isinstance(runtime_plan, dict) else None
        manager_kwargs = {"resolved_device": resolved_device, "dtype": dtype}
        if resolved_device == "cuda:0" and self.execution_gate is not None:
            manager_kwargs["execution_gate"] = self.execution_gate
        manager = ModelManager(**manager_kwargs)
        _validate_snapshot_paths(spec, manager)
        output_root = Path(spec["output"]["directory"])
        workflow_staging = output_root / ".staging" / workflow_id
        staging_dir = workflow_staging / attempt_id
        payload = {
            "job_id": workflow_id,
            "source_path": spec["source"]["path"],
            "output_dir": str(staging_dir),
            "output_file_name": "transcript.md",
            "job_workspace_dir": str(output_root / ".jobs" / workflow_id),
            "asr_backend": "local",
            "language_mode": spec["transcription"].get("language", {}).get("mode", "auto"),
            "fixed_language": spec["transcription"].get("language", {}).get("value"),
            "enable_speaker_diarization": True,
            "force_external_diarization": True,
            "local_asr_model": "qwen3_asr_1_7b",
            "context_text": _context_text(spec),
            "attempt_id": attempt_id,
            "terms": spec["transcription"].get("prompt_input", {}).get("hotwords", []),
            "replacements": spec["transcription"].get("postprocess", {}).get("replacements", []),
            "keep_fillers": spec["transcription"].get("postprocess", {}).get("keep_fillers", True),
            "auto_punctuation": spec["transcription"].get("postprocess", {}).get("auto_punctuation", True),
        }

        def emit(job_id: str, update: dict[str, Any]) -> None:
            del job_id
            if progress is None:
                return
            progress({
                "phase": _phase_name(str(update.get("stage", "transcribing"))),
                "detail": _phase_detail(str(update.get("stage", "transcribing"))),
                **update,
            })

        uses_cuda = manager.device_map().startswith("cuda")
        effective_gate = self.execution_gate if uses_cuda else None
        if effective_gate is not None:
            # The task manager owns only Pyannote. Attach the shared gate only
            # after resolving the actual device so forced CPU never observes
            # or waits on a CUDA gate.
            manager.execution_gate = effective_gate
        try:
            if progress:
                device_label = "GPU" if uses_cuda else "CPU"
                progress({"phase": f"{device_label.lower()}_waiting", "detail": f"正在等待本地 {device_label} 推理通道"})
            # CPU remains task-local.  CUDA segments enter the shared
            # dispatcher, which performs fair cross-workflow micro-batching.
            executor = self.batch_executor if uses_cuda else None
            _LOCAL_INFERENCE_LANE.acquire()
            result = run_job(
                payload,
                emit=emit,
                model_manager=manager,
                batch_executor=executor,
                attempt_id=attempt_id,
                execution_gate=effective_gate,
            )
            path = Path(result["md_path"])
            return {
                "kind": "transcript_markdown",
                "text": path.read_text(encoding="utf-8"),
                "warnings": result.get("warnings", []),
                "diagnostics": {
                    "backend_id": self.backend_id,
                    "asr_model": result.get("asr_model"),
                    "segment_count": result.get("segments", 0),
                    "speaker_count": result.get("speakers", 0),
                    "warnings": result.get("warnings", []),
                },
            }
        finally:
            _LOCAL_INFERENCE_LANE.release()
            # This task manager owns only Pyannote in the CUDA path; Qwen is
            # closed exactly once by GpuBatchDispatcher.close().
            manager.close_local_models()

    def close(self) -> None:
        with self._lifecycle:
            if self._dispatcher_closed:
                return None
            self._closing = True
            active_attempts = list(self._active_attempts)

        # A sync call may be waiting on a dispatcher Future. Mark those
        # attempts stale before waiting for their thread wrappers to exit;
        # otherwise waiting for the dispatcher first would deadlock shutdown.
        for workflow_id, attempt_id in active_attempts:
            key = (workflow_id, attempt_id)
            with self._lifecycle:
                already_cancelled = key in self._cancelled_attempts
            if not already_cancelled:
                self.cancel_attempt(workflow_id, attempt_id)

        close_dispatcher = False
        with self._lifecycle:
            deadline = time.monotonic() + self.close_wait_seconds
            while self._active_sync_calls:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._deferred_dispatcher_close = True
                    return None
                self._lifecycle.wait(timeout=remaining)
            if not self._dispatcher_close_started:
                self._dispatcher_close_started = True
                close_dispatcher = True
        if close_dispatcher:
            self._close_dispatcher_once()
        return None

    def _close_dispatcher_once(self) -> None:
        close = getattr(self.batch_executor, "close", None)
        try:
            if callable(close):
                close()
        finally:
            with self._lifecycle:
                self._dispatcher_closed = True
                self._closed = True
                self._deferred_dispatcher_close = False
                self._lifecycle.notify_all()

    def cancel_attempt(self, workflow_id: str, attempt_id: str) -> int:
        key = (str(workflow_id), str(attempt_id))
        with self._lifecycle:
            self._cancelled_attempts.add(key)
        cancel = getattr(self.batch_executor, "cancel_attempt", None)
        if not callable(cancel):
            cancel = getattr(self.batch_executor, "cancel", None)
        if not callable(cancel):
            return 0
        return int(cancel(*key) or 0)


def _context_text(spec: dict[str, Any]) -> str:
    prompt = spec["transcription"].get("prompt_input", {})
    values = []
    if prompt.get("recording_background"):
        values.append(str(prompt["recording_background"]))
    if prompt.get("extra_instruction"):
        values.append(str(prompt["extra_instruction"]))
    return "\n\n".join(values)


def _phase_name(stage: str) -> str:
    return {
        "preparing": "preparing",
        "decoding": "audio_normalizing",
        "diarizing": "diarizing",
        "segmenting": "segmenting",
        "transcribing": "transcribing",
        "merging": "merging",
        "normalizing": "normalizing",
        "exporting": "exporting",
    }.get(stage, stage)


def _phase_detail(stage: str) -> str:
    return {
        "preparing": "正在准备任务",
        "decoding": "正在标准化音频",
        "diarizing": "正在执行 Pyannote 说话人分析",
        "segmenting": "正在生成安全转录分块",
        "transcribing": "正在按分块执行语音识别",
        "merging": "正在合并转录结果",
        "normalizing": "正在整理文本",
        "exporting": "正在写入转录产物",
    }.get(stage, "正在处理任务")


def _validate_snapshot_paths(spec: dict[str, Any], manager: ModelManager) -> None:
    components = spec["transcription"].get("model_snapshot", {}).get("components", [])
    expected = {item.get("role"): Path(item.get("resolved_path", "")).resolve() for item in components}
    actual = {
        "transcriber": manager.qwen_path.resolve(),
        "diarization": manager.pyannote_path.resolve(),
    }
    for role, path in actual.items():
        if role in expected and expected[role] != path:
            raise RuntimeError(f"MODEL_SNAPSHOT_MISMATCH: {role} path changed after workflow submission")
