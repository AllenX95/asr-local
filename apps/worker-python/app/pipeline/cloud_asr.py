from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import json
import uuid
import wave
import urllib.error
import urllib.request

import numpy as np

from app.schemas import AsrCloudProfile


DEFAULT_TIMEOUT_SECONDS = 600


@dataclass(slots=True)
class CloudAsrResult:
    text: str
    language: str | None = None


class CloudAsrClient:
    def __init__(self, profile: AsrCloudProfile) -> None:
        self.profile = profile
        self.url = transcription_url(profile.base_url)

    def transcribe(
        self,
        audio,
        sample_rate: int,
        context: str,
        language: str | None,
    ) -> CloudAsrResult:
        fields = {
            "model": self.profile.model,
            "response_format": "json",
        }
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
        headers = {
            "Content-Type": content_type,
            "Accept": "application/json",
        }
        if self.profile.api_key:
            headers["Authorization"] = f"Bearer {self.profile.api_key}"

        request = urllib.request.Request(
            self.url,
            data=body,
            headers=headers,
            method="POST",
        )

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


def encode_multipart(
    fields: dict[str, str],
    file_field: str,
    filename: str,
    file_content_type: str,
    file_bytes: bytes,
) -> tuple[bytes, str]:
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
    body.extend(
        (
            f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
            f"Content-Type: {file_content_type}\r\n\r\n"
        ).encode("utf-8")
    )
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
