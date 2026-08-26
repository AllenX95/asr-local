from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
import threading
import time
import unittest
from typing import Any

from app.pipeline.cloud_asr import CloudAttemptCancelled, CloudAsrTranscriber


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

    def test_network_thread_cancel_is_deferred_and_close_has_bounded_wait(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                source = root / "audio.wav"
                source.write_bytes(b"audio")
                started = threading.Event()
                release = threading.Event()

                def request_fn(_url, _path, _fields, _headers):
                    started.set()
                    release.wait(timeout=3)
                    return {"text": "late result"}

                spec = {
                    "workflow_id": "wf-cloud-cancel",
                    "source": {"path": str(source)},
                    "transcription": {
                        "cloud_profile": {"base_url": "https://example.com/v1", "auth_mode": "none", "model": "asr"},
                        "prompt_snapshot": {"compiled_text": "prompt"},
                        "language": {"mode": "auto", "value": None},
                        "postprocess": {"replacements": []},
                    },
                    "output": {"directory": str(root / "output")},
                }
                adapter = CloudAsrTranscriber(request_fn=request_fn, close_wait_seconds=0.05)
                task = asyncio.create_task(adapter.transcribe(spec, "attempt-cloud-cancel"))
                await asyncio.to_thread(started.wait, 2)
                self.assertEqual(adapter.cancel_attempt("wf-cloud-cancel", "attempt-cloud-cancel"), 1)
                await asyncio.to_thread(adapter.close)
                self.assertTrue(adapter._closed)
                self.assertFalse(task.done())
                release.set()
                with self.assertRaises(CloudAttemptCancelled):
                    await task

        asyncio.run(scenario())

    def test_blocking_daemon_request_does_not_extend_asyncio_run_shutdown(self) -> None:
        started = threading.Event()
        release = threading.Event()

        def request_fn(_url, _path, _fields, _headers):
            started.set()
            release.wait(timeout=1.2)
            return {"text": "late result"}

        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                source = root / "audio.wav"
                source.write_bytes(b"audio")
                spec = {
                    "workflow_id": "wf-cloud-daemon",
                    "source": {"path": str(source)},
                    "transcription": {
                        "cloud_profile": {"base_url": "https://example.com/v1", "auth_mode": "none", "model": "asr"},
                        "prompt_snapshot": {"compiled_text": "prompt"},
                        "language": {"mode": "auto", "value": None},
                        "postprocess": {"replacements": []},
                    },
                    "output": {"directory": str(root / "output")},
                }
                adapter = CloudAsrTranscriber(request_fn=request_fn, close_wait_seconds=0.05)
                task = asyncio.create_task(adapter.transcribe(spec, "attempt-cloud-daemon"))
                await asyncio.to_thread(started.wait, 2)
                self.assertEqual(adapter.cancel_attempt("wf-cloud-daemon", "attempt-cloud-daemon"), 1)
                started_close = time.monotonic()
                adapter.close()
                self.assertLess(time.monotonic() - started_close, 0.5)
                self.assertTrue(adapter._closed)
                self.assertEqual(adapter._active_sync_calls, 1)
                # Do not release the request here: asyncio.run() must return
                # while this daemon native call is still blocked.
                del task

        started_run = time.monotonic()
        asyncio.run(scenario())
        self.assertLess(time.monotonic() - started_run, 1.0)
        release.set()


if __name__ == "__main__":
    unittest.main()
