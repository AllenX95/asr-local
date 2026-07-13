"""Stable output and temporary workspace layout for workflow artifacts."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


TRANSCRIPTS_DIRECTORY = "transcripts"
SUMMARY_DIRECTORY = "summary"
STAGING_DIRECTORY = ".staging"
JOBS_DIRECTORY = ".jobs"

_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


def safe_filename(value: str, fallback: str = "meeting") -> str:
    """Return a filesystem-safe display name while preserving Unicode text."""

    cleaned = _INVALID_FILENAME_CHARS.sub("_", str(value or "").strip()).strip(" .")
    return cleaned or fallback


def workflow_staging_dir(output_root: Path, workflow_id: str) -> Path:
    return output_root / STAGING_DIRECTORY / workflow_id


def workflow_jobs_dir(output_root: Path, workflow_id: str) -> Path:
    return output_root / JOBS_DIRECTORY / workflow_id


def artifact_directory(output_root: Path, kind: str) -> Path | None:
    if kind in {"transcript_markdown", "transcript_json"}:
        return output_root / TRANSCRIPTS_DIRECTORY
    if kind in {"final_summary_markdown", "final_summary_json"}:
        return output_root / SUMMARY_DIRECTORY
    return None


def artifact_path(snapshot: dict[str, Any], kind: str, revision: int) -> Path:
    output = snapshot["spec"]["output"]
    output_root = Path(str(output["directory"])).expanduser().resolve()
    workflow_id = str(snapshot["workflow_id"])
    if kind == "summary_checkpoint_json":
        return workflow_staging_dir(output_root, workflow_id) / _artifact_filename(snapshot, kind, revision)

    directory = artifact_directory(output_root, kind)
    if directory is None:
        return workflow_staging_dir(output_root, workflow_id) / _artifact_filename(snapshot, kind, revision)
    return directory / _artifact_filename(snapshot, kind, revision)


def _artifact_filename(snapshot: dict[str, Any], kind: str, revision: int) -> str:
    if kind == "summary_checkpoint_json":
        stem = "summary-checkpoint"
        if revision > 1:
            stem += f"-r{revision}"
        return f"{stem}.json"
    base_name = safe_filename(str(snapshot["spec"]["output"].get("base_name") or snapshot.get("display_name") or "meeting"))
    workflow_id = safe_filename(str(snapshot["workflow_id"]), fallback="workflow")
    stem = f"{base_name}--{workflow_id}"
    if revision > 1:
        stem += f"-r{revision}"
    suffix = ".json" if kind.endswith("_json") else ".md"
    return f"{stem}{suffix}"
