from __future__ import annotations

from collections import Counter
from pathlib import Path
import json
import os
import re
import shutil
import time

from app.audio import TARGET_SR, audio_duration_ms, load_and_normalize_audio, slice_audio
from app.config import project_root
from app.exporters import export_transcript_bundle
from app.logging_utils import get_logger
from app.models.manager import ModelManager
from app.pipeline.cloud_asr import CloudAsrClient
from app.pipeline.segment_planner import plan_segments
from app.pipeline.segment_types import DiarizationTurn
from app.pipeline.pyannote_provider import PyannoteDiarizationProvider
from app.schemas import SpeakerSegment, TaskSpec, TranscriptSegment


MIN_SEGMENT_MS = 800
MERGE_GAP_MS = 300
MAX_SEGMENT_MS = 30_000
ASR_SEGMENT_BATCH_SIZE = 2
DEFAULT_JOB_RETENTION_DAYS = 14
DEFAULT_JOB_RETENTION_COUNT = 100
KEEP_NORMALIZED_WAV_ENV = "ASR_LOCAL_KEEP_NORMALIZED_WAV"
JOB_RETENTION_DAYS_ENV = "ASR_LOCAL_JOB_RETENTION_DAYS"
JOB_RETENTION_COUNT_ENV = "ASR_LOCAL_JOB_RETENTION_COUNT"

_MODEL_MANAGER = ModelManager()
LOGGER = get_logger()


class JobTerminated(RuntimeError):
    pass


