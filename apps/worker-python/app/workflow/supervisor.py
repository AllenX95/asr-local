from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import inspect
import json
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol
import uuid

from app.ipc.v2.canonical import canonical_operation_digest
from app.ipc.v2.codec import normalize_workflow_draft

from .registry import OperationConflictError, WorkflowNotFoundError, WorkflowRegistry
from .runtime_plan import HardwareSnapshot, profile_hardware, resolve_runtime_plan
from .model_snapshot import resolve_model_components
from .state_machine import create_initial_snapshot, mark_interrupted, retry_snapshot


class Transcriber(Protocol):
    async def transcribe(self, spec: dict[str, Any], attempt_id: str, *, progress: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]: ...


class SummaryGenerator(Protocol):
    async def summarize(self, spec: dict[str, Any], transcript: dict[str, Any], attempt_id: str) -> dict[str, Any]: ...


EventSink = Callable[[dict[str, Any]], Awaitable[None] | None]


@dataclass(slots=True)
class FakeTranscriber:
    delay_seconds: float = 0.0
    active: int = 0
    peak_active: int = 0

    async def transcribe(self, spec: dict[str, Any], attempt_id: str, *, progress=None) -> dict[str, Any]:
        del progress
        del spec, attempt_id
        self.active += 1
        self.peak_active = max(self.peak_active, self.active)
        try:
            if self.delay_seconds:
                await asyncio.sleep(self.delay_seconds)
            return {"kind": "transcript_markdown", "path": "fake/transcript.md", "text": "fake transcript"}
        finally:
            self.active -= 1


@dataclass(slots=True)
class FakeSummaryGenerator:
    delay_seconds: float = 0.0

    async def summarize(self, spec: dict[str, Any], transcript: dict[str, Any], attempt_id: str) -> dict[str, Any]:
        del spec, transcript, attempt_id
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        return {"kind": "final_summary_markdown", "path": "fake/final-summary.md", "text": "fake summary"}


