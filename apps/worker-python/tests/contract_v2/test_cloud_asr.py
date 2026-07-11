from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
import unittest
from typing import Any

from app.pipeline.cloud_asr import CloudAsrTranscriber


class CloudAsrTests(unittest.TestCase):
    def test_no_auth_adapter_formats_segments_and_applies_replacements(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                source = root / "audio.wav"
                source.write_bytes(b"audio")
                calls: list[tuple[str, dict, dict]] = []

                def request_fn(url, path, fields, headers):
                    calls.append((url, fields, headers))
                    self.assertEqual(path, source)
                    return {"segments": [{"start": 1.25, "end": 2.5, "speaker": "S01", "text": "ASRLocal"}]}

                spec = {
                    "workflow_id": "wf-cloud",
                    "source": {"path": str(source)},
                    "transcription": {
                        "cloud_profile": {"base_url": "https://example.com/v1", "auth_mode": "none", "model": "asr", "profile_id": "p", "profile_version": 1, "credential_ref": None, "provider_binding_sha256": "binding"},
                        "prompt_snapshot": {"compiled_text": "prompt"},
                        "language": {"mode": "auto", "value": None},
                        "postprocess": {"replacements": [{"wrong": "ASRLocal", "correct": "ASR Local"}]},
                    },
                    "output": {"directory": str(root / "output")},
                }
                result = await CloudAsrTranscriber(request_fn=request_fn).transcribe(spec, "att-cloud")
                self.assertIn("[00:00:01.250-00:00:02.500] S01: ASR Local", result["text"])
                self.assertNotIn("path", result)
                self.assertEqual(calls[0][0], "https://example.com/v1/audio/transcriptions")
                self.assertNotIn("Authorization", calls[0][2])

        asyncio.run(scenario())

    def test_bearer_adapter_requests_secret_just_in_time(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                source = root / "audio.wav"
                source.write_bytes(b"audio")
                captured: dict[str, Any] = {}

                class Provider:
                    async def provide(self, **kwargs):
                        captured["request"] = kwargs
                        return "ephemeral-secret"

                def request_fn(url, path, fields, headers):
                    captured["headers"] = headers
                    return {"text": "cloud transcript"}

                spec = {
                    "workflow_id": "wf-cloud-secret",
                    "source": {"path": str(source)},
                    "transcription": {
                        "cloud_profile": {"base_url": "https://example.com/v1", "auth_mode": "bearer", "model": "asr", "profile_id": "p", "profile_version": 3, "credential_ref": "credential://p", "provider_binding_sha256": "binding"},
                        "prompt_snapshot": {"compiled_text": "prompt"},
                        "language": {"mode": "auto", "value": None},
                        "postprocess": {"replacements": []},
                    },
                    "output": {"directory": str(root / "output")},
                }
                await CloudAsrTranscriber(secret_provider=Provider(), request_fn=request_fn).transcribe(spec, "att-cloud-secret")
                self.assertEqual(captured["request"]["purpose"], "cloud_asr")
                self.assertEqual(captured["headers"]["Authorization"], "Bearer ephemeral-secret")

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
