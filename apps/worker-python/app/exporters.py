from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import json
from datetime import datetime, timezone

from app.schemas import TaskSpec, TranscriptSegment


def export_transcript_bundle(
    task: TaskSpec,
    transcript_segments: list[TranscriptSegment],
    detected_languages: list[str],
    job_dir: Path,
) -> dict:
    task.output_dir.mkdir(parents=True, exist_ok=True)
    job_dir.mkdir(parents=True, exist_ok=True)

    markdown = build_markdown(task, transcript_segments, detected_languages)
    transcript_json = {
        "job_id": task.job_id,
        "source_path": str(task.source_path),
        "output_md_path": str(task.output_md_path),
        "asr_backend": task.asr_backend,
        "asr_profile_name": task.asr_profile_name,
        "asr_model": task.asr_model_name,
        "context_text": task.context_text,
        "terms": task.terms,
        "segments": [segment.to_dict() for segment in transcript_segments],
        "detected_languages": detected_languages,
    }
    job_json = {
        "job_id": task.job_id,
        "source_path": str(task.source_path),
        "output_dir": str(task.output_dir),
        "output_file_name": task.output_file_name,
        "asr_backend": task.asr_backend,
        "asr_profile_name": task.asr_profile_name,
        "asr_model": task.asr_model_name,
        "language_mode": task.language_mode,
        "fixed_language": task.fixed_language,
        "enable_speaker_diarization": task.enable_speaker_diarization,
        "context_text": task.context_text,
        "terms": task.terms,
        "replacements": [asdict(rule) for rule in task.replacements],
    }

    task.output_md_path.write_text(markdown, encoding="utf-8")
    task.output_json_path.write_text(
        json.dumps(transcript_json, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (job_dir / "job.json").write_text(
        json.dumps(job_json, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return {
        "md_path": str(task.output_md_path),
        "transcript_json_path": str(task.output_json_path),
        "job_json_path": str(job_dir / "job.json"),
    }


def build_markdown(
    task: TaskSpec,
    transcript_segments: list[TranscriptSegment],
    detected_languages: list[str],
) -> str:
    generated_at = datetime.now(timezone.utc).astimezone().isoformat()
    language = detected_languages[0] if detected_languages else "unknown"

    lines = [
        "---",
        f"source_file: {task.source_path}",
        f"generated_at: {generated_at}",
        f"asr_backend: {task.asr_backend}",
        f"asr_profile: {task.asr_profile_name or 'local'}",
        f"model: {task.asr_model_name}",
        "speaker_diarization: true" if task.enable_speaker_diarization else "speaker_diarization: false",
        f"language: {language}",
        "---",
        "",
        "# Transcript",
        "",
        "## Task Info",
        "",
        f"- Source file: `{task.source_path}`",
        f"- Output file: `{task.output_file_name}`",
        f"- ASR backend: `{task.asr_backend}`",
        f"- ASR model: `{task.asr_model_name}`",
        f"- Speaker diarization: `{'enabled' if task.enable_speaker_diarization else 'disabled'}`",
        f"- Language mode: `{task.language_mode}`",
        "",
        "## Context",
        "",
        task.context_text or "_none_",
        "",
    ]

    if task.terms:
        lines.extend(["## Terms", ""])
        for term in task.terms:
            lines.append(f"- {term}")
        lines.append("")

    lines.extend(["## Transcript Body", ""])
    if not transcript_segments:
        lines.append("_no transcript segments generated_")
    else:
        for segment in transcript_segments:
            lines.append(
                f"- [{format_ms(segment.start_ms)} - {format_ms(segment.end_ms)}] "
                f"{segment.speaker}: {segment.normalized_text or segment.text or '[empty]'}"
            )
    lines.append("")

    return "\n".join(lines)


def format_ms(value: int) -> str:
    total_seconds = max(0, value // 1000)
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
