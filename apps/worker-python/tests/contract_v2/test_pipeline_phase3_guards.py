from __future__ import annotations

import unittest

from app.pipeline.job_runner import build_context, normalize_speaker_segments
from app.schemas import ReplacementRule, SpeakerSegment, TaskSpec
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
