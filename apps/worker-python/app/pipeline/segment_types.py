"""Model-independent types shared by diarization and ASR adapters."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import os
from typing import Any


@dataclass(slots=True, frozen=True)
class BatchItemIdentity:
    """Stable identity used to route a batched ASR result.

    The identity deliberately contains both workflow and attempt.  A segment
    id on its own is not sufficient because a retry may legitimately contain
    the same segment id as an earlier attempt.
    """

    workflow_id: str
    attempt_id: str
    segment_id: str
    ordinal: int

    def __post_init__(self) -> None:
        for name in ("workflow_id", "attempt_id", "segment_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a non-empty string")
        if not isinstance(self.ordinal, int) or isinstance(self.ordinal, bool):
            raise TypeError("ordinal must be an integer")
        if self.ordinal < 0:
            raise ValueError("ordinal must be non-negative")


@dataclass(slots=True, frozen=True)
class RuntimeKey:
    """Identity of the model runtime owned by a GPU batch dispatcher."""

    model_name: str = "qwen3_asr_1_7b"
    device: str = "cuda:0"
    dtype: str = "float16"
    model_path: str = ""

    def __post_init__(self) -> None:
        path = str(self.model_path or "").strip()
        if path:
            path = os.path.normcase(os.path.abspath(os.path.expanduser(path)))
        object.__setattr__(self, "model_path", path)

    @property
    def normalized_model_path(self) -> str:
        return self.model_path


@dataclass(slots=True, frozen=True)
class AsrBatchItem:
    """One independent audio input submitted to the shared ASR runtime.

    ``duration_ms`` is optional because adapters often only have a waveform
    and sample rate.  The dispatcher derives it from ``audio`` when absent.
    ``audio_duration_ms`` is accepted as an explicit, descriptive alias for
    callers that already use that name; both properties resolve to the same
    effective duration without mutating the item.
    """

    identity: BatchItemIdentity
    audio: Any
    sample_rate: int
    context: str = ""
    language: str | None = None
    duration_ms: int | None = None
    runtime_key: RuntimeKey | None = None
    audio_duration_ms: int | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    input_start_ms: int | None = None
    input_end_ms: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.identity, BatchItemIdentity):
            raise TypeError("identity must be a BatchItemIdentity")
        if not isinstance(self.sample_rate, int) or isinstance(self.sample_rate, bool):
            raise TypeError("sample_rate must be an integer")
        if self.sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        if self.duration_ms is not None and self.duration_ms < 0:
            raise ValueError("duration_ms must be non-negative")
        if self.audio_duration_ms is not None and self.audio_duration_ms < 0:
            raise ValueError("audio_duration_ms must be non-negative")
        if self.duration_ms is not None and self.audio_duration_ms is not None:
            if int(self.duration_ms) != int(self.audio_duration_ms):
                raise ValueError("duration_ms and audio_duration_ms disagree")

    @property
    def effective_duration_ms(self) -> int | None:
        if self.duration_ms is not None:
            return int(self.duration_ms)
        if self.audio_duration_ms is not None:
            return int(self.audio_duration_ms)
        return None


@dataclass(slots=True, frozen=True)
class Outcome:
    """Result routed back to one submitted batch item.

    Cancellation and shutdown are represented as outcomes instead of killing
    a shared model call.  This makes it explicit that a completed in-flight
    result was intentionally discarded and prevents stale attempts from
    leaking into a newer attempt.
    """

    identity: BatchItemIdentity
    result: Any = None
    error: BaseException | None = None
    cancelled: bool = False
    discarded: bool = False

    @property
    def transcription(self) -> Any:
        return self.result

    @property
    def success(self) -> bool:
        return self.error is None and not self.cancelled and not self.discarded

    @property
    def failed(self) -> bool:
        return self.error is not None and not self.cancelled


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
