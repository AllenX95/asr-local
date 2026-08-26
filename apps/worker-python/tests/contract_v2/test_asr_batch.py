from __future__ import annotations

from concurrent.futures import Future
from types import SimpleNamespace
import unittest

from app.pipeline.asr_batch import (
    NullTranscriptionError,
    is_cuda_oom_error,
    transcribe_with_isolation,
)
from app.pipeline.job_runner import (
    BatchAttemptCancelled,
    BatchOutcomeFatalError,
    transcribe_audio_batch,
)
from app.schemas import SpeakerSegment
from app.pipeline.segment_types import Outcome
from app.runtime.gpu_batch_scheduler import BatchCancelledError


class _FakeModel:
    def __init__(self, *, fail_index: int | None = None, fail_batch: bool = False) -> None:
        self.fail_index = fail_index
        self.fail_batch = fail_batch
        self.calls: list[tuple[int, ...]] = []

    def transcribe(self, *, audio, context, language, return_time_stamps):
        indexes = tuple(int(chunk[0][0]) for chunk in audio)
        self.calls.append(indexes)
        if self.fail_batch and len(indexes) > 1:
            raise RuntimeError("synthetic batch failure")
        if self.fail_index is not None and self.fail_index in indexes:
            raise RuntimeError(f"synthetic failure for {self.fail_index}")
        return [SimpleNamespace(text=f"segment-{index}", language="en") for index in indexes]


