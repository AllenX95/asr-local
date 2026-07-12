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
    def test_new_profiles_share_explicit_backend_adapters(self):
        qwen = _Adapter("qwen")
        moss = _Adapter("moss")
        router = ProfileRoutingTranscriber(moss=moss, cloud=_Adapter("cloud"), qwen=qwen)

        async def run():
            qwen_result = await router.transcribe({"transcription": {"pipeline_profile": "pyannote_qwen3_asr"}}, "a", progress=lambda _: None)
            moss_result = await router.transcribe({"transcription": {"pipeline_profile": "pyannote_moss_asr"}}, "b", progress=lambda _: None)
            return qwen_result, moss_result

        qwen_result, moss_result = asyncio.run(run())
        self.assertEqual(qwen_result["text"], "qwen")
        self.assertEqual(moss_result["text"], "moss")
        self.assertEqual(qwen.calls[0][0], "pyannote_qwen3_asr")
        self.assertEqual(moss.calls[0][0], "pyannote_moss_asr")


if __name__ == "__main__":
    unittest.main()
