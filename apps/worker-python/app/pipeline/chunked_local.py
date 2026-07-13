"""Shared v2 adapter for Pyannote-first local ASR backends.

The existing, well-tested segment/export implementation remains in
``job_runner`` for this first migration slice.  This adapter makes the
backend choice explicit per immutable workflow snapshot and forces every
local model through external Pyannote segmentation.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import threading
from typing import Any

from app.models.manager import ModelManager
from app.pipeline.job_runner import run_job


_LOCAL_GPU_LANE = threading.Semaphore(1)


class ChunkedLocalTranscriber:
    backend_id = "pyannote_qwen3_asr"

    async def transcribe(self, spec: dict[str, Any], attempt_id: str, *, progress=None) -> dict[str, Any]:
        return await asyncio.to_thread(self._transcribe_sync, spec, attempt_id, progress)

    def _transcribe_sync(self, spec: dict[str, Any], attempt_id: str, progress=None) -> dict[str, Any]:
        workflow_id = str(spec.get("workflow_id", "workflow-v2"))
        manager = ModelManager()
        _validate_snapshot_paths(spec, manager)
        output_root = Path(spec["output"]["directory"])
        workflow_staging = output_root / ".staging" / workflow_id
        staging_dir = workflow_staging / f"chunked-{self.backend_id}-{attempt_id}"
        payload = {
            "job_id": workflow_id,
            "source_path": spec["source"]["path"],
            "output_dir": str(staging_dir),
            "output_file_name": "transcript.md",
            "job_workspace_dir": str(output_root / ".jobs" / workflow_id),
            "asr_backend": "local",
            "language_mode": spec["transcription"].get("language", {}).get("mode", "auto"),
            "fixed_language": spec["transcription"].get("language", {}).get("value"),
            "enable_speaker_diarization": True,
            "force_external_diarization": True,
            "local_asr_model": "qwen3_asr_1_7b",
            "context_text": _context_text(spec),
            "terms": spec["transcription"].get("prompt_input", {}).get("hotwords", []),
            "replacements": spec["transcription"].get("postprocess", {}).get("replacements", []),
            "keep_fillers": spec["transcription"].get("postprocess", {}).get("keep_fillers", True),
            "auto_punctuation": spec["transcription"].get("postprocess", {}).get("auto_punctuation", True),
        }

        def emit(job_id: str, update: dict[str, Any]) -> None:
            del job_id
            if progress is None:
                return
            progress({
                "phase": _phase_name(str(update.get("stage", "transcribing"))),
                "detail": _phase_detail(str(update.get("stage", "transcribing"))),
                **update,
            })

        acquired = False
        try:
            if progress:
                progress({"phase": "gpu_waiting", "detail": "正在等待本地 GPU 推理通道"})
            _LOCAL_GPU_LANE.acquire()
            acquired = True
            result = run_job(payload, emit=emit, model_manager=manager)
            path = Path(result["md_path"])
            return {
                "kind": "transcript_markdown",
                "text": path.read_text(encoding="utf-8"),
                "warnings": result.get("warnings", []),
                "diagnostics": {
                    "backend_id": self.backend_id,
                    "asr_model": result.get("asr_model"),
                    "segment_count": result.get("segments", 0),
                    "speaker_count": result.get("speakers", 0),
                    "warnings": result.get("warnings", []),
                },
            }
        finally:
            if acquired:
                _LOCAL_GPU_LANE.release()
            manager.close_local_models()

    def close(self) -> None:
        # A manager is deliberately task-scoped, so cleanup happens in the
        # synchronous finally block.  Keep this method for adapter symmetry.
        return None


def _context_text(spec: dict[str, Any]) -> str:
    prompt = spec["transcription"].get("prompt_input", {})
    values = []
    if prompt.get("recording_background"):
        values.append(str(prompt["recording_background"]))
    if prompt.get("extra_instruction"):
        values.append(str(prompt["extra_instruction"]))
    return "\n\n".join(values)


def _phase_name(stage: str) -> str:
    return {
        "preparing": "preparing",
        "decoding": "audio_normalizing",
        "diarizing": "diarizing",
        "segmenting": "segmenting",
        "transcribing": "transcribing",
        "merging": "merging",
        "normalizing": "normalizing",
        "exporting": "exporting",
    }.get(stage, stage)


def _phase_detail(stage: str) -> str:
    return {
        "preparing": "正在准备任务",
        "decoding": "正在标准化音频",
        "diarizing": "正在执行 Pyannote 说话人分析",
        "segmenting": "正在生成安全转录分块",
        "transcribing": "正在按分块执行语音识别",
        "merging": "正在合并转录结果",
        "normalizing": "正在整理文本",
        "exporting": "正在写入转录产物",
    }.get(stage, "正在处理任务")


def _validate_snapshot_paths(spec: dict[str, Any], manager: ModelManager) -> None:
    components = spec["transcription"].get("model_snapshot", {}).get("components", [])
    expected = {item.get("role"): Path(item.get("resolved_path", "")).resolve() for item in components}
    actual = {
        "transcriber": manager.qwen_path.resolve(),
        "diarization": manager.pyannote_path.resolve(),
    }
    for role, path in actual.items():
        if role in expected and expected[role] != path:
            raise RuntimeError(f"MODEL_SNAPSHOT_MISMATCH: {role} path changed after workflow submission")
