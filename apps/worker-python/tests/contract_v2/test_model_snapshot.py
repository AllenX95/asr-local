from __future__ import annotations

import unittest

from app.workflow.model_snapshot import resolve_model_components


class ModelSnapshotTests(unittest.TestCase):
    def test_moss_snapshot_has_stable_identity_and_resolved_path(self) -> None:
        components = resolve_model_components("moss_transcribe_diarize")
        self.assertEqual(len(components), 1)
        self.assertEqual(components[0]["role"], "transcriber")
        self.assertEqual(components[0]["model_id"], "OpenMOSS-Team/MOSS-Transcribe-Diarize")
        self.assertTrue(components[0]["resolved_path"])
        self.assertIn("revision", components[0])
        self.assertNotEqual(components[0]["revision"], "unknown")

    def test_legacy_snapshot_includes_diarization_component(self) -> None:
        components = resolve_model_components("qwen3_asr_with_pyannote")
        self.assertEqual([item["role"] for item in components], ["transcriber", "diarization"])


if __name__ == "__main__":
    unittest.main()