def run_job(payload: dict, emit=None, model_manager: ModelManager | None = None) -> dict:
    task = TaskSpec.from_payload(payload)
    manager = model_manager or _MODEL_MANAGER
    manager.refresh_config()
    LOGGER.info(
        "job runner started | job_id=%s | source=%s | output_dir=%s | asr_backend=%s | diarization=%s",
        task.job_id,
        task.source_path,
        task.output_dir,
        task.asr_backend,
        task.enable_speaker_diarization,
    )
    task.output_dir.mkdir(parents=True, exist_ok=True)

    if not task.source_path.exists():
        LOGGER.error("source file missing | job_id=%s | source=%s", task.job_id, task.source_path)
        raise FileNotFoundError(f"Source file does not exist: {task.source_path}")

    job_dir = project_root() / "outputs" / ".jobs" / task.job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    normalized_wav_path = job_dir / "normalized.wav"

    _emit(
        emit,
        task,
        stage="preparing",
        progress=0.02,
        total_ms=0,
        payload={
            "runtime": manager.runtime_summary(
                include_device=task.asr_backend == "local" or task.enable_speaker_diarization
            ),
            "asr_backend": task.asr_backend,
            "asr_profile_name": task.asr_profile_name,
            "asr_model": resolve_asr_model_name(task, manager),
        },
    )
    check_job_control(task, job_dir, emit=emit, progress=0.02, total_ms=0)

    audio, sample_rate, audio_backend = load_and_normalize_audio(
        task.source_path,
        normalized_wav_path,
    )
    LOGGER.info(
        "audio decoded | job_id=%s | backend=%s | sample_rate=%s | normalized_wav=%s",
        task.job_id,
        audio_backend,
        sample_rate,
        normalized_wav_path,
    )
    total_ms = audio_duration_ms(audio, sample_rate)
    _emit(
        emit,
        task,
        stage="decoding",
        progress=0.10,
        total_ms=total_ms,
        payload={
            "audio_backend": audio_backend,
            "normalized_wav_path": str(normalized_wav_path),
            "sample_rate": sample_rate,
        },
    )
    check_job_control(task, job_dir, emit=emit, progress=0.10, total_ms=total_ms)

    speaker_segments = build_speaker_segments(
        task,
        audio=audio,
        sample_rate=sample_rate,
        normalized_wav_path=normalized_wav_path,
        total_ms=total_ms,
        model_manager=manager,
    )
    LOGGER.info(
        "speaker diarization finished | job_id=%s | raw_segment_count=%s",
        task.job_id,
        len(speaker_segments),
    )
    _emit(
        emit,
        task,
        stage="diarizing",
        progress=0.28,
        total_ms=total_ms,
        payload={"segment_count": len(speaker_segments)},
    )
    check_job_control(task, job_dir, emit=emit, progress=0.28, total_ms=total_ms)

    integrated_diarization = (
        task.asr_backend == "local"
        and not task.force_external_diarization
        and manager.local_asr_uses_integrated_diarization()
    )
    normalized_segments = normalize_speaker_segments(
        speaker_segments,
        total_ms,
        split_long=not integrated_diarization,
    )
    LOGGER.info(
        "speaker segments normalized | job_id=%s | normalized_segment_count=%s",
        task.job_id,
        len(normalized_segments),
    )
    _emit(
        emit,
        task,
        stage="segmenting",
        progress=0.36,
        total_ms=total_ms,
        payload={"normalized_segment_count": len(normalized_segments)},
    )
    check_job_control(task, job_dir, emit=emit, progress=0.36, total_ms=total_ms)

    if task.force_external_diarization and task.asr_backend == "local":
        _emit(
            emit,
            task,
            stage="releasing_model",
            progress=0.40,
            total_ms=total_ms,
            payload={"detail": "正在释放 Pyannote 显存，准备加载 ASR 模型"},
        )
        manager.close_pyannote_pipeline()

    transcription_warnings: list[dict] = []
    transcript_segments = transcribe_segments(
        task=task,
        audio=audio,
        sample_rate=sample_rate,
        speaker_segments=normalized_segments,
        total_ms=total_ms,
        job_dir=job_dir,
        emit=emit,
        model_manager=manager,
        warnings=transcription_warnings,
    )

    _emit(
        emit,
        task,
        stage="merging",
        progress=0.88,
        total_ms=total_ms,
        processed_ms=total_ms,
        payload={"transcript_segment_count": len(transcript_segments)},
    )
    check_job_control(
        task,
        job_dir,
        emit=emit,
        progress=0.88,
        total_ms=total_ms,
        processed_ms=total_ms,
    )

    detected_languages = sorted(
        {
            segment.language
            for segment in transcript_segments
            if segment.language and segment.language.strip()
        }
    )

    _emit(
        emit,
        task,
        stage="normalizing",
        progress=0.93,
        total_ms=total_ms,
        processed_ms=total_ms,
        payload={"detected_languages": detected_languages},
    )
    check_job_control(
        task,
        job_dir,
        emit=emit,
        progress=0.93,
        total_ms=total_ms,
        processed_ms=total_ms,
    )

    exported = export_transcript_bundle(
        task=task,
        transcript_segments=transcript_segments,
        detected_languages=detected_languages,
        job_dir=job_dir,
    )
    LOGGER.info(
        "transcript exported | job_id=%s | md_path=%s | transcript_json_path=%s",
        task.job_id,
        exported.get("md_path"),
        exported.get("transcript_json_path"),
    )

    (job_dir / "segments.json").write_text(
        json.dumps(
            {
                "speaker_segments": [segment.to_dict() for segment in normalized_segments],
                "transcript_segments": [segment.to_dict() for segment in transcript_segments],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    speaker_count = len(
        {
            segment.speaker
            for segment in transcript_segments
            if segment.speaker and segment.speaker.strip()
        }
    )

    _emit(
        emit,
        task,
        stage="exporting",
        progress=0.99,
        total_ms=total_ms,
        processed_ms=total_ms,
        payload={
            "md_path": exported["md_path"],
            "speaker_count": speaker_count,
        },
    )
    cleanup_normalized_wav(task, normalized_wav_path)
    cleanup_job_cache(current_job_id=task.job_id)

    return {
        **exported,
        "job_dir": str(job_dir),
        "source_path": str(task.source_path),
        "segments": len(transcript_segments),
        "speakers": speaker_count,
        "total_ms": total_ms,
        "detected_languages": detected_languages,
        "asr_backend": task.asr_backend,
        "asr_profile_name": task.asr_profile_name,
        "asr_model": resolve_asr_model_name(task, manager),
        "warnings": transcription_warnings,
    }


def clear_job_control(job_dir: Path) -> None:
    for file_name in ("control.pause", "control.cancel"):
        path = job_dir / file_name
        if path.exists():
            try:
                path.unlink()
            except OSError:
                LOGGER.warning("failed to remove stale control flag | path=%s", path)


def cleanup_normalized_wav(task: TaskSpec, normalized_wav_path: Path) -> None:
    if env_flag(KEEP_NORMALIZED_WAV_ENV):
        LOGGER.info(
            "keeping normalized wav for debugging | job_id=%s | path=%s | env=%s",
            task.job_id,
            normalized_wav_path,
            KEEP_NORMALIZED_WAV_ENV,
        )
        return

    if not normalized_wav_path.exists():
        return

    try:
        size_bytes = normalized_wav_path.stat().st_size
        normalized_wav_path.unlink()
        LOGGER.info(
            "normalized wav deleted | job_id=%s | path=%s | released_bytes=%s",
            task.job_id,
            normalized_wav_path,
            size_bytes,
        )
    except OSError as exc:
        LOGGER.warning(
            "failed to delete normalized wav | job_id=%s | path=%s | error=%s",
            task.job_id,
            normalized_wav_path,
            exc,
        )


def cleanup_job_cache(current_job_id: str) -> None:
    jobs_root = project_root() / "outputs" / ".jobs"
    if not jobs_root.exists():
        return

    retention_days = env_int(JOB_RETENTION_DAYS_ENV, DEFAULT_JOB_RETENTION_DAYS)
    retention_count = env_int(JOB_RETENTION_COUNT_ENV, DEFAULT_JOB_RETENTION_COUNT)
    job_dirs = [
        item
        for item in jobs_root.iterdir()
        if item.is_dir() and item.name != current_job_id and not has_control_flag(item)
    ]

    deleted: set[Path] = set()
    if retention_days > 0:
        cutoff = time.time() - (retention_days * 24 * 60 * 60)
        for job_dir in job_dirs:
            try:
                modified_at = job_dir.stat().st_mtime
            except OSError:
                continue
            if modified_at < cutoff and delete_job_dir(job_dir, reason=f"older than {retention_days} days"):
                deleted.add(job_dir)

    if retention_count > 0:
        remaining_dirs = [job_dir for job_dir in job_dirs if job_dir not in deleted]
        remaining_dirs.sort(key=job_dir_mtime, reverse=True)
        for job_dir in remaining_dirs[retention_count:]:
            if delete_job_dir(job_dir, reason=f"exceeds newest {retention_count} jobs"):
                deleted.add(job_dir)

    if deleted:
        LOGGER.info("job cache cleanup finished | deleted_dirs=%s", len(deleted))


def has_control_flag(job_dir: Path) -> bool:
    return (job_dir / "control.pause").exists() or (job_dir / "control.cancel").exists()


def job_dir_mtime(job_dir: Path) -> float:
    try:
        return job_dir.stat().st_mtime
    except OSError:
        return 0.0


def delete_job_dir(job_dir: Path, reason: str) -> bool:
    try:
        shutil.rmtree(job_dir)
        LOGGER.info("job cache directory deleted | path=%s | reason=%s", job_dir, reason)
        return True
    except OSError as exc:
        LOGGER.warning(
            "failed to delete job cache directory | path=%s | reason=%s | error=%s",
            job_dir,
            reason,
            exc,
        )
        return False


def env_flag(name: str, default: bool = False) -> bool:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    raw_value = os.environ.get(name, "").strip()
    if not raw_value:
        return default
    try:
        return max(0, int(raw_value))
    except ValueError:
        LOGGER.warning("invalid integer environment value | name=%s | value=%s", name, raw_value)
        return default


def check_job_control(
    task: TaskSpec,
    job_dir: Path,
    emit=None,
    progress: float = 0.0,
    total_ms: int = 0,
    processed_ms: int = 0,
) -> None:
    pause_path = job_dir / "control.pause"
    cancel_path = job_dir / "control.cancel"

    if cancel_path.exists():
        LOGGER.warning("job termination requested | job_id=%s", task.job_id)
        _emit(
            emit,
            task,
            stage="terminating",
            progress=progress,
            total_ms=total_ms,
            processed_ms=processed_ms,
        )
        raise JobTerminated("Job terminated by user.")

    if not pause_path.exists():
        return

    LOGGER.info("job pause requested | job_id=%s", task.job_id)
    _emit(
        emit,
        task,
        stage="paused",
        progress=progress,
        total_ms=total_ms,
        processed_ms=processed_ms,
    )

    while pause_path.exists():
        if cancel_path.exists():
            LOGGER.warning("job termination requested while paused | job_id=%s", task.job_id)
            _emit(
                emit,
                task,
                stage="terminating",
                progress=progress,
                total_ms=total_ms,
                processed_ms=processed_ms,
            )
            raise JobTerminated("Job terminated by user.")
        time.sleep(0.25)

    LOGGER.info("job resumed | job_id=%s", task.job_id)
    _emit(
        emit,
        task,
        stage="resumed",
        progress=progress,
        total_ms=total_ms,
        processed_ms=processed_ms,
    )


def build_speaker_segments(
    task: TaskSpec,
    audio,
    sample_rate: int,
    normalized_wav_path: Path,
    total_ms: int,
    model_manager: ModelManager | None = None,
) -> list[SpeakerSegment]:
    manager = model_manager or _MODEL_MANAGER
    if not task.enable_speaker_diarization:
        LOGGER.info("speaker diarization disabled | job_id=%s", task.job_id)
        return [
            SpeakerSegment(
                segment_id="segment-0001",
                speaker="Speaker 1",
                start_ms=0,
                end_ms=total_ms,
                duration_ms=total_ms,
            )
        ]

    if (
        task.asr_backend == "local"
        and not task.force_external_diarization
        and manager.local_asr_uses_integrated_diarization()
    ):
        LOGGER.info(
            "speaker diarization handled by active local ASR model | job_id=%s | model=%s",
            task.job_id,
            manager.local_asr_model_name(),
        )
        return [
            SpeakerSegment(
                segment_id="segment-0001",
                speaker="Speaker 1",
                start_ms=0,
                end_ms=total_ms,
                duration_ms=total_ms,
            )
        ]

    LOGGER.info(
        "running pyannote diarization | job_id=%s | sample_rate=%s | wav=%s",
        task.job_id,
        sample_rate,
        normalized_wav_path,
    )
    turns = PyannoteDiarizationProvider(model_manager=manager).diarize(
        audio=audio,
        sample_rate=sample_rate,
        uri=normalized_wav_path.name,
        total_ms=total_ms,
    )
    segments = [
        SpeakerSegment(
            segment_id=f"segment-{index:04d}",
            speaker=turn.speaker,
            start_ms=turn.start_ms,
            end_ms=turn.end_ms,
            duration_ms=turn.duration_ms,
        )
        for index, turn in enumerate(turns, start=1)
    ]

    if not segments:
        LOGGER.warning("pyannote produced no segments; falling back to single speaker | job_id=%s", task.job_id)
        segments.append(
            SpeakerSegment(
                segment_id="segment-0001",
                speaker="Speaker 1",
                start_ms=0,
                end_ms=total_ms,
                duration_ms=total_ms,
            )
        )

    return segments


def normalize_speaker_segments(
    segments: list[SpeakerSegment],
    total_ms: int,
    *,
    split_long: bool = True,
) -> list[SpeakerSegment]:
    planned = plan_segments(
        [DiarizationTurn(item.speaker, item.start_ms, item.end_ms) for item in segments],
        total_ms,
        min_segment_ms=MIN_SEGMENT_MS,
        merge_gap_ms=MERGE_GAP_MS,
        max_segment_ms=MAX_SEGMENT_MS if split_long else max(total_ms, 1),
        padding_ms=0,
    )
    return [
        SpeakerSegment(
            segment_id=item.segment_id,
            speaker=item.speaker,
            start_ms=item.start_ms,
            end_ms=item.end_ms,
            duration_ms=item.duration_ms,
        )
        for item in planned
    ]


def transcribe_segments(
    task: TaskSpec,
    audio,
    sample_rate: int,
    speaker_segments: list[SpeakerSegment],
    total_ms: int,
    job_dir: Path,
    emit=None,
    model_manager: ModelManager | None = None,
    warnings: list[dict] | None = None,
) -> list[TranscriptSegment]:
    manager = model_manager or _MODEL_MANAGER
    check_job_control(task, job_dir, emit=emit, progress=0.36, total_ms=total_ms)
    language = resolve_language(task)
    context = build_context(task)
    if task.asr_backend == "cloud":
        if task.cloud_asr_profile is None:
            raise ValueError("Cloud ASR profile is required.")
        model = None
        cloud_client = CloudAsrClient(task.cloud_asr_profile)
        batch_size = 1
    else:
        if manager.active_local_asr_model == "qwen3_asr_1_7b":
            _emit(
                emit,
                task,
                stage="model_loading",
                progress=0.40,
                total_ms=total_ms,
                processed_ms=0,
                payload={"asr_model": manager.local_asr_model_name()},
            )
        model = manager.get_local_asr_model()
        cloud_client = None
        batch_size = min(ASR_SEGMENT_BATCH_SIZE, manager.local_asr_batch_size())
    LOGGER.info(
        "starting ASR transcription | job_id=%s | asr_backend=%s | asr_profile=%s | segment_count=%s | batch_size=%s | language=%s | context_chars=%s | terms=%s",
        task.job_id,
        task.asr_backend,
        task.asr_profile_name or "",
        len(speaker_segments),
        batch_size,
        language,
        len(context),
        len(task.terms),
    )
    output: list[TranscriptSegment] = []
    chunk_origins: dict[int, int] = {}

    for batch_start in range(0, len(speaker_segments), batch_size):
        batch_segments = speaker_segments[
            batch_start : batch_start + batch_size
        ]
        batch_processed_ms = batch_segments[-1].end_ms if batch_segments else 0
        batch_progress = 0.36 + (
            0.48 * min(batch_start + len(batch_segments), len(speaker_segments))
            / max(len(speaker_segments), 1)
        )
        check_job_control(
            task,
            job_dir,
            emit=emit,
            progress=batch_progress,
            total_ms=total_ms,
            processed_ms=batch_processed_ms,
        )
        batch_inputs = []
        for offset, segment in enumerate(batch_segments):
            segment_index = batch_start + offset + 1
            input_start_ms = max(0, segment.start_ms - 200) if task.force_external_diarization else segment.start_ms
            input_end_ms = min(total_ms, segment.end_ms + 200) if task.force_external_diarization else segment.end_ms
            audio_chunk = slice_audio(
                audio,
                sample_rate,
                start_ms=input_start_ms,
                end_ms=input_end_ms,
            )
            chunk_origins[segment_index] = input_start_ms
            batch_inputs.append((segment_index, segment, audio_chunk))

        try:
            if task.asr_backend == "cloud":
                transcriptions = transcribe_cloud_audio_batch(
                    client=cloud_client,
                    batch_inputs=batch_inputs,
                    sample_rate=sample_rate,
                    context=context,
                    language=language,
                    job_id=task.job_id,
                )
            else:
                transcriptions = transcribe_audio_batch(
                    model=model,
                    batch_inputs=batch_inputs,
                    sample_rate=sample_rate,
                    context=context,
                    language=language,
                    job_id=task.job_id,
                )
        except Exception as exc:
            LOGGER.exception(
                "ASR segment batch failed; continuing with warning placeholders | job_id=%s | batch_start=%s | batch_size=%s",
                task.job_id,
                batch_start,
                len(batch_inputs),
            )
            for segment_index, segment, _audio_chunk in batch_inputs:
                warning = {
                    "code": "ASR_SEGMENT_FAILED",
                    "segment_id": segment.segment_id,
                    "segment_index": segment_index,
                    "start_ms": segment.start_ms,
                    "end_ms": segment.end_ms,
                    "speaker": canonical_speaker_name(segment.speaker),
                    "message": str(exc),
                    "retryable": True,
                }
                if warnings is not None:
                    warnings.append(warning)
                output.append(
                    TranscriptSegment(
                        segment_id=segment.segment_id,
                        speaker=canonical_speaker_name(segment.speaker),
                        start_ms=segment.start_ms,
                        end_ms=segment.end_ms,
                        text="",
                        normalized_text="",
                        language=None,
                    )
                )
            _emit(
                emit,
                task,
                stage="transcribing",
                progress=batch_progress,
                total_ms=total_ms,
                processed_ms=batch_processed_ms,
                payload={
                    "segment_error": "ASR_SEGMENT_FAILED",
                    "segment_count": len(speaker_segments),
                    "failed_segment_count": len(batch_inputs),
                },
            )
            continue
        check_job_control(
            task,
            job_dir,
            emit=emit,
            progress=batch_progress,
            total_ms=total_ms,
            processed_ms=batch_processed_ms,
        )

        for segment_index, segment, _audio_chunk in batch_inputs:
            transcription = transcriptions.get(segment_index)
            text = (getattr(transcription, "text", "") or "").strip()
            detected_language = getattr(transcription, "language", None)
            expanded_segments = (
                transcript_segments_from_model_output(
                    segment,
                    transcription,
                    task.replacements,
                    speaker=segment.speaker,
                    model_origin_ms=chunk_origins.get(segment_index, segment.start_ms),
                )
                if task.enable_speaker_diarization
                else []
            )
            if expanded_segments:
                LOGGER.debug(
                    "segment expanded from model diarization | job_id=%s | segment_id=%s | expanded_count=%s",
                    task.job_id,
                    segment.segment_id,
                    len(expanded_segments),
                )
                output.extend(expanded_segments)
            else:
                normalized_text = normalize_text(text, task.replacements)
                LOGGER.debug(
                    "segment transcribed | job_id=%s | segment_id=%s | speaker=%s | start_ms=%s | end_ms=%s | text_chars=%s | detected_language=%s",
                    task.job_id,
                    segment.segment_id,
                    canonical_speaker_name(segment.speaker),
                    segment.start_ms,
                    segment.end_ms,
                    len(normalized_text),
                    detected_language,
                )
                output.append(
                    TranscriptSegment(
                        segment_id=segment.segment_id,
                        speaker=canonical_speaker_name(segment.speaker),
                        start_ms=segment.start_ms,
                        end_ms=segment.end_ms,
                        text=text,
                        normalized_text=normalized_text,
                        language=detected_language,
                    )
                )

            progress = 0.36 + (0.48 * segment_index / max(len(speaker_segments), 1))
            _emit(
                emit,
                task,
                stage="transcribing",
                progress=progress,
                total_ms=total_ms,
                processed_ms=segment.end_ms,
                payload={
                    "current_segment_index": segment_index,
                    "segment_count": len(speaker_segments),
                    "current_speaker_label": canonical_speaker_name(segment.speaker),
                    "batch_size": batch_size,
                    "asr_backend": task.asr_backend,
                    "asr_profile_name": task.asr_profile_name,
                },
            )

    return output


def transcript_segments_from_model_output(
    source_segment: SpeakerSegment,
    transcription: object,
    replacements,
    *,
    speaker: str | None = None,
    model_origin_ms: int | None = None,
) -> list[TranscriptSegment]:
    model_segments = getattr(transcription, "segments", None)
    if not model_segments:
        return []

    output: list[TranscriptSegment] = []
    detected_language = getattr(transcription, "language", None)
    for index, model_segment in enumerate(model_segments, start=1):
        text = getattr(model_segment, "text", "") or ""
        normalized_text = normalize_text(text, replacements)
        origin_ms = source_segment.start_ms if model_origin_ms is None else model_origin_ms
        start_ms = origin_ms + int(getattr(model_segment, "start_ms", 0))
        end_ms = origin_ms + int(getattr(model_segment, "end_ms", 0))
        start_ms = max(source_segment.start_ms, start_ms)
        end_ms = min(max(end_ms, start_ms), source_segment.end_ms)
        if end_ms <= start_ms or not normalized_text:
            continue
        output.append(
            TranscriptSegment(
                segment_id=f"{source_segment.segment_id}-{index:03d}",
                speaker=canonical_speaker_name(speaker or getattr(model_segment, "speaker", "")),
                start_ms=start_ms,
                end_ms=end_ms,
                text=text,
                normalized_text=normalized_text,
                language=detected_language,
            )
        )
    return output


def transcribe_audio_batch(
    model,
    batch_inputs: list[tuple[int, SpeakerSegment, object]],
    sample_rate: int,
    context: str,
    language: str | None,
    job_id: str,
) -> dict[int, object]:
    non_empty_inputs = [
        (segment_index, audio_chunk)
        for segment_index, _segment, audio_chunk in batch_inputs
        if len(audio_chunk) > 0
    ]
    if not non_empty_inputs:
        return {}

    if len(non_empty_inputs) == 1:
        segment_index, audio_chunk = non_empty_inputs[0]
        result = model.transcribe(
            audio=[(audio_chunk, sample_rate)],
            context=[context],
            language=[language] if language is not None else [None],
            return_time_stamps=False,
        )
        return {segment_index: result[0] if result else None}

    try:
        LOGGER.debug(
            "transcribing ASR batch | job_id=%s | batch_size=%s",
            job_id,
            len(non_empty_inputs),
        )
        result = model.transcribe(
            audio=[(audio_chunk, sample_rate) for _index, audio_chunk in non_empty_inputs],
            context=[context for _index, _audio_chunk in non_empty_inputs],
            language=[
                language if language is not None else None
                for _index, _audio_chunk in non_empty_inputs
            ],
            return_time_stamps=False,
        )
        return {
            segment_index: transcription
            for (segment_index, _audio_chunk), transcription in zip(non_empty_inputs, result)
        }
    except Exception:
        LOGGER.exception(
            "ASR batch transcription failed; falling back to single-segment inference | job_id=%s | batch_size=%s",
            job_id,
            len(non_empty_inputs),
        )
        fallback_results: dict[int, object] = {}
        for segment_index, audio_chunk in non_empty_inputs:
            result = model.transcribe(
                audio=[(audio_chunk, sample_rate)],
                context=[context],
                language=[language] if language is not None else [None],
                return_time_stamps=False,
            )
            fallback_results[segment_index] = result[0] if result else None
        return fallback_results


def transcribe_cloud_audio_batch(
    client: CloudAsrClient | None,
    batch_inputs: list[tuple[int, SpeakerSegment, object]],
    sample_rate: int,
    context: str,
    language: str | None,
    job_id: str,
) -> dict[int, object]:
    if client is None:
        raise ValueError("Cloud ASR client is not initialized.")

    transcriptions: dict[int, object] = {}
    for segment_index, _segment, audio_chunk in batch_inputs:
        if len(audio_chunk) <= 0:
            continue
        LOGGER.debug(
            "transcribing cloud ASR segment | job_id=%s | segment_index=%s | profile=%s",
            job_id,
            segment_index,
            client.profile.name,
        )
        transcriptions[segment_index] = client.transcribe(
            audio=audio_chunk,
            sample_rate=sample_rate,
            context=context,
            language=language,
        )
    return transcriptions


def resolve_language(task: TaskSpec) -> str | None:
    if task.language_mode == "fixed" and task.fixed_language:
        return task.fixed_language
    return None


def build_context(task: TaskSpec) -> str:
    parts: list[str] = []
    if task.context_text:
        parts.append(f"Context:\n{task.context_text}")
    if task.terms:
        parts.append("Preferred terms:\n" + "\n".join(f"- {term}" for term in task.terms))
    return "\n\n".join(parts)


def resolve_asr_model_name(task: TaskSpec, model_manager: ModelManager | None = None) -> str:
    if task.asr_backend == "local":
        return (model_manager or _MODEL_MANAGER).local_asr_model_name()
    return task.asr_model_name


def normalize_text(text: str, replacements) -> str:
    value = re.sub(r"\s+", " ", text or "").strip()
    for rule in replacements:
        if rule.wrong and rule.correct:
            value = value.replace(rule.wrong, rule.correct)
    return value


def canonical_speaker_name(raw: str) -> str:
    explicit_pyannote = re.match(r"^speaker_(\d+)$", (raw or "").strip(), re.IGNORECASE)
    if explicit_pyannote:
        return f"Speaker {int(explicit_pyannote.group(1)) + 1}"

    match = re.search(r"(\d+)$", raw or "")
    if match:
        return f"Speaker {int(match.group(1))}"
    return raw or "Speaker"


def _emit(
    emit,
    task: TaskSpec,
    stage: str,
    progress: float,
    total_ms: int,
    processed_ms: int | None = None,
    payload: dict | None = None,
) -> None:
    if emit is None:
        return
    body = {
        "stage": stage,
        "progress": round(progress, 4),
        "processed_ms": processed_ms if processed_ms is not None else 0,
        "total_ms": total_ms,
    }
    if payload:
        body.update(payload)
    emit(task.job_id, body)
