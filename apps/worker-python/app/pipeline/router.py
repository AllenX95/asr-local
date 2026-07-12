from __future__ import annotations

from typing import Any


class ProfileRoutingTranscriber:
    """Select the adapter from the immutable workflow snapshot, never globals."""

    def __init__(self, *, qwen, cloud) -> None:
        self.cloud = cloud
        self.qwen = qwen

    async def transcribe(self, spec: dict[str, Any], attempt_id: str, *, progress=None) -> dict[str, Any]:
        profile = spec["transcription"]["pipeline_profile"]
        if profile == "pyannote_qwen3_asr":
            if self.qwen is None:
                raise RuntimeError("QWEN_ADAPTER_UNAVAILABLE: install the Qwen inference runtime")
            return await self.qwen.transcribe(spec, attempt_id, progress=progress) if progress else await self.qwen.transcribe(spec, attempt_id)
        if profile == "cloud_asr":
            return await self.cloud.transcribe(spec, attempt_id, progress=progress) if progress else await self.cloud.transcribe(spec, attempt_id)
        raise RuntimeError(f"UNSUPPORTED_PIPELINE_PROFILE: {profile}")
