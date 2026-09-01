from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import shutil
import sqlite3
from threading import RLock
from typing import Any, Iterator


SCHEMA_VERSION = 2
TERMINAL_STATUSES = {
    "completed",
    "completed_with_warnings",
    "failed",
    "cancelled",
    "interrupted",
}


class OperationConflictError(ValueError):
    pass


class WorkflowNotFoundError(KeyError):
    pass


class WorkflowRegistry:
    """SQLite registry with one transactional writer for snapshot + event."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._prepare_database()
        self._lock = RLock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._initialize()

    def _prepare_database(self) -> None:
        if not self.path.exists():
            return
        check = sqlite3.connect(self.path)
        try:
            result = check.execute("PRAGMA quick_check").fetchone()
            if result is None or result[0] != "ok":
                raise sqlite3.DatabaseError(f"workflow database integrity check failed: {result}")
            version = int(check.execute("PRAGMA user_version").fetchone()[0])
        finally:
            check.close()
        if version > SCHEMA_VERSION:
            raise sqlite3.DatabaseError(
                f"workflow database schema v{version} is newer than supported v{SCHEMA_VERSION}"
            )
        if version < SCHEMA_VERSION:
            backup_version = 1 if version == 0 else SCHEMA_VERSION
            backup = self.path.with_suffix(self.path.suffix + f".pre-v{backup_version}.bak")
            if not backup.exists():
                shutil.copy2(self.path, backup)

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _initialize(self) -> None:
        with self._transaction() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS workflows (
                    workflow_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS attempts (
                    attempt_id TEXT PRIMARY KEY,
                    workflow_id TEXT NOT NULL REFERENCES workflows(workflow_id),
                    attempt_number INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    stage TEXT,
                    started_at TEXT,
                    ended_at TEXT,
                    UNIQUE(workflow_id, attempt_number)
                );
                CREATE TABLE IF NOT EXISTS operations (
                    operation_id TEXT PRIMARY KEY,
                    method TEXT NOT NULL,
                    payload_digest TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    workflow_id TEXT REFERENCES workflows(workflow_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    workflow_id TEXT NOT NULL REFERENCES workflows(workflow_id),
                    attempt_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    stale INTEGER NOT NULL DEFAULT 0,
                    metadata_json TEXT NOT NULL,
                    UNIQUE(workflow_id, kind, revision)
                );
                CREATE TABLE IF NOT EXISTS workflow_events (
                    workflow_id TEXT NOT NULL REFERENCES workflows(workflow_id),
                    sequence INTEGER NOT NULL,
                    event_json TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    PRIMARY KEY(workflow_id, sequence)
                );
                CREATE INDEX IF NOT EXISTS workflow_status_idx ON workflows(status, created_at DESC);
                CREATE INDEX IF NOT EXISTS attempts_workflow_idx ON attempts(workflow_id, attempt_number DESC);
                """
            )
            self._migrate_to_v2(connection)
            connection.execute("CREATE INDEX IF NOT EXISTS operations_workflow_idx ON operations(workflow_id, created_at DESC)")
            connection.execute("CREATE INDEX IF NOT EXISTS operations_method_idx ON operations(method, created_at DESC)")
            connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    def _migrate_to_v2(self, connection: sqlite3.Connection) -> None:
        columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(operations)").fetchall()
        }
        if "workflow_id" not in columns:
            connection.execute(
                "ALTER TABLE operations ADD COLUMN workflow_id TEXT REFERENCES workflows(workflow_id) ON DELETE CASCADE"
            )

        rows = connection.execute(
            "SELECT operation_id, method, result_json FROM operations WHERE workflow_id IS NULL"
        ).fetchall()
        for row in rows:
            try:
                result = json.loads(row["result_json"])
            except (TypeError, json.JSONDecodeError):
                continue
            workflow_id = _operation_workflow_id(result)
            if workflow_id is None:
                continue
            workflow_exists = connection.execute(
                "SELECT 1 FROM workflows WHERE workflow_id = ?",
                (workflow_id,),
            ).fetchone()
            if workflow_exists is not None:
                connection.execute(
                    "UPDATE operations SET workflow_id = ? WHERE operation_id = ?",
                    (workflow_id, row["operation_id"]),
                )
            elif row["method"] == "workflow.clear":
                connection.execute(
                    "UPDATE operations SET result_json = ? WHERE operation_id = ?",
                    (_dump({"cleared": True, "workflow_id": workflow_id}), row["operation_id"]),
                )
            else:
                connection.execute(
                    "DELETE FROM operations WHERE operation_id = ?",
                    (row["operation_id"],),
                )

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self._connection
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise

    def operation_result(self, operation_id: str, method: str, payload_digest: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT method, payload_digest, result_json FROM operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if row is None:
                return None
            if row["method"] != method or row["payload_digest"] != payload_digest:
                raise OperationConflictError("OPERATION_ID_REUSED")
            return json.loads(row["result_json"])

    def save_operation_result(
        self,
        *,
        operation_id: str,
        method: str,
        payload_digest: str,
        result: dict[str, Any],
        now: str,
        workflow_id: str | None = None,
    ) -> dict[str, Any]:
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT method, payload_digest, result_json FROM operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if existing is not None:
                if existing["method"] != method or existing["payload_digest"] != payload_digest:
                    raise OperationConflictError("OPERATION_ID_REUSED")
                return json.loads(existing["result_json"])
            connection.execute(
                "INSERT INTO operations(operation_id,method,payload_digest,result_json,created_at,workflow_id) VALUES(?,?,?,?,?,?)",
                (operation_id, method, payload_digest, _dump(result), now, workflow_id),
            )
            return result

    def create_workflow(
        self,
        *,
        operation_id: str,
        method: str,
        payload_digest: str,
        workflow_id: str,
        attempt_id: str,
        snapshot: dict[str, Any],
        event: dict[str, Any],
        now: str,
    ) -> tuple[dict[str, Any], bool]:
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT method, payload_digest, result_json FROM operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if existing is not None:
                if existing["method"] != method or existing["payload_digest"] != payload_digest:
                    raise OperationConflictError("OPERATION_ID_REUSED")
                return json.loads(existing["result_json"]), True

            connection.execute(
                "INSERT INTO workflows(workflow_id,status,sequence,snapshot_json,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (workflow_id, snapshot["status"], snapshot["sequence"], _dump(snapshot), now, now),
            )
            attempt = snapshot["attempt"]
            connection.execute(
                "INSERT INTO attempts(attempt_id,workflow_id,attempt_number,status,stage,started_at,ended_at) VALUES(?,?,?,?,?,?,?)",
                (attempt_id, workflow_id, attempt["number"], snapshot["status"], snapshot.get("stage"), None, None),
            )
            connection.execute(
                "INSERT INTO workflow_events(workflow_id,sequence,event_json,occurred_at) VALUES(?,?,?,?)",
                (workflow_id, snapshot["sequence"], _dump(event), event["occurred_at"]),
            )
            result = {"created": True, "deduplicated": False, "snapshot": snapshot}
            connection.execute(
                "INSERT INTO operations(operation_id,method,payload_digest,result_json,created_at,workflow_id) VALUES(?,?,?,?,?,?)",
                (operation_id, method, payload_digest, _dump(result), now, workflow_id),
            )
            return result, False

    def save_snapshot(self, snapshot: dict[str, Any], event: dict[str, Any]) -> None:
        with self._transaction() as connection:
            self._save_snapshot(connection, snapshot, event)

    def save_snapshot_with_operation(
        self,
        *,
        snapshot: dict[str, Any],
        event: dict[str, Any],
        operation_id: str,
        method: str,
        payload_digest: str,
        result: dict[str, Any],
        now: str,
    ) -> tuple[dict[str, Any], bool]:
        with self._transaction() as connection:
            existing = self._operation_result_in_transaction(
                connection,
                operation_id,
                method,
                payload_digest,
            )
            if existing is not None:
                return existing, True
            if not self._save_snapshot(connection, snapshot, event):
                raise ValueError("SEQUENCE_CONFLICT")
            connection.execute(
                "INSERT INTO operations(operation_id,method,payload_digest,result_json,created_at,workflow_id) VALUES(?,?,?,?,?,?)",
                (
                    operation_id,
                    method,
                    payload_digest,
                    _dump(result),
                    now,
                    snapshot["workflow_id"],
                ),
            )
            return result, False

    def clear_workflow_with_operation(
        self,
        *,
        workflow_id: str,
        operation_id: str,
        method: str,
        payload_digest: str,
        now: str,
    ) -> tuple[dict[str, Any], bool]:
        with self._transaction() as connection:
            existing = self._operation_result_in_transaction(
                connection,
                operation_id,
                method,
                payload_digest,
            )
            if existing is not None:
                return existing, True
            row = connection.execute(
                "SELECT status FROM workflows WHERE workflow_id = ?",
                (workflow_id,),
            ).fetchone()
            if row is None:
                raise WorkflowNotFoundError(workflow_id)
            if row["status"] not in TERMINAL_STATUSES:
                raise ValueError("WORKFLOW_NOT_TERMINAL")

            connection.execute("DELETE FROM operations WHERE workflow_id = ?", (workflow_id,))
            connection.execute("DELETE FROM artifacts WHERE workflow_id = ?", (workflow_id,))
            connection.execute("DELETE FROM workflow_events WHERE workflow_id = ?", (workflow_id,))
            connection.execute("DELETE FROM attempts WHERE workflow_id = ?", (workflow_id,))
            connection.execute("DELETE FROM workflows WHERE workflow_id = ?", (workflow_id,))
            result = {"cleared": True, "workflow_id": workflow_id}
            connection.execute(
                "INSERT INTO operations(operation_id,method,payload_digest,result_json,created_at,workflow_id) VALUES(?,?,?,?,?,NULL)",
                (operation_id, method, payload_digest, _dump(result), now),
            )
            return result, False

    def _operation_result_in_transaction(
        self,
        connection: sqlite3.Connection,
        operation_id: str,
        method: str,
        payload_digest: str,
    ) -> dict[str, Any] | None:
        row = connection.execute(
            "SELECT method, payload_digest, result_json FROM operations WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        if row is None:
            return None
        if row["method"] != method or row["payload_digest"] != payload_digest:
            raise OperationConflictError("OPERATION_ID_REUSED")
        return json.loads(row["result_json"])

    def _save_snapshot(
        self,
        connection: sqlite3.Connection,
        snapshot: dict[str, Any],
        event: dict[str, Any],
    ) -> bool:
        workflow_id = snapshot["workflow_id"]
        attempt = snapshot["attempt"]
        row = connection.execute(
            "SELECT sequence FROM workflows WHERE workflow_id = ?",
            (workflow_id,),
        ).fetchone()
        if row is None:
            raise WorkflowNotFoundError(workflow_id)
        if snapshot["sequence"] <= row["sequence"]:
            return False
        connection.execute(
            "UPDATE workflows SET status=?, sequence=?, snapshot_json=?, updated_at=? WHERE workflow_id=?",
            (
                snapshot["status"],
                snapshot["sequence"],
                _dump(snapshot),
                snapshot["timestamps"]["updated_at"],
                workflow_id,
            ),
        )
        connection.execute(
            "INSERT INTO attempts(attempt_id,workflow_id,attempt_number,status,stage,started_at,ended_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(attempt_id) DO UPDATE SET status=excluded.status, stage=excluded.stage, started_at=COALESCE(excluded.started_at, attempts.started_at), ended_at=excluded.ended_at",
            (
                attempt["attempt_id"],
                workflow_id,
                attempt["number"],
                snapshot["status"],
                snapshot.get("stage"),
                snapshot["timestamps"].get("started_at"),
                snapshot["timestamps"].get("completed_at"),
            ),
        )
        connection.execute(
            "INSERT INTO workflow_events(workflow_id,sequence,event_json,occurred_at) VALUES(?,?,?,?)",
            (workflow_id, snapshot["sequence"], _dump(event), event["occurred_at"]),
        )
        self._sync_artifacts(connection, snapshot)
        return True

    def get_snapshot(self, workflow_id: str) -> dict[str, Any]:
        row = self._connection.execute(
            "SELECT snapshot_json FROM workflows WHERE workflow_id = ?",
            (workflow_id,),
        ).fetchone()
        if row is None:
            raise WorkflowNotFoundError(workflow_id)
        return json.loads(row["snapshot_json"])

    def list_snapshots(self, statuses: set[str] | None = None) -> list[dict[str, Any]]:
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            rows = self._connection.execute(
                f"SELECT snapshot_json FROM workflows WHERE status IN ({placeholders}) ORDER BY created_at DESC, workflow_id DESC",
                tuple(sorted(statuses)),
            ).fetchall()
        else:
            rows = self._connection.execute(
                "SELECT snapshot_json FROM workflows ORDER BY created_at DESC, workflow_id DESC"
            ).fetchall()
        return [json.loads(row["snapshot_json"]) for row in rows]

    def active_snapshots(self) -> list[dict[str, Any]]:
        return self.list_snapshots({"running", "waiting_for_secret", "paused"})

    def timeline(self, workflow_id: str, limit: int = 200) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            "SELECT event_json FROM workflow_events WHERE workflow_id = ? ORDER BY sequence DESC LIMIT ?",
            (workflow_id, max(0, min(limit, 500))),
        ).fetchall()
        return [json.loads(row["event_json"]) for row in reversed(rows)]

    def delete_workflow(self, workflow_id: str) -> None:
        """Delete registry metadata while deliberately leaving artifact files untouched."""
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT status FROM workflows WHERE workflow_id = ?",
                (workflow_id,),
            ).fetchone()
            if row is None:
                raise WorkflowNotFoundError(workflow_id)
            if row["status"] not in TERMINAL_STATUSES:
                raise ValueError("WORKFLOW_NOT_TERMINAL")
            connection.execute("DELETE FROM artifacts WHERE workflow_id = ?", (workflow_id,))
            connection.execute("DELETE FROM workflow_events WHERE workflow_id = ?", (workflow_id,))
            connection.execute("DELETE FROM attempts WHERE workflow_id = ?", (workflow_id,))
            connection.execute("DELETE FROM workflows WHERE workflow_id = ?", (workflow_id,))

    def _sync_artifacts(self, connection: sqlite3.Connection, snapshot: dict[str, Any]) -> None:
        for artifact in snapshot.get("artifacts", []):
            connection.execute(
                "INSERT INTO artifacts(artifact_id,workflow_id,attempt_id,kind,revision,stale,metadata_json) VALUES(?,?,?,?,?,?,?) ON CONFLICT(artifact_id) DO UPDATE SET stale=excluded.stale, metadata_json=excluded.metadata_json",
                (
                    artifact["artifact_id"],
                    snapshot["workflow_id"],
                    snapshot["attempt"]["attempt_id"],
                    artifact["kind"],
                    artifact["revision"],
                    int(bool(artifact.get("stale"))),
                    _dump(artifact),
                ),
            )


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _operation_workflow_id(result: Any) -> str | None:
    if not isinstance(result, dict):
        return None
    workflow_id = result.get("workflow_id")
    if isinstance(workflow_id, str) and workflow_id:
        return workflow_id
    snapshot = result.get("snapshot")
    if isinstance(snapshot, dict):
        workflow_id = snapshot.get("workflow_id")
        if isinstance(workflow_id, str) and workflow_id:
            return workflow_id
    return None
