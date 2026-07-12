from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from app.config import load_models_config, project_root


MODEL_IDS = {
    "moss_transcribe_diarize": "OpenMOSS-Team/MOSS-Transcribe-Diarize",
    "qwen3_asr_1_7b": "Qwen/Qwen3-ASR-1.7B",
    "pyannote_speaker_diarization": "pyannote/speaker-diarization-community-1",
}


def resolve_model_components(pipeline_profile: str) -> list[dict[str, Any]]:
    config = load_models_config()
    root = project_root()
    if pipeline_profile == "moss_transcribe_diarize":
        return [_component("transcriber", MODEL_IDS["moss_transcribe_diarize"], config.moss_transcribe_diarize.resolved_path(root))]
    if pipeline_profile in {"qwen3_asr_with_pyannote", "pyannote_qwen3_asr"}:
        return [
            _component("transcriber", MODEL_IDS["qwen3_asr_1_7b"], config.qwen3_asr_1_7b.resolved_path(root)),
            _component("diarization", MODEL_IDS["pyannote_speaker_diarization"], config.pyannote_speaker_diarization.resolved_path(root)),
        ]
    if pipeline_profile == "pyannote_moss_asr":
        return [
            _component("transcriber", MODEL_IDS["moss_transcribe_diarize"], config.moss_transcribe_diarize.resolved_path(root)),
            _component("diarization", MODEL_IDS["pyannote_speaker_diarization"], config.pyannote_speaker_diarization.resolved_path(root)),
        ]
    return []


def _component(role: str, model_id: str, path: Path) -> dict[str, Any]:
    return {
        "role": role,
        "model_id": model_id,
        "revision": _read_revision(path),
        "config_sha256": _sha256(path / "config.json"),
        "resolved_path": str(path.resolve()),
    }


def _read_revision(path: Path) -> str:
    candidates = (
        path / ".cache" / "huggingface" / "refs" / "main",
        path / "refs" / "main",
        # `snapshot_download` stores the resolved commit in per-file metadata
        # when the refs directory is not materialized in an offline model copy.
        path / ".cache" / "huggingface" / "download" / "config.json.metadata",
    )
    for candidate in candidates:
        if candidate.is_file():
            value = candidate.read_text(encoding="utf-8").splitlines()[0].strip()
            if value:
                return value
    return "unknown"


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
