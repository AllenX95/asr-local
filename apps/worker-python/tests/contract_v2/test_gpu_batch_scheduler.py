from __future__ import annotations

from concurrent.futures import Future
import gc
from types import SimpleNamespace
import threading
import time
import unittest
import weakref

from app.pipeline.segment_types import AsrBatchItem, BatchItemIdentity, Outcome, RuntimeKey
from app.runtime.gpu_batch_scheduler import (
    BatchCancelledError,
    DuplicateBatchIdentityError,
    GpuBatchDispatcher,
    RuntimeKeyMismatchError,
    SchedulerClosedError,
)


class _Model:
    def __init__(self, *, reverse=False, block=False, mapping_mode=None):
        self.reverse = reverse
        self.block = block
        self.mapping_mode = mapping_mode
        self.calls = []
        self.contexts = []
        self.languages = []
        self.entered = threading.Event()
        self.release = threading.Event()
        self.close_count = 0
        self.concurrent = 0
        self.max_concurrent = 0
        self._lock = threading.Lock()

    def transcribe(self, *, audio, context, language, return_time_stamps):
        values = tuple(int(chunk[0][0]) for chunk in audio)
        identities = tuple(chunk[0][1] for chunk in audio)
        self.calls.append(values)
        self.contexts.append(tuple(context))
        self.languages.append(tuple(language))
        with self._lock:
            self.concurrent += 1
            self.max_concurrent = max(self.max_concurrent, self.concurrent)
        self.entered.set()
        try:
            if self.block:
                self.release.wait(timeout=3)
            results = [SimpleNamespace(value=value, identity=identity) for value, identity in zip(values, identities)]
            if self.mapping_mode == "missing":
                return {identities[0]: results[0]}
            if self.mapping_mode == "extra":
                return {identity: result for identity, result in zip(identities, results)} | {
                    BatchItemIdentity("foreign", "a", "extra", 0): results[0]
                }
            if self.mapping_mode == "duplicate":
                return [SimpleNamespace(value=0, identity=identities[0]) for _ in identities]
            if self.mapping_mode == "foreign":
                return [SimpleNamespace(value=0, identity=BatchItemIdentity("foreign", "a", "x", 0)) for _ in identities]
            if self.reverse:
                return list(reversed(results))
            return results
        finally:
            with self._lock:
                self.concurrent -= 1

    def close(self):
        self.close_count += 1


class _Manager:
    def __init__(self, model=None, load_error=None):
        self.model = model or _Model()
        self.load_error = load_error
        self.load_count = 0
        self.close_count = 0

    def get_local_asr_model(self):
        self.load_count += 1
        if self.load_error:
            raise self.load_error
        return self.model

    def close_local_models(self):
        self.close_count += 1


class _Audio:
    def __init__(self, value, identity):
        self.value = value
        self.identity = identity

    def __getitem__(self, index):
        if index == 0:
            return self.value
        if index == 1:
            return self.identity
        raise IndexError(index)


def _item(workflow, attempt, ordinal, *, value=None, duration_ms=1_000, context="", language=None, key=None):
    value = ordinal if value is None else value
    identity = BatchItemIdentity(workflow, attempt, f"segment-{ordinal}", ordinal)
    return AsrBatchItem(
        identity=identity,
        audio=[value, identity],
        sample_rate=2,
        context=context,
        language=language,
        duration_ms=duration_ms,
        runtime_key=key,
    )