class WorkflowSupervisor:
    """In-process workflow supervisor behind the versioned stdio adapter."""

    def __init__(
        self,
        registry: WorkflowRegistry,
        *,
        transcriber: Transcriber | None = None,
        summary_generator: SummaryGenerator | None = None,
        max_inflight: int = 3,
        id_factory: Callable[[str], str] | None = None,
        clock: Callable[[], str] | None = None,
        event_sink: EventSink | None = None,
        hardware: HardwareSnapshot | None = None,
        heartbeat_interval_seconds: float = 10.0,
    ) -> None:
        if max_inflight < 1:
            raise ValueError("max_inflight must be positive")
        self.registry = registry
        self.transcriber = transcriber or FakeTranscriber()
        self.summary_generator = summary_generator or FakeSummaryGenerator()
        self.max_inflight = max_inflight
        self.id_factory = id_factory or (lambda prefix: f"{prefix}_{uuid.uuid4().hex[:12]}")
        self.clock = clock or _now
        self.event_sink = event_sink
        self.hardware = hardware or profile_hardware()
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self._queue: asyncio.Queue[tuple[str, str | None]] = asyncio.Queue()
        self._workers: list[asyncio.Task[None]] = []
        self._started = False
        self._stopping = False
        self._event_lock = asyncio.Lock()
        self._control_events: dict[str, asyncio.Event] = {}
        self._enqueued_workflows: set[str] = set()
        self._mutation_locks: dict[str, asyncio.Lock] = {}

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._stopping = False
        await self.recover_on_startup()
        self._workers = [asyncio.create_task(self._worker_loop(), name=f"workflow-worker-{index}") for index in range(self.max_inflight)]

    async def shutdown(self, *, interrupt: bool = True) -> None:
        if interrupt:
            await self.recover_on_startup()
        elif self._started:
            await self._queue.join()
        self._stopping = True
        for worker in self._workers:
            worker.cancel()
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers = []
        while True:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            else:
                self._queue.task_done()
        self._enqueued_workflows.clear()
        self._started = False

    async def submit(self, draft: dict[str, Any], *, operation_id: str) -> dict[str, Any]:
        await self.start()
        normalized = normalize_workflow_draft(draft)
        _validate_source_path(normalized["source"]["path"])
        digest = canonical_operation_digest("workflow.submit", {"draft": normalized})
        existing = self.registry.operation_result(operation_id, "workflow.submit", digest)
        if existing is not None:
            return {**existing, "deduplicated": True}

        workflow_id = self.id_factory("wf")
        attempt_id = self.id_factory("att")
        spec = build_spec(normalized, workflow_id=workflow_id)
        snapshot = create_initial_snapshot(workflow_id, attempt_id, spec, created_at=self.clock())
        event = self._event(snapshot, "submitted", operation_id=operation_id)
        result, deduplicated = self.registry.create_workflow(
            operation_id=operation_id,
            method="workflow.submit",
            payload_digest=digest,
            workflow_id=workflow_id,
            attempt_id=attempt_id,
            snapshot=snapshot,
            event=event,
            now=self.clock(),
        )
        if not deduplicated:
            self._control_events[workflow_id] = asyncio.Event()
            self._control_events[workflow_id].set()
            await self._publish(event)
            await self._enqueue(workflow_id, None)
        return result

    async def list(self, statuses: set[str] | None = None) -> list[dict[str, Any]]:
        return self.registry.list_snapshots(statuses)

    async def get(self, workflow_id: str) -> dict[str, Any]:
        return self.registry.get_snapshot(workflow_id)

    async def clear(self, params: dict[str, Any], *, operation_id: str) -> dict[str, Any]:
        digest = canonical_operation_digest("workflow.clear", params)
        existing = self.registry.operation_result(operation_id, "workflow.clear", digest)
        if existing is not None:
            return {**existing, "deduplicated": True}
        snapshot = self.registry.get_snapshot(params["workflow_id"])
        if snapshot["status"] not in {"completed", "failed", "cancelled", "interrupted"}:
            raise ValueError("WORKFLOW_NOT_TERMINAL")
        result = {"cleared": True, "workflow_id": params["workflow_id"]}
        self.registry.save_operation_result(
            operation_id=operation_id,
            method="workflow.clear",
            payload_digest=digest,
            result=result,
            now=self.clock(),
        )
        self.registry.delete_workflow(params["workflow_id"])
        self._control_events.pop(params["workflow_id"], None)
        return result

    async def control(self, params: dict[str, Any], *, operation_id: str) -> dict[str, Any]:
        await self.start()
        digest = canonical_operation_digest("workflow.control", params)
        existing = self.registry.operation_result(operation_id, "workflow.control", digest)
        if existing is not None:
            return {**existing, "deduplicated": True}
        async with self._mutation_lock(params["workflow_id"]):
            snapshot = self.registry.get_snapshot(params["workflow_id"])
            if snapshot["attempt"]["attempt_id"] != params["expected_attempt_id"]:
                raise ValueError("STALE_ATTEMPT")
            next_snapshot = _apply_control(snapshot, params["action"], self.clock())
            event = self._event(next_snapshot, params["action"], operation_id=operation_id)
            self.registry.save_snapshot(next_snapshot, event)
        result = {"accepted": True, "snapshot": next_snapshot}
        self.registry.save_operation_result(operation_id=operation_id, method="workflow.control", payload_digest=digest, result=result, now=self.clock())
        control_event = self._control_events.setdefault(params["workflow_id"], asyncio.Event())
        if params["action"] == "pause":
            control_event.clear()
        else:
            control_event.set()
        await self._publish(event)
        return result

    async def retry(self, params: dict[str, Any], *, operation_id: str) -> dict[str, Any]:
        await self.start()
        digest = canonical_operation_digest("workflow.retry", params)
        existing = self.registry.operation_result(operation_id, "workflow.retry", digest)
        if existing is not None:
            return {**existing, "deduplicated": True}
        current = self.registry.get_snapshot(params["workflow_id"])
        decision = retry_snapshot(
            current,
            expected_attempt_id=params["expected_attempt_id"],
            expected_sequence=params["expected_sequence"],
            from_stage=params["from_stage"],
            input_artifact_id=params.get("input_artifact_id"),
            new_attempt_id=self.id_factory("att"),
            updated_at=self.clock(),
        )
        event = self._event(decision.snapshot, "retry_started", operation_id=operation_id, data={"from_stage": decision.from_stage})
        self.registry.save_snapshot(decision.snapshot, event)
        result = {"accepted": True, "snapshot": decision.snapshot, "from_stage": decision.from_stage}
        self.registry.save_operation_result(operation_id=operation_id, method="workflow.retry", payload_digest=digest, result=result, now=self.clock())
        await self._publish(event)
        control_event = self._control_events.setdefault(decision.snapshot["workflow_id"], asyncio.Event())
        control_event.set()
        await self._enqueue(decision.snapshot["workflow_id"], decision.from_stage)
        return result

    async def register_revision(self, params: dict[str, Any], *, operation_id: str) -> dict[str, Any]:
        await self.start()
        digest = canonical_operation_digest("artifact.register_revision", params)
        existing = self.registry.operation_result(operation_id, "artifact.register_revision", digest)
        if existing is not None:
            return {**existing, "deduplicated": True}
        current = self.registry.get_snapshot(params["workflow_id"])
        if current["attempt"]["attempt_id"] != params["expected_attempt_id"]:
            raise ValueError("STALE_ATTEMPT")
        if current["sequence"] != params["expected_sequence"]:
            raise ValueError("SEQUENCE_CONFLICT")
        source = next((item for item in current.get("artifacts", []) if item.get("artifact_id") == params["source_artifact_id"]), None)
        if source is None or source.get("kind") != params["kind"]:
            raise ValueError("INVALID_REQUEST")
        next_revision = max(
            (int(item.get("revision", 0)) for item in current.get("artifacts", []) if item.get("kind") == params["kind"]),
            default=0,
        ) + 1
        promoted_path = _validate_and_promote_staged_artifact(
            current,
            staged_path=params["staged_path"],
            expected_size=params["size_bytes"],
            expected_sha256=params["sha256"],
            kind=params["kind"],
            revision=next_revision,
        )
        revised = json.loads(json.dumps(current))
        now = self.clock()
        artifact = {
            "artifact_id": self.id_factory("artifact"),
            "kind": params["kind"],
            "revision": next_revision,
            "origin": "user_edited",
            "derived_from_artifact_id": source["artifact_id"],
            "input_artifact_ids": list(source.get("input_artifact_ids", [])),
            "stale": False,
            "path": str(promoted_path),
            "size_bytes": params["size_bytes"],
            "sha256": params["sha256"],
            "created_at": now,
        }
        if params["kind"] == "transcript_markdown":
            for existing_artifact in revised["artifacts"]:
                if existing_artifact.get("kind") in {"summary_checkpoint_json", "final_summary_markdown", "final_summary_json"} and source["artifact_id"] in existing_artifact.get("input_artifact_ids", []):
                    existing_artifact["stale"] = True
        revised["artifacts"].append(artifact)
        revised["sequence"] += 1
        revised["timestamps"]["updated_at"] = now
        event = self._event(revised, "artifact_ready", data={"origin": "user_edited", "derived_from_artifact_id": source["artifact_id"]}, operation_id=operation_id)
        self.registry.save_snapshot(revised, event)
        result = {"artifact": artifact, "snapshot": revised}
        self.registry.save_operation_result(operation_id=operation_id, method="artifact.register_revision", payload_digest=digest, result=result, now=now)
        await self._publish(event)
        return result

    async def recover_on_startup(self) -> None:
        for snapshot in self.registry.list_snapshots({"queued"}):
            self._control_events.setdefault(snapshot["workflow_id"], asyncio.Event()).set()
            await self._enqueue(snapshot["workflow_id"], snapshot.get("recovery", {}).get("recommended_retry_stage"))
        for snapshot in self.registry.active_snapshots():
            if snapshot.get("status") == "queued":
                continue
            recommended = infer_recovery_stage(snapshot)
            interrupted = mark_interrupted(snapshot, recommended_retry_stage=recommended, updated_at=self.clock())
            event = self._event(interrupted, "interrupted", data={"automatic_retry": False})
            self.registry.save_snapshot(interrupted, event)
            await self._publish(event)

    async def _enqueue(self, workflow_id: str, retry_stage: str | None) -> None:
        if workflow_id in self._enqueued_workflows:
            return
        self._enqueued_workflows.add(workflow_id)
        await self._queue.put((workflow_id, retry_stage))

    async def mark_waiting_for_secret(self, request_data: dict[str, Any]) -> None:
        """Persist a credentials_required checkpoint without exposing the secret."""
        snapshot = self.registry.get_snapshot(str(request_data["workflow_id"]))
        if snapshot["attempt"]["attempt_id"] != request_data["attempt_id"]:
            raise ValueError("STALE_ATTEMPT")
        waiting = json.loads(json.dumps(snapshot))
        waiting["status"] = "waiting_for_secret"
        waiting["stage"] = "summarizing" if request_data["purpose"] == "summary_api" else "transcribing"
        waiting["sequence"] += 1
        waiting["timestamps"]["updated_at"] = self.clock()
        event = self._event(waiting, "credentials_required", data=request_data)
        self.registry.save_snapshot(waiting, event)
        await self._publish(event)

    async def mark_secret_granted(self, workflow_id: str, attempt_id: str) -> None:
        snapshot = self.registry.get_snapshot(workflow_id)
        if snapshot["attempt"]["attempt_id"] != attempt_id or snapshot["status"] != "waiting_for_secret":
            return
        resumed = _transition(snapshot, status="running", stage=snapshot.get("stage") or "summarizing", clock=self.clock())
        event = self._event(resumed, "state_changed", data={"secret_granted": True})
        self.registry.save_snapshot(resumed, event)
        await self._publish(event)

    async def _worker_loop(self) -> None:
        while not self._stopping:
            workflow_id, retry_stage = await self._queue.get()
            try:
                snapshot = self.registry.get_snapshot(workflow_id)
                if snapshot["status"] != "queued":
                    continue
                await self._run_workflow(snapshot, retry_stage)
            except asyncio.CancelledError:
                raise
            except WorkflowNotFoundError:
                continue
            except Exception as exc:
                snapshot = self.registry.get_snapshot(workflow_id)
                failed = _failed_snapshot(snapshot, exc, self.clock())
                event = self._event(failed, "failed", data={"error": str(exc)})
                self.registry.save_snapshot(failed, event)
                await self._publish(event)
            finally:
                self._enqueued_workflows.discard(workflow_id)
                try:
                    final_status = self.registry.get_snapshot(workflow_id).get("status")
                    if final_status in {"completed", "failed", "cancelled", "interrupted"}:
                        self._control_events.pop(workflow_id, None)
                except WorkflowNotFoundError:
                    self._control_events.pop(workflow_id, None)
                self._queue.task_done()

    async def _run_workflow(self, snapshot: dict[str, Any], retry_stage: str | None) -> None:
        running = _transition(snapshot, status="running", stage="preparing", clock=self.clock())
        running["progress"] = {**running.get("progress", {}), "stage_ratio": 0.15, "overall_ratio": 0.03, "queue_position": None, "detail": "正在校验输入并准备运行环境"}
        event = self._event(running, "attempt_started")
        self.registry.save_snapshot(running, event)
        await self._publish(event)

        device_policy = running["spec"]["transcription"].get("device_policy", "auto")
        plan = resolve_runtime_plan(device_policy, self.hardware, workflow_capacity=self.max_inflight)
        running["runtime_plan"] = plan.as_dict()
        running["sequence"] += 1
        running["timestamps"]["updated_at"] = self.clock()
        event = self._event(running, "runtime_plan_resolved")
        self.registry.save_snapshot(running, event)
        await self._publish(event)

        execution_spec = {
            **running["spec"],
            "workflow_id": running["workflow_id"],
            "runtime_plan": running["runtime_plan"],
        }
        _ensure_source_unchanged(execution_spec["source"])
        selected_transcript_id = running.get("recovery", {}).get("input_artifact_id")
        if retry_stage in {"summarizing", "writing_final"}:
            transcript_artifact = _select_transcript_artifact(running, selected_transcript_id)
            if transcript_artifact is None:
                raise ValueError("TRANSCRIPT_CHECKPOINT_MISSING: requested summary retry has no readable transcript artifact")
            transcript = _reuse_artifact_snapshot(running, transcript_artifact, clock=self.clock())
            event = self._event(transcript, "artifact_reused", data={"artifact_id": transcript_artifact["artifact_id"]})
            self.registry.save_snapshot(transcript, event)
            await self._publish(event)
        else:
            running["attempt"]["stage_attempts"]["transcription"] += 1
            transcribing = _transition(running, status="running", stage="transcribing", clock=self.clock())
            transcribing["progress"] = {**transcribing.get("progress", {}), "stage_ratio": 0.05, "overall_ratio": 0.08, "queue_position": None, "detail": "正在加载音频并执行语音识别与说话人分析"}
            event = self._event(transcribing, "progress")
            self.registry.save_snapshot(transcribing, event)
            await self._publish(event)
            running = transcribing
            attempt_id = running["attempt"]["attempt_id"]
            loop = asyncio.get_running_loop()
            latest_progress: dict[str, Any] = {"phase": "starting_transcription", "detail": "正在启动转录"}
            progress_tasks: set[asyncio.Task[None]] = set()

            def report_progress(update: dict[str, Any]) -> None:
                latest_progress.update(update)
                def schedule() -> None:
                    task = asyncio.create_task(self._record_transcription_progress(running["workflow_id"], attempt_id, dict(latest_progress), heartbeat=False))
                    progress_tasks.add(task)
                    task.add_done_callback(progress_tasks.discard)
                loop.call_soon_threadsafe(schedule)

            heartbeat_task = asyncio.create_task(
                self._transcription_heartbeat(running["workflow_id"], attempt_id, latest_progress),
                name=f"transcription-heartbeat-{running['workflow_id']}",
            )
            try:
                parameters = inspect.signature(self.transcriber.transcribe).parameters
                if "progress" in parameters:
                    transcript_result = await self.transcriber.transcribe(execution_spec, attempt_id, progress=report_progress)
                else:
                    transcript_result = await self.transcriber.transcribe(execution_spec, attempt_id)
            finally:
                heartbeat_task.cancel()
                await asyncio.gather(heartbeat_task, return_exceptions=True)
                if progress_tasks:
                    await asyncio.gather(*progress_tasks, return_exceptions=True)
            # Blocking model calls cannot always be interrupted safely. Honor
            # pending control before publishing their result so cancellation
            # never creates a late transcript artifact.
            if not await self._wait_for_control(running["workflow_id"]):
                return
            # Secret waits and user controls can advance the persisted snapshot
            # while the provider/model call is in flight. Rebase before writing
            # the artifact so the registry sequence is never moved backwards.
            transcript_base = self.registry.get_snapshot(running["workflow_id"])
            transcript = _add_artifact(transcript_base, transcript_result, kind="transcript_markdown", clock=self.clock())
            transcript["stage"] = "transcript_ready"
            transcript["progress"] = {**transcript.get("progress", {}), "stage_ratio": 1.0, "overall_ratio": 0.68, "detail": "转录完成，正在准备总结输入"}
            transcript["sequence"] += 1
            transcript["timestamps"]["updated_at"] = self.clock()
            event = self._event(transcript, "artifact_ready")
            self.registry.save_snapshot(transcript, event)
            await self._publish(event)

        transcript_artifact = _select_transcript_artifact(transcript, selected_transcript_id)
        if transcript_artifact is None:
            raise ValueError("TRANSCRIPT_CHECKPOINT_MISSING: workflow has no readable transcript artifact")

        if not await self._wait_for_control(snapshot["workflow_id"]):
            return

        summary_base = self.registry.get_snapshot(running["workflow_id"])
        if retry_stage == "writing_final":
            checkpoint = _select_checkpoint_artifact(summary_base)
            if checkpoint is None:
                raise ValueError("SUMMARY_CHECKPOINT_MISSING: final-write retry has no summary checkpoint")
            summary = _transition(summary_base, status="running", stage="writing_final", clock=self.clock())
            summary["attempt"]["stage_attempts"]["writing_final"] += 1
        else:
            summary = _transition(summary_base, status="running", stage="summarizing", clock=self.clock())
            summary["attempt"]["stage_attempts"]["summary"] += 1
        summary["progress"] = {**summary.get("progress", {}), "stage_ratio": 0.1, "overall_ratio": 0.72 if retry_stage != "writing_final" else 0.93, "detail": "正在调用总结模型" if retry_stage != "writing_final" else "正在写入最终文件"}
        event = self._event(summary, "progress")
        self.registry.save_snapshot(summary, event)
        await self._publish(event)
        if retry_stage == "writing_final":
            summary_result = _read_summary_checkpoint(checkpoint)
        else:
            summary_result = await self.summary_generator.summarize(execution_spec, transcript_artifact, summary["attempt"]["attempt_id"])
        if not await self._wait_for_control(snapshot["workflow_id"]):
            return

        # The summary provider may also have crossed a credentials checkpoint;
        # always use the latest persisted state before publishing checkpoints.
        summary = self.registry.get_snapshot(running["workflow_id"])
        if retry_stage != "writing_final":
            checkpoint_result = _summary_checkpoint_result(summary_result)
            checkpoint_snapshot = _add_artifact(
                summary,
                checkpoint_result,
                kind="summary_checkpoint_json",
                clock=self.clock(),
                input_artifact_ids=[transcript_artifact["artifact_id"]],
            )
            checkpoint_snapshot["stage"] = "writing_final"
            checkpoint_snapshot["progress"] = {**checkpoint_snapshot.get("progress", {}), "stage_ratio": 0.4, "overall_ratio": 0.93, "detail": "总结已生成，正在写入最终文件"}
            checkpoint_snapshot["sequence"] += 1
            checkpoint_snapshot["timestamps"]["updated_at"] = self.clock()
            event = self._event(checkpoint_snapshot, "artifact_ready", data={"kind": "summary_checkpoint_json"})
            self.registry.save_snapshot(checkpoint_snapshot, event)
            await self._publish(event)
            summary = checkpoint_snapshot
            checkpoint = _select_checkpoint_artifact(summary)
        else:
            checkpoint = _select_checkpoint_artifact(summary)

        if not await self._wait_for_control(snapshot["workflow_id"]):
            return
        final_result = {"kind": "final_summary_markdown", "text": str(summary_result.get("text", ""))}
        input_ids = [transcript_artifact["artifact_id"]]
        if checkpoint is not None:
            input_ids.append(checkpoint["artifact_id"])
        completed = _add_artifact(
            summary,
            final_result,
            kind="final_summary_markdown",
            clock=self.clock(),
            input_artifact_ids=input_ids,
        )
        completed["status"] = "completed"
        completed["stage"] = "completed"
        completed["sequence"] += 1
        completed["progress"] = {**completed.get("progress", {}), "stage_ratio": 1.0, "overall_ratio": 1.0, "queue_position": None, "detail": "任务已完成，所有产物均已写入"}
        completed["timestamps"]["updated_at"] = self.clock()
        completed["timestamps"]["completed_at"] = completed["timestamps"]["updated_at"]
        event = self._event(completed, "completed")
        self.registry.save_snapshot(completed, event)
        await self._publish(event)

    async def _wait_for_control(self, workflow_id: str) -> bool:
        """Honor pause/cancel at stage-safe boundaries.

        Model/provider calls are not forcibly killed mid-request. A pause or
        cancel is therefore observed immediately after the current stage and
        before the next artifact/state transition.
        """
        control_event = self._control_events.setdefault(workflow_id, asyncio.Event())
        while True:
            current = self.registry.get_snapshot(workflow_id)
            if current["status"] == "cancelled" and current.get("control", {}).get("pending_action") is None:
                return False
            if current.get("control", {}).get("pending_action") == "cancel":
                cancelled = _cancelled_snapshot(current, self.clock())
                event = self._event(cancelled, "cancelled")
                self.registry.save_snapshot(cancelled, event)
                await self._publish(event)
                control_event.set()
                return False
            if current["status"] == "paused":
                await control_event.wait()
                continue
            return True

    def _event(self, snapshot: dict[str, Any], event_type: str, *, operation_id: str | None = None, data: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "workflow_id": snapshot["workflow_id"],
            "attempt_id": snapshot["attempt"]["attempt_id"],
            "sequence": snapshot["sequence"],
            "occurred_at": snapshot["timestamps"]["updated_at"],
            "caused_by_operation_id": operation_id,
            "type": event_type,
            "stage": snapshot["stage"],
            "data": data or {},
            "state": snapshot,
        }

    async def _publish(self, event: dict[str, Any]) -> None:
        if self.event_sink is None:
            return
        async with self._event_lock:
            result = self.event_sink(event)
            if asyncio.iscoroutine(result):
                await result

    def _mutation_lock(self, workflow_id: str) -> asyncio.Lock:
        return self._mutation_locks.setdefault(workflow_id, asyncio.Lock())

    async def _transcription_heartbeat(self, workflow_id: str, attempt_id: str, latest: dict[str, Any]) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_interval_seconds)
            await self._record_transcription_progress(workflow_id, attempt_id, dict(latest), heartbeat=True)

    async def _record_transcription_progress(self, workflow_id: str, attempt_id: str, update: dict[str, Any], *, heartbeat: bool) -> None:
        async with self._mutation_lock(workflow_id):
            current = self.registry.get_snapshot(workflow_id)
            if current["attempt"]["attempt_id"] != attempt_id or current["status"] != "running" or current["stage"] != "transcribing":
                return
            now = self.clock()
            next_snapshot = json.loads(json.dumps(current))
            previous_phase = next_snapshot["progress"].get("phase")
            phase = str(update.get("phase") or previous_phase or "transcribing")
            next_snapshot["progress"]["phase"] = phase
            next_snapshot["progress"]["detail"] = str(update.get("detail") or next_snapshot["progress"].get("detail") or phase)
            if previous_phase != phase:
                next_snapshot["progress"]["phase_started_at"] = now
            next_snapshot["progress"]["heartbeat_at"] = now
            next_snapshot["sequence"] += 1
            next_snapshot["timestamps"]["updated_at"] = now
            event = self._event(next_snapshot, "heartbeat" if heartbeat else "phase_progress", data={"phase": phase, "heartbeat": heartbeat})
            self.registry.save_snapshot(next_snapshot, event)
        await self._publish(event)


