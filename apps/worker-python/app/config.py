from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib


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
    qwen3_asr_1_7b: LocalModelConfig
    pyannote_speaker_diarization: LocalModelConfig


def load_models_config() -> ModelsConfig:
    raw = tomllib.loads(config_path().read_text(encoding="utf-8"))
    return ModelsConfig(
        model_root=raw["model_root"],
        qwen3_asr_1_7b=LocalModelConfig(**raw["qwen3_asr_1_7b"]),
        pyannote_speaker_diarization=LocalModelConfig(
            **raw["pyannote_speaker_diarization"]
        ),
    )
