from __future__ import annotations

import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

import numpy as np

from app.models.manager import ModelManager
from app.pipeline.job_runner import (
    build_context,
    normalize_speaker_segments,
    resolve_asr_model_name,
    run_job,
    transcribe_segments,
)
from app.schemas import ReplacementRule, SpeakerSegment, TaskSpec
from pathlib import Path
import tempfile


class PipelineGuardTests(unittest.TestCase):
    def test_integrated_diarization_can_preserve_full_audio_segment(self) -> None:
        segment = SpeakerSegment("segment-0001", "Speaker 1", 0, 120_000, 120_000)
        normalized = normalize_speaker_segments([segment], 120_000, split_long=False)
        self.assertEqual(len(normalized), 1)
        self.assertEqual(normalized[0].duration_ms, 120_000)

    def test_legacy_segment_normalization_still_splits_long_audio(self) -> None:
        segment = SpeakerSegment("segment-0001", "Speaker 1", 0, 120_000, 120_000)
        normalized = normalize_speaker_segments([segment], 120_000)
        self.assertEqual([item.duration_ms for item in normalized], [30_000, 30_000, 30_000, 30_000])

    def test_replacements_are_not_inserted_into_asr_context(self) -> None:
        task = TaskSpec(
            job_id="job",
            source_path=Path("audio.wav"),
            output_dir=Path("outputs"),
            output_file_name="audio.md",
            context_text="customer meeting",
            terms=["Qwen"],
            replacements=[ReplacementRule("ASRLocal", "ASR Local")],
        )
        context = build_context(task)
        self.assertIn("customer meeting", context)
        self.assertIn("Qwen", context)
        self.assertNotIn("ASR Local", context)

    def test_task_result_model_identity_is_qwen(self) -> None:
        task = TaskSpec(
            job_id="job",
            source_path=Path("audio.wav"),
            output_dir=Path("outputs"),
            output_file_name="audio.md",
            local_asr_model="qwen3_asr_1_7b",
        )
        manager = ModelManager()
        self.assertEqual(resolve_asr_model_name(task, manager), "Qwen/Qwen3-ASR-1.7B")

    def test_segment_failure_keeps_placeholder_and_warning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task = TaskSpec(
                job_id="job",
                source_path=root / "audio.wav",
                output_dir=root,
                output_file_name="audio.md",
                local_asr_model="qwen3_asr_1_7b",
                force_external_diarization=True,
            )
            segments = [SpeakerSegment("segment-0001", "SPEAKER_0", 0, 1_000, 1_000)]
            warnings: list[dict] = []
            manager = Mock(spec=ModelManager)
            manager.get_local_asr_model.return_value = object()
            manager.local_asr_model_name.return_value = "Qwen/Qwen3-ASR-1.7B"
            manager.local_asr_batch_size.return_value = 4
            manager.local_asr_max_batch_size.return_value = 8
            with patch("app.pipeline.job_runner.transcribe_audio_batch", side_effect=RuntimeError("synthetic ASR failure")):
                result = transcribe_segments(
                    task,
                    audio=[0.0] * 16_000,
                    sample_rate=16_000,
                    speaker_segments=segments,
                    total_ms=1_000,
                    job_dir=root,
                    model_manager=manager,
                    warnings=warnings,
                )
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0].text, "")
            self.assertEqual(warnings[0]["code"], "ASR_SEGMENT_FAILED")

    def test_null_model_result_has_explicit_warning_code(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task = TaskSpec(
                job_id="job-null-result",
                source_path=root / "audio.wav",
                output_dir=root,
                output_file_name="audio.md",
                local_asr_model="qwen3_asr_1_7b",
                enable_speaker_diarization=False,
            )
            segments = [SpeakerSegment("segment-0001", "Speaker 1", 0, 1_000, 1_000)]
            warnings: list[dict] = []
            manager = Mock(spec=ModelManager)
            fake_model = Mock()
            fake_model.transcribe.return_value = [None]
            manager.get_local_asr_model.return_value = fake_model
            manager.local_asr_model_name.return_value = "Qwen/Qwen3-ASR-1.7B"
            manager.local_asr_batch_size.return_value = 4
            manager.local_asr_max_batch_size.return_value = 8
            result = transcribe_segments(
                task,
                audio=[0.0] * 16_000,
                sample_rate=16_000,
                speaker_segments=segments,
                total_ms=1_000,
                job_dir=root,
                model_manager=manager,
                warnings=warnings,
            )

            self.assertEqual(result[0].text, "")
            self.assertEqual(warnings[0]["code"], "ASR_NULL_RESULT")

    def test_transcript_output_is_stably_sorted_by_timeline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task = TaskSpec(
                job_id="job-sort",
                source_path=root / "audio.wav",
                output_dir=root,
                output_file_name="audio.md",
                local_asr_model="qwen3_asr_1_7b",
                enable_speaker_diarization=False,
            )
            manager = Mock(spec=ModelManager)
            manager.get_local_asr_model.return_value = object()
            manager.local_asr_model_name.return_value = "Qwen/Qwen3-ASR-1.7B"
            manager.local_asr_batch_size.return_value = 4
            manager.local_asr_max_batch_size.return_value = 8
            segments = [
                SpeakerSegment("segment-0002", "Speaker 1", 1_000, 2_000, 1_000),
                SpeakerSegment("segment-0001", "Speaker 1", 0, 1_000, 1_000),
            ]

            def fake_transcribe(*, batch_inputs, **_kwargs):
                return {
                    index: SimpleNamespace(text=segment.segment_id, language="en")
                    for index, segment, _audio in batch_inputs
                }

            with patch("app.pipeline.job_runner.transcribe_audio_batch", side_effect=fake_transcribe):
                result = transcribe_segments(
                    task,
                    audio=[0.0] * 32_000,
                    sample_rate=16_000,
                    speaker_segments=segments,
                    total_ms=2_000,
                    job_dir=root,
                    model_manager=manager,
                )

            self.assertEqual([item.segment_id for item in result], ["segment-0001", "segment-0002"])

    def test_transcript_output_preserves_stable_order_for_equal_timestamps(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task = TaskSpec(
                job_id="job-equal-sort",
                source_path=root / "audio.wav",
                output_dir=root,
                output_file_name="audio.md",
                local_asr_model="qwen3_asr_1_7b",
                enable_speaker_diarization=False,
            )
            manager = Mock(spec=ModelManager)
            manager.get_local_asr_model.return_value = object()
            manager.local_asr_model_name.return_value = "Qwen/Qwen3-ASR-1.7B"
            manager.local_asr_batch_size.return_value = 4
            manager.local_asr_max_batch_size.return_value = 8
            segments = [
                SpeakerSegment("segment-b", "Speaker 1", 0, 1_000, 1_000),
                SpeakerSegment("segment-a", "Speaker 1", 0, 1_000, 1_000),
                SpeakerSegment("segment-c", "Speaker 1", 0, 1_000, 1_000),
            ]

            def fake_transcribe(*, batch_inputs, **_kwargs):
                return {
                    index: SimpleNamespace(text=segment.segment_id, language="en")
                    for index, segment, _audio in batch_inputs
                }

            with patch("app.pipeline.job_runner.transcribe_audio_batch", side_effect=fake_transcribe):
                result = transcribe_segments(
                    task,
                    audio=[0.0] * 16_000,
                    sample_rate=16_000,
                    speaker_segments=segments,
                    total_ms=1_000,
                    job_dir=root,
                    model_manager=manager,
                )

            self.assertEqual(
                [item.segment_id for item in result],
                ["segment-b", "segment-a", "segment-c"],
            )

    def test_local_run_job_smoke_covers_pyannote_release_qwen_batch_and_export(self) -> None:
        class FakeTensor:
            def unsqueeze(self, _dimension):
                return self

        class FakeTorch:
            def from_numpy(self, _audio):
                return FakeTensor()

        class FakeAnnotation:
            def itertracks(self, yield_label=True):
                self_yield_label = yield_label
                if not self_yield_label:
                    return iter(())
                return iter(
                    [
                        (SimpleNamespace(start=0.0, end=1.0), None, "SPEAKER_0"),
                        (SimpleNamespace(start=1.0, end=2.0), None, "SPEAKER_1"),
                    ]
                )

        class FakePyannote:
            def __call__(self, _payload):
                return SimpleNamespace(speaker_diarization=FakeAnnotation())

        events: list[str] = []
        progress_updates: list[dict] = []

        class FakeQwen:
            def transcribe(self, *, audio, context, language, return_time_stamps):
                events.append(f"qwen_batch_{len(audio)}")
                return [
                    SimpleNamespace(text=f"recognized-{index}", language="en")
                    for index, _item in enumerate(audio, start=1)
                ]

        class FakeManager:
            torch = FakeTorch()

            def refresh_config(self):
                pass

            def runtime_summary(self, include_device=True):
                return {"device_map": "cpu", "torch_dtype": "float32"}

            def local_asr_model_name(self):
                return "Qwen/Qwen3-ASR-1.7B"

            def local_asr_uses_integrated_diarization(self):
                return False

            def get_pyannote_pipeline(self):
                events.append("pyannote_load")
                return FakePyannote()

            def close_pyannote_pipeline(self):
                events.append("pyannote_release")

            def get_local_asr_model(self):
                events.append("qwen_load")
                return FakeQwen()

            def local_asr_batch_size(self):
                return 4

            def local_asr_max_batch_size(self):
                return 8

            def clear_cuda_cache(self):
                events.append("cuda_cache_clear")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.wav"
            source.write_bytes(b"fake")
            output_dir = root / "output"
            job_workspace = root / "job-workspace"
            payload = {
                "job_id": "job-smoke",
                "source_path": str(source),
                "output_dir": str(output_dir),
                "output_file_name": "transcript.md",
                "asr_backend": "local",
                "enable_speaker_diarization": True,
                "force_external_diarization": True,
                "job_workspace_dir": str(job_workspace),
            }

            with patch(
                "app.pipeline.job_runner.load_and_normalize_audio",
                return_value=(np.zeros(32_000, dtype=np.float32), 16_000, "fake"),
            ), patch("app.pipeline.job_runner.cleanup_job_cache"):
                result = run_job(
                    payload,
                    emit=lambda _job_id, update: progress_updates.append(update),
                    model_manager=FakeManager(),
                )

            self.assertEqual(result["segments"], 2)
            self.assertTrue(Path(result["md_path"]).is_file())
            self.assertTrue(Path(result["transcript_json_path"]).is_file())
            self.assertLess(events.index("pyannote_release"), events.index("qwen_load"))
            self.assertIn("qwen_batch_2", events)
            segmenting = next(update for update in progress_updates if update["stage"] == "segmenting")
            self.assertEqual(segmenting["normalized_segment_count"], 2)
            transcribing = [update for update in progress_updates if update["stage"] == "transcribing"]
            self.assertEqual(
                [(update["current_segment_index"], update["segment_count"]) for update in transcribing],
                [(1, 2), (2, 2)],
            )


if __name__ == "__main__":
    unittest.main()
