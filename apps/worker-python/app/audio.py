from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import os
from pathlib import Path
import shutil
import subprocess
from typing import Literal

import numpy as np


TARGET_SR = 16_000
AudioChannelStrategy = Literal["mixdown", "split_stereo"]


@dataclass(slots=True)
class NormalizedAudioStream:
    label: str
    audio: np.ndarray
    sample_rate: int
    wav_path: Path


@dataclass(slots=True)
class NormalizedAudio:
    source_path: Path
    channel_strategy: AudioChannelStrategy
    resampler: str
    streams: list[NormalizedAudioStream]


def load_and_normalize_audio(source_path: Path, normalized_wav_path: Path) -> tuple[np.ndarray, int, str]:
    """Compatibility wrapper for callers that require one mixed mono stream."""
    normalized = _normalize_audio(
        source_path,
        channel_strategy="mixdown",
        output_paths=[normalized_wav_path],
    )
    stream = normalized.streams[0]
    return stream.audio, stream.sample_rate, "ffmpeg"


def normalize_audio(
    source_path: Path,
    output_dir: Path,
    *,
    channel_strategy: AudioChannelStrategy = "mixdown",
) -> NormalizedAudio:
    """Create model-ready PCM WAV stream(s) while preserving the original input.

    ``mixdown`` is the normal speech-recognition path. ``split_stereo`` keeps
    left and right stereo tracks independent so dual-mono recorder inputs can
    be transcribed separately by pipelines that support it.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    if channel_strategy == "mixdown":
        paths = [output_dir / "normalized.wav"]
    elif channel_strategy == "split_stereo":
        paths = [output_dir / "channel-1.wav", output_dir / "channel-2.wav"]
    else:
        raise ValueError(f"Unsupported audio channel strategy: {channel_strategy}")

    return _normalize_audio(
        source_path,
        channel_strategy=channel_strategy,
        output_paths=paths,
    )


def _normalize_audio(
    source_path: Path,
    *,
    channel_strategy: AudioChannelStrategy,
    output_paths: list[Path],
) -> NormalizedAudio:
    if not source_path.is_file():
        raise FileNotFoundError(f"audio source does not exist: {source_path}")

    try:
        import soundfile as sf
    except ModuleNotFoundError as exc:
        raise RuntimeError("Audio normalization requires the soundfile package.") from exc

    for output_path in output_paths:
        output_path.parent.mkdir(parents=True, exist_ok=True)

    ffmpeg = resolve_ffmpeg_executable()
    resampler, resample_filter = _resampler_filter(ffmpeg)
    if channel_strategy == "mixdown":
        command = _ffmpeg_prefix(ffmpeg, source_path) + [
            "-map",
            "0:a:0",
            "-vn",
            "-sn",
            "-dn",
            "-af",
            resample_filter,
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            "-f",
            "wav",
            str(output_paths[0]),
        ]
        _run_ffmpeg(command, source_path, output_paths)
        streams = [_read_normalized_stream(sf, output_paths[0], "Mixed")]
    elif channel_strategy == "split_stereo":
        command = _ffmpeg_prefix(ffmpeg, source_path) + [
            "-filter_complex",
            (
                f"[0:a:0]pan=mono|c0=c0,{resample_filter}[left];"
                f"[0:a:0]pan=mono|c0=c1,{resample_filter}[right]"
            ),
            "-map",
            "[left]",
            "-c:a",
            "pcm_s16le",
            "-f",
            "wav",
            str(output_paths[0]),
            "-map",
            "[right]",
            "-c:a",
            "pcm_s16le",
            "-f",
            "wav",
            str(output_paths[1]),
        ]
        _run_ffmpeg(command, source_path, output_paths)
        streams = [
            _read_normalized_stream(sf, output_paths[0], "Channel 1"),
            _read_normalized_stream(sf, output_paths[1], "Channel 2"),
        ]
        _validate_distinct_stereo_channels(streams)
    else:  # pragma: no cover - validated by normalize_audio
        raise ValueError(f"Unsupported audio channel strategy: {channel_strategy}")

    return NormalizedAudio(
        source_path=source_path,
        channel_strategy=channel_strategy,
        resampler=resampler,
        streams=streams,
    )


def _ffmpeg_prefix(ffmpeg: str, source_path: Path) -> list[str]:
    return [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-i",
        str(source_path),
    ]


def _run_ffmpeg(command: list[str], source_path: Path, output_paths: list[Path]) -> None:
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
            text=True,
        )
    except OSError as exc:
        raise RuntimeError(f"Failed to start ffmpeg at {command[0]}: {exc}") from exc

    if completed.returncode != 0 or any(not path.is_file() for path in output_paths):
        detail = (completed.stderr or "").strip()
        raise RuntimeError(
            f"ffmpeg could not decode audio source {source_path}: {detail or f'exit code {completed.returncode}'}"
        )


def _read_normalized_stream(sf, wav_path: Path, label: str) -> NormalizedAudioStream:
    audio, sample_rate = sf.read(wav_path, always_2d=False)
    audio = _ensure_float32_mono(audio)
    if sample_rate != TARGET_SR:
        raise RuntimeError(
            f"ffmpeg normalization returned {sample_rate} Hz instead of {TARGET_SR} Hz for {wav_path}"
        )
    return NormalizedAudioStream(
        label=label,
        audio=audio,
        sample_rate=sample_rate,
        wav_path=wav_path,
    )


def _validate_distinct_stereo_channels(streams: list[NormalizedAudioStream]) -> None:
    left, right = streams
    if len(left.audio) != len(right.audio):
        raise RuntimeError("ffmpeg returned mismatched split-channel lengths.")
    if not np.any(np.abs(right.audio) > 1e-6):
        raise ValueError("split_stereo requires a stereo source with an audible right channel.")
    if np.allclose(left.audio, right.audio, rtol=1e-5, atol=1e-6):
        raise ValueError("split_stereo requires two distinct stereo channels; use mixdown for mono audio.")


@lru_cache(maxsize=8)
def _resampler_filter(ffmpeg: str) -> tuple[str, str]:
    soxr_filter = f"aresample={TARGET_SR}:resampler=soxr:precision=28:cheby=0"
    try:
        completed = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=48000:cl=mono",
                "-t",
                "0.01",
                "-af",
                soxr_filter,
                "-f",
                "null",
                "-",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            text=True,
        )
    except OSError:
        completed = None

    if completed is not None and completed.returncode == 0:
        return "soxr", soxr_filter
    return "swr", f"aresample={TARGET_SR}:filter_size=64:phase_shift=10:cutoff=0.97"


def resolve_ffmpeg_executable() -> str:
    """Locate the configured, system, or packaged ffmpeg executable."""
    configured = os.environ.get("ASR_LOCAL_FFMPEG", "").strip()
    if configured:
        resolved = shutil.which(configured) or (configured if Path(configured).is_file() else None)
        if resolved:
            return resolved
        raise RuntimeError(
            f"ASR_LOCAL_FFMPEG does not point to an executable ffmpeg binary: {configured}"
        )

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        return ffmpeg

    try:
        import imageio_ffmpeg
    except ModuleNotFoundError:
        imageio_ffmpeg = None

    if imageio_ffmpeg is not None:
        candidate = imageio_ffmpeg.get_ffmpeg_exe()
        if candidate and Path(candidate).is_file():
            return candidate

    raise RuntimeError(
        "Audio normalization requires the worker inference runtime, "
        "install ffmpeg on PATH, or set ASR_LOCAL_FFMPEG to its executable path."
    )


def audio_duration_ms(audio: np.ndarray, sample_rate: int) -> int:
    return int(round((len(audio) / sample_rate) * 1000))


def slice_audio(audio: np.ndarray, sample_rate: int, start_ms: int, end_ms: int) -> np.ndarray:
    start_index = max(0, int(round((start_ms / 1000) * sample_rate)))
    end_index = min(len(audio), int(round((end_ms / 1000) * sample_rate)))
    if end_index <= start_index:
        return np.zeros(0, dtype=np.float32)
    return np.asarray(audio[start_index:end_index], dtype=np.float32)


def _ensure_float32_mono(audio: np.ndarray) -> np.ndarray:
    if audio.ndim == 2:
        audio = audio.mean(axis=1)
    return np.asarray(audio, dtype=np.float32)