class GpuBatchSchedulerContractTests(unittest.TestCase):
    def setUp(self):
        self.dispatcher = None

    def tearDown(self):
        if self.dispatcher is not None:
            self.dispatcher.close()

    def _make(self, model=None, **options):
        manager = _Manager(model)
        self.dispatcher = GpuBatchDispatcher(manager, RuntimeKey(model_path="models/Qwen"), **options)
        return manager

    def test_atomic_concurrent_attempts_coalesce_across_workflows(self):
        model = _Model()
        manager = self._make(model, coalesce_window_ms=80)
        barrier = threading.Barrier(3)
        result = {}

        def submit(workflow, count):
            barrier.wait()
            result[workflow] = self.dispatcher.submit_attempt(
                [
                    _item(
                        workflow,
                        "a1",
                        index,
                        value=len(result) + index,
                        context=workflow,
                    )
                    for index in range(count)
                ]
            )

        threads = [
            threading.Thread(target=submit, args=("A", 4)),
            threading.Thread(target=submit, args=("B", 1)),
            threading.Thread(target=submit, args=("C", 1)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        for futures in result.values():
            for future in futures:
                self.assertTrue(future.result(timeout=3).success)
        self.assertEqual(len(model.calls[0]), 4)
        self.assertTrue({"A", "B", "C"}.issubset(set(model.contexts[0])))
        self.assertEqual(manager.load_count, 1)

    def test_hard_batch_size_does_not_skip_first_coalesce_window(self):
        model = _Model()
        self._make(model, target_batch_size=8, hard_batch_size=8, coalesce_window_ms=500)
        a = self.dispatcher.submit_attempt(
            [_item("A", "a1", index, context="A") for index in range(8)]
        )
        b = self.dispatcher.submit_attempt([_item("B", "b1", 0, context="B")])
        c = self.dispatcher.submit_attempt([_item("C", "c1", 0, context="C")])
        self.assertTrue(model.entered.wait(timeout=3))
        self.assertTrue({"A", "B", "C"}.issubset(set(model.contexts[0])))
        for future in a + b + c:
            self.assertTrue(future.result(timeout=3).success)

    def test_round_robin_has_no_workflow_starvation_and_preserves_context(self):
        model = _Model()
        self._make(model, coalesce_window_ms=30)
        futures = []
        for workflow in ("A", "B", "C"):
            futures.extend(
                self.dispatcher.submit_attempt(
                    [_item(workflow, "a1", index, context=workflow, language=workflow) for index in range(5)]
                )
            )
        for future in futures:
            self.assertTrue(future.result(timeout=3).success)
        self.assertGreaterEqual(len(model.calls), 3)
        self.assertTrue(all(any(value in call for value in range(15)) for call in model.calls[:2]))
        self.assertEqual(model.contexts[0][:3], ("A", "B", "C"))
        self.assertEqual(model.languages[0][:3], ("A", "B", "C"))
        self.assertEqual(model.max_concurrent, 1)
        self.assertEqual(model.max_concurrent, 1)

    def test_budget_and_over_budget_singleton(self):
        model = _Model()
        self._make(
            model,
            coalesce_window_ms=0,
            length_bucket_ms=1_000,
            max_total_audio_ms=3_000,
            max_duration_times_count_ms=4_000,
        )
        futures = self.dispatcher.submit_attempt(
            [_item("A", "a1", index, duration_ms=1_000) for index in range(4)]
        )
        for future in futures:
            self.assertTrue(future.result(timeout=3).success)
        self.assertEqual([len(call) for call in model.calls], [3, 1])

        model2 = _Model()
        self.dispatcher.close()
        self.dispatcher = GpuBatchDispatcher(_Manager(model2), RuntimeKey(), coalesce_window_ms=0, max_total_audio_ms=100)
        future = self.dispatcher.submit_attempt([_item("B", "a1", 0, duration_ms=1_000)])[0]
        self.assertTrue(future.result(timeout=3).success)
        self.assertEqual(model2.calls, [(0,)])

    def test_result_identity_routing_is_strict(self):
        model = _Model(reverse=True)
        self._make(model, coalesce_window_ms=0)
        futures = self.dispatcher.submit_attempt([_item("A", "a1", index) for index in range(4)])
        outcomes = [future.result(timeout=3) for future in futures]
        self.assertEqual([outcome.result.value for outcome in outcomes], [0, 1, 2, 3])

        for mode in ("missing", "extra", "duplicate", "foreign"):
            malformed = _Model(mapping_mode=mode)
            self.dispatcher.close()
            self.dispatcher = GpuBatchDispatcher(_Manager(malformed), RuntimeKey(), coalesce_window_ms=0)
            outcomes = [
                future.result(timeout=3)
                for future in self.dispatcher.submit_attempt([_item("B", "a1", index) for index in range(2)])
            ]
            if mode in {"missing", "duplicate"}:
                self.assertTrue(all(outcome.success for outcome in outcomes))
            else:
                self.assertTrue(any(outcome.error is not None for outcome in outcomes), mode)

    def test_duplicate_and_runtime_key_are_rejected(self):
        key = RuntimeKey(model_path="models/Qwen")
        self._make(_Model(), coalesce_window_ms=0)
        item = _item("A", "a1", 0, key=key)
        self.dispatcher.submit_attempt([item])
        with self.assertRaises(DuplicateBatchIdentityError):
            self.dispatcher.submit_attempt([item])
        with self.assertRaises(RuntimeKeyMismatchError):
            self.dispatcher.submit_attempt([_item("A", "a1", 1, key=RuntimeKey(model_name="other"))])

    def test_cancel_only_B_and_old_attempt_result_cannot_pollute_new_attempt(self):
        model = _Model(block=True)
        self._make(model, coalesce_window_ms=0, target_batch_size=1)
        old = self.dispatcher.submit_attempt([_item("B", "old", 0)])[0]
        self.assertTrue(model.entered.wait(timeout=3))
        self.assertEqual(self.dispatcher.cancel("B", "old"), 1)
        new = self.dispatcher.submit_attempt([_item("B", "new", 0, value=9)])[0]
        model.release.set()
        self.assertTrue(old.result(timeout=3).cancelled)
        self.assertEqual(new.result(timeout=3).result.value, 9)

    def test_cancelled_attempt_tombstone_rejects_late_resubmission(self):
        model = _Model()
        self._make(model, target_batch_size=1, coalesce_window_ms=500)
        item = _item("B", "cancelled", 0)
        original = self.dispatcher.submit_attempt([item])[0]
        self.assertEqual(self.dispatcher.cancel("B", "cancelled"), 1)
        self.assertTrue(original.result(timeout=3).cancelled)
        late = self.dispatcher.submit_attempt([item])[0]
        self.assertTrue(late.done())
        self.assertTrue(late.result(timeout=3).cancelled)
        self.assertEqual(model.calls, [])

    def test_planner_exception_terminates_all_queued_futures(self):
        self._make(_Model(), coalesce_window_ms=500)
        def broken_plan():
            raise RuntimeError("planner exploded")
        self.dispatcher._plan_locked = broken_plan
        futures = self.dispatcher.submit_attempt([_item("A", "a1", index) for index in range(2)])
        for future in futures:
            self.assertIsInstance(future.result(timeout=3).error, RuntimeError)
            self.assertTrue(future.done())

    def test_future_callback_can_wait_for_sibling(self):
        model = _Model()
        self._make(model, coalesce_window_ms=60)
        futures = self.dispatcher.submit_attempt([_item("A", "a1", i) for i in range(2)])
        callback_done = threading.Event()

        def callback(_future):
            futures[1].result(timeout=2)
            callback_done.set()

        futures[0].add_done_callback(callback)
        self.assertTrue(futures[0].result(timeout=3).success)
        self.assertTrue(futures[1].result(timeout=3).success)
        self.assertTrue(callback_done.wait(timeout=3))

    def test_completed_batch_releases_audio_while_scheduler_is_idle(self):
        model = _Model()
        self._make(model, coalesce_window_ms=0)
        identity = BatchItemIdentity("A", "gc", "segment", 0)
        audio = _Audio(42, identity)
        audio_ref = weakref.ref(audio)
        item = AsrBatchItem(
            identity=identity,
            audio=audio,
            sample_rate=2,
            duration_ms=1_000,
        )
        future = self.dispatcher.submit_attempt([item])[0]
        self.assertTrue(future.result(timeout=3).success)
        del item
        del audio
        for _ in range(20):
            gc.collect()
            if audio_ref() is None:
                break
            time.sleep(0.01)
        self.assertIsNone(audio_ref())
        self.assertFalse(self.dispatcher.snapshot()["inflight_items"])

    def test_callback_can_close_without_waiting_for_delivery_threads(self):
        model = _Model(block=True)
        self._make(model, coalesce_window_ms=0)
        futures = self.dispatcher.submit_attempt([_item("A", "close", i) for i in range(2)])
        self.assertTrue(model.entered.wait(timeout=3))
        callback_done = threading.Event()

        def callback(_future):
            self.dispatcher.close()
            callback_done.set()

        futures[0].add_done_callback(callback)
        model.release.set()
        self.assertTrue(callback_done.wait(timeout=3))
        self.assertTrue(self.dispatcher.snapshot()["closed"])
        for future in futures:
            self.assertTrue(future.done())
            self.assertTrue(future.result(timeout=3).success)

    def test_load_failure_worker_failure_and_shutdown_clear_references(self):
        manager = _Manager(load_error=RuntimeError("load failed"))
        self.dispatcher = GpuBatchDispatcher(manager, RuntimeKey(), coalesce_window_ms=0)
        failed = self.dispatcher.submit_attempt([_item("A", "a1", 0)])[0]
        self.assertIsInstance(failed.result(timeout=3).error, RuntimeError)
        self.dispatcher.close()
        self.assertEqual(manager.close_count, 1)
        self.assertFalse(self.dispatcher._futures)
        self.assertFalse(self.dispatcher._items)
        self.assertFalse(self.dispatcher._seen)
        self.assertFalse(self.dispatcher._inflight)
        self.assertFalse(self.dispatcher._cancelled_identities)

        model = _Model(block=True)
        self.dispatcher = GpuBatchDispatcher(_Manager(model), RuntimeKey(), coalesce_window_ms=0)
        current = self.dispatcher.submit_attempt([_item("A", "a1", 0)])[0]
        self.assertTrue(model.entered.wait(timeout=3))
        pending = self.dispatcher.submit_attempt([_item("A", "a1", 1)])[0]
        self.dispatcher.close()
        self.assertTrue(pending.result(timeout=3).discarded)
        model.release.set()
        self.assertTrue(current.result(timeout=3).discarded)
        snapshot = self.dispatcher.snapshot()
        self.assertFalse(snapshot["inflight_items"])
        self.assertEqual(self.dispatcher.model_manager, None)


if __name__ == "__main__":
    unittest.main()
