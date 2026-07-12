from __future__ import annotations

import asyncio
import unittest

from app.pipeline.router import ProfileRoutingTranscriber


class _Adapter:
    def __init__(self, name):
        self.name = name
        self.calls = []

    async def transcribe(self, spec, attempt_id, *, progress=None):
        self.calls.append((spec["transcription"]["pipeline_profile"], attempt_id, progress is not None))
        return {"kind": "transcript_markdown", "text": self.name}


class ChunkedRouterTests(unittest.TestCase):
    def test_qwen_and_cloud_profiles_have_explicit_adapters(self):
        qwen = _Adapter("qwen")
        cloud = _Adapter("cloud")
        router = ProfileRoutingTranscriber(cloud=cloud, qwen=qwen)

        async def run():
            qwen_result = await router.transcribe({"transcription": {"pipeline_profile": "pyannote_qwen3_asr"}}, "a", progress=lambda _: None)
            cloud_result = await router.transcribe({"transcription": {"pipeline_profile": "cloud_asr"}}, "b", progress=lambda _: None)
            return qwen_result, cloud_result

        qwen_result, cloud_result = asyncio.run(run())
        self.assertEqual(qwen_result["text"], "qwen")
        self.assertEqual(cloud_result["text"], "cloud")
        self.assertEqual(qwen.calls[0][0], "pyannote_qwen3_asr")
        self.assertEqual(cloud.calls[0][0], "cloud_asr")

    def test_removed_profiles_are_rejected(self):
        router = ProfileRoutingTranscriber(cloud=_Adapter("cloud"), qwen=_Adapter("qwen"))

        async def run():
            with self.assertRaisesRegex(RuntimeError, "UNSUPPORTED_PIPELINE_PROFILE"):
                await router.transcribe({"transcription": {"pipeline_profile": "pyannote_moss_asr"}}, "x")

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
