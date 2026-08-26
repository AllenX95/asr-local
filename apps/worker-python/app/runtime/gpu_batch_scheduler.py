from __future__ import annotations
from collections import deque
from collections.abc import Iterable, Mapping
from concurrent.futures import Future
from contextlib import nullcontext
from dataclasses import dataclass
import logging
import threading
import time
from typing import Any, TypeAlias
from app.pipeline.asr_batch import transcribe_with_isolation
from app.pipeline.segment_types import AsrBatchItem, BatchItemIdentity, Outcome, RuntimeKey
LOGGER = logging.getLogger(__name__)
AttemptKey: TypeAlias = tuple[str, str]
class DuplicateBatchIdentityError(ValueError): pass
class RuntimeKeyMismatchError(ValueError): pass
class SchedulerClosedError(RuntimeError): pass
class BatchCancelledError(RuntimeError): pass
class BatchIdentityRoutingError(RuntimeError): pass


class GpuExecutionGate:
    """Serialize native CUDA ownership transitions and inference.

    The gate is deliberately re-entrant because model-manager cleanup may be
    called from code which already owns the gate.  Scheduler callers never
    hold it while waiting on a Future; it covers only native load, inference,
    device movement, and release operations.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()

    def __enter__(self):
        self._lock.acquire()
        return self

    def __exit__(self, exc_type, exc, traceback):
        self._lock.release()
        return False


@dataclass(slots=True)
class _Entry:
    item: AsrBatchItem; future: Future[Outcome]; enqueued_at: float
def _duration_ms(item: AsrBatchItem) -> int:
    if item.effective_duration_ms is not None:
        return max(0, int(item.effective_duration_ms))
    try:
        return max(0, round(len(item.audio) * 1000 / item.sample_rate))
    except (TypeError, AttributeError):
        return 0
class GpuBatchDispatcher:
    """Fair micro-batch dispatcher for one model manager runtime."""
    def __init__(
        self,
        model_manager: Any,
        runtime_key: RuntimeKey,
        *,
        target_batch_size: int = 4,
        hard_batch_size: int = 8,
        max_active_workflows: int = 3,
        coalesce_window_ms: int = 12,
        length_bucket_ms: int = 5_000,
        max_total_audio_ms: int = 120_000,
        max_duration_times_count_ms: int = 120_000,
        execution_gate: Any | None = None,
    ) -> None:
        if model_manager is None:
            raise ValueError("model_manager is required")
        if not isinstance(runtime_key, RuntimeKey):
            raise TypeError("runtime_key must be RuntimeKey")
        if not 0 < target_batch_size <= hard_batch_size:
            raise ValueError("target_batch_size must be positive and <= hard_batch_size")
        if max_active_workflows <= 0 or coalesce_window_ms < 0 or length_bucket_ms <= 0:
            raise ValueError("invalid scheduler limits")
        if max_total_audio_ms <= 0 or max_duration_times_count_ms <= 0:
            raise ValueError("audio budgets must be positive")
        self.model_manager = model_manager
        self.runtime_key = runtime_key
        self.target_batch_size = target_batch_size
        self.hard_batch_size = hard_batch_size
        self.max_active_workflows = max_active_workflows
        self.coalesce_window_ms = coalesce_window_ms
        self.length_bucket_ms = length_bucket_ms
        self.max_total_audio_ms = max_total_audio_ms
        self.max_duration_times_count_ms = max_duration_times_count_ms
        self.execution_gate = execution_gate
        self._condition = threading.Condition(threading.Lock())
        self._attempt_queues: dict[AttemptKey, deque[_Entry]] = {}
        self._workflow_attempts: dict[str, deque[AttemptKey]] = {}
        self._workflow_order: deque[str] = deque()
        self._futures: dict[BatchItemIdentity, Future[Outcome]] = {}
        self._items: dict[BatchItemIdentity, AsrBatchItem] = {}
        self._seen: set[BatchItemIdentity] = set()
        self._cancelled_attempts: set[AttemptKey] = set()
        self._cancelled_tombstone_order: deque[AttemptKey] = deque()
        self._max_cancelled_tombstones = 1024
        self._cancelled_identities: set[BatchItemIdentity] = set()
        self._inflight: dict[BatchItemIdentity, _Entry] = {}
        self._first_queued_at: float | None = None
        self._model: Any | None = None
        self._model_loaded = False
        self._model_closed = False
        self._closing = False
        self._closed = False
        self._thread = threading.Thread(
            target=self._run,
            name="gpu-asr-batch-dispatcher",
            daemon=True,
        )
        self._thread.start()
    def submit_attempt(self, items: Iterable[AsrBatchItem]) -> list[Future[Outcome]]:
        """Atomically enqueue one attempt."""
        values = list(items)
        if not values:
            raise ValueError("submit_attempt requires at least one item")
        attempt = (values[0].identity.workflow_id, values[0].identity.attempt_id)
        completions: list[tuple[Future[Outcome], Outcome]] = []
        futures: list[Future[Outcome]] = []
        with self._condition:
            if self._closing or self._closed:
                raise SchedulerClosedError("GPU batch dispatcher is closed")
            local_seen: set[BatchItemIdentity] = set()
            for item in values:
                self._validate_item(item)
                identity = item.identity
                if (identity.workflow_id, identity.attempt_id) != attempt:
                    raise ValueError("submit_attempt must contain one workflow/attempt")
                if identity in local_seen or identity in self._seen:
                    raise DuplicateBatchIdentityError(f"duplicate ASR identity: {identity!r}")
                local_seen.add(identity)
            now = time.monotonic()
            queue = self._attempt_queues.get(attempt)
            if queue is None and attempt not in self._cancelled_attempts:
                queue = deque()
                self._attempt_queues[attempt] = queue
                attempts = self._workflow_attempts.setdefault(attempt[0], deque())
                attempts.append(attempt)
                if attempt[0] not in self._workflow_order:
                    self._workflow_order.append(attempt[0])
            for item in values:
                identity = item.identity
                future: Future[Outcome] = Future()
                futures.append(future)
                self._seen.add(identity)
                self._futures[identity] = future
                self._items[identity] = item
                if attempt in self._cancelled_attempts:
                    self._cancelled_identities.add(identity)
                    completions.append(
                        self._prepare_completion_locked(
                            identity,
                            Outcome(
                                identity=identity,
                                error=BatchCancelledError("attempt is already cancelled"),
                                cancelled=True,
                                discarded=True,
                            ),
                        )
                    )
                else:
                    assert queue is not None
                    queue.append(_Entry(item, future, now))
            if queue:
                self._first_queued_at = self._first_queued_at or now
            self._log("enqueue_attempt", workflow_id=attempt[0], attempt_id=attempt[1], count=len(values))
            self._condition.notify_all()
        self._deliver(completions)
        return futures
    def cancel(self, workflow_id: str, attempt_id: str) -> int:
        """Cancel queued work and mark in-flight work stale."""
        key = (workflow_id, attempt_id)
        completions: list[tuple[Future[Outcome], Outcome]] = []
        affected = 0
        with self._condition:
            self._remember_cancelled_locked(key)
            queue = self._attempt_queues.pop(key, None)
            if queue:
                while queue:
                    entry = queue.popleft()
                    self._cancelled_identities.add(entry.item.identity)
                    completions.append(
                        self._prepare_completion_locked(
                            entry.item.identity,
                            Outcome(
                                identity=entry.item.identity,
                                error=BatchCancelledError("ASR item cancelled before inference"),
                                cancelled=True,
                                discarded=True,
                            ),
                        )
                    )
                    affected += 1
                self._remove_attempt_from_workflow_locked(key)
            for identity in self._inflight:
                if (identity.workflow_id, identity.attempt_id) == key:
                    self._cancelled_identities.add(identity)
                    affected += 1
            self._prune_workflows_locked()
            self._refresh_first_queued_locked()
            self._log("cancel_attempt", workflow_id=workflow_id, attempt_id=attempt_id, affected=affected)
            self._condition.notify_all()
        self._deliver(completions)
        return affected

    def cancel_attempt(self, workflow_id: str, attempt_id: str) -> int:
        """Named adapter used by transcriber/router cancellation forwarding."""

        return self.cancel(workflow_id, attempt_id)
    def close(self) -> None:
        """Resolve queued work, wait for in-flight work, close the model once."""
        completions: list[tuple[Future[Outcome], Outcome]] = []
        with self._condition:
            if self._closed:
                return
            self._closing = True
            for key, queue in list(self._attempt_queues.items()):
                self._remember_cancelled_locked(key)
                while queue:
                    entry = queue.popleft()
                    completions.append(
                        self._prepare_completion_locked(
                            entry.item.identity,
                            Outcome(
                                identity=entry.item.identity,
                                error=SchedulerClosedError("dispatcher closed before inference"),
                                discarded=True,
                            ),
                        )
                    )
                self._remove_attempt_from_workflow_locked(key)
            self._attempt_queues.clear()
            self._prune_workflows_locked()
            self._refresh_first_queued_locked()
            self._condition.notify_all()
            thread = self._thread
        self._deliver(completions)
        if thread is not threading.current_thread():
            thread.join()
        with self._condition:
            self._thread = None
    @property
    def active_workflows(self) -> tuple[str, ...]:
        with self._condition:
            return tuple(sorted(self._active_workflow_ids_locked()))
    def snapshot(self) -> dict[str, Any]:
        with self._condition:
            return {
                "runtime_key": self.runtime_key,
                "pending_items": sum(len(queue) for queue in self._attempt_queues.values()),
                "inflight_items": [entry.item.identity for entry in self._inflight.values()],
                "active_workflows": sorted(self._active_workflow_ids_locked()),
                "model_loaded": self._model_loaded, "model_closed": self._model_closed, "closing": self._closing, "closed": self._closed,
            }
    def _validate_item(self, item: AsrBatchItem) -> None:
        if not isinstance(item, AsrBatchItem):
            raise TypeError("scheduler accepts AsrBatchItem values")
        if item.runtime_key is not None and item.runtime_key != self.runtime_key:
            raise RuntimeKeyMismatchError(
                f"item runtime {item.runtime_key!r} does not match {self.runtime_key!r}"
            )
    def _active_workflow_ids_locked(self) -> set[str]:
        return set(self._workflow_order) | {entry.item.identity.workflow_id for entry in self._inflight.values()}
    def _remove_attempt_from_workflow_locked(self, key: AttemptKey) -> None:
        attempts = self._workflow_attempts.get(key[0])
        if attempts is None:
            return
        try:
            attempts.remove(key)
        except ValueError: pass
        if not attempts: self._workflow_attempts.pop(key[0], None)
    def _prune_workflows_locked(self) -> None:
        for workflow_id in list(self._workflow_order):
            if not self._workflow_attempts.get(workflow_id): self._workflow_order.remove(workflow_id)
    def _refresh_first_queued_locked(self) -> None:
        self._first_queued_at = min((entry.enqueued_at for queue in self._attempt_queues.values() for entry in queue), default=None)
    def _remember_cancelled_locked(self, key: AttemptKey) -> None:
        if key in self._cancelled_attempts:
            return
        self._cancelled_attempts.add(key)
        self._cancelled_tombstone_order.append(key)
        while len(self._cancelled_tombstone_order) > self._max_cancelled_tombstones:
            self._cancelled_attempts.discard(self._cancelled_tombstone_order.popleft())
    def _prepare_completion_locked(
        self,
        identity: BatchItemIdentity,
        outcome: Outcome,
    ) -> tuple[Future[Outcome], Outcome] | tuple[None, None]:
        future = self._futures.pop(identity, None)
        self._items.pop(identity, None)
        self._seen.discard(identity)
        self._cancelled_identities.discard(identity)
        if future is None:
            return None, None
        return future, outcome
    def _fits(self, entries: list[_Entry], item: AsrBatchItem) -> bool:
        if len(entries) >= self.target_batch_size: return False
        durations = [_duration_ms(entry.item) for entry in entries] + [_duration_ms(item)]
        return sum(durations) <= self.max_total_audio_ms and max(durations, default=0) * len(durations) <= self.max_duration_times_count_ms
    def _heads_locked(self, workflow_id: str) -> list[tuple[AttemptKey, deque[_Entry]]]:
        result: list[tuple[AttemptKey, deque[_Entry]]] = []
        attempts = self._workflow_attempts.get(workflow_id, deque())
        for key in list(attempts):
            queue = self._attempt_queues.get(key)
            if queue:
                result.append((key, queue))
            else:
                self._remove_attempt_from_workflow_locked(key)
        return result
    def _plan_locked(self) -> tuple[list[_Entry], bool]:
        workflows = list(self._workflow_order)
        seed: _Entry | None = None
        for workflow_id in workflows:
            heads = self._heads_locked(workflow_id)
            if heads:
                seed = heads[0][1][0]
                break
        if seed is None:
            return [], False
        seed_duration = _duration_ms(seed.item)
        if seed_duration > self.max_total_audio_ms or seed_duration > self.max_duration_times_count_ms:
            return [seed], True
        bucket = seed_duration // self.length_bucket_ms
        eligible = workflows[: self.max_active_workflows]
        selected: list[_Entry] = []
        selected_ids: set[BatchItemIdentity] = set()
        offsets: dict[AttemptKey, int] = {}
        budget_blocked = False
        while len(selected) < self.target_batch_size:
            added = False
            for workflow_id in eligible:
                for key, queue in self._heads_locked(workflow_id):
                    offset = offsets.get(key, 0)
                    if offset >= len(queue):
                        continue
                    entry = queue[offset]
                    identity = entry.item.identity
                    if identity in selected_ids:
                        continue
                    if _duration_ms(entry.item) // self.length_bucket_ms != bucket:
                        continue
                    if not self._fits(selected, entry.item):
                        budget_blocked = True
                        continue
                    selected.append(entry)
                    selected_ids.add(identity)
                    offsets[key] = offset + 1
                    added = True
                    break
            if not added:
                break
        if not selected:
            return [seed], True
        # ``hard_batch_size`` is a ceiling, not a reason to bypass the first
        # coalesce window.  Only a budget violation (or an over-budget seed
        # above) is allowed to force an early dispatch.
        return selected, budget_blocked
    def _take_locked(self, entries: list[_Entry]) -> list[_Entry]:
        taken: list[_Entry] = []
        for expected in entries:
            identity = expected.item.identity
            key = (identity.workflow_id, identity.attempt_id)
            queue = self._attempt_queues.get(key)
            if not queue or queue[0].item.identity != identity:
                continue
            entry = queue.popleft()
            taken.append(entry)
            self._inflight[identity] = entry
            attempts = self._workflow_attempts.get(key[0])
            if attempts and key in attempts:
                attempts.remove(key)
                attempts.append(key)
            if not queue:
                self._attempt_queues.pop(key, None)
                self._remove_attempt_from_workflow_locked(key)
        self._prune_workflows_locked()
        if self._workflow_order:
            self._workflow_order.rotate(-1)
        self._refresh_first_queued_locked()
        return taken
    def _run(self) -> None:
        self._log("thread_started")
        try:
            while True:
                planned = None
                batch: list[_Entry] = []
                try:
                    with self._condition:
                        planned = self._wait_plan_locked()
                        if planned is None:
                            break
                        batch = self._take_locked(planned[0])
                    if batch:
                        self._process(batch)
                finally:
                    planned = None
                    batch.clear()
        except BaseException as exc:
            self._terminate_with_failure(exc)
        finally:
            self._close_model_once()
            with self._condition:
                for collection in (self._attempt_queues, self._workflow_attempts, self._workflow_order,
                                   self._seen, self._cancelled_attempts, self._cancelled_identities,
                                   self._items, self._futures, self._inflight):
                    collection.clear()
                self._cancelled_tombstone_order.clear()
                self._closed = True
                self._condition.notify_all()
            self._log("thread_stopped")
    def _terminate_with_failure(self, error: BaseException) -> None:
        completions: list[tuple[Future[Outcome], Outcome]] = []
        with self._condition:
            self._closing = True
            for key, queue in list(self._attempt_queues.items()):
                self._attempt_queues.pop(key, None)
                self._remove_attempt_from_workflow_locked(key)
                while queue:
                    identity = queue.popleft().item.identity
                    prepared = self._prepare_completion_locked(
                        identity, Outcome(identity=identity, error=error, discarded=True)
                    )
                    if prepared[0] is not None:
                        completions.append(prepared)  # type: ignore[arg-type]
            for identity in list(self._inflight):
                self._inflight.pop(identity, None)
                key = (identity.workflow_id, identity.attempt_id)
                outcome = Outcome(
                    identity=identity,
                    error=BatchCancelledError("in-flight result discarded after dispatcher failure")
                    if key in self._cancelled_attempts else error,
                    cancelled=key in self._cancelled_attempts,
                    discarded=True,
                )
                prepared = self._prepare_completion_locked(identity, outcome)
                if prepared[0] is not None:
                    completions.append(prepared)  # type: ignore[arg-type]
            self._workflow_attempts.clear()
            self._workflow_order.clear()
            self._refresh_first_queued_locked()
            self._condition.notify_all()
        self._deliver(completions)
        completions.clear()
    def _wait_plan_locked(self) -> tuple[list[_Entry], bool] | None:
        while True:
            if self._closing and not self._attempt_queues and not self._inflight:
                return None
            if not self._attempt_queues:
                self._condition.wait()
                continue
            entries, force = self._plan_locked()
            if not entries:
                self._condition.wait()
                continue
            if force or self.coalesce_window_ms == 0:
                return entries, force
            started = self._first_queued_at or time.monotonic()
            remaining = started + self.coalesce_window_ms / 1000 - time.monotonic()
            if remaining <= 0:
                return entries, force
            self._condition.wait(timeout=remaining)
    def _ensure_model(self) -> Any:
        if self._model_loaded:
            return self._model
        with self._gate_context():
            model = self.model_manager.get_local_asr_model()
        self._model = model
        self._model_loaded = True
        self._log("model_loaded", runtime_key=repr(self.runtime_key))
        return model
    def _process(self, batch: list[_Entry]) -> None:
        identities = [entry.item.identity for entry in batch]
        by_identity = {entry.item.identity: entry.item for entry in batch}
        failures: dict[BatchItemIdentity, BaseException] = {}
        results: dict[BatchItemIdentity, Any] = {}
        try:
            model = self._ensure_model()
            def invoke(values: list[tuple[BatchItemIdentity, Any]]) -> list[Any]:
                items = [by_identity[identity] for identity, _audio in values]
                with self._gate_context():
                    raw = model.transcribe(
                        audio=[(item.audio, item.sample_rate) for item in items],
                        context=[item.context for item in items],
                        language=[item.language for item in items],
                        return_time_stamps=False,
                    )
                return self._route_results(raw, [identity for identity, _audio in values])
            try:
                clear_cache = getattr(self.model_manager, "clear_cuda_cache", None)

                def gated_clear_cache() -> None:
                    if not callable(clear_cache):
                        return
                    with self._gate_context():
                        clear_cache()

                results = transcribe_with_isolation(
                    model=model,
                    inputs=[(entry.item.identity, entry.item.audio) for entry in batch],
                    invoke=invoke,
                    job_id=f"gpu:{self.runtime_key.model_name}",
                    clear_cache=gated_clear_cache if callable(clear_cache) else None,
                    failures=failures,
                )
            except BaseException as exc:
                failures = {identity: exc for identity in identities}
            self._finish(batch, results, failures)
        finally:
            identities.clear()
            by_identity.clear()
            results.clear()
            failures.clear()
    @staticmethod
    def _route_results(raw: Iterable[Any], identities: list[BatchItemIdentity]) -> list[Any]:
        if isinstance(raw, Mapping):
            if set(raw.keys()) != set(identities):
                raise BatchIdentityRoutingError("mapping result keys do not exactly match batch identities")
            return GpuBatchDispatcher._route_object_results([raw[identity] for identity in identities], identities)
        results = list(raw)
        if len(results) != len(identities):
            return results
        return GpuBatchDispatcher._route_object_results(results, identities)
    @staticmethod
    def _route_object_results(results: list[Any], identities: list[BatchItemIdentity]) -> list[Any]:
        result_ids = [value.get("identity") if isinstance(value, Mapping) else getattr(value, "identity", None) for value in results]
        present = [value is not None for value in result_ids]
        if not any(present):
            return results
        if not all(present) or len(set(result_ids)) != len(result_ids) or set(result_ids) != set(identities):
            raise BatchIdentityRoutingError("object result identities are not a unique exact batch match")
        routed = dict(zip(result_ids, results))
        return [routed[identity] for identity in identities]
    def _finish(
        self,
        batch: list[_Entry],
        results: dict[BatchItemIdentity, Any],
        failures: dict[BatchItemIdentity, BaseException],
    ) -> None:
        completions: list[tuple[Future[Outcome], Outcome]] = []
        with self._condition:
            for entry in batch:
                identity = entry.item.identity
                key = (identity.workflow_id, identity.attempt_id)
                self._inflight.pop(identity, None)
                cancelled = key in self._cancelled_attempts or identity in self._cancelled_identities or self._closing
                if cancelled:
                    outcome = Outcome(
                        identity=identity,
                        error=BatchCancelledError("ASR result discarded for cancelled attempt"),
                        cancelled=True,
                        discarded=True,
                    )
                elif identity in failures:
                    outcome = Outcome(identity=identity, error=failures[identity])
                elif identity in results:
                    outcome = Outcome(identity=identity, result=results[identity])
                else:
                    outcome = Outcome(identity=identity, error=RuntimeError("ASR result missing identity"))
                prepared = self._prepare_completion_locked(identity, outcome)
                if prepared[0] is not None:
                    completions.append(prepared)  # type: ignore[arg-type]
            self._condition.notify_all()
        self._deliver(completions)
        completions.clear()
    @staticmethod
    def _deliver(completions: list[tuple[Future[Outcome], Outcome]]) -> None:
        """Deliver siblings concurrently without waiting for user callbacks."""
        if not completions:
            return
        barrier = threading.Barrier(len(completions))
        def deliver(future: Future[Outcome], outcome: Outcome) -> None:
            try:
                barrier.wait()
                future.set_result(outcome)
            except Exception:
                LOGGER.debug("future delivery skipped", exc_info=True)
        threads = [
            threading.Thread(target=deliver, args=(future, outcome), daemon=True)
            for future, outcome in completions
        ]
        for thread in threads:
            thread.start()
    def _close_model_once(self) -> None:
        if self._model_closed:
            return
        self._model_closed = True
        manager = self.model_manager
        self._model = None
        try:
            with self._gate_context():
                close_qwen = getattr(manager, "close_qwen_model", None)
                if callable(close_qwen):
                    close_qwen()
                else:
                    manager.close_local_models()
        except Exception:
            self._log("model_close_failed")
        finally:
            self.model_manager = None
        self._log("model_closed", runtime_key=repr(self.runtime_key))

    def _gate_context(self):
        return self.execution_gate if self.execution_gate is not None else nullcontext()
    def _log(self, event: str, **fields: Any) -> None:
        payload = {"event": event, **fields}
        try:
            LOGGER.info("gpu_batch event=%s fields=%r", event, payload, extra={"gpu_batch": payload})
        except Exception:
            pass
