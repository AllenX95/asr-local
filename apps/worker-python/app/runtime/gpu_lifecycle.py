"""Explicit GPU resource lifecycle helpers.

The helpers are deliberately small and optional-dependency friendly so that
contract tests can run without torch installed.
"""

from __future__ import annotations

import gc
import logging
from typing import Any


LOGGER = logging.getLogger("asr_local.worker.gpu")


def gpu_snapshot(torch_module: Any | None = None) -> dict[str, Any]:
    if torch_module is None:
        try:
            import torch as torch_module  # type: ignore[no-redef]
        except Exception:
            return {"available": False}
    cuda = getattr(torch_module, "cuda", None)
    if cuda is None or not bool(cuda.is_available()):
        return {"available": False}
    value: dict[str, Any] = {"available": True}
    for name in ("memory_allocated", "memory_reserved"):
        try:
            value[name] = int(getattr(cuda, name)())
        except Exception:
            value[name] = None
    try:
        free_bytes, total_bytes = cuda.mem_get_info()
        value["free"] = int(free_bytes)
        value["total"] = int(total_bytes)
    except Exception:
        value["free"] = None
        value["total"] = None
    return value


def release_gpu_resources(*objects: Any, torch_module: Any | None = None, label: str = "resource") -> dict[str, Any]:
    """Drop model references and best-effort release CUDA allocator state."""

    before = gpu_snapshot(torch_module)
    for obj in objects:
        if obj is None:
            continue
        try:
            close = getattr(obj, "close", None)
            if callable(close):
                close()
        except Exception:
            LOGGER.exception("resource close failed | label=%s", label)
    gc.collect()
    if torch_module is None:
        try:
            import torch as torch_module  # type: ignore[no-redef]
        except Exception:
            torch_module = None
    if torch_module is not None:
        cuda = getattr(torch_module, "cuda", None)
        if cuda is not None and bool(cuda.is_available()):
            try:
                synchronize = getattr(cuda, "synchronize", None)
                if callable(synchronize):
                    synchronize()
            except Exception:
                LOGGER.debug("cuda synchronize failed during cleanup", exc_info=True)
            try:
                empty_cache = getattr(cuda, "empty_cache", None)
                if callable(empty_cache):
                    empty_cache()
            except Exception:
                LOGGER.debug("cuda empty_cache failed during cleanup", exc_info=True)
    after = gpu_snapshot(torch_module)
    LOGGER.info("GPU resources released | label=%s | before=%s | after=%s", label, before, after)
    return {"label": label, "before": before, "after": after}