def build_spec(draft: dict[str, Any], *, workflow_id: str) -> dict[str, Any]:
    source_path = Path(draft["source"]["path"]).expanduser().resolve()
    stat = source_path.stat() if source_path.exists() else None
    source_sha256 = _sha256_file(source_path) if stat and source_path.is_file() else None
    transcription = json.loads(json.dumps(draft["transcription"]))
    prompt_input = transcription["prompt_input"]
    compiled_text = _compile_prompt(prompt_input, transcription["pipeline_profile"])
    transcription["prompt_snapshot"] = {
        "compiler_id": "moss-prompt" if transcription["pipeline_profile"] in {"moss_transcribe_diarize", "pyannote_moss_asr"} else "legacy-prompt",
        "compiler_version": 1,
        "base_template_version": "openmoss-official-2026-07-09" if transcription["pipeline_profile"] in {"moss_transcribe_diarize", "pyannote_moss_asr"} else "legacy-v1",
        "compiled_text": compiled_text,
        "sha256": hashlib.sha256(compiled_text.encode("utf-8")).hexdigest(),
    }
    transcription["model_snapshot"] = {"components": resolve_model_components(transcription["pipeline_profile"])}
    summary = json.loads(json.dumps(draft["summary"]))
    template_text = summary["template"]["prompt_snapshot"]
    summary["template"]["sha256"] = hashlib.sha256(template_text.encode("utf-8")).hexdigest()
    output = json.loads(json.dumps(draft["output"]))
    output_root = Path(output["directory"]).expanduser().resolve()
    base_output = output_root / output["base_name"]
    if output["collision_policy"] == "reject":
        if base_output.exists():
            raise ValueError(f"OUTPUT_CONFLICT: output already exists: {base_output}")
        output["directory"] = str(base_output)
    else:
        output["directory"] = str(output_root / f"{output['base_name']}--{workflow_id}")
    return {
        "spec_version": 2,
        "display_name": draft["display_name"],
        "source": {"path": str(source_path), "fingerprint": {"size_bytes": stat.st_size if stat else 0, "modified_ms": int(stat.st_mtime_ns / 1_000_000) if stat else 0, "sha256": source_sha256}},
        "transcription": transcription,
        "summary": summary,
        "output": output,
    }


