from __future__ import annotations

import unittest
from unittest.mock import patch

from app.supervisor.server import capabilities, resolve_pipeline_mode


class PipelineModeTests(unittest.TestCase):
    def test_capabilities_advertise_the_implemented_cloud_asr_pipeline(self) -> None:
        self.assertIn("cloud_asr", capabilities()["pipeline_profiles"])

    def test_explicit_modes_are_preserved(self) -> None:
        self.assertEqual(resolve_pipeline_mode("fake"), "fake")
        self.assertEqual(resolve_pipeline_mode("production"), "production")

    def test_auto_selects_production_only_when_native_gate_is_ready(self) -> None:
        ready_snapshot = {
            "models": {"moss_transcribe_diarize": {"exists": True}},
            "optional_modules": {"torch": True, "transformers": True},
        }
        with patch("app.runtime.env.environment_snapshot", return_value=ready_snapshot), patch(
            "app.supervisor.server.importlib.util.find_spec", return_value=object()
        ):
            self.assertEqual(resolve_pipeline_mode("auto"), "production")

    def test_auto_falls_back_to_fake_when_native_gate_is_incomplete(self) -> None:
        snapshot = {
            "models": {"moss_transcribe_diarize": {"exists": True}},
            "optional_modules": {"torch": True, "transformers": False},
        }
        with patch("app.runtime.env.environment_snapshot", return_value=snapshot):
            self.assertEqual(resolve_pipeline_mode("auto"), "fake")

    def test_unknown_mode_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            resolve_pipeline_mode("sideways")


if __name__ == "__main__":
    unittest.main()
