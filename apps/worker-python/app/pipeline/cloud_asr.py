from __future__ import annotations

import asyncio
from dataclasses import dataclass
from io import BytesIO
import hashlib
import json
from pathlib import Path
from typing import Any, Callable
import urllib.error
import urllib.request
import wave
import uuid

import numpy as np

from app.schemas import AsrCloudProfile


DEFAULT_TIMEOUT_SECONDS = 600


@dataclass(slots=True)
class CloudAsrResult:
    text: str
    language: str | None = None


class CloudAsrTranscriber:
    """OpenAI-compatible audio transcription adapter with just-in-time auth."""

    def __init__(self, *, secret_provider=None, request_fn: Callable[..., dict[str, Any]] | None = None) -> None:
        self.secret_provider = secret_provider
        self.request_fn = request_fn or _request_multipart

    async def transcribe(self, spec: dict[str, Any], attempt_id: str) -> dict[str, Any]:
        profile = spec["transcription"].get("cloud_profile")
        if not profile:
            raise RuntimeError("CLOUD_PROFILE_REQUIRED")
        secret = ""
        if profile["auth_mode"] == "bearer":
            if self.secret_provider is None:
                raise RuntimeError("CREDENTIAL_REQUIRED")
            secret = await self.secret_provider.provide(
                workflow_id=str(spec["workflow_id"]),
                attempt_id=attempt_id,
                profile=profile,
                purpose="cloud_asr",
            )
        source = Path(spec["source"]["path"])
        headers = {"X-Idempotency-Key": hashlib.sha256(f"{spec['workflow_id']}:{attempt_id}:cloud-asr".encode()).hexdigest()}
        if secret:
            headers["Authorization"] = f"Bearer {secret}"
        fields = {
            "model": profile["model"],
            "response_format": "verbose_json",
            "prompt": spec["transcription"]["prompt_snapshot"]["compiled_text"],
        }
        language = spec["transcription"].get("language", {})
        if language.get("mode") == "fixed" and language.get("value"):
            fields["language"] = language["value"]
        payload = await asyncio.to_thread(self.request_fn, _audio_url(profile["base_url"]), source, fields, headers)
        markdown = _format_response(payload)
        markdown = _apply_replacements(markdown, spec["transcription"].get("postprocess", {}).get("replacements", []))
        # Artifact materialization and revision naming belong to the supervisor.
        return {"kind": "transcript_markdown", "text": markdown}


class CloudAsrClient:
    """Compatibility client for the frozen v1 segment pipeline."""

    def __init__(self, profile: AsrCloudProfile) -> None:
        self.profile = profile
        self.url = transcription_url(profile.base_url)

    def transcribe(self, audio, sample_rate: int, context: str, language: str | None) -> CloudAsrResult:
        fields = {"model": self.profile.model, "response_format": "json"}
        if context.strip():
            fields["prompt"] = context.strip()
        if language:
            fields["language"] = language
        body, content_type = encode_multipart(
            fields=fields,
            file_field="file",
            filename="segment.wav",
            file_content_type="audio/wav",
            file_bytes=audio_to_wav_bytes(audio, sample_rate),
        )
        headers = {"Content-Type": content_type, "Accept": "application/json"}
        if self.profile.api_key:
            headers["Authorization"] = f"Bearer {self.profile.api_key}"
        request = urllib.request.Request(self.url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT_SECONDS) as response:
                response_body = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Cloud ASR API returned HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Cloud ASR API request failed: {exc.reason}") from exc
        try:
            payload = json.loads(response_body)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Cloud ASR API response is not valid JSON.") from exc
        text = extract_text(payload)
        if text is None:
            raise RuntimeError("Cloud ASR API response did not contain a transcript text field.")
        return CloudAsrResult(text=text, language=extract_language(payload))

def transcription_url(base_url: str) -> str:
    trimmed = base_url.strip()
    if not trimmed:
        raise ValueError("Cloud ASR base URL is empty.")
    if trimmed.endswith("/audio/transcriptions"):
        return trimmed
    return f"{trimmed.rstrip('/')}/audio/transcriptions"


def audio_to_wav_bytes(audio, sample_rate: int) -> bytes:
    clipped = np.clip(np.asarray(audio, dtype=np.float32), -1.0, 1.0)
    pcm = (clipped * 32767.0).astype("<i2", copy=False)
    buffer = BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm.tobytes())
    return buffer.getvalue()


def encode_multipart(fields: dict[str, str], file_field: str, filename: str, file_content_type: str, file_bytes: bytes) -> tuple[bytes, str]:
    boundary = f"----asr-local-{uuid.uuid4().hex}"
    body = bytearray()
    for name, value in fields.items():
        if value is None or str(value).strip() == "":
            continue
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        body.extend(str(value).encode("utf-8"))
        body.extend(b"\r\n")
    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend((f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n' f"Content-Type: {file_content_type}\r\n\r\n").encode("utf-8"))
    body.extend(file_bytes)
    body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode("utf-8"))
    return bytes(body), f"multipart/form-data; boundary={boundary}"


def extract_text(payload: dict) -> str | None:
    text = payload.get("text")
    if isinstance(text, str):
        return text.strip()
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        content = choices[0].get("message", {}).get("content")
        if isinstance(content, str):
            return content.strip()
    return None


def extract_language(payload: dict) -> str | None:
    language = payload.get("language")
    if isinstance(language, str) and language.strip():
        return language.strip()
    segments = payload.get("segments")
    if isinstance(segments, list):
        for segment in segments:
            if isinstance(segment, dict):
                segment_language = segment.get("language")
                if isinstance(segment_language, str) and segment_language.strip():
                    return segment_language.strip()
    return None


def _audio_url(base_url: str) -> str:
    trimmed = base_url.rstrip("/")
    return trimmed if trimmed.endswith("/audio/transcriptions") else f"{trimmed}/audio/transcriptions"


def _format_response(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    for segment in payload.get("segments", []) or []:
        start = float(segment.get("start", 0))
        end = float(segment.get("end", start))
        speaker = str(segment.get("speaker") or segment.get("speaker_id") or "Speaker 1")
        text = str(segment.get("text", "")).strip()
        if text:
            lines.append(f"[{_timestamp(start)}-{_timestamp(end)}] {speaker}: {text}")
    if lines:
        return "\n".join(lines) + "\n"
    text = str(payload.get("text", "")).strip()
    return text + ("\n" if text else "")


def _timestamp(seconds: float) -> str:
    total_ms = max(0, int(round(seconds * 1000)))
    total_seconds, milliseconds = divmod(total_ms, 1000)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds_value = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds_value:02d}.{milliseconds:03d}"


def _apply_replacements(text: str, replacements: list[dict[str, Any]]) -> str:
    for rule in replacements:
        wrong = str(rule.get("wrong", ""))
        if wrong:
            text = text.replace(wrong, str(rule.get("correct", "")))
    return text


def _request_multipart(url: str, source: Path, fields: dict[str, str], headers: dict[str, str]) -> dict[str, Any]:
    boundary = "----asr-local-v2-boundary"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
            str(value).encode("utf-8"),
            b"\r\n",
        ])
    chunks.extend([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="file"; filename="{source.name}"\r\n'.encode(),
        b"Content-Type: application/octet-stream\r\n\r\n",
        source.read_bytes(),
        b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ])
    request_headers = {**headers, "Content-Type": f"multipart/form-data; boundary={boundary}"}
    request = urllib.request.Request(url, data=b"".join(chunks), headers=request_headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"cloud ASR returned HTTP {exc.code}: {detail}") from exc
