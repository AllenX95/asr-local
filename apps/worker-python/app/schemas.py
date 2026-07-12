from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
import uuid

from app.config import project_root


@dataclass(slots=True)
class ReplacementRule:
    wrong: str
    correct: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "ReplacementRule":
        return cls(
            wrong=str(payload.get("wrong", "")).strip(),
            correct=str(payload.get("correct", "")).strip(),
        )


@dataclass(slots=True)
class AsrCloudProfile:
    name: str
    base_url: str
    model: str
    api_key: str = ""

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "AsrCloudProfile":
        return cls(
            name=str(payload.get("name", "")).strip(),
            base_url=str(payload.get("base_url", "")).strip(),
            model=str(payload.get("model", "")).strip(),
            api_key=str(payload.get("api_key", "")).strip(),
        )


@dataclass(slots=True)
class TaskSpec:
    job_id: str
    source_path: Path
    output_dir: Path
    output_file_name: str
    asr_backend: str = "local"
    cloud_asr_profile: AsrCloudProfile | None = None
    language_mode: str = "auto"
    fixed_language: str | None = None
    enable_speaker_diarization: bool = True
    context_text: str = ""
    terms: list[str] = field(default_factory=list)
    replacements: list[ReplacementRule] = field(default_factory=list)
    keep_fillers: bool = True
    auto_punctuation: bool = True
    local_asr_model: str | None = None
    force_external_diarization: bool = False

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "TaskSpec":
        source_path = Path(payload["source_path"]).expanduser()
        output_dir = payload.get("output_dir")
        resolved_output_dir = (
            Path(output_dir).expanduser()
            if output_dir
            else project_root() / "outputs"
        )

        output_file_name = str(payload.get("output_file_name") or "").strip()
        if not output_file_name:
            output_file_name = f"{source_path.stem}.transcript.md"

        asr_backend = str(payload.get("asr_backend") or "local").strip().lower()
        if asr_backend not in {"local", "cloud"}:
            raise ValueError(f"Unsupported ASR backend: {asr_backend}")
        cloud_profile_payload = payload.get("cloud_asr_profile")
        cloud_asr_profile = (
            AsrCloudProfile.from_payload(cloud_profile_payload)
            if isinstance(cloud_profile_payload, dict)
            else None
        )
        if asr_backend == "cloud":
            if cloud_asr_profile is None:
                raise ValueError("Cloud ASR profile is required.")
            if not cloud_asr_profile.base_url:
                raise ValueError("Cloud ASR base URL is empty.")
            if not cloud_asr_profile.model:
                raise ValueError("Cloud ASR model is empty.")

        return cls(
            job_id=str(payload.get("job_id") or f"job_{uuid.uuid4().hex[:12]}"),
            source_path=source_path,
            output_dir=resolved_output_dir,
            output_file_name=output_file_name,
            asr_backend=asr_backend,
            cloud_asr_profile=cloud_asr_profile,
            language_mode=str(payload.get("language_mode") or "auto"),
            fixed_language=payload.get("fixed_language"),
            enable_speaker_diarization=bool(
                payload.get("enable_speaker_diarization", True)
            ),
            context_text=str(payload.get("context_text") or "").strip(),
            terms=[
                str(item).strip()
                for item in payload.get("terms", [])
                if str(item).strip()
            ],
            replacements=[
                ReplacementRule.from_payload(item)
                for item in payload.get("replacements", [])
            ],
            keep_fillers=bool(payload.get("keep_fillers", True)),
            auto_punctuation=bool(payload.get("auto_punctuation", True)),
            local_asr_model=(
                str(payload.get("local_asr_model")).strip()
                if payload.get("local_asr_model")
                else None
            ),
            force_external_diarization=bool(payload.get("force_external_diarization", False)),
        )

    @property
    def output_md_path(self) -> Path:
        return self.output_dir / self.output_file_name

    @property
    def output_json_path(self) -> Path:
        return self.output_dir / self.output_file_name.replace(".md", ".json")

    @property
    def asr_profile_name(self) -> str | None:
        if self.asr_backend != "cloud" or self.cloud_asr_profile is None:
            return None
        return self.cloud_asr_profile.name or None

    @property
    def asr_model_name(self) -> str:
        if self.asr_backend == "cloud" and self.cloud_asr_profile is not None:
            return self.cloud_asr_profile.model or "cloud-asr"
        return "Qwen/Qwen3-ASR-1.7B"


@dataclass(slots=True)
class SpeakerSegment:
    segment_id: str
    speaker: str
    start_ms: int
    end_ms: int
    duration_ms: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class TranscriptSegment:
    segment_id: str
    speaker: str
    start_ms: int
    end_ms: int
    text: str
    normalized_text: str
    language: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
