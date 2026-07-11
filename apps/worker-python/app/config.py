from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib

DEFAULT_LOCAL_ASR_MODEL = "moss_transcribe_diarize"
SUPPORTED_LOCAL_ASR_MODELS = {
    "qwen3_asr_1_7b",
    "moss_transcribe_diarize",
}

DEFAULT_QWEN_CONFIG = {
    "path": "models/Qwen/Qwen3-ASR-1.7B",
    "required": True,
    "description": "Manual local path for the downloaded Qwen3-ASR-1.7B model directory.",
}
DEFAULT_MOSS_CONFIG = {
    "path": "models/OpenMOSS-Team/MOSS-Transcribe-Diarize",
    "required": False,
    "description": "Manual local path for the downloaded MOSS-Transcribe-Diarize model directory.",
}
DEFAULT_PYANNOTE_CONFIG = {
    "path": "models/pyannote/speaker-diarization-community-1",
    "required": True,
    "description": "Manual local path for the downloaded pyannote speaker diarization model directory.",
}


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def config_path() -> Path:
    return project_root() / "config" / "models.toml"


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
    active_local_asr_model: str
    qwen3_asr_1_7b: LocalModelConfig
    moss_transcribe_diarize: LocalModelConfig
    pyannote_speaker_diarization: LocalModelConfig


def load_models_config() -> ModelsConfig:
    raw = tomllib.loads(config_path().read_text(encoding="utf-8"))
    active_local_asr_model = str(
        raw.get("active_local_asr_model") or DEFAULT_LOCAL_ASR_MODEL
    ).strip()
    if active_local_asr_model not in SUPPORTED_LOCAL_ASR_MODELS:
        supported = ", ".join(sorted(SUPPORTED_LOCAL_ASR_MODELS))
        raise ValueError(
            f"Unsupported active_local_asr_model: {active_local_asr_model}. "
            f"Supported values: {supported}"
        )
    return ModelsConfig(
        model_root=raw.get("model_root", "models"),
        active_local_asr_model=active_local_asr_model,
        qwen3_asr_1_7b=LocalModelConfig(
            **raw.get("qwen3_asr_1_7b", DEFAULT_QWEN_CONFIG)
        ),
        moss_transcribe_diarize=LocalModelConfig(
            **raw.get("moss_transcribe_diarize", DEFAULT_MOSS_CONFIG)
        ),
        pyannote_speaker_diarization=LocalModelConfig(
            **raw.get("pyannote_speaker_diarization", DEFAULT_PYANNOTE_CONFIG)
        ),
    )