def infer_recovery_stage(snapshot: dict[str, Any]) -> str:
    artifacts = snapshot.get("artifacts", [])
    if any(item.get("kind") == "summary_checkpoint_json" and not item.get("stale") for item in artifacts):
        return "writing_final"
    if any(item.get("kind") in {"transcript_markdown", "transcript_json"} and not item.get("stale") for item in artifacts):
        return "summarizing"
    return "transcribing"


def _transition(snapshot: dict[str, Any], *, status: str, stage: str, clock: str) -> dict[str, Any]:
    result = json.loads(json.dumps(snapshot))
    result["sequence"] += 1
    result["status"] = status
    result["stage"] = stage
    result["timestamps"]["updated_at"] = clock
    if stage == "preparing":
        result["timestamps"]["started_at"] = clock
    return result


def _failed_snapshot(snapshot: dict[str, Any], error: Exception, clock: str) -> dict[str, Any]:
    result = json.loads(json.dumps(snapshot))
    result["sequence"] += 1
    result["status"] = "failed"
    code = str(getattr(error, "code", "INTERNAL"))
    result["last_error"] = {"code": code, "message": str(error), "retryable": code not in {"CREDENTIAL_REJECTED", "SUMMARY_RESULT_UNKNOWN"}, "field_errors": [], "details": {}, "diagnostic_id": f"diag_{uuid.uuid4().hex[:12]}"}
    result["recovery"]["recommended_retry_stage"] = infer_recovery_stage(result)
    result["timestamps"]["updated_at"] = clock
    return result


