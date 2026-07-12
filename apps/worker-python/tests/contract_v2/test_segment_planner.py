from __future__ import annotations

import unittest

from app.pipeline.segment_planner import plan_segments
from app.pipeline.segment_types import DiarizationTurn


class SegmentPlannerTests(unittest.TestCase):
    def test_sorts_clips_and_assigns_stable_ids(self) -> None:
        planned = plan_segments(
            [
                DiarizationTurn("B", 8_000, 12_000),
                DiarizationTurn("A", -500, 2_000),
            ],
            10_000,
            padding_ms=200,
        )
        self.assertEqual([item.segment_id for item in planned], ["segment-0001", "segment-0002"])
        self.assertEqual((planned[0].start_ms, planned[0].end_ms), (0, 2_000))
        self.assertEqual((planned[0].input_start_ms, planned[0].input_end_ms), (0, 2_200))
        self.assertEqual((planned[1].start_ms, planned[1].end_ms), (8_000, 10_000))

    def test_merges_same_speaker_turns_with_small_gap(self) -> None:
        planned = plan_segments(
            [
                DiarizationTurn("A", 0, 1_000),
                DiarizationTurn("A", 1_200, 2_000),
            ],
            2_000,
            merge_gap_ms=300,
            padding_ms=0,
        )
        self.assertEqual(len(planned), 1)
        self.assertEqual((planned[0].start_ms, planned[0].end_ms), (0, 2_000))

    def test_does_not_merge_different_speakers(self) -> None:
        planned = plan_segments(
            [DiarizationTurn("A", 0, 1_000), DiarizationTurn("B", 1_100, 2_000)],
            2_000,
            padding_ms=0,
        )
        self.assertEqual([(item.speaker, item.start_ms, item.end_ms) for item in planned], [
            ("A", 0, 1_000),
            ("B", 1_100, 2_000),
        ])

    def test_splits_long_turns_without_changing_speaker(self) -> None:
        planned = plan_segments(
            [DiarizationTurn("A", 0, 65_000)],
            65_000,
            max_segment_ms=30_000,
            padding_ms=200,
        )
        self.assertEqual([(item.start_ms, item.end_ms) for item in planned], [
            (0, 30_000),
            (30_000, 60_000),
            (60_000, 65_000),
        ])
        self.assertTrue(all(item.speaker == "A" for item in planned))
        self.assertEqual((planned[1].input_start_ms, planned[1].input_end_ms), (29_800, 60_200))

    def test_invalid_and_zero_length_turns_are_ignored(self) -> None:
        planned = plan_segments(
            [DiarizationTurn("A", 3_000, 3_000), DiarizationTurn("B", 5_000, 1_000)],
            10_000,
        )
        self.assertEqual(planned, [])

    def test_rejects_invalid_parameters(self) -> None:
        with self.assertRaises(ValueError):
            plan_segments([], 10_000, max_segment_ms=0)


if __name__ == "__main__":
    unittest.main()
