from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from app.models.manager import ModelManager


class LegacyQwenPyannoteTranscriber:
    """v2 wrapper around the frozen v1 Qwen + pyannote pipeline.

    The wrapper creates an explicit Qwen model manager from the immutable
    workflow snapshot, so changing the global MOSS default cannot silently
    substitute the Legacy model. v1 remains the compatibility fallback during
    migration.
    """

    async def transcribe(self, spec: dict[str, Any], attempt_id: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._transcribe_sync, spec, attempt_id)

    def _transcribe_sync(self, spec: dict[str, Any], attempt_id: str) -> dict[str, Any]:
        from app.pipeline.job_runner import run_job

        transcription = spec["transcription"]
        model_manager = ModelManager(active_local_asr_model_override="qwen3_asr_1_7b")
        _validate_snapshot_paths(spec, model_manager)
        prompt = transcription.get("prompt_input", {})
        result = run_job(
            {
                "job_id": spec.get("workflow_id", "workflow-v2-legacy"),
                "source_path": spec["source"]["path"],
                "output_dir": str(Path(spec["output"]["directory"]) / ".staging" / f"legacy-{attempt_id}"),
                "output_file_name": "transcript.md",
                "asr_backend": "local",
                "cloud_asr_profile": None,
                "language_mode": transcription.get("language", {}).get("mode", "auto"),
                "fixed_language": transcription.get("language", {}).get("value"),
                "enable_speaker_diarization": True,
                "context_text": prompt.get("recording_background", ""),
                "terms": prompt.get("hotwords", []),
                "replacements": transcription.get("postprocess", {}).get("replacements", []),
                "keep_fillers": transcription.get("postprocess", {}).get("keep_fillers", True),
                "auto_punctuation": transcription.get("postprocess", {}).get("auto_punctuation", True),
            },
            model_manager=model_manager,
        )
        path = Path(result["md_path"])
        return {"kind": "transcript_markdown", "text": path.read_text(encoding="utf-8")}


def _validate_snapshot_paths(spec: dict[str, Any], manager: ModelManager) -> None:
    components = spec["transcription"].get("model_snapshot", {}).get("components", [])
    expected = {item.get("role"): Path(item.get("resolved_path", "")) for item in components}
    if expected.get("transcriber") != manager.qwen_path.resolve() or expected.get("diarization") != manager.pyannote_path.resolve():
        raise RuntimeError("MODEL_SNAPSHOT_MISMATCH: Legacy model paths changed after workflow submission")
