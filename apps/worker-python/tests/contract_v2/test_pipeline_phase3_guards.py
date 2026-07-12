from __future__ import annotations

import unittest
from unittest.mock import patch

from app.models.manager import ModelManager
from app.pipeline.job_runner import build_context, normalize_speaker_segments, resolve_asr_model_name, transcribe_segments
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
            terms=["MOSS"],
            replacements=[ReplacementRule("ASRLocal", "ASR Local")],
        )
        context = build_context(task)
        self.assertIn("customer meeting", context)
        self.assertIn("MOSS", context)
        self.assertNotIn("ASR Local", context)

    def test_task_result_model_identity_uses_task_manager_override(self) -> None:
        task = TaskSpec(
            job_id="job",
            source_path=Path("audio.wav"),
            output_dir=Path("outputs"),
            output_file_name="audio.md",
            local_asr_model="moss_transcribe_diarize",
        )
        manager = ModelManager(active_local_asr_model_override="moss_transcribe_diarize")
        self.assertEqual(resolve_asr_model_name(task, manager), "OpenMOSS-Team/MOSS-Transcribe-Diarize")

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
            with patch("app.pipeline.job_runner.transcribe_audio_batch", side_effect=RuntimeError("synthetic ASR failure")):
                result = transcribe_segments(
                    task,
                    audio=[0.0] * 16_000,
                    sample_rate=16_000,
                    speaker_segments=segments,
                    total_ms=1_000,
                    job_dir=root,
                    model_manager=ModelManager(active_local_asr_model_override="qwen3_asr_1_7b"),
                    warnings=warnings,
                )
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0].text, "")
            self.assertEqual(warnings[0]["code"], "ASR_SEGMENT_FAILED")


if __name__ == "__main__":
    unittest.main()
