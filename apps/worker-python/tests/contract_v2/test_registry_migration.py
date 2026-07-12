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
                self.assertEqual(check.execute("PRAGMA user_version").fetchone()[0], 1)
            finally:
                check.close()

    def test_corrupt_database_is_not_silently_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "registry.sqlite3"
            path.write_bytes(b"not a sqlite database")
            with self.assertRaises(sqlite3.DatabaseError):
                WorkflowRegistry(path)
            self.assertEqual(path.read_bytes(), b"not a sqlite database")


if __name__ == "__main__":
    unittest.main()