def _cancelled_snapshot(snapshot: dict[str, Any], clock: str) -> dict[str, Any]:
    result = json.loads(json.dumps(snapshot))
    if result.get("status") == "cancelled" and result.get("control", {}).get("pending_action") is None:
        return result
    result["sequence"] += 1
    result["status"] = "cancelled"
    result["control"]["pending_action"] = None
    result["timestamps"]["updated_at"] = clock
    return result


def _select_transcript_artifact(snapshot: dict[str, Any], artifact_id: str | None) -> dict[str, Any] | None:
    candidates = [
        item
        for item in snapshot.get("artifacts", [])
        if item.get("kind") in {"transcript_markdown", "transcript_json"} and not item.get("stale")
    ]
    if artifact_id is not None:
        candidates = [item for item in candidates if item.get("artifact_id") == artifact_id]
    candidates.sort(key=lambda item: (int(item.get("revision", 0)), str(item.get("created_at", ""))), reverse=True)
    if not candidates:
        return None
    selected = candidates[0]
    path = Path(str(selected.get("path", "")))
    if not path.is_file():
        return None
    result = dict(selected)
    result["text"] = path.read_text(encoding="utf-8")
    return result


def _select_checkpoint_artifact(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    candidates = [
        item
        for item in snapshot.get("artifacts", [])
        if item.get("kind") == "summary_checkpoint_json" and not item.get("stale")
    ]
    candidates.sort(key=lambda item: (int(item.get("revision", 0)), str(item.get("created_at", ""))), reverse=True)
    return candidates[0] if candidates else None


def _read_summary_checkpoint(artifact: dict[str, Any]) -> dict[str, Any]:
    path = Path(str(artifact.get("path", "")))
    if not path.is_file():
        raise ValueError("SUMMARY_CHECKPOINT_MISSING: checkpoint file is not readable")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("text"), str):
        raise ValueError("SUMMARY_CHECKPOINT_INVALID: checkpoint does not contain summary text")
    return {"kind": "final_summary_markdown", "text": payload["text"]}


