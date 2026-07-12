"""Pyannote speaker-turn provider for the shared local pipeline."""

from __future__ import annotations

import logging
from typing import Any

from app.pipeline.interfaces import ProgressCallback
from app.pipeline.segment_types import DiarizationTurn


LOGGER = logging.getLogger("asr_local.worker.pyannote")


class PyannoteDiarizationProvider:
    def __init__(self, *, model_manager: Any) -> None:
        self.model_manager = model_manager
        self._pipeline = None

    def diarize(
        self,
        *,
        audio: Any,
        sample_rate: int,
        uri: str,
        total_ms: int,
        progress: ProgressCallback | None = None,
    ) -> list[DiarizationTurn]:
        if progress:
            progress({"phase": "diarization_loading", "detail": "正在加载 Pyannote 说话人模型"})
        self._pipeline = self.model_manager.get_pyannote_pipeline()
        if progress:
            progress({"phase": "diarizing", "detail": "正在分析说话人时间轴"})
        waveform = self.model_manager.torch.from_numpy(audio).unsqueeze(0)
        diarization = self._pipeline(
            {"waveform": waveform, "sample_rate": sample_rate, "uri": uri}
        )
        annotation = getattr(diarization, "exclusive_speaker_diarization", None)
        if annotation is None:
            annotation = getattr(diarization, "speaker_diarization", diarization)

        turns: list[DiarizationTurn] = []
        for turn, _, speaker in annotation.itertracks(yield_label=True):
            start_ms = max(0, int(round(float(turn.start) * 1000)))
            end_ms = min(total_ms, int(round(float(turn.end) * 1000)))
            if end_ms > start_ms:
                turns.append(DiarizationTurn(str(speaker), start_ms, end_ms))

        if not turns:
            LOGGER.warning("pyannote returned no speaker turns | uri=%s", uri)
            return [DiarizationTurn("Speaker 1", 0, total_ms)] if total_ms > 0 else []
        return turns

    def close(self) -> None:
        close = getattr(self.model_manager, "close_pyannote_pipeline", None)
        if callable(close):
            close()
        self._pipeline = None
