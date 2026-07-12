from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
import importlib.util
import os
import sys

from app.config import config_path, load_models_config, project_root


@dataclass(slots=True)
class ModelPathStatus:
    configured_path: str
    exists: bool
    required: bool
    description: str


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except ModuleNotFoundError:
        return False


def _status_for(path: Path, required: bool, description: str) -> ModelPathStatus:
    return ModelPathStatus(
        configured_path=str(path),
        exists=path.exists(),
        required=required,
        description=description,
    )


def _torch_runtime_snapshot() -> dict:
    if not _module_available("torch"):
        return {"available": False}
    try:
        import torch
    except Exception as exc:
        return {"available": False, "error": str(exc)}

    cuda_available = bool(torch.cuda.is_available())
    return {
        "available": True,
        "cuda_available": cuda_available,
        "selected_device": "cuda:0" if cuda_available else "cpu",
        "cuda_device_count": torch.cuda.device_count() if cuda_available else 0,
        "cuda_device_name": (
            torch.cuda.get_device_name(0) if cuda_available else None
        ),
    }


def environment_snapshot() -> dict:
    models = load_models_config()
    root = project_root()

    qwen_path = models.qwen3_asr_1_7b.resolved_path(root)
    moss_path = models.moss_transcribe_diarize.resolved_path(root)
    pyannote_path = models.pyannote_speaker_diarization.resolved_path(root)
    qwen_runtime_path = _resolve_qwen_runtime_path(root)

    return {
        "project_root": str(root),
        "config_path": str(config_path()),
        "python_version": sys.version.split()[0],
        "optional_modules": {
            "torch": _module_available("torch"),
            "transformers": _module_available("transformers"),
            "qwen_asr": _module_available("qwen_asr"),
            "qwen_asr_runtime": bool(
                qwen_runtime_path
                and (qwen_runtime_path.parent.parent / "Lib" / "site-packages" / "qwen_asr").exists()
            ),
            "pyannote.audio": _module_available("pyannote.audio"),
            "cloud_asr_stdlib_client": True,
        },
        "torch_runtime": _torch_runtime_snapshot(),
        "qwen_runtime": str(qwen_runtime_path) if qwen_runtime_path else None,
        "models": {
            "active_local_asr_model": models.active_local_asr_model,
            "qwen3_asr_1_7b": asdict(
                _status_for(
                    qwen_path,
                    models.qwen3_asr_1_7b.required,
                    models.qwen3_asr_1_7b.description,
                )
            ),
            "moss_transcribe_diarize": asdict(
                _status_for(
                    moss_path,
                    models.moss_transcribe_diarize.required,
                    models.moss_transcribe_diarize.description,
                )
            ),
            "pyannote_speaker_diarization": asdict(
                _status_for(
                    pyannote_path,
                    models.pyannote_speaker_diarization.required,
                    models.pyannote_speaker_diarization.description,
                )
            ),
        },
    }


def _resolve_qwen_runtime_path(root: Path) -> Path | None:
    configured = os.environ.get("ASR_LOCAL_QWEN_PYTHON", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    candidates = (
        root / "runtime" / "qwen-python" / "python.exe",
        root / "apps" / "worker-python" / ".venv-qwen" / "Scripts" / "python.exe",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None
