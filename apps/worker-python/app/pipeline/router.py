from __future__ import annotations

from typing import Any


class ProfileRoutingTranscriber:
    """Select the adapter from the immutable workflow snapshot, never globals."""

    def __init__(self, *, moss, cloud, legacy=None, qwen=None) -> None:
        self.moss = moss
        self.cloud = cloud
        self.legacy = legacy
        self.qwen = qwen or legacy

    async def transcribe(self, spec: dict[str, Any], attempt_id: str, *, progress=None) -> dict[str, Any]:
        profile = spec["transcription"]["pipeline_profile"]
        if profile in {"moss_transcribe_diarize", "pyannote_moss_asr"}:
            return await self.moss.transcribe(spec, attempt_id, progress=progress) if progress else await self.moss.transcribe(spec, attempt_id)
        if profile == "pyannote_qwen3_asr":
            if self.qwen is None:
                raise RuntimeError("QWEN_ADAPTER_UNAVAILABLE: install the Qwen compatibility runtime")
            return await self.qwen.transcribe(spec, attempt_id, progress=progress) if progress else await self.qwen.transcribe(spec, attempt_id)
        if profile == "cloud_asr":
            return await self.cloud.transcribe(spec, attempt_id, progress=progress) if progress else await self.cloud.transcribe(spec, attempt_id)
        if profile == "qwen3_asr_with_pyannote":
            if self.legacy is None:
                raise RuntimeError("LEGACY_ADAPTER_UNAVAILABLE: install the separate Legacy compatibility runtime or select MOSS")
            return await self.legacy.transcribe(spec, attempt_id, progress=progress) if progress else await self.legacy.transcribe(spec, attempt_id)
        raise RuntimeError(f"UNSUPPORTED_PIPELINE_PROFILE: {profile}")
