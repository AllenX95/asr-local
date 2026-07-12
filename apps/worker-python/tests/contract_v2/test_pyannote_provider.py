from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.pipeline.pyannote_provider import PyannoteDiarizationProvider


class _Annotation:
    def __init__(self, tracks):
        self.tracks = tracks

    def itertracks(self, yield_label=True):
        del yield_label
        return iter(self.tracks)


class _Pipeline:
    def __init__(self, result):
        self.result = result

    def __call__(self, payload):
        self.payload = payload
        return self.result


class _Manager:
    def __init__(self, result):
        self.torch = SimpleNamespace(from_numpy=lambda value: SimpleNamespace(unsqueeze=lambda axis: value))
        self.pipeline = _Pipeline(result)
        self.closed = 0

    def get_pyannote_pipeline(self):
        return self.pipeline

    def close_pyannote_pipeline(self):
        self.closed += 1


class PyannoteProviderTests(unittest.TestCase):
    def test_prefers_exclusive_speaker_annotation(self):
        exclusive = _Annotation([(SimpleNamespace(start=0.2, end=1.4), None, "SPEAKER_A")])
        fallback = _Annotation([(SimpleNamespace(start=2.0, end=3.0), None, "SPEAKER_B")])
        manager = _Manager(SimpleNamespace(exclusive_speaker_diarization=exclusive, speaker_diarization=fallback))
        provider = PyannoteDiarizationProvider(model_manager=manager)
        turns = provider.diarize(audio=object(), sample_rate=16_000, uri="x.wav", total_ms=5_000)
        self.assertEqual([(item.speaker, item.start_ms, item.end_ms) for item in turns], [("SPEAKER_A", 200, 1400)])
        provider.close()
        self.assertEqual(manager.closed, 1)

    def test_empty_annotation_falls_back_to_one_speaker(self):
        manager = _Manager(SimpleNamespace(speaker_diarization=_Annotation([])))
        turns = PyannoteDiarizationProvider(model_manager=manager).diarize(
            audio=object(), sample_rate=16_000, uri="x.wav", total_ms=5_000
        )
        self.assertEqual([(item.speaker, item.start_ms, item.end_ms) for item in turns], [("Speaker 1", 0, 5000)])


if __name__ == "__main__":
    unittest.main()
