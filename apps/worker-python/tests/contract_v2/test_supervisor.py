from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from app.summary.openai_compatible import OpenAICompatibleSummaryGenerator
from app.workflow.registry import WorkflowRegistry
from app.workflow.state_machine import create_initial_snapshot
from app.workflow.supervisor import (
    FakeSummaryGenerator,
    FakeTranscriber,
    WorkflowSupervisor,
    _apply_control,
    _read_summary_artifact,
    _summary_checkpoint_result,
    build_spec,
)


class SelectiveFailTranscriber:
    async def transcribe(self, spec: dict, attempt_id: str) -> dict:
        del attempt_id
        if spec["display_name"] == "bad":
            raise RuntimeError("MODEL_LOAD_FAILED: injected")
        return {"kind": "transcript_markdown", "path": "", "text": "good transcript"}


class BlockingTranscriber:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def transcribe(self, spec: dict, attempt_id: str) -> dict:
        del spec, attempt_id
        self.started.set()
        await self.release.wait()
        return {"kind": "transcript_markdown", "path": "", "text": "controlled transcript"}


class ProgressBlockingTranscriber(BlockingTranscriber):
    async def transcribe(self, spec: dict, attempt_id: str, *, progress=None) -> dict:
        del spec, attempt_id
        if progress:
            progress({"phase": "model_loading", "detail": "正在加载 Qwen3-ASR 模型"})
        self.started.set()
        await self.release.wait()
        return {"kind": "transcript_markdown", "path": "", "text": "controlled transcript"}


class CountingSummaryGenerator:
    def __init__(self) -> None:
        self.calls = 0

    async def summarize(self, spec: dict, transcript: dict, attempt_id: str) -> dict:
        del spec, transcript, attempt_id
        self.calls += 1
        return {"kind": "final_summary_markdown", "text": f"summary-{self.calls}"}


class ProvenanceSummaryGenerator:
    def __init__(self) -> None:
        self.calls = 0

    async def summarize(self, spec: dict, transcript: dict, attempt_id: str) -> dict:
        del spec, transcript, attempt_id
        self.calls += 1
        return {
            "kind": "final_summary_markdown",
            "text": "summary-with-provenance",
            "strategy": "single_pass",
            "provider_request_keys": ["request-key"],
            "deterministic_repairs": [
                {
                    "transformer_version": "summary-output-deterministic-repair-v1",
                    "repair_types": [
                        "forbidden_financing_completion_summary",
                        "missing_first_mention_reference_attribution",
                        "missing_named_conflict_rows",
                        "missing_explicit_unsettled_financing_state",
                        "unsupported_derived_company_age",
                        "forbidden_third_round_settlement_progress",
                        "untraceable_conflict_row",
                    ],
                    "repair_counts": {
                        "forbidden_financing_completion_summary": 1,
                        "missing_first_mention_reference_attribution": 2,
                        "missing_named_conflict_rows": 3,
                        "missing_explicit_unsettled_financing_state": 1,
                        "unsupported_derived_company_age": 1,
                        "forbidden_third_round_settlement_progress": 1,
                        "untraceable_conflict_row": 1,
                    },
                    "before_sha256": "a" * 64,
                    "after_sha256": "b" * 64,
                }
            ],
        }


class CountingTranscriber:
    def __init__(self) -> None:
        self.calls = 0

    async def transcribe(self, spec: dict, attempt_id: str) -> dict:
        del spec, attempt_id
        self.calls += 1
        return {"kind": "transcript_markdown", "path": "", "text": "stable transcript"}


class NoCallSummaryGenerator:
    def __init__(self) -> None:
        self.calls = 0

    async def summarize(self, spec: dict, transcript: dict, attempt_id: str) -> dict:
        del spec, transcript, attempt_id
        self.calls += 1
        raise AssertionError("writing_final retry must reuse the summary checkpoint")


def make_draft(source: Path, name: str = "sample") -> dict:
    return {
        "draft_version": 2,
        "display_name": name,
        "source": {"path": str(source)},
        "transcription": {
            "pipeline_profile": "pyannote_qwen3_asr",
            "pipeline_profile_version": 1,
            "device_policy": "auto",
            "language": {"mode": "auto", "value": None},
            "prompt_input": {"recording_background": "meeting", "hotwords": ["Qwen"], "extra_instruction": ""},
            "postprocess": {"replacements": [], "keep_fillers": True, "auto_punctuation": True},
            "cloud_profile": None,
        },
        "summary": {
            "profile_id": "summary-profile-uuid",
            "profile_version": 1,
            "base_url": "https://example.com/v1",
            "auth_mode": "none",
            "model": "summary-model",
            "model_source": "profile_default",
            "credential_ref": None,
            "provider_binding_sha256": "hex-digest",
            "policy_snapshot": {"id": "asr-primary-reference-advisory", "version": 1},
            "template": {"id": "template-uuid", "version": 1, "name": "default", "prompt_snapshot": "Summarize."},
            "context_strategy": "auto",
            "input_token_budget": 1000,
            "max_output_tokens": 100,
        },
        "output": {"directory": "outputs", "base_name": name, "collision_policy": "unique_suffix"},
    }


