"""Protocols for the shared chunked local transcription pipeline."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, Protocol

from app.pipeline.segment_types import DiarizationTurn, SegmentRequest, SegmentResult


ProgressCallback = Callable[[dict[str, Any]], None]


class DiarizationProvider(Protocol):
    def diarize(
        self,
        *,
        audio: Any,
        sample_rate: int,
        uri: str,
        total_ms: int,
        progress: ProgressCallback | None = None,
    ) -> list[DiarizationTurn]: ...

    def close(self) -> None: ...


class SegmentTranscriber(Protocol):
    backend_id: str

    def transcribe_batch(
        self,
        requests: Sequence[SegmentRequest],
        *,
        progress: ProgressCallback | None = None,
    ) -> list[SegmentResult]: ...

    def close(self) -> None: ...
