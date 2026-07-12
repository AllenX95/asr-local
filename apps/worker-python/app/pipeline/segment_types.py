"""Model-independent types shared by diarization and ASR adapters."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True, frozen=True)
class DiarizationTurn:
    """A speaker turn returned by a diarization provider."""

    speaker: str
    start_ms: int
    end_ms: int

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True, frozen=True)
class PlannedSegment:
    """A bounded ASR unit with separate authority and input boundaries.

    ``start_ms``/``end_ms`` are the only boundaries allowed to reach the
    transcript exporter.  ``input_start_ms``/``input_end_ms`` may include a
    small amount of neighbouring audio to avoid cutting words at a hard
    diarization boundary.
    """

    segment_id: str
    speaker: str
    start_ms: int
    end_ms: int
    input_start_ms: int
    input_end_ms: int

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)

    @property
    def input_duration_ms(self) -> int:
        return max(0, self.input_end_ms - self.input_start_ms)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True, frozen=True)
class SegmentRequest:
    """A single backend-independent ASR request."""

    segment: PlannedSegment
    audio: Any
    sample_rate: int
    language: str | None
    context: str = ""


@dataclass(slots=True)
class SegmentFailure:
    segment_id: str
    code: str
    message: str
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SegmentResult:
    """Backend output; speaker remains owned by the planned segment."""

    segment_id: str
    text: str = ""
    language: str | None = None
    relative_segments: list[Any] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    failure: SegmentFailure | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        if self.failure is not None:
            value["failure"] = self.failure.to_dict()
        return value
