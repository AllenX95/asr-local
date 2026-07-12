from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch


def _ffmpeg_for_fixture() -> str | None:
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg
    try:
        import imageio_ffmpeg
    except ModuleNotFoundError:
        return None
    return imageio_ffmpeg.get_ffmpeg_exe()


FFMPEG = _ffmpeg_for_fixture()


@unittest.skipUnless(FFMPEG, "ffmpeg is required for audio normalization tests")
class AudioNormalizationTests(unittest.TestCase):
    def _create_tone(self, directory: Path, file_name: str, codec: str) -> Path:
        output = directory / file_name
        subprocess.run(
            [
                FFMPEG,
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=1000:sample_rate=44100:duration=1",
                "-c:a",
                codec,
                str(output),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return output

    def _create_stereo_tones(self, directory: Path) -> Path:
        output = directory / "dual-channel.wav"
        subprocess.run(
            [
                FFMPEG,
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:sample_rate=44100:duration=1",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=880:sample_rate=44100:duration=1",
                "-filter_complex",
                "[0:a][1:a]join=inputs=2:channel_layout=stereo",
                "-c:a",
                "pcm_s16le",
                str(output),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return output

    def test_mainstream_containers_normalize_to_model_input(self) -> None:
        from app.audio import TARGET_SR, load_and_normalize_audio

        formats = (
            ("tone.wav", "pcm_s16le"),
            ("tone.mp3", "libmp3lame"),
            ("tone.m4a", "aac"),
            ("tone.flac", "flac"),
            ("tone.ogg", "libvorbis"),
            ("tone.opus", "libopus"),
            ("tone.webm", "libopus"),
        )
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            for index, (file_name, codec) in enumerate(formats):
                with self.subTest(container=file_name):
                    source = self._create_tone(directory, file_name, codec)
                    normalized = directory / f"normalized-{index}.wav"
                    audio, sample_rate, backend = load_and_normalize_audio(source, normalized)

                    self.assertEqual(backend, "ffmpeg")
                    self.assertEqual(sample_rate, TARGET_SR)
                    self.assertEqual(getattr(audio, "ndim", None), 1)
                    self.assertTrue(normalized.is_file())
                    self.assertGreater(len(audio), 15_000)
                    self.assertLess(len(audio), 17_000)

    def test_split_stereo_preserves_two_independent_model_inputs(self) -> None:
        from app.audio import TARGET_SR, normalize_audio

        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            source = self._create_stereo_tones(directory)
            normalized = normalize_audio(
                source,
                directory / "normalized",
                channel_strategy="split_stereo",
            )

        self.assertEqual(normalized.channel_strategy, "split_stereo")
        self.assertEqual(len(normalized.streams), 2)
        self.assertEqual([stream.label for stream in normalized.streams], ["Channel 1", "Channel 2"])
        self.assertTrue(all(stream.sample_rate == TARGET_SR for stream in normalized.streams))
        self.assertTrue(all(stream.audio.ndim == 1 for stream in normalized.streams))
        self.assertFalse((normalized.streams[0].audio == normalized.streams[1].audio).all())

    def test_split_stereo_rejects_a_mono_source(self) -> None:
        from app.audio import normalize_audio

        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            source = self._create_tone(directory, "mono.wav", "pcm_s16le")
            with self.assertRaisesRegex(ValueError, "stereo"):
                normalize_audio(source, directory / "normalized", channel_strategy="split_stereo")

    def test_packaged_ffmpeg_is_available_without_a_system_path_entry(self) -> None:
        from app.audio import resolve_ffmpeg_executable

        with patch.dict(os.environ, {"ASR_LOCAL_FFMPEG": ""}, clear=False):
            with patch("app.audio.shutil.which", return_value=None):
                executable = Path(resolve_ffmpeg_executable())

        self.assertTrue(executable.is_file())


if __name__ == "__main__":
    unittest.main()
