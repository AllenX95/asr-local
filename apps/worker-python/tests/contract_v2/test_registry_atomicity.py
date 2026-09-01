from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from app.workflow.registry import WorkflowRegistry


def _snapshot(*, sequence: int = 1, status: str = "completed") -> dict:
    return {
        "snapshot_version": 2,
        "workflow_id": "wf_atomic",
        "sequence": sequence,
        "spec": {
            "summary": {
                "reference_document": {
                    "name": "private.md",
                    "content": "sensitive reference content",
                }
            }
        },
        "status": status,
        "stage": "completed",
        "attempt": {
            "attempt_id": "att_atomic",
            "number": 1,
            "stage_attempts": {"transcription": 1, "summary": 1, "writing_final": 1},
        },
        "progress": {"stage_ratio": 1.0, "overall_ratio": 1.0},
        "control": {"pending_action": None},
        "runtime_plan": None,
        "artifacts": [],
        "recovery": {
            "recommended_retry_stage": None,
            "interrupted_attempt_id": None,
        },
        "last_error": None,
        "timestamps": {
            "created_at": "2026-08-28T00:00:00Z",
            "updated_at": f"2026-08-28T00:00:0{sequence}Z",
            "started_at": "2026-08-28T00:00:00Z",
            "completed_at": "2026-08-28T00:00:01Z",
        },
    }


def _event(snapshot: dict, event_type: str = "completed") -> dict:
    return {
        "workflow_id": snapshot["workflow_id"],
        "attempt_id": snapshot["attempt"]["attempt_id"],
        "sequence": snapshot["sequence"],
        "occurred_at": snapshot["timestamps"]["updated_at"],
        "type": event_type,
        "stage": snapshot["stage"],
        "data": {},
        "state": snapshot,
    }


class RegistryAtomicityTests(unittest.TestCase):
    def _create(self, registry: WorkflowRegistry) -> None:
        snapshot = _snapshot()
        registry.create_workflow(
            operation_id="op_submit_atomic",
            method="workflow.submit",
            payload_digest="submit-digest",
            workflow_id=snapshot["workflow_id"],
            attempt_id=snapshot["attempt"]["attempt_id"],
            snapshot=snapshot,
            event=_event(snapshot),
            now="2026-08-28T00:00:01Z",
        )

    def test_snapshot_and_operation_roll_back_together(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            registry = WorkflowRegistry(Path(temp) / "registry.sqlite3")
            self._create(registry)
            next_snapshot = _snapshot(sequence=2)

            with self.assertRaises(sqlite3.Error):
                registry.save_snapshot_with_operation(
                    snapshot=next_snapshot,
                    event=_event(next_snapshot, "controlled"),
                    operation_id="op_control_atomic",
                    method="workflow.control",
                    payload_digest="control-digest",
                    result={"accepted": True, "snapshot": next_snapshot},
                    now=object(),  # type: ignore[arg-type]
                )

            self.assertEqual(registry.get_snapshot("wf_atomic")["sequence"], 1)
            self.assertEqual(len(registry.timeline("wf_atomic")), 1)
            self.assertIsNone(
                registry.operation_result(
                    "op_control_atomic",
                    "workflow.control",
                    "control-digest",
                )
            )
            registry.close()

    def test_clear_rolls_back_if_operation_insert_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            registry = WorkflowRegistry(Path(temp) / "registry.sqlite3")
            self._create(registry)

            with self.assertRaises(sqlite3.Error):
                registry.clear_workflow_with_operation(
                    workflow_id="wf_atomic",
                    operation_id="op_clear_atomic",
                    method="workflow.clear",
                    payload_digest="clear-digest",
                    now=object(),  # type: ignore[arg-type]
                )

            self.assertEqual(registry.get_snapshot("wf_atomic")["status"], "completed")
            self.assertIsNotNone(
                registry.operation_result(
                    "op_submit_atomic",
                    "workflow.submit",
                    "submit-digest",
                )
            )
            registry.close()

    def test_clear_removes_sensitive_workflow_operations_and_keeps_compact_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "registry.sqlite3"
            registry = WorkflowRegistry(path)
            self._create(registry)

            result, deduplicated = registry.clear_workflow_with_operation(
                workflow_id="wf_atomic",
                operation_id="op_clear_atomic",
                method="workflow.clear",
                payload_digest="clear-digest",
                now="2026-08-28T00:00:02Z",
            )
            self.assertFalse(deduplicated)
            self.assertEqual(result, {"cleared": True, "workflow_id": "wf_atomic"})

            replay, deduplicated = registry.clear_workflow_with_operation(
                workflow_id="wf_atomic",
                operation_id="op_clear_atomic",
                method="workflow.clear",
                payload_digest="clear-digest",
                now="2026-08-28T00:00:03Z",
            )
            self.assertTrue(deduplicated)
            self.assertEqual(replay, result)

            registry.close()
            check = sqlite3.connect(path)
            try:
                rows = check.execute(
                    "SELECT operation_id, workflow_id, result_json FROM operations"
                ).fetchall()
            finally:
                check.close()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0][0], "op_clear_atomic")
            self.assertIsNone(rows[0][1])
            self.assertEqual(json.loads(rows[0][2]), result)
            self.assertNotIn("sensitive reference content", rows[0][2])


if __name__ == "__main__":
    unittest.main()
