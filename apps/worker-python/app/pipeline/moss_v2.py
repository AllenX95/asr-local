from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import tempfile
import threading
from typing import Any

from app.audio import NormalizedAudio, normalize_audio


LOGGER = logging.getLogger("asr_local.worker.moss")


def _read_audio_with_fallback(source_path: Path) -> tuple[Any, int]:
    """Compatibility helper for callers that require the default mixed stream."""
    normalized = _normalize_for_moss(source_path, "mixdown")
    stream = normalized.streams[0]
    return stream.audio, stream.sample_rate


def _normalize_for_moss(source_path: Path, channel_strategy: str) -> NormalizedAudio:
    """Decode the source while preserving channel intent until transcription."""
    with tempfile.TemporaryDirectory(prefix="asr-local-moss-") as raw_directory:
        return normalize_audio(
            source_path,
            Path(raw_directory),
            channel_strategy=channel_strategy,
        )


class MossTranscriber:
    """Production MOSS adapter for a v2 task spec.

    It receives one full normalized audio stream per task. The v1 30-second
    segment loop is intentionally not used here, so speaker labels remain in
    the model's own long-context output.
    """

    def __init__(self) -> None:
        self._model_adapter = None
        self._model_key: tuple[str, str, str] | None = None
        self._lock = threading.Lock()

    async def transcribe(self, spec: dict[str, Any], attempt_id: str, *, progress=None) -> dict[str, Any]:
        return await asyncio.to_thread(self._transcribe_sync, spec, attempt_id, progress=progress)

    def _transcribe_sync(self, spec: dict[str, Any], attempt_id: str, *, progress=None) -> dict[str, Any]:
        workflow_id = str(spec.get("workflow_id", "unknown"))
        def emit_progress(item: dict[str, Any]) -> None:
            LOGGER.info("moss phase | workflow_id=%s | attempt_id=%s | phase=%s | detail=%s", workflow_id, attempt_id, item.get("phase"), item.get("detail"))
            if progress:
                progress(item)
        transcription = spec["transcription"]
        component = next(item for item in transcription["model_snapshot"]["components"] if item.get("role") == "transcriber")
        model_path = Path(component["resolved_path"])
        source_path = Path(spec["source"]["path"])
        if not model_path.is_dir():
            raise FileNotFoundError(f"MOSS model path does not exist: {model_path}")
        if not source_path.is_file():
            raise FileNotFoundError(f"source audio does not exist: {source_path}")
        emit_progress({"phase": "dependency_importing", "detail": "正在确认 MOSS 推理依赖"})
        try:
            import torch
        except ModuleNotFoundError as exc:
            raise RuntimeError(f"MOSS production adapter dependency missing: {exc.name}") from exc

        emit_progress({"phase": "audio_normalizing", "detail": "正在解码并标准化音频"})
        channel_strategy = _audio_channel_strategy(transcription)
        normalized = _normalize_for_moss(source_path, channel_strategy)
        plan = spec.get("runtime_plan") or {}
        device = torch.device(plan.get("resolved_device", "cpu"))
        dtype = {
            "float32": torch.float32,
            "float16": torch.float16,
            "bfloat16": torch.bfloat16,
        }.get(plan.get("dtype", "float32"), torch.float32)
        with self._lock:
            model_key = (str(model_path), str(device), str(dtype))
            if self._model_adapter is None or self._model_key != model_key:
                from app.models.manager import MossTranscribeDiarizeAdapter

                self._model_adapter = MossTranscribeDiarizeAdapter(
                    path=model_path,
                    torch_module=torch,
                    device=device,
                    dtype=dtype,
                    progress=emit_progress,
                )
                self._model_key = model_key
            output = self._model_adapter.transcribe(
                audio=[(stream.audio, stream.sample_rate) for stream in normalized.streams],
                context=[transcription["prompt_snapshot"]["compiled_text"]] * len(normalized.streams),
                language=[_language(transcription)] * len(normalized.streams),
                return_time_stamps=False,
            )
        if not output:
            raise RuntimeError("MOSS returned no transcription result")
        if len(output) != len(normalized.streams):
            raise RuntimeError(
                "MOSS returned a different number of results than normalized audio streams "
                f"({len(output)} results for {len(normalized.streams)} streams)"
            )
        emit_progress({"phase": "formatting_transcript", "detail": "正在整理转录结果"})
        markdown = format_moss_transcript_streams(
            output,
            [stream.label for stream in normalized.streams],
        )
        markdown = _apply_replacements(markdown, transcription.get("postprocess", {}).get("replacements", []))
        # The supervisor owns immutable artifact paths and atomic promotion.
        # Returning text here prevents a retry from overwriting revision 1.
        return {"kind": "transcript_markdown", "text": markdown}


