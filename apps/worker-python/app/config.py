from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import tomllib

DEFAULT_QWEN_CONFIG = {
    "path": "models/Qwen/Qwen3-ASR-1.7B",
    "required": True,
    "description": "Manual local path for the downloaded Qwen3-ASR-1.7B model directory.",
}
DEFAULT_PYANNOTE_CONFIG = {
    "path": "models/pyannote/speaker-diarization-community-1",
    "required": True,
    "description": "Manual local path for the downloaded pyannote speaker diarization model directory.",
}


def project_root() -> Path:
    configured = os.environ.get("ASR_LOCAL_PROJECT_ROOT", "").strip()
    return Path(configured).expanduser().resolve() if configured else Path(__file__).resolve().parents[3]


def state_dir() -> Path:
    configured = os.environ.get("ASR_LOCAL_STATE_DIR", "").strip()
    return Path(configured).expanduser().resolve() if configured else project_root() / "outputs" / ".workflow"


def config_dir() -> Path:
    configured = os.environ.get("ASR_LOCAL_CONFIG_DIR", "").strip()
    return Path(configured).expanduser().resolve() if configured else project_root() / "config"


def config_path() -> Path:
    return config_dir() / "models.toml"


@dataclass(slots=True)
class LocalModelConfig:
    path: str
    required: bool
    description: str

    def resolved_path(self, root: Path) -> Path:
        configured = Path(self.path)
        return configured if configured.is_absolute() else root / configured


@dataclass(slots=True)
class ModelsConfig:
    model_root: str
    qwen3_asr_1_7b: LocalModelConfig
    pyannote_speaker_diarization: LocalModelConfig


def load_models_config() -> ModelsConfig:
    raw = tomllib.loads(config_path().read_text(encoding="utf-8"))
    return ModelsConfig(
        model_root=raw.get("model_root", "models"),
        qwen3_asr_1_7b=LocalModelConfig(
            **raw.get("qwen3_asr_1_7b", DEFAULT_QWEN_CONFIG)
        ),
        pyannote_speaker_diarization=LocalModelConfig(
            **raw.get("pyannote_speaker_diarization", DEFAULT_PYANNOTE_CONFIG)
        ),
    )
