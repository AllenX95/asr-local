from __future__ import annotations

import logging
import os
from pathlib import Path


_LOGGER = logging.getLogger("asr_local.worker")
_CONFIGURED = False


def configure_worker_logging() -> Path | None:
    global _CONFIGURED
    if _CONFIGURED:
        return _log_path()

    log_path = _log_path()
    if log_path is None:
        logging.basicConfig(level=logging.INFO)
        _LOGGER.info("worker logging initialized without file output")
        _CONFIGURED = True
        return None

    log_path.parent.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s | %(message)s")
    handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(handler)

    _LOGGER.setLevel(logging.INFO)
    _LOGGER.propagate = True

    _CONFIGURED = True
    _LOGGER.info("worker logging initialized | path=%s", log_path)
    return log_path


def get_logger() -> logging.Logger:
    if not _CONFIGURED:
        configure_worker_logging()
    return _LOGGER


def _log_path() -> Path | None:
    raw_value = os.environ.get("ASR_LOCAL_WORKER_LOG", "").strip()
    if not raw_value:
        return None
    return Path(raw_value)