class AsrBatchContractTests(unittest.TestCase):
    def test_message_fallback_requires_cuda_and_out_of_memory(self) -> None:
        self.assertTrue(is_cuda_oom_error(RuntimeError("CUDA out of memory")))
        self.assertTrue(is_cuda_oom_error(RuntimeError("CUDA error: out of memory")))
        self.assertFalse(is_cuda_oom_error(RuntimeError("out of memory")))
        self.assertFalse(is_cuda_oom_error(RuntimeError("CUDA kernel failure")))

    def test_result_count_is_strict_even_for_single_item(self) -> None:
        model = _FakeModel()
        failures: dict[int, BaseException] = {}

        def invoke(_items):
            return []

        result = transcribe_with_isolation(
            model=model,
            inputs=[(1, [1])],
            invoke=invoke,
            job_id="job-count",
            failures=failures,
        )

        self.assertEqual(result, {1: None})
        self.assertIn(1, failures)
        self.assertIn("returned 0 result", str(failures[1]))

    def test_batch_failure_is_bisected_without_polluting_siblings(self) -> None:
        model = _FakeModel(fail_index=2)
        failures: dict[int, BaseException] = {}
        result = transcribe_audio_batch(
            model=model,
            batch_inputs=[
                (1, SpeakerSegment("s1", "Speaker 1", 0, 1, 1), [1]),
                (2, SpeakerSegment("s2", "Speaker 1", 1, 2, 1), [2]),
                (3, SpeakerSegment("s3", "Speaker 1", 2, 3, 1), [3]),
            ],
            sample_rate=16_000,
            context="",
            language=None,
            job_id="job-isolation",
            failures=failures,
        )

        self.assertEqual(result[1].text, "segment-1")
        self.assertIsNone(result[2])
        self.assertEqual(result[3].text, "segment-3")
        self.assertIn(2, failures)
        self.assertTrue(any(call == (1, 2, 3) for call in model.calls))
        self.assertTrue(any(call == (1,) for call in model.calls))
        self.assertTrue(any(call == (2,) for call in model.calls))

    def test_eight_item_oom_is_deterministically_isolated_8_4_2_1(self) -> None:
        identities = [("workflow-a", f"segment-{index:02d}") for index in range(8)]
        bad_identity = identities[6]
        calls: list[tuple[tuple[str, str], ...]] = []
        cache_clears: list[int] = []
        failures = {}

        def invoke(items):
            current = tuple(identity for identity, _audio in items)
            calls.append(current)
            if bad_identity in current:
                raise RuntimeError("CUDA out of memory")
            return [SimpleNamespace(identity=identity) for identity in current]

        result = transcribe_with_isolation(
            model=object(),
            inputs=[(identity, [index]) for index, identity in enumerate(identities)],
            invoke=invoke,
            job_id="job-eight",
            clear_cache=lambda: cache_clears.append(1),
            failures=failures,
        )

        self.assertEqual(
            calls,
            [
                tuple(identities),
                tuple(identities[:4]),
                tuple(identities[4:]),
                tuple(identities[4:6]),
                tuple(identities[6:]),
                (identities[6],),
                (identities[7],),
            ],
        )
        self.assertEqual(len(cache_clears), 4)
        self.assertEqual(set(result), set(identities))
        self.assertIsNone(result[bad_identity])
        self.assertEqual(set(failures), {bad_identity})
        self.assertIsNone(failures[bad_identity].__traceback__)
        for identity in identities:
            if identity != bad_identity:
                self.assertEqual(result[identity].identity, identity)

    def test_multiple_result_count_mismatch_is_bisected(self) -> None:
        failures = {}

        def invoke(items):
            if len(items) > 1:
                return [SimpleNamespace(identity=items[0][0])]
            return [SimpleNamespace(identity=items[0][0])]

        result = transcribe_with_isolation(
            model=object(),
            inputs=[(("wf", "a"), [1]), (("wf", "b"), [2])],
            invoke=invoke,
            job_id="job-count-many",
            failures=failures,
        )

        self.assertEqual(set(result), {("wf", "a"), ("wf", "b")})
        self.assertFalse(failures)

    def test_null_result_is_recorded_as_an_explicit_failure(self) -> None:
        failures = {}

        def invoke(_items):
            return [None]

        result = transcribe_with_isolation(
            model=object(),
            inputs=[(("wf", "null"), [1])],
            invoke=invoke,
            job_id="job-null",
            failures=failures,
        )

        self.assertIsNone(result[("wf", "null")])
        self.assertIsInstance(failures[("wf", "null")], NullTranscriptionError)

    def test_oom_clears_cache_but_keeps_model_for_recursive_retry(self) -> None:
        model = _FakeModel(fail_batch=True)
        cache_clears: list[int] = []

        def invoke(items):
            indexes = tuple(item[0] for item in items)
            if len(indexes) > 1:
                raise RuntimeError("CUDA out of memory")
            return [SimpleNamespace(text=f"segment-{indexes[0]}")]

        result = transcribe_with_isolation(
            model=model,
            inputs=[(1, [1]), (2, [2])],
            invoke=invoke,
            job_id="job-oom",
            clear_cache=lambda: cache_clears.append(1),
        )

        self.assertEqual(set(result), {1, 2})
        self.assertTrue(all(value is not None for value in result.values()))
        self.assertGreaterEqual(len(cache_clears), 1)

    def test_empty_audio_is_a_per_item_failure(self) -> None:
        model = _FakeModel()
        failures: dict[int, BaseException] = {}
        result = transcribe_audio_batch(
            model=model,
            batch_inputs=[
                (1, SpeakerSegment("s1", "Speaker 1", 0, 1, 1), []),
                (2, SpeakerSegment("s2", "Speaker 1", 1, 2, 1), [2]),
            ],
            sample_rate=16_000,
            context="",
            language=None,
            job_id="job-empty",
            failures=failures,
        )

        self.assertIsNone(result[1])
        self.assertEqual(result[2].text, "segment-2")
        self.assertIn(1, failures)
        self.assertEqual(model.calls, [(2,)])

    def test_discarded_fatal_outcome_is_not_treated_as_user_cancel(self) -> None:
        class Executor:
            target_batch_size = 4

            def submit_attempt(self, items):
                future = Future()
                future.set_result(
                    Outcome(
                        identity=items[0].identity,
                        error=RuntimeError("planner exploded"),
                        discarded=True,
                    )
                )
                return [future]

        with self.assertRaises(BatchOutcomeFatalError):
            transcribe_audio_batch(
                model=None,
                batch_inputs=[
                    (1, SpeakerSegment("segment-fatal", "Speaker 1", 0, 1_000, 1_000), [1]),
                ],
                sample_rate=16_000,
                context="",
                language=None,
                job_id="job-fatal",
                batch_executor=Executor(),
                workflow_id="wf-fatal",
                attempt_id="attempt-fatal",
            )

    def test_cancelled_outcome_is_the_only_discarded_user_cancel_path(self) -> None:
        class Executor:
            target_batch_size = 4

            def submit_attempt(self, items):
                future = Future()
                future.set_result(
                    Outcome(
                        identity=items[0].identity,
                        error=BatchCancelledError("cancelled"),
                        cancelled=True,
                        discarded=True,
                    )
                )
                return [future]

        with self.assertRaises(BatchAttemptCancelled):
            transcribe_audio_batch(
                model=None,
                batch_inputs=[
                    (1, SpeakerSegment("segment-cancel", "Speaker 1", 0, 1_000, 1_000), [1]),
                ],
                sample_rate=16_000,
                context="",
                language=None,
                job_id="job-cancel",
                batch_executor=Executor(),
                workflow_id="wf-cancel",
                attempt_id="attempt-cancel",
            )


if __name__ == "__main__":
    unittest.main()
