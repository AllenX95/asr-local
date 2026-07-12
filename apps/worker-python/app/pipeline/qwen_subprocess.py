"""Qwen ASR adapter backed by an isolated Python process.

Qwen's official package currently pins Transformers 4.57.x while the MOSS
runtime uses Transformers 5.x. Keeping Qwen behind this tiny JSONL boundary
allows both backends to remain production-selectable without importing the
incompatible package into the MOSS worker.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from collections import deque
from types import SimpleNamespace
from typing import Any

import soundfile as sf

from app.config import project_root


LOGGER = logging.getLogger("asr_local.worker.qwen_subprocess")


class QwenSubprocessAdapter:
    def __init__(
        self,
        *,
        python_executable: Path,
        model_path: Path,
        device: str,
        dtype: Any,
        batch_size: int = 1,
        max_new_tokens: int = 256,
    ) -> None:
        self.python_executable = python_executable.resolve()
        self.model_path = model_path.resolve()
        self.device = device
        self.dtype = dtype
        self.batch_size = batch_size
        self.max_new_tokens = max_new_tokens
        self._process: subprocess.Popen[str] | None = None
        self._lock = threading.RLock()
        self._stderr_tail: deque[str] = deque(maxlen=40)
        self._stderr_thread: threading.Thread | None = None

    def transcribe(
        self,
        *,
        audio,
        context,
        language,
        return_time_stamps: bool = False,
    ) -> list[SimpleNamespace]:
        if return_time_stamps:
            raise ValueError("QWEN_SUBPROCESS_TIMESTAMPS_UNSUPPORTED: use Pyannote segment boundaries")
        with self._lock:
            process = self._ensure_process()
            with tempfile.TemporaryDirectory(prefix="asr-local-qwen-segment-") as directory:
                paths: list[str] = []
                for index, (audio_array, sample_rate) in enumerate(audio):
                    path = Path(directory) / f"segment-{index:03d}.wav"
                    sf.write(str(path), audio_array, int(sample_rate), subtype="PCM_16")
                    paths.append(str(path))
                request = {
                    "audio_paths": paths,
                    "context": context,
                    "language": language,
                }
                try:
                    assert process.stdin is not None
                    process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
                    process.stdin.flush()
                    raw = process.stdout.readline() if process.stdout is not None else ""
                except (BrokenPipeError, OSError) as exc:
                    self.close()
                    raise RuntimeError("QWEN_SUBPROCESS_BROKEN: isolated Qwen worker stopped unexpectedly") from exc
                if not raw:
                    stderr = "\n".join(self._stderr_tail)
                    self.close()
                    raise RuntimeError(f"QWEN_SUBPROCESS_EXITED: {stderr or 'no response from Qwen worker'}")
                try:
                    response = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise RuntimeError(f"QWEN_SUBPROCESS_PROTOCOL: invalid response: {raw[:200]}") from exc
                if not response.get("ok"):
                    raise RuntimeError(str(response.get("error") or "QWEN_SUBPROCESS_FAILED"))
                return [
                    SimpleNamespace(
                        text=str(item.get("text", "")),
                        language=item.get("language"),
                        segments=[],
                    )
                    for item in response.get("results", [])
                ]

    def _ensure_process(self) -> subprocess.Popen[str]:
        if self._process is not None and self._process.poll() is None:
            return self._process
        if self._process is not None:
            # Reap a previous child before starting another one.  This keeps
            # the stderr drain thread and the OS process handle bounded when
            # a segment causes the child to exit unexpectedly.
            self.close()
        if not self.python_executable.is_file():
            raise RuntimeError(f"QWEN_RUNTIME_UNAVAILABLE: Python executable does not exist: {self.python_executable}")
        if not self.model_path.is_dir():
            raise FileNotFoundError(f"Qwen model path does not exist: {self.model_path}")
        script = project_root() / "apps" / "worker-python" / "scripts" / "qwen_segment_worker.py"
        env = os.environ.copy()
        worker_root = str(project_root() / "apps" / "worker-python")
        env["PYTHONPATH"] = worker_root + os.pathsep + env.get("PYTHONPATH", "")
        command = [
            str(self.python_executable),
            "-X",
            "utf8",
            "-u",
            str(script),
            "--model-path",
            str(self.model_path),
            "--device",
            self.device,
            "--dtype",
            _dtype_name(self.dtype),
            "--batch-size",
            str(self.batch_size),
            "--max-new-tokens",
            str(self.max_new_tokens),
        ]
        LOGGER.info("starting isolated Qwen runtime | python=%s | model=%s", self.python_executable, self.model_path)
        self._process = subprocess.Popen(
            command,
            cwd=worker_root,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        self._stderr_tail.clear()
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr,
            args=(self._process,),
            name="asr-local-qwen-stderr",
            daemon=True,
        )
        self._stderr_thread.start()
        return self._process

    def _drain_stderr(self, process: subprocess.Popen[str]) -> None:
        stream = process.stderr
        if stream is None:
            return
        try:
            for line in stream:
                message = line.strip()
                if not message:
                    continue
                with self._lock:
                    self._stderr_tail.append(message)
                LOGGER.debug("isolated Qwen runtime stderr | %s", message)
        except (OSError, ValueError):
            LOGGER.debug("isolated Qwen stderr stream closed", exc_info=True)

    def close(self) -> None:
        thread: threading.Thread | None = None
        with self._lock:
            process = self._process
            self._process = None
            if process is None:
                return
            try:
                if process.poll() is None and process.stdin is not None:
                    process.stdin.write(json.dumps({"shutdown": True}) + "\n")
                    process.stdin.flush()
                    process.wait(timeout=10)
            except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                except OSError:
                    pass
            finally:
                thread = self._stderr_thread
                self._stderr_thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=1)


def resolve_qwen_python() -> Path | None:
    configured = os.environ.get("ASR_LOCAL_QWEN_PYTHON", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    candidates = (
        project_root() / "runtime" / "qwen-python" / "python.exe",
        project_root() / "apps" / "worker-python" / ".venv-qwen" / "Scripts" / "python.exe",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def _dtype_name(dtype: Any) -> str:
    name = str(dtype).split(".")[-1]
    return name if name in {"float32", "float16", "bfloat16"} else "float16"
