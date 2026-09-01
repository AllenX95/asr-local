from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest

from app.workflow.registry import WorkflowRegistry


class RegistryMigrationTests(unittest.TestCase):
    def test_legacy_unversioned_database_is_backed_up_and_versioned(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "registry.sqlite3"
            connection = sqlite3.connect(path)
            connection.execute("CREATE TABLE legacy_marker(value TEXT)")
            connection.commit()
            connection.close()

            registry = WorkflowRegistry(path)
            registry.close()

            self.assertTrue(path.with_suffix(".sqlite3.pre-v1.bak").is_file())
            check = sqlite3.connect(path)
            try:
                self.assertEqual(check.execute("PRAGMA user_version").fetchone()[0], 2)
            finally:
                check.close()

    def test_v1_operations_are_linked_to_workflows_and_backed_up(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "registry.sqlite3"
            connection = sqlite3.connect(path)
            connection.executescript(
                """
                CREATE TABLE workflows (
                    workflow_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE operations (
                    operation_id TEXT PRIMARY KEY,
                    method TEXT NOT NULL,
                    payload_digest TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                PRAGMA user_version=1;
                """
            )
            snapshot = {"workflow_id": "wf_v1", "status": "completed"}
            connection.execute(
                "INSERT INTO workflows VALUES(?,?,?,?,?,?)",
                ("wf_v1", "completed", 1, '{"workflow_id":"wf_v1","status":"completed"}', "now", "now"),
            )
            connection.execute(
                "INSERT INTO operations VALUES(?,?,?,?,?)",
                (
                    "op_v1",
                    "workflow.submit",
                    "digest",
                    '{"created":true,"snapshot":{"workflow_id":"wf_v1","status":"completed"}}',
                    "now",
                ),
            )
            connection.commit()
            connection.close()

            registry = WorkflowRegistry(path)
            registry.close()

            self.assertTrue(path.with_suffix(".sqlite3.pre-v2.bak").is_file())
            check = sqlite3.connect(path)
            try:
                columns = {row[1] for row in check.execute("PRAGMA table_info(operations)")}
                self.assertIn("workflow_id", columns)
                self.assertEqual(check.execute("PRAGMA user_version").fetchone()[0], 2)
                self.assertEqual(
                    check.execute(
                        "SELECT workflow_id FROM operations WHERE operation_id='op_v1'"
                    ).fetchone()[0],
                    "wf_v1",
                )
            finally:
                check.close()

    def test_corrupt_database_is_not_silently_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "registry.sqlite3"
            path.write_bytes(b"not a sqlite database")
            with self.assertRaises(sqlite3.DatabaseError):
                WorkflowRegistry(path)
            self.assertEqual(path.read_bytes(), b"not a sqlite database")

    def test_newer_database_schema_is_not_downgraded(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "registry.sqlite3"
            connection = sqlite3.connect(path)
            connection.execute("PRAGMA user_version=99")
            connection.commit()
            connection.close()

            with self.assertRaisesRegex(sqlite3.DatabaseError, "newer than supported"):
                WorkflowRegistry(path)
            check = sqlite3.connect(path)
            try:
                self.assertEqual(check.execute("PRAGMA user_version").fetchone()[0], 99)
            finally:
                check.close()


if __name__ == "__main__":
    unittest.main()
