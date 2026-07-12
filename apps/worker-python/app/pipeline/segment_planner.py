"""Deterministic, model-independent diarization-to-ASR planning."""

from __future__ import annotations

from app.pipeline.segment_types import DiarizationTurn, PlannedSegment


DEFAULT_MIN_SEGMENT_MS = 800
DEFAULT_MERGE_GAP_MS = 300
DEFAULT_MAX_SEGMENT_MS = 30_000
DEFAULT_PADDING_MS = 200


def plan_segments(
    turns: list[DiarizationTurn],
    total_ms: int,
    *,
    min_segment_ms: int = DEFAULT_MIN_SEGMENT_MS,
    merge_gap_ms: int = DEFAULT_MERGE_GAP_MS,
    max_segment_ms: int = DEFAULT_MAX_SEGMENT_MS,
    padding_ms: int = DEFAULT_PADDING_MS,
) -> list[PlannedSegment]:
    """Normalize diarization turns into bounded, deterministic ASR units."""

    if total_ms < 0:
        raise ValueError("total_ms must be non-negative")
    if min_segment_ms < 0 or merge_gap_ms < 0 or max_segment_ms <= 0 or padding_ms < 0:
        raise ValueError("segment planner parameters must be non-negative and max_segment_ms positive")

    normalized: list[DiarizationTurn] = []
    for turn in sorted(turns, key=lambda item: (item.start_ms, item.end_ms, item.speaker)):
        start_ms = max(0, min(int(turn.start_ms), total_ms))
        end_ms = max(start_ms, min(int(turn.end_ms), total_ms))
        if end_ms <= start_ms:
            continue
        normalized.append(DiarizationTurn(str(turn.speaker or "Speaker 1"), start_ms, end_ms))

    merged: list[DiarizationTurn] = []
    for current in normalized:
        if not merged:
            merged.append(current)
            continue
        previous = merged[-1]
        same_speaker = previous.speaker == current.speaker
        small_gap = current.start_ms - previous.end_ms <= merge_gap_ms
        if same_speaker and (small_gap or current.duration_ms < min_segment_ms):
            merged[-1] = DiarizationTurn(
                previous.speaker,
                previous.start_ms,
                max(previous.end_ms, current.end_ms),
            )
            continue
        merged.append(current)

    planned: list[PlannedSegment] = []
    counter = 1
    for turn in merged:
        cursor = turn.start_ms
        while cursor < turn.end_ms:
            chunk_end = min(cursor + max_segment_ms, turn.end_ms)
            planned.append(
                _planned_segment(
                    counter,
                    turn.speaker,
                    cursor,
                    chunk_end,
                    total_ms,
                    padding_ms,
                )
            )
            counter += 1
            cursor = chunk_end
    return planned


def _planned_segment(
    counter: int,
    speaker: str,
    start_ms: int,
    end_ms: int,
    total_ms: int,
    padding_ms: int,
) -> PlannedSegment:
    return PlannedSegment(
        segment_id=f"segment-{counter:04d}",
        speaker=speaker,
        start_ms=start_ms,
        end_ms=end_ms,
        input_start_ms=max(0, start_ms - padding_ms),
        input_end_ms=min(total_ms, end_ms + padding_ms),
    )
