from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import numpy as np


TARGET_SR = 16_000


def load_and_normalize_audio(source_path: Path, normalized_wav_path: Path) -> tuple[np.ndarray, int, str]:
    normalized_wav_path.parent.mkdir(parents=True, exist_ok=True)

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        try:
            import imageio_ffmpeg
        except ModuleNotFoundError:
            imageio_ffmpeg = None

        if imageio_ffmpeg is not None:
            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()

    if ffmpeg:
        import soundfile as sf

        command = [
            ffmpeg,
            "-y",
            "-i",
            str(source_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(TARGET_SR),
            str(normalized_wav_path),
        ]
        completed = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if completed.returncode == 0 and normalized_wav_path.exists():
            audio, sample_rate = sf.read(normalized_wav_path, always_2d=False)
            audio = _ensure_float32_mono(audio)
            return audio, sample_rate, "ffmpeg"

    try:
        import librosa
        import soundfile as sf
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Audio decoding requires ffmpeg, or both librosa and soundfile installed."
        ) from exc

    audio, sample_rate = librosa.load(
        str(source_path),
        sr=TARGET_SR,
        mono=True,
    )
    audio = np.asarray(audio, dtype=np.float32)
    sf.write(normalized_wav_path, audio, sample_rate)
    return audio, sample_rate, "librosa"


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
