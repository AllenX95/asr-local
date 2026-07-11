from __future__ import annotations

from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.audio import NormalizedAudio, NormalizedAudioStream
from app.pipeline.moss_v2 import MossTranscriber, format_moss_transcript, format_moss_transcript_streams


class MossV2AdapterTests(unittest.TestCase):
    def test_formatter_preserves_model_segment_order_and_timestamps(self) -> None:
        text = format_moss_transcript([
            SimpleNamespace(start_ms=1000, end_ms=2500, speaker="S01", text="Hello"),
            SimpleNamespace(start_ms=3000, end_ms=4000, speaker="S02", text="World"),
        ])
        self.assertEqual(text, "[00:00:01-00:00:02] S01: Hello\n[00:00:03-00:00:04] S02: World\n")

    def test_formatter_uses_fallback_when_model_has_no_segments(self) -> None:
        self.assertEqual(format_moss_transcript([], fallback_text="raw output"), "raw output\n")

    def test_split_stream_formatter_merges_timestamps_and_preserves_channel_labels(self) -> None:
        text = format_moss_transcript_streams(
            [
                SimpleNamespace(
                    segments=[SimpleNamespace(start_ms=3000, end_ms=4000, speaker="S01", text="left")],
                    text="",
                ),
                SimpleNamespace(
                    segments=[SimpleNamespace(start_ms=1000, end_ms=2000, speaker="S02", text="right")],
                    text="",
                ),
            ],
            ["Channel 1", "Channel 2"],
        )
        self.assertEqual(
            text,
            "[00:00:01-00:00:02] Channel 2 / S02: right\n"
            "[00:00:03-00:00:04] Channel 1 / S01: left\n",
        )

    def test_split_stream_formatter_rejects_missing_stream_result(self) -> None:
        with self.assertRaisesRegex(ValueError, "matching normalized audio stream"):
            format_moss_transcript_streams([SimpleNamespace(segments=[], text="")], ["Channel 1", "Channel 2"])

    def test_transcriber_sends_each_split_channel_to_moss(self) -> None:
        class FakeAdapter:
            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []

            def transcribe(self, **kwargs):
                self.calls.append(kwargs)
                return [
                    SimpleNamespace(segments=[SimpleNamespace(start_ms=0, end_ms=500, speaker="S01", text="left")], text=""),
                    SimpleNamespace(segments=[SimpleNamespace(start_ms=1000, end_ms=1500, speaker="S02", text="right")], text=""),
                ]

        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            source = directory / "source.wav"
            source.touch()
            model_path = directory / "model"
            model_path.mkdir()
            normalized = NormalizedAudio(
                source_path=source,
                channel_strategy="split_stereo",
                resampler="soxr",
                streams=(
                    NormalizedAudioStream("Channel 1", object(), 16000, directory / "left.wav"),
                    NormalizedAudioStream("Channel 2", object(), 16000, directory / "right.wav"),
                ),
            )
            fake_adapter = FakeAdapter()
            transcriber = MossTranscriber()
            transcriber._model_adapter = fake_adapter

            import torch

            transcriber._model_key = (str(model_path), "cpu", str(torch.float32))
            spec = {
                "source": {"path": str(source)},
                "transcription": {
                    "audio": {"channel_strategy": "split_stereo"},
                    "model_snapshot": {"components": [{"role": "transcriber", "resolved_path": str(model_path)}]},
                    "prompt_snapshot": {"compiled_text": "context"},
                    "language": {"mode": "auto", "value": None},
                    "postprocess": {"replacements": []},
                },
                "runtime_plan": {"resolved_device": "cpu", "dtype": "float32"},
            }
            with patch("app.pipeline.moss_v2._normalize_for_moss", return_value=normalized):
                result = transcriber._transcribe_sync(spec, "att_1")

        self.assertEqual(fake_adapter.calls[0]["audio"], [(normalized.streams[0].audio, 16000), (normalized.streams[1].audio, 16000)])
        self.assertEqual(fake_adapter.calls[0]["context"], ["context", "context"])
        self.assertIn("Channel 1 / S01: left", result["text"])
        self.assertIn("Channel 2 / S02: right", result["text"])


if __name__ == "__main__":
    unittest.main()
