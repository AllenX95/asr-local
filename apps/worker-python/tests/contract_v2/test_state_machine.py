from __future__ import annotations

import unittest

from app.workflow.state_machine import (
    WorkflowStateError,
    apply_event,
    create_initial_snapshot,
    mark_interrupted,
    retry_snapshot,
)


class WorkflowStateMachineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot = create_initial_snapshot(
            "wf_001",
            "att_001",
            {"spec_version": 2, "display_name": "test"},
            created_at="2026-07-10T12:00:00Z",
        )

    def test_submit_creates_queued_attempt_one(self) -> None:
        self.assertEqual(self.snapshot["status"], "queued")
        self.assertEqual(self.snapshot["attempt"]["number"], 1)
        self.assertEqual(self.snapshot["sequence"], 1)

    def test_event_reducer_ignores_duplicate_and_rejects_old_attempt(self) -> None:
        state = dict(self.snapshot)
        state["sequence"] = 2
        state["status"] = "running"
        state["stage"] = "preparing"
        state["attempt"] = {"attempt_id": "att_001", "number": 1, "stage_attempts": {}}
        event = {"workflow_id": "wf_001", "attempt_id": "att_001", "sequence": 2, "stage": "preparing", "state": state}
        accepted = apply_event(self.snapshot, event)
        self.assertEqual(accepted["status"], "running")
        duplicate = apply_event(accepted, event)
        self.assertEqual(duplicate["sequence"], 2)

        stale = dict(event)
        stale["sequence"] = 3
        stale["attempt_id"] = "att_old"
        stale["state"] = {**state, "sequence": 3, "attempt": {**state["attempt"], "attempt_id": "att_old"}}
        self.assertEqual(apply_event(accepted, stale), accepted)

    def test_interrupted_requires_explicit_retry_and_enters_queue(self) -> None:
        interrupted = mark_interrupted(self.snapshot, recommended_retry_stage="transcribing", updated_at="2026-07-10T12:01:00Z")
        self.assertEqual(interrupted["status"], "interrupted")
        self.assertEqual(interrupted["recovery"]["recommended_retry_stage"], "transcribing")
        decision = retry_snapshot(
            interrupted,
            expected_attempt_id="att_001",
            expected_sequence=2,
            from_stage="auto",
            new_attempt_id="att_002",
            updated_at="2026-07-10T12:02:00Z",
        )
        self.assertEqual(decision.from_stage, "transcribing")
        self.assertEqual(decision.snapshot["status"], "queued")
        self.assertEqual(decision.snapshot["attempt"]["attempt_id"], "att_002")

    def test_summary_retry_keeps_transcript_artifact(self) -> None:
        failed = {**self.snapshot, "sequence": 5, "status": "failed", "stage": "summarizing"}
        failed["attempt"] = {"attempt_id": "att_001", "number": 1, "stage_attempts": {}}
        failed["artifacts"] = [{"artifact_id": "transcript-1", "kind": "transcript_markdown", "stale": False}]
        decision = retry_snapshot(
            failed,
            expected_attempt_id="att_001",
            expected_sequence=5,
            from_stage="summarizing",
            input_artifact_id="transcript-1",
            new_attempt_id="att_002",
            updated_at="2026-07-10T12:03:00Z",
        )
        self.assertEqual(decision.from_stage, "summarizing")
        self.assertEqual(decision.snapshot["artifacts"][0]["artifact_id"], "transcript-1")

    def test_retry_rejects_stale_attempt(self) -> None:
        failed = {**self.snapshot, "status": "failed"}
        with self.assertRaises(WorkflowStateError):
            retry_snapshot(
                failed,
                expected_attempt_id="att_old",
                expected_sequence=1,
                from_stage="auto",
                new_attempt_id="att_002",
                updated_at="2026-07-10T12:03:00Z",
            )


if __name__ == "__main__":
    unittest.main()
