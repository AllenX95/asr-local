"""Failure-isolated batching helpers for local ASR inference.

The Qwen wrapper accepts a list of independent audio samples.  This module
keeps the batch contract deliberately small: every model call must return
exactly one result per input, and a failed batch is recursively bisected until
the failing item(s) are isolated.  The caller can then turn the per-item
failures into workflow warnings without losing successful siblings.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable, Iterable
import logging
from typing import Any, TypeVar


LOGGER = logging.getLogger(__name__)
IdentityT = TypeVar("IdentityT", bound=Hashable)


class BatchResultCountError(RuntimeError):
    """Raised when an ASR model returns a result count different from inputs."""

    def __init__(self, expected: int, actual: int | str) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"ASR model returned {actual} result(s) for {expected} input(s)"
        )


class NullTranscriptionError(RuntimeError):
    """Raised when a model returns one slot containing ``None``."""


def is_cuda_oom_error(error: BaseException) -> bool:
    """Return whether *error* represents a CUDA out-of-memory failure.

    Importing torch just to classify an exception is intentionally avoided: a
    contract test and CPU runtime can use the message-only fallback, while a
    real torch ``OutOfMemoryError`` is recognised when torch is available.
    """

    try:
        import torch

        out_of_memory_error = getattr(torch.cuda, "OutOfMemoryError", None)
        if out_of_memory_error is not None and isinstance(error, out_of_memory_error):
            return True
    except Exception:
        # Classification must never mask the original inference error.
        pass

    message = str(error).lower()
    return "cuda" in message and "out of memory" in message


def detach_exception(error: BaseException) -> BaseException:
    """Remove traceback/context links before retaining an inference error.

    CUDA exceptions can retain model/input locals through their traceback.
    Failure maps live until the workflow is exported, so keeping those links
    would unnecessarily pin GPU tensors and the model frame.  The textual
    exception remains available for warning messages.
    """

    try:
        error = error.with_traceback(None)
    except Exception:
        pass
    try:
        error.__traceback__ = None
    except Exception:
        pass
    try:
        error.__context__ = None
    except Exception:
        pass
    try:
        error.__cause__ = None
    except Exception:
        pass
    return error


def clear_cuda_cache(clear_cache: Callable[[], Any] | None = None) -> None:
    """Clear temporary CUDA allocations without unloading the ASR model."""

    if clear_cache is not None:
        try:
            clear_cache()
        except Exception:
            LOGGER.debug("failed to clear CUDA cache after ASR OOM", exc_info=True)
        return

    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        LOGGER.debug("failed to clear CUDA cache after ASR OOM", exc_info=True)


def _materialize_model_results(raw_results: Iterable[Any], expected: int) -> list[Any]:
    """Materialise and strictly validate a model response."""

    try:
        results = list(raw_results)
    except TypeError as exc:
        raise BatchResultCountError(expected, "non-iterable") from exc
    actual = len(results)
    if actual != expected:
        raise BatchResultCountError(expected, actual)
    return results


def transcribe_with_isolation(
    *,
    model: Any,
    inputs: list[tuple[IdentityT, Any]],
    invoke: Callable[[list[tuple[IdentityT, Any]]], Iterable[Any]],
    job_id: str,
    clear_cache: Callable[[], Any] | None = None,
    failures: dict[IdentityT, BaseException] | None = None,
) -> dict[IdentityT, Any]:
    """Run independent ASR inputs with recursive failure isolation.

    ``invoke`` receives a non-empty list of ``(stable_index, audio)`` pairs
    and must return one result per pair.  Both ordinary exceptions and strict
    result-count violations are recursively bisected.  At a single input the
    exception is recorded in ``failures`` and the result is ``None``; sibling
    items continue normally.

    ``model`` is accepted for logging and to make the ownership explicit.  It
    is never closed or replaced during recovery, including after OOM.
    """

    del model  # The model remains owned by ModelManager; never unload here.
    result_map: dict[IdentityT, Any] = {}
    failure_map = failures if failures is not None else {}

    def run(items: list[tuple[IdentityT, Any]]) -> None:
        if not items:
            return

        results: list[Any] | None = None
        failure: BaseException | None = None
        try:
            raw_results = invoke(items)
            results = _materialize_model_results(raw_results, len(items))
        except Exception as caught:
            # Do not clear/recurse from inside this handler.  The exception
            # target is still active there and may retain CUDA frames.
            failure = caught

        # This runs after leaving the except scope.  Detach before any cache
        # operation or recursive call, and only retain this lightweight error
        # object in the persistent failure map.
        if failure is not None:
            failure = detach_exception(failure)
            if is_cuda_oom_error(failure):
                # Only temporary allocator state is cleared.  The model is
                # intentionally kept resident for the recursive retry.
                clear_cuda_cache(clear_cache)
            if len(items) == 1:
                segment_index = items[0][0]
                failure_map[segment_index] = failure
                result_map[segment_index] = None
                LOGGER.warning(
                    "ASR item isolated as failed | job_id=%s | segment_index=%s | error=%s",
                    job_id,
                    segment_index,
                    failure,
                )
                return

            midpoint = max(1, len(items) // 2)
            LOGGER.warning(
                "ASR batch failed; recursively bisecting | job_id=%s | batch_size=%s | error=%s",
                job_id,
                len(items),
                failure,
            )
            run(items[:midpoint])
            run(items[midpoint:])
            return

        assert results is not None
        for (segment_index, _audio), transcription in zip(items, results):
            result_map[segment_index] = transcription
            if transcription is None:
                failure_map.setdefault(
                    segment_index,
                    NullTranscriptionError("ASR model returned a null transcription"),
                )

    run(list(inputs))
    return result_map
