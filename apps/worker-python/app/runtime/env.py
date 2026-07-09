from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
import importlib.util
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


def environment_snapshot() -> dict:
    models = load_models_config()
    root = project_root()

    qwen_path = models.qwen3_asr_1_7b.resolved_path(root)
    pyannote_path = models.pyannote_speaker_diarization.resolved_path(root)

    return {
        "project_root": str(root),
        "config_path": str(config_path()),
        "python_version": sys.version.split()[0],
        "optional_modules": {
            "torch": _module_available("torch"),
            "transformers": _module_available("transformers"),
            "qwen_asr": _module_available("qwen_asr"),
            "pyannote.audio": _module_available("pyannote.audio"),
            "cloud_asr_stdlib_client": True,
        },
        "models": {
            "qwen3_asr_1_7b": asdict(
                _status_for(
                    qwen_path,
                    models.qwen3_asr_1_7b.required,
                    models.qwen3_asr_1_7b.description,
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
