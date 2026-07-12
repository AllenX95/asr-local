from __future__ import annotations

import unittest

from app.workflow.model_snapshot import resolve_model_components


class ModelSnapshotTests(unittest.TestCase):
    def test_qwen_snapshot_includes_diarization_component(self) -> None:
        components = resolve_model_components("pyannote_qwen3_asr")
        self.assertEqual([item["role"] for item in components], ["transcriber", "diarization"])

    def test_removed_profiles_have_no_snapshot(self) -> None:
        for profile in ("pyannote_moss_asr", "moss_transcribe_diarize", "qwen3_asr_with_pyannote"):
            with self.subTest(profile=profile):
                self.assertEqual(resolve_model_components(profile), [])


if __name__ == "__main__":
    unittest.main()
