from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest import mock

from app.pipeline.chunked_local import ChunkedLocalTranscriber


class ChunkedLocalDeviceTests(unittest.TestCase):
    @mock.patch("app.pipeline.chunked_local.run_job")
    @mock.patch("app.pipeline.chunked_local._validate_snapshot_paths")
    @mock.patch("app.pipeline.chunked_local.ModelManager")
    def test_forced_cpu_plan_reaches_model_manager_and_progress(self, manager_type, _validate, run_job):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            transcript_path = root / "transcript.md"
            transcript_path.write_text("CPU transcript", encoding="utf-8")
            run_job.return_value = {
                "md_path": str(transcript_path),
                "warnings": [],
                "segments": 1,
                "speakers": 1,
                "asr_model": "Qwen/Qwen3-ASR-1.7B",
            }
            progress: list[dict] = []
            manager_type.return_value.device_map.return_value = "cpu"
            spec = {
                "workflow_id": "wf_cpu",
                "source": {"path": str(root / "audio.wav")},
                "output": {"directory": str(root)},
                "runtime_plan": {"resolved_device": "cpu", "dtype": "float32"},
                "transcription": {
                    "language": {"mode": "auto", "value": None},
                    "prompt_input": {"recording_background": "", "hotwords": [], "extra_instruction": ""},
                    "postprocess": {"replacements": [], "keep_fillers": True, "auto_punctuation": True},
                    "model_snapshot": {"components": []},
                },
            }

            with mock.patch("app.pipeline.chunked_local._LOCAL_INFERENCE_LANE") as inference_lane:
                result = ChunkedLocalTranscriber()._transcribe_sync(spec, "attempt_cpu", progress.append)

            manager_type.assert_called_once_with(resolved_device="cpu", dtype="float32")
            inference_lane.acquire.assert_called_once_with()
            inference_lane.release.assert_called_once_with()
            self.assertEqual(progress[0]["phase"], "cpu_waiting")
            self.assertIn("CPU", progress[0]["detail"])
            self.assertEqual(result["text"], "CPU transcript")
            manager_type.return_value.close_local_models.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
