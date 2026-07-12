from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import time


def default_registry() -> Path:
    configured = os.environ.get("ASR_LOCAL_STATE_DIR")
    if configured:
        return Path(configured) / "registry.sqlite3"
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "ASR Local" / "workflow" / "registry.sqlite3"
    return Path("outputs/.workflow/registry.sqlite3")


def read_snapshot(registry: Path, workflow_id: str | None) -> dict | None:
    uri = registry.resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=1) as connection:
        if workflow_id:
            row = connection.execute("SELECT snapshot_json FROM workflows WHERE workflow_id = ?", (workflow_id,)).fetchone()
        else:
            row = connection.execute("SELECT snapshot_json FROM workflows ORDER BY updated_at DESC LIMIT 1").fetchone()
    return None if row is None else json.loads(row[0])


def summary(snapshot: dict) -> dict:
    progress = snapshot.get("progress", {})
    updated = snapshot.get("timestamps", {}).get("updated_at")
    age = None
    if updated:
        age = max(0, int((datetime.now(timezone.utc) - datetime.fromisoformat(updated.replace("Z", "+00:00"))).total_seconds()))
    return {
        "workflow_id": snapshot.get("workflow_id"),
        "status": snapshot.get("status"),
        "stage": snapshot.get("stage"),
        "sequence": snapshot.get("sequence"),
        "overall_ratio": progress.get("overall_ratio"),
        "stage_ratio": progress.get("stage_ratio"),
        "phase": progress.get("phase"),
        "detail": progress.get("detail"),
        "updated_at": updated,
        "age_seconds": age,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only ASR Local workflow state watcher")
    parser.add_argument("workflow_id", nargs="?")
    parser.add_argument("--registry", type=Path, default=default_registry())
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=float, default=1.0)
    args = parser.parse_args()
    if not args.registry.is_file():
        parser.error(f"registry does not exist: {args.registry}")
    previous = None
    while True:
        snapshot = read_snapshot(args.registry, args.workflow_id)
        current = None if snapshot is None else summary(snapshot)
        encoded = json.dumps(current, ensure_ascii=False, sort_keys=True)
        if encoded != previous:
            print(encoded, flush=True)
            previous = encoded
        if args.once or snapshot is None or snapshot.get("status") in {"completed", "failed", "cancelled", "interrupted"}:
            return 0
        time.sleep(max(args.interval, 0.1))


if __name__ == "__main__":
    raise SystemExit(main())