def format_moss_transcript(segments: list[Any], *, fallback_text: str = "") -> str:
    lines: list[str] = []
    for segment in segments:
        start_ms = int(getattr(segment, "start_ms", 0))
        end_ms = int(getattr(segment, "end_ms", start_ms))
        speaker = str(getattr(segment, "speaker", "Speaker 1"))
        text = str(getattr(segment, "text", "")).strip()
        if not text:
            continue
        lines.append(f"[{_timestamp(start_ms)}-{_timestamp(end_ms)}] {speaker}: {text}")
    if lines:
        return "\n".join(lines) + "\n"
    return (fallback_text or "").strip() + ("\n" if fallback_text.strip() else "")


def format_moss_transcript_streams(results: list[Any], stream_labels: list[str]) -> str:
    """Merge independently transcribed streams by timestamp without losing origin."""
    if len(results) != len(stream_labels):
        raise ValueError("Each MOSS result must have a matching normalized audio stream label")
    entries: list[tuple[int, int, int, int, str, str]] = []
    fallback_lines: list[str] = []
    for stream_index, (result, label) in enumerate(zip(results, stream_labels)):
        segments = getattr(result, "segments", None) or []
        for segment_index, segment in enumerate(segments):
            start_ms = int(getattr(segment, "start_ms", 0))
            end_ms = int(getattr(segment, "end_ms", start_ms))
            speaker = str(getattr(segment, "speaker", "Speaker 1"))
            text = str(getattr(segment, "text", "")).strip()
            if not text:
                continue
            if label != "Mixed":
                speaker = f"{label} / {speaker}"
            entries.append((start_ms, end_ms, stream_index, segment_index, speaker, text))

        if not segments:
            fallback = str(getattr(result, "text", "")).strip()
            if fallback:
                prefix = f"{label}: " if label != "Mixed" else ""
                fallback_lines.append(prefix + fallback)

    if entries:
        entries.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
        lines = [
            f"[{_timestamp(start_ms)}-{_timestamp(end_ms)}] {speaker}: {text}"
            for start_ms, end_ms, _, _, speaker, text in entries
        ]
        return "\n".join(lines) + "\n"
    return "\n".join(fallback_lines) + ("\n" if fallback_lines else "")


def _audio_channel_strategy(transcription: dict[str, Any]) -> str:
    audio = transcription.get("audio") or {}
    channel_strategy = audio.get("channel_strategy", "mixdown")
    if channel_strategy not in {"mixdown", "split_stereo"}:
        raise ValueError(f"Unsupported audio channel strategy: {channel_strategy}")
    return channel_strategy


def _timestamp(milliseconds: int) -> str:
    total_seconds = max(0, milliseconds) // 1000
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _language(transcription: dict[str, Any]) -> str | None:
    language = transcription.get("language", {})
    return language.get("value") if language.get("mode") == "fixed" else None


def _apply_replacements(text: str, replacements: list[dict[str, Any]]) -> str:
    result = text
    for rule in replacements:
        wrong = str(rule.get("wrong", ""))
        correct = str(rule.get("correct", ""))
        if wrong:
            result = result.replace(wrong, correct)
    return result