class SupervisorTests(unittest.TestCase):
    def test_materialized_artifact_metadata_matches_exact_utf8_bytes(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                source = root / "source.wav"
                source.write_bytes(b"audio")
                draft = make_draft(source, "utf8-bytes")
                draft["output"]["directory"] = str(root / "outputs")
                registry = WorkflowRegistry(root / "registry.sqlite3")
                supervisor = WorkflowSupervisor(registry, transcriber=FakeTranscriber(), summary_generator=FakeSummaryGenerator())
                submitted = await supervisor.submit(draft, operation_id="op_utf8_bytes")
                await supervisor._queue.join()
                completed = await supervisor.get(submitted["snapshot"]["workflow_id"])
                for artifact in completed["artifacts"]:
                    path = Path(artifact["path"])
                    raw = path.read_bytes()
                    self.assertEqual(artifact["size_bytes"], len(raw))
                    self.assertEqual(artifact["sha256"], hashlib.sha256(raw).hexdigest())
                await supervisor.shutdown(interrupt=False)
                registry.close()

        asyncio.run(scenario())

    def test_summary_checkpoint_survives_cleanup_registry_reopen_and_writing_final_retry(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                source = root / "source.wav"
                source.write_bytes(b"audio")
                draft = make_draft(source, "checkpoint-reopen")
                draft["output"]["directory"] = str(root / "outputs")
                registry_path = root / "registry.sqlite3"
                registry = WorkflowRegistry(registry_path)
                initial_summary = ProvenanceSummaryGenerator()
                supervisor = WorkflowSupervisor(registry, transcriber=FakeTranscriber(), summary_generator=initial_summary)
                submitted = await supervisor.submit(draft, operation_id="op_checkpoint_reopen")
                workflow_id = submitted["snapshot"]["workflow_id"]
                await supervisor._queue.join()
                completed = await supervisor.get(workflow_id)
                checkpoint = next(item for item in completed["artifacts"] if item["kind"] == "summary_checkpoint_json")
                checkpoint_path = Path(checkpoint["path"])
                self.assertEqual(checkpoint_path.parent.parent.name, ".checkpoints")
                self.assertTrue(checkpoint_path.is_file())
                original_checkpoint_payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
                self.assertEqual(original_checkpoint_payload["text"], "summary-with-provenance")
                self.assertEqual(len(original_checkpoint_payload["deterministic_repairs"]), 1)
                self.assertEqual(
                    set(original_checkpoint_payload["deterministic_repairs"][0]),
                    {
                        "transformer_version",
                        "repair_types",
                        "repair_counts",
                        "before_sha256",
                        "after_sha256",
                    },
                )
                self.assertNotIn("summary-with-provenance", json.dumps(original_checkpoint_payload["deterministic_repairs"], ensure_ascii=False))
                original_final = next(item for item in completed["artifacts"] if item["kind"] == "final_summary_markdown" and not item.get("stale"))
                original_final_bytes = Path(original_final["path"]).read_bytes()
                original_final_sha256 = hashlib.sha256(original_final_bytes).hexdigest()
                await supervisor.shutdown(interrupt=False)
                registry.close()

                reopened = WorkflowRegistry(registry_path)
                restored = reopened.get_snapshot(workflow_id)
                self.assertEqual(restored["spec"]["summary"]["policy_snapshot"], {"id": "asr-primary-reference-advisory", "version": 1})
                restored_checkpoint = next(item for item in restored["artifacts"] if item["kind"] == "summary_checkpoint_json")
                restored_checkpoint_path = Path(restored_checkpoint["path"])
                self.assertTrue(restored_checkpoint_path.is_file())
                self.assertEqual(json.loads(restored_checkpoint_path.read_text(encoding="utf-8"))["deterministic_repairs"], original_checkpoint_payload["deterministic_repairs"])
                final = next(item for item in restored["artifacts"] if item["kind"] == "final_summary_markdown" and not item.get("stale"))
                Path(final["path"]).unlink()
                no_call = NoCallSummaryGenerator()
                retry_supervisor = WorkflowSupervisor(reopened, transcriber=FakeTranscriber(), summary_generator=no_call)
                retried = await retry_supervisor.retry(
                    {
                        "workflow_id": workflow_id,
                        "expected_attempt_id": restored["attempt"]["attempt_id"],
                        "expected_sequence": restored["sequence"],
                        "from_stage": "writing_final",
                    },
                    operation_id="op_checkpoint_write_retry",
                )
                await retry_supervisor._queue.join()
                after_retry = await retry_supervisor.get(workflow_id)
                self.assertEqual(after_retry["status"], "completed")
                self.assertEqual(after_retry["spec"]["summary"]["policy_snapshot"], {"id": "asr-primary-reference-advisory", "version": 1})
                self.assertEqual(no_call.calls, 0)
                retried_final = next(item for item in after_retry["artifacts"] if item["kind"] == "final_summary_markdown" and not item.get("stale"))
                self.assertTrue(Path(retried_final["path"]).is_file())
                self.assertEqual(Path(retried_final["path"]).read_bytes(), original_final_bytes)
                self.assertEqual(retried_final["sha256"], original_final_sha256)
                after_retry_checkpoint = next(item for item in after_retry["artifacts"] if item["kind"] == "summary_checkpoint_json" and not item.get("stale"))
                self.assertEqual(
                    json.loads(Path(after_retry_checkpoint["path"]).read_text(encoding="utf-8"))["deterministic_repairs"],
                    original_checkpoint_payload["deterministic_repairs"],
                )
                await retry_supervisor.shutdown(interrupt=False)
                reopened.close()

        asyncio.run(scenario())

    def test_legacy_summary_checkpoint_without_repair_metadata_reads_as_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "legacy-summary.json"
            path.write_text(
                json.dumps(
                    {
                        "text": "legacy summary",
                        "strategy": "single_pass",
                        "provider_request_keys": ["legacy-key"],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            restored = _read_summary_artifact({"kind": "summary_checkpoint_json", "path": str(path)})
            self.assertEqual(restored["text"], "legacy summary")
            self.assertEqual(restored["deterministic_repairs"], [])

            checkpoint = _summary_checkpoint_result({"text": "legacy summary"})
            payload = json.loads(checkpoint["text"])
            self.assertEqual(payload["text"], "legacy summary")
            self.assertEqual(payload["deterministic_repairs"], [])

    def test_waiting_for_secret_can_be_cancelled(self) -> None:
        snapshot = {
            "status": "waiting_for_secret",
            "control": {"pending_action": None},
            "sequence": 10,
            "timestamps": {"updated_at": "before"},
        }
        cancelled = _apply_control(snapshot, "cancel", "after")
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertIsNone(cancelled["control"]["pending_action"])
        self.assertEqual(cancelled["sequence"], 11)

    def test_transcription_heartbeat_persists_truthful_progress(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                source = root / "source.wav"
                source.write_bytes(b"audio")
                registry = WorkflowRegistry(root / "registry.sqlite3")
                transcriber = ProgressBlockingTranscriber()
                supervisor = WorkflowSupervisor(
                    registry,
                    transcriber=transcriber,
                    summary_generator=FakeSummaryGenerator(),
                    heartbeat_interval_seconds=0.01,
                )
                submitted = await supervisor.submit(make_draft(source), operation_id="op_heartbeat")
                workflow_id = submitted["snapshot"]["workflow_id"]
                await transcriber.started.wait()
                await asyncio.sleep(0.035)
                snapshot = registry.get_snapshot(workflow_id)
                self.assertEqual(snapshot["stage"], "transcribing")
                self.assertEqual(snapshot["progress"]["overall_ratio"], 0.08)
                self.assertEqual(snapshot["progress"]["phase"], "model_loading")
                self.assertIn("heartbeat_at", snapshot["progress"])
                self.assertGreater(snapshot["sequence"], 4)
                transcriber.release.set()
                await supervisor._queue.join()
                self.assertEqual(registry.get_snapshot(workflow_id)["status"], "completed")
                await supervisor.shutdown(interrupt=False)
                registry.close()

        asyncio.run(scenario())

    def test_three_inflight_and_fourth_backlog(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                source = root / "source.wav"
                source.write_bytes(b"audio")
                registry = WorkflowRegistry(root / "registry.sqlite3")
                transcriber = FakeTranscriber(delay_seconds=0.02)
                supervisor = WorkflowSupervisor(
                    registry,
                    transcriber=transcriber,
                    summary_generator=FakeSummaryGenerator(delay_seconds=0.01),
                    max_inflight=3,
                    id_factory=(lambda prefix, counter=iter(range(1, 100)): f"{prefix}_{next(counter)}"),
                    clock=lambda: "2026-07-10T12:00:00Z",
                )
                results = await asyncio.gather(*[
                    supervisor.submit(make_draft(source, f"sample-{index}"), operation_id=f"op_{index}")
                    for index in range(4)
                ])
                await supervisor._queue.join()
                snapshots = await supervisor.list()
                self.assertEqual(len(results), 4)
                self.assertEqual(len(snapshots), 4)
                self.assertTrue(all(item["status"] == "completed" for item in snapshots))
                self.assertLessEqual(transcriber.peak_active, 3)
                await supervisor.shutdown(interrupt=False)
                registry.close()

        asyncio.run(scenario())

    def test_submit_operation_is_idempotent(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                source = root / "source.wav"
                source.write_bytes(b"audio")
                registry = WorkflowRegistry(root / "registry.sqlite3")
                supervisor = WorkflowSupervisor(registry, clock=lambda: "2026-07-10T12:00:00Z")
                first = await supervisor.submit(make_draft(source), operation_id="op_same")
                second = await supervisor.submit(make_draft(source), operation_id="op_same")
                self.assertEqual(first["snapshot"]["workflow_id"], second["snapshot"]["workflow_id"])
                self.assertTrue(second["deduplicated"])
                await supervisor.shutdown(interrupt=False)
                registry.close()

        asyncio.run(scenario())

    def test_startup_marks_running_attempt_interrupted_without_new_attempt(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                registry = WorkflowRegistry(root / "registry.sqlite3")
                supervisor = WorkflowSupervisor(registry, clock=lambda: "2026-07-10T12:00:00Z")
                spec = build_spec(make_draft(root / "missing.wav"), workflow_id="wf_existing")
                snapshot = create_initial_snapshot("wf_existing", "att_existing", spec, created_at="2026-07-10T11:00:00Z")
                snapshot["sequence"] = 2
                snapshot["status"] = "running"
                snapshot["stage"] = "transcribing"
                event = supervisor._event(snapshot, "attempt_started")
                registry.create_workflow(operation_id="op_existing", method="workflow.submit", payload_digest="digest", workflow_id="wf_existing", attempt_id="att_existing", snapshot={**snapshot, "sequence": 1, "status": "queued", "stage": "queued"}, event={**event, "sequence": 1, "stage": "queued", "state": {**event["state"], "sequence": 1, "status": "queued", "stage": "queued"}}, now="2026-07-10T11:00:00Z")
                registry.save_snapshot(snapshot, event)
                await supervisor.start()
                recovered = registry.get_snapshot("wf_existing")
                self.assertEqual(recovered["status"], "interrupted")
                self.assertEqual(recovered["attempt"]["attempt_id"], "att_existing")
                self.assertEqual(recovered["recovery"]["recommended_retry_stage"], "transcribing")
                await supervisor.shutdown(interrupt=False)
                registry.close()

        asyncio.run(scenario())

    def test_startup_requeues_existing_queued_attempt_without_new_attempt(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                source = root / "source.wav"
                source.write_bytes(b"audio")
                registry = WorkflowRegistry(root / "registry.sqlite3")
                spec = build_spec(make_draft(source, "queued-existing"), workflow_id="wf_queued_existing")
                snapshot = create_initial_snapshot("wf_queued_existing", "att_queued_existing", spec, created_at="2026-07-10T11:00:00Z")
                event = {
                    "workflow_id": snapshot["workflow_id"],
                    "attempt_id": snapshot["attempt"]["attempt_id"],
                    "sequence": snapshot["sequence"],
                    "occurred_at": snapshot["timestamps"]["updated_at"],
                    "caused_by_operation_id": None,
                    "type": "submitted",
                    "stage": "queued",
                    "data": {},
                    "state": snapshot,
                }
                registry.create_workflow(
                    operation_id="op_queued_existing",
                    method="workflow.submit",
                    payload_digest="queued-digest",
                    workflow_id=snapshot["workflow_id"],
                    attempt_id=snapshot["attempt"]["attempt_id"],
                    snapshot=snapshot,
                    event=event,
                    now=snapshot["timestamps"]["created_at"],
                )
                supervisor = WorkflowSupervisor(registry, transcriber=FakeTranscriber(), summary_generator=FakeSummaryGenerator())
                await supervisor.start()
                await supervisor._queue.join()
                recovered = await supervisor.get(snapshot["workflow_id"])
                self.assertEqual(recovered["status"], "completed")
                self.assertEqual(recovered["attempt"]["attempt_id"], "att_queued_existing")
                await supervisor.shutdown(interrupt=False)
                registry.close()

        asyncio.run(scenario())

    def test_transcript_revision_marks_existing_summary_stale(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                source = root / "source.wav"
                source.write_bytes(b"audio")
                registry = WorkflowRegistry(root / "registry.sqlite3")
                supervisor = WorkflowSupervisor(registry, clock=lambda: "2026-07-10T12:00:00Z")
                submitted = await supervisor.submit(make_draft(source), operation_id="op_revision_workflow")
                await supervisor._queue.join()
                current = await supervisor.get(submitted["snapshot"]["workflow_id"])
                transcript = next(item for item in current["artifacts"] if item["kind"] == "transcript_markdown")
                staging = Path(current["spec"]["output"]["directory"]) / ".staging"
                staging.mkdir(parents=True, exist_ok=True)
                staged = staging / "edit.md"
                staged.write_text("edited transcript", encoding="utf-8")
                import hashlib
                staged_digest = hashlib.sha256(staged.read_bytes()).hexdigest()
                result = await supervisor.register_revision(
                    {
                        "workflow_id": current["workflow_id"],
                        "expected_attempt_id": current["attempt"]["attempt_id"],
                        "expected_sequence": current["sequence"],
                        "source_artifact_id": transcript["artifact_id"],
                        "kind": "transcript_markdown",
                        "staged_path": str(staged),
                        "size_bytes": staged.stat().st_size,
                        "sha256": staged_digest,
                    },
                    operation_id="op_revision_001",
                )
                self.assertEqual(result["artifact"]["origin"], "user_edited")
                final_summary = next(item for item in result["snapshot"]["artifacts"] if item["kind"] == "final_summary_markdown")
                self.assertTrue(final_summary["stale"])
                await supervisor.shutdown(interrupt=False)
                registry.close()

        asyncio.run(scenario())

    def test_generated_artifacts_are_materialized_and_source_is_fingerprinted(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                source = root / "source.wav"
                source.write_bytes(b"audio")
                registry = WorkflowRegistry(root / "registry.sqlite3")
                supervisor = WorkflowSupervisor(
                    registry,
                    transcriber=FakeTranscriber(),
                    summary_generator=FakeSummaryGenerator(),
                    clock=lambda: "2026-07-10T12:00:00Z",
                )
                submitted = await supervisor.submit(make_draft(source), operation_id="op_materialize")
                spec = submitted["snapshot"]["spec"]
                self.assertEqual(spec["source"]["fingerprint"]["sha256"], "6ed8919ce20490a5e3ad8630a4fab69475297abd07db73918dd5f36fcfaeb11b")
                await supervisor._queue.join()
                current = await supervisor.get(submitted["snapshot"]["workflow_id"])
                paths = {item["kind"]: Path(item["path"]) for item in current["artifacts"]}
                self.assertTrue(paths["transcript_markdown"].is_file())
                self.assertTrue(paths["final_summary_markdown"].is_file())
                self.assertEqual(paths["transcript_markdown"].parent.name, "transcripts")
                self.assertEqual(paths["final_summary_markdown"].parent.name, "summary")
                self.assertFalse((Path(spec["output"]["directory"]) / ".staging" / current["workflow_id"]).exists())
                self.assertEqual(paths["final_summary_markdown"].read_text(encoding="utf-8"), "fake summary")
                await supervisor.shutdown(interrupt=False)
                registry.close()

        asyncio.run(scenario())

    def test_failure_is_isolated_and_drain_waits_for_active_workflows(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                source = root / "source.wav"
                source.write_bytes(b"audio")
                registry = WorkflowRegistry(root / "registry.sqlite3")
                supervisor = WorkflowSupervisor(
                    registry,
                    transcriber=SelectiveFailTranscriber(),
                    summary_generator=FakeSummaryGenerator(delay_seconds=0.02),
                    max_inflight=2,
                )
                bad = await supervisor.submit(make_draft(source, "bad"), operation_id="op_bad")
                good = await supervisor.submit(make_draft(source, "good"), operation_id="op_good")
                await supervisor.shutdown(interrupt=False)
                self.assertEqual((await supervisor.get(bad["snapshot"]["workflow_id"]))["status"], "failed")
                self.assertEqual((await supervisor.get(good["snapshot"]["workflow_id"]))["status"], "completed")
                registry.close()

        asyncio.run(scenario())

    def test_running_pause_resume_and_cancel_are_honored_at_stage_boundaries(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                source = root / "source.wav"
                source.write_bytes(b"audio")
                registry = WorkflowRegistry(root / "registry.sqlite3")
                transcriber = BlockingTranscriber()
                supervisor = WorkflowSupervisor(
                    registry,
                    transcriber=transcriber,
                    summary_generator=FakeSummaryGenerator(),
                    max_inflight=1,
                )
                submitted = await supervisor.submit(make_draft(source, "controlled"), operation_id="op_controlled")
                workflow_id = submitted["snapshot"]["workflow_id"]
                await asyncio.wait_for(transcriber.started.wait(), timeout=1)
                running = await supervisor.get(workflow_id)
                paused = await supervisor.control(
                    {"workflow_id": workflow_id, "expected_attempt_id": running["attempt"]["attempt_id"], "action": "pause"},
                    operation_id="op_pause",
                )
                self.assertEqual(paused["snapshot"]["status"], "paused")
                transcriber.release.set()
                await asyncio.sleep(0.02)
                self.assertEqual((await supervisor.get(workflow_id))["status"], "paused")
                resumed = await supervisor.control(
                    {"workflow_id": workflow_id, "expected_attempt_id": paused["snapshot"]["attempt"]["attempt_id"], "action": "resume"},
                    operation_id="op_resume",
                )
                self.assertEqual(resumed["snapshot"]["status"], "running")
                await supervisor._queue.join()
                self.assertEqual((await supervisor.get(workflow_id))["status"], "completed")

                cancel_transcriber = BlockingTranscriber()
                cancel_supervisor = WorkflowSupervisor(
                    WorkflowRegistry(root / "cancel-registry.sqlite3"),
                    transcriber=cancel_transcriber,
                    summary_generator=FakeSummaryGenerator(),
                    max_inflight=1,
                )
                cancelled = await cancel_supervisor.submit(make_draft(source, "cancelled"), operation_id="op_cancelled")
                cancel_id = cancelled["snapshot"]["workflow_id"]
                await asyncio.wait_for(cancel_transcriber.started.wait(), timeout=1)
                current = await cancel_supervisor.get(cancel_id)
                await cancel_supervisor.control(
                    {"workflow_id": cancel_id, "expected_attempt_id": current["attempt"]["attempt_id"], "action": "cancel"},
                    operation_id="op_cancel",
                )
                cancel_transcriber.release.set()
                await cancel_supervisor._queue.join()
                cancel_snapshot = await cancel_supervisor.get(cancel_id)
                self.assertEqual(cancel_snapshot["status"], "cancelled")
                self.assertFalse(any(item["kind"] == "transcript_markdown" for item in cancel_snapshot["artifacts"]))
                self.assertFalse(any(item["kind"] == "final_summary_markdown" for item in cancel_snapshot["artifacts"]))
                await supervisor.shutdown(interrupt=False)
                await cancel_supervisor.shutdown(interrupt=False)
                registry.close()
                cancel_supervisor.registry.close()

        asyncio.run(scenario())

    def test_summary_retry_reuses_transcript_and_writing_retry_reuses_checkpoint(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                source = root / "source.wav"
                source.write_bytes(b"audio")
                registry = WorkflowRegistry(root / "registry.sqlite3")
                summary_generator = CountingSummaryGenerator()
                supervisor = WorkflowSupervisor(
                    registry,
                    transcriber=FakeTranscriber(),
                    summary_generator=summary_generator,
                    max_inflight=1,
                )
                retry_draft = make_draft(source, "retryable")
                retry_content = "retry notes"
                retry_draft["summary"]["reference_document"] = {
                    "name": "retry.md",
                    "content": retry_content,
                    "size_bytes": len(retry_content.encode("utf-8")),
                    "sha256": hashlib.sha256(retry_content.encode("utf-8")).hexdigest(),
                }
                submitted = await supervisor.submit(retry_draft, operation_id="op_retryable")
                await supervisor._queue.join()
                first = await supervisor.get(submitted["snapshot"]["workflow_id"])
                first_transcript = next(item for item in first["artifacts"] if item["kind"] == "transcript_markdown")
                first_final = next(item for item in first["artifacts"] if item["kind"] == "final_summary_markdown")
                retry_result = await supervisor.retry(
                    {
                        "workflow_id": first["workflow_id"],
                        "expected_attempt_id": first["attempt"]["attempt_id"],
                        "expected_sequence": first["sequence"],
                        "from_stage": "summarizing",
                        "input_artifact_id": first_transcript["artifact_id"],
                    },
                    operation_id="op_retry_summary",
                )
                await supervisor._queue.join()
                second = await supervisor.get(first["workflow_id"])
                self.assertEqual(second["spec"]["summary"]["reference_document"], first["spec"]["summary"]["reference_document"])
                second_final = max((item for item in second["artifacts"] if item["kind"] == "final_summary_markdown"), key=lambda item: item["revision"])
                self.assertEqual(summary_generator.calls, 2)
                self.assertEqual(second_final["revision"], 2)
                self.assertTrue(next(item for item in second["artifacts"] if item["artifact_id"] == first_final["artifact_id"])["stale"])
                self.assertEqual(
                    [item["artifact_id"] for item in second["artifacts"] if item["kind"] == "transcript_markdown"],
                    [first_transcript["artifact_id"]],
                )
                final_retry = await supervisor.retry(
                    {
                        "workflow_id": second["workflow_id"],
                        "expected_attempt_id": second["attempt"]["attempt_id"],
                        "expected_sequence": second["sequence"],
                        "from_stage": "writing_final",
                        "input_artifact_id": None,
                    },
                    operation_id="op_retry_writing",
                )
                await supervisor._queue.join()
                third = await supervisor.get(first["workflow_id"])
                self.assertEqual(third["status"], "completed")
                self.assertEqual(summary_generator.calls, 2)
                self.assertEqual(max(item["revision"] for item in third["artifacts"] if item["kind"] == "final_summary_markdown"), 3)
                await supervisor.shutdown(interrupt=False)
                registry.close()

        asyncio.run(scenario())

    def test_reopened_historical_v8_summary_retry_without_policy_uses_compatibility_resolver(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                source = root / "source.wav"
                source.write_bytes(b"audio")
                calls: list[dict] = []

                def request(_url, payload, _headers):
                    calls.append(payload)
                    return "historical summary"

                draft = make_draft(source, "historical-v8")
                draft["output"]["directory"] = str(root / "outputs")
                draft["summary"]["template"] = {
                    "id": "summary-template-first-meeting",
                    "version": 8,
                    "name": "首次交流模板",
                    "prompt_snapshot": "首次交流历史模板。",
                }
                registry_path = root / "registry.sqlite3"
                registry = WorkflowRegistry(registry_path)
                supervisor = WorkflowSupervisor(
                    registry,
                    transcriber=FakeTranscriber(),
                    summary_generator=OpenAICompatibleSummaryGenerator(request_fn=request),
                    max_inflight=1,
                )
                submitted = await supervisor.submit(draft, operation_id="op_historical_v8_submit")
                await supervisor._queue.join()
                completed = await supervisor.get(submitted["snapshot"]["workflow_id"])
                transcript = next(item for item in completed["artifacts"] if item["kind"] == "transcript_markdown")
                await supervisor.shutdown(interrupt=False)

                historical = json.loads(json.dumps(completed))
                historical["spec"]["summary"].pop("policy_snapshot")
                historical["sequence"] += 1
                historical["timestamps"]["updated_at"] = "2026-07-10T12:00:01Z"
                registry.save_snapshot(historical, supervisor._event(historical, "legacy_snapshot_restored"))
                registry.close()

                calls.clear()
                reopened = WorkflowRegistry(registry_path)
                reopened_supervisor = WorkflowSupervisor(
                    reopened,
                    transcriber=FakeTranscriber(),
                    summary_generator=OpenAICompatibleSummaryGenerator(request_fn=request),
                    max_inflight=1,
                )
                persisted = await reopened_supervisor.get(historical["workflow_id"])
                self.assertNotIn("policy_snapshot", persisted["spec"]["summary"])
                retry = await reopened_supervisor.retry(
                    {
                        "workflow_id": persisted["workflow_id"],
                        "expected_attempt_id": persisted["attempt"]["attempt_id"],
                        "expected_sequence": persisted["sequence"],
                        "from_stage": "summarizing",
                        "input_artifact_id": transcript["artifact_id"],
                    },
                    operation_id="op_historical_v8_retry",
                )
                await reopened_supervisor._queue.join()
                final = await reopened_supervisor.get(persisted["workflow_id"])
                self.assertTrue(retry["accepted"])
                self.assertEqual(final["status"], "completed")
                self.assertNotIn("policy_snapshot", final["spec"]["summary"])
                self.assertEqual(len(calls), 1)
                self.assertIn("参考速记可能不完整或有误", calls[0]["messages"][0]["content"])
                self.assertNotIn("<summary_output_audit>", calls[0]["messages"][1]["content"])
                await reopened_supervisor.shutdown(interrupt=False)
                reopened.close()

        asyncio.run(scenario())

    def test_reject_collision_policy_is_checked_before_queueing(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                source = root / "source.wav"
                source.write_bytes(b"audio")
                output = root / "out"
                (output / "sample").mkdir(parents=True)
                draft = make_draft(source)
                draft["output"] = {"directory": str(output), "base_name": "sample", "collision_policy": "reject"}
                registry = WorkflowRegistry(root / "registry.sqlite3")
                supervisor = WorkflowSupervisor(registry)
                with self.assertRaisesRegex(ValueError, "OUTPUT_CONFLICT"):
                    await supervisor.submit(draft, operation_id="op_collision")
                self.assertEqual(await supervisor.list(), [])
                await supervisor.shutdown(interrupt=False)
                registry.close()

        asyncio.run(scenario())

    def test_resummarize_creates_summary_only_workflow_from_transcript(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                source = root / "source.wav"
                source.write_bytes(b"audio")
                registry = WorkflowRegistry(root / "registry.sqlite3")
                transcriber = CountingTranscriber()
                summary_generator = CountingSummaryGenerator()
                supervisor = WorkflowSupervisor(
                    registry,
                    transcriber=transcriber,
                    summary_generator=summary_generator,
                    max_inflight=1,
                )
                draft = make_draft(source, "resummary")
                draft["output"]["directory"] = str(root / "outputs")
                reference_content = "# 速记\n项目代号 Orion。"
                draft["summary"]["reference_document"] = {
                    "name": "notes.md",
                    "content": reference_content,
                    "size_bytes": len(reference_content.encode("utf-8")),
                    "sha256": hashlib.sha256(reference_content.encode("utf-8")).hexdigest(),
                }
                submitted = await supervisor.submit(draft, operation_id="op_resummary_source")
                await supervisor._queue.join()
                original = await supervisor.get(submitted["snapshot"]["workflow_id"])
                self.assertEqual(original["spec"]["summary"]["policy_snapshot"], {"id": "asr-primary-reference-advisory", "version": 1})
                original_transcript = next(item for item in original["artifacts"] if item["kind"] == "transcript_markdown")
                original_final = next(item for item in original["artifacts"] if item["kind"] == "final_summary_markdown")
                source.unlink()

                changed_summary = {**draft["summary"], "model": "summary-model-v2"}
                changed_summary["template"] = {
                    "id": "template-v2",
                    "version": 1,
                    "name": "action-items",
                    "prompt_snapshot": "Extract action items.",
                }
                result = await supervisor.resummarize(
                    {
                        "source_workflow_id": original["workflow_id"],
                        "expected_attempt_id": original["attempt"]["attempt_id"],
                        "expected_sequence": original["sequence"],
                        "input_artifact_id": original_transcript["artifact_id"],
                        "summary": changed_summary,
                    },
                    operation_id="op_resummary_derived",
                )
                derived_id = result["snapshot"]["workflow_id"]
                self.assertNotEqual(derived_id, original["workflow_id"])
                await supervisor._queue.join()
                derived = await supervisor.get(derived_id)
                derived_transcript = next(item for item in derived["artifacts"] if item["kind"] == "transcript_markdown")
                derived_final = next(item for item in derived["artifacts"] if item["kind"] == "final_summary_markdown")
                self.assertEqual(transcriber.calls, 1)
                self.assertEqual(summary_generator.calls, 2)
                self.assertEqual(derived["spec"]["summary"]["model"], "summary-model-v2")
                self.assertEqual(derived["spec"]["summary"]["policy_snapshot"], {"id": "asr-primary-reference-advisory", "version": 1})
                self.assertEqual(derived["spec"]["summary"]["reference_document"], draft["summary"]["reference_document"])
                self.assertEqual(derived["spec"]["summary"]["template"]["prompt_snapshot"], "Extract action items.")
                self.assertEqual(derived_transcript["derived_from_artifact_id"], original_transcript["artifact_id"])
                self.assertNotEqual(derived_transcript["path"], original_transcript["path"])
                self.assertEqual(Path(derived_final["path"]).read_text(encoding="utf-8"), "summary-2")
                self.assertFalse(original_final["stale"])
                await supervisor.shutdown(interrupt=False)
                registry.close()

        asyncio.run(scenario())

    def test_clear_only_removes_terminal_registry_records_and_keeps_artifacts(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                source = root / "source.wav"
                source.write_bytes(b"audio")
                draft = make_draft(source, "clearable")
                draft["output"]["directory"] = str(root / "outputs")
                registry = WorkflowRegistry(root / "registry.sqlite3")
                supervisor = WorkflowSupervisor(registry, transcriber=FakeTranscriber(), summary_generator=FakeSummaryGenerator())
                submitted = await supervisor.submit(draft, operation_id="op_submit_clearable")
                workflow_id = submitted["snapshot"]["workflow_id"]
                with self.assertRaisesRegex(ValueError, "WORKFLOW_NOT_TERMINAL"):
                    await supervisor.clear({"workflow_id": workflow_id}, operation_id="op_clear_too_soon")
                await supervisor._queue.join()
                completed = await supervisor.get(workflow_id)
                artifact_paths = [Path(item["path"]) for item in completed["artifacts"]]
                persisted_artifacts = [
                    item for item in completed["artifacts"]
                    if item["kind"] != "summary_checkpoint_json"
                ]
                self.assertTrue(all(Path(item["path"]).is_file() for item in persisted_artifacts))
                checkpoint = next(item for item in completed["artifacts"] if item["kind"] == "summary_checkpoint_json")
                self.assertTrue(Path(checkpoint["path"]).is_file())
                result = await supervisor.clear({"workflow_id": workflow_id}, operation_id="op_clear_done")
                self.assertTrue(result["cleared"])
                self.assertEqual(await supervisor.list(), [])
                with self.assertRaises(KeyError):
                    await supervisor.get(workflow_id)
                self.assertTrue(all(path.is_file() for path in artifact_paths))
                await supervisor.shutdown(interrupt=False)
                registry.close()

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