def _summary_checkpoint_result(result: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "text": str(result.get("text", "")),
        "strategy": result.get("strategy"),
        "provider_request_keys": list(result.get("provider_request_keys", [])),
    }
    return {"kind": "summary_checkpoint_json", "text": json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"}


def _reuse_artifact_snapshot(snapshot: dict[str, Any], artifact: dict[str, Any], *, clock: str) -> dict[str, Any]:
    result = json.loads(json.dumps(snapshot))
    result["status"] = "running"
    result["stage"] = "transcript_ready"
    result["sequence"] += 1
    result["timestamps"]["updated_at"] = clock
    return result


def _add_artifact(
    snapshot: dict[str, Any],
    result: dict[str, Any],
    *,
    kind: str,
    clock: str,
    input_artifact_ids: list[str] | None = None,
) -> dict[str, Any]:
    next_snapshot = json.loads(json.dumps(snapshot))
    revision = max((int(item.get("revision", 0)) for item in next_snapshot.get("artifacts", []) if item.get("kind") == kind), default=0) + 1
    path, text = _materialize_artifact(next_snapshot, result, kind=kind, revision=revision)
    if kind in {"transcript_markdown", "transcript_json"}:
        for existing in next_snapshot.get("artifacts", []):
            if existing.get("kind") in {"summary_checkpoint_json", "final_summary_markdown", "final_summary_json"}:
                existing["stale"] = True
    if kind in {"summary_checkpoint_json", "final_summary_markdown"} and input_artifact_ids:
        for existing in next_snapshot.get("artifacts", []):
            if existing.get("kind") != kind:
                continue
            if set(input_artifact_ids).intersection(existing.get("input_artifact_ids", [])):
                existing["stale"] = True
    artifact = {
        "artifact_id": f"artifact_{uuid.uuid4().hex[:12]}",
        "kind": kind,
        "revision": revision,
        "origin": "generated",
        "derived_from_artifact_id": None,
        "input_artifact_ids": list(input_artifact_ids or []),
        "stale": False,
        "path": path,
        "size_bytes": len(text.encode("utf-8")),
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "created_at": clock,
    }
    next_snapshot["artifacts"].append(artifact)
    return next_snapshot


def _materialize_artifact(snapshot: dict[str, Any], result: dict[str, Any], *, kind: str, revision: int) -> tuple[str, str]:
    text = str(result.get("text", ""))
    existing_path = Path(str(result.get("path", ""))) if result.get("path") else None
    registered_paths = {str(item.get("path")) for item in snapshot.get("artifacts", [])}
    if existing_path and existing_path.is_file() and str(existing_path) not in registered_paths:
        try:
            text = existing_path.read_text(encoding="utf-8")
        except OSError:
            pass
        return str(existing_path), text
    output_dir = Path(snapshot["spec"]["output"]["directory"])
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = _artifact_filename(kind, revision)
    output_path = output_dir / filename
    temp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    temp_path.write_text(text, encoding="utf-8")
    temp_path.replace(output_path)
    return str(output_path), text


def _artifact_filename(kind: str, revision: int) -> str:
    stem_by_kind = {
        "transcript_markdown": "transcript",
        "transcript_json": "transcript",
        "summary_checkpoint_json": "summary-checkpoint",
        "final_summary_markdown": "final-summary",
        "final_summary_json": "final-summary",
    }
    suffix = "json" if kind.endswith("_json") else "md"
    stem = stem_by_kind.get(kind, kind.replace("_", "-"))
    return f"{stem}.{suffix}" if revision == 1 else f"{stem}-r{revision}.{suffix}"


def _ensure_source_unchanged(source: dict[str, Any]) -> None:
    path = Path(source["path"])
    fingerprint = source.get("fingerprint", {})
    if not path.is_file():
        raise ValueError(f"SOURCE_CHANGED: source is no longer readable: {path}")
    stat = path.stat()
    if int(fingerprint.get("size_bytes", -1)) != stat.st_size or int(fingerprint.get("modified_ms", -1)) != int(stat.st_mtime_ns / 1_000_000):
        raise ValueError(f"SOURCE_CHANGED: source fingerprint changed: {path}")
    expected_sha = fingerprint.get("sha256")
    if expected_sha and _sha256_file(path) != expected_sha:
        raise ValueError(f"SOURCE_CHANGED: source digest changed: {path}")


def _validate_and_promote_staged_artifact(
    snapshot: dict[str, Any],
    *,
    staged_path: str,
    expected_size: int,
    expected_sha256: str,
    kind: str,
    revision: int,
) -> Path:
    output_root = Path(snapshot["spec"]["output"]["directory"]).expanduser().resolve()
    staging_root = (output_root / ".staging").resolve()
    candidate = Path(staged_path).expanduser().resolve()
    try:
        candidate.relative_to(staging_root)
    except ValueError as exc:
        raise ValueError("INVALID_REQUEST: staged artifact is outside the workflow staging directory") from exc
    if not candidate.is_file():
        raise ValueError("INVALID_REQUEST: staged artifact does not exist")
    if candidate.stat().st_size != expected_size:
        raise ValueError("INVALID_REQUEST: staged artifact size does not match")
    if _sha256_file(candidate) != expected_sha256:
        raise ValueError("INVALID_REQUEST: staged artifact digest does not match")
    output_root.mkdir(parents=True, exist_ok=True)
    promoted = output_root / f"{kind}-r{revision}.md"
    candidate.replace(promoted)
    return promoted


def _apply_control(snapshot: dict[str, Any], action: str, clock: str) -> dict[str, Any]:
    result = json.loads(json.dumps(snapshot))
    if action == "cancel" and result["status"] == "queued":
        result["status"] = "cancelled"
        result["control"]["pending_action"] = None
    elif action == "cancel" and result["status"] in {"running", "paused"}:
        result["control"]["pending_action"] = "cancel"
        if result["status"] == "paused":
            result["status"] = "cancelled"
            result["control"]["pending_action"] = None
    elif action == "pause" and result["status"] == "running":
        result["status"] = "paused"
        result["control"]["pending_action"] = None
    elif action == "resume" and result["status"] == "paused":
        result["status"] = "running"
        result["control"]["pending_action"] = None
    else:
        raise ValueError("CONTROL_NOT_SUPPORTED")
    result["sequence"] += 1
    result["timestamps"]["updated_at"] = clock
    return result


def _compile_prompt(prompt_input: dict[str, Any], pipeline_profile: str) -> str:
    if pipeline_profile in {"moss_transcribe_diarize", "pyannote_moss_asr"}:
        base = "请将音频转写为文本，保留清晰的时间范围和自然段落。"
    else:
        base = "请准确转写音频内容，保留原意、专有名词和自然段落，不要添加音频中不存在的信息。"
    parts = [base]
    if prompt_input.get("recording_background"):
        parts.append(f"录音背景：\n{prompt_input['recording_background']}")
    if prompt_input.get("hotwords"):
        parts.append("热词提示：" + "、".join(str(word) for word in prompt_input["hotwords"]))
    if prompt_input.get("extra_instruction"):
        parts.append(prompt_input["extra_instruction"])
    return "\n\n".join(parts)


def _validate_source_path(raw_path: str) -> Path:
    path = Path(raw_path).expanduser().resolve()
    if not path.exists() or not path.is_file():
        raise ValueError(f"SOURCE_NOT_FOUND: audio source does not exist: {path}")
    try:
        with path.open("rb") as handle:
            handle.read(1)
    except OSError as exc:
        raise ValueError(f"SOURCE_UNREADABLE: cannot read audio source: {path}") from exc
    return path


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
