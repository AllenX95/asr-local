from __future__ import annotations

import asyncio
from dataclasses import dataclass
from collections import deque
from io import BytesIO
import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Any, Callable
import urllib.error
import urllib.request
import wave
import uuid

import numpy as np

from app.schemas import AsrCloudProfile


DEFAULT_TIMEOUT_SECONDS = 600
# A cloud request may be backed by a native socket call that cannot be
# interrupted safely.  Keep shutdown bounded by default; callers can opt into
# a longer grace period explicitly when their endpoint supports cancellation.
CLOUD_CLOSE_WAIT_SECONDS = 0.25


@dataclass(slots=True)
class CloudAsrResult:
    text: str
    language: str | None = None


class CloudAttemptCancelled(RuntimeError):
    """A cloud request completed after its workflow attempt was cancelled."""

    cancelled = True


class CloudAsrTranscriber:
    """OpenAI-compatible audio transcription adapter with just-in-time auth."""

    def __init__(
        self,
        *,
        secret_provider=None,
        request_fn: Callable[..., dict[str, Any]] | None = None,
        close_wait_seconds: float = CLOUD_CLOSE_WAIT_SECONDS,
        close_wait_timeout_seconds: float | None = None,
    ) -> None:
        self.secret_provider = secret_provider
        self.request_fn = request_fn or _request_multipart
        self.close_wait_seconds = max(
            0.0,
            float(
                close_wait_seconds
                if close_wait_timeout_seconds is None
                else close_wait_timeout_seconds
            ),
        )
        self._lifecycle = threading.Condition(threading.Lock())
        self._closing = False
        self._closed = False
        self._active_sync_calls = 0
        self._active_attempts: set[tuple[str, str]] = set()
        self._active_call_tokens: dict[object, tuple[str, str]] = {}
        self._cancelled_attempts: set[tuple[str, str]] = set()
        self._cancelled_tombstone_order: deque[tuple[str, str]] = deque()
        self._max_cancelled_tombstones = 1024

    async def transcribe(self, spec: dict[str, Any], attempt_id: str, *, progress=None) -> dict[str, Any]:
        if progress:
            progress({"phase": "cloud_asr_request", "detail": "正在调用云端语音识别"})
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
        key = (str(spec["workflow_id"]), attempt_id)
        if self._is_cancelled(key):
            self._clear_cancelled(key)
            raise CloudAttemptCancelled(f"cloud ASR attempt cancelled: {attempt_id}")
        try:
            payload = await self._run_native_request(
                key,
                _audio_url(profile["base_url"]),
                source,
                fields,
                headers,
            )
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            if self._is_cancelled(key):
                self._clear_cancelled(key)
                raise CloudAttemptCancelled(
                    f"cloud ASR attempt cancelled: {attempt_id}"
                ) from exc
            raise
        if self._is_cancelled(key):
            self._clear_cancelled(key)
            raise CloudAttemptCancelled(f"cloud ASR attempt cancelled: {attempt_id}")
        self._clear_cancelled(key)
        markdown = _format_response(payload)
        markdown = _apply_replacements(markdown, spec["transcription"].get("postprocess", {}).get("replacements", []))
        # Artifact materialization and revision naming belong to the supervisor.
        return {"kind": "transcript_markdown", "text": markdown}

    def cancel_attempt(self, workflow_id: str, attempt_id: str) -> int:
        key = (str(workflow_id), str(attempt_id))
        with self._lifecycle:
            self._remember_cancelled_locked(key)
            return int(key in self._active_attempts)

    def close(self) -> None:
        """Stop new requests and wait a bounded time for network threads.

        Python cannot safely kill a blocking urllib worker. Marking active
        attempts cancelled prevents late results from being committed; the
        bounded wait keeps runtime shutdown from hanging indefinitely on a
        remote endpoint that ignores its timeout.
        """

        with self._lifecycle:
            if self._closed:
                return None
            self._closing = True
            for key in self._active_attempts:
                self._remember_cancelled_locked(key)
            deadline = time.monotonic() + self.close_wait_seconds
            while self._active_sync_calls:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._lifecycle.wait(timeout=remaining)
            self._closed = True
        return None

    async def _run_native_request(
        self,
        key: tuple[str, str],
        url: str,
        source: Path,
        fields: dict[str, Any],
        headers: dict[str, str],
    ) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        token = self._register_native_call(key)
        future = loop.create_future()
        native_thread = threading.Thread(
            target=self._native_request_runner,
            args=(token, loop, future, url, source, fields, headers),
            name=f"cloud-asr-{key[0]}-{key[1]}",
            daemon=True,
        )
        try:
            native_thread.start()
        except BaseException:
            # Thread creation is the only path without a native runner
            # finally block available to debit the reservation.
            self._finish_native_call(token)
            raise
        return await future

    def _register_native_call(self, key: tuple[str, str]):
        with self._lifecycle:
            if self._closing or self._closed:
                raise RuntimeError("CLOUD_TRANSCRIBER_CLOSED: no new requests accepted")
            token = object()
            self._active_call_tokens[token] = key
            self._active_sync_calls += 1
            self._active_attempts.add(key)
            return token

    def _native_request_runner(self, token, loop, future, *args) -> None:
        try:
            result = self.request_fn(*args)
        except BaseException as exc:
            self._deliver_future(loop, future, exception=exc)
        else:
            self._deliver_future(loop, future, result=result)
        finally:
            # Only the native runner owns the active-call decrement.  A
            # cancelled asyncio task may leave urllib running in this daemon
            # thread until the endpoint returns.
            self._finish_native_call(token)

    @staticmethod
    def _deliver_future(loop, future, *, result=None, exception=None) -> None:
        def deliver() -> None:
            if future.done():
                return
            if exception is not None:
                future.set_exception(exception)
            else:
                future.set_result(result)

        try:
            loop.call_soon_threadsafe(deliver)
        except RuntimeError:
            # The caller may have cancelled the asyncio.run loop already.
            return

    def _finish_native_call(self, token) -> None:
        with self._lifecycle:
            key = self._active_call_tokens.pop(token, None)
            if key is None:
                return
            self._active_sync_calls = max(0, self._active_sync_calls - 1)
            if not any(active_key == key for active_key in self._active_call_tokens.values()):
                self._active_attempts.discard(key)
            self._lifecycle.notify_all()

    def _remember_cancelled_locked(self, key: tuple[str, str]) -> None:
        if key in self._cancelled_attempts:
            return
        self._cancelled_attempts.add(key)
        self._cancelled_tombstone_order.append(key)
        while len(self._cancelled_tombstone_order) > self._max_cancelled_tombstones:
            self._cancelled_attempts.discard(self._cancelled_tombstone_order.popleft())

    def _clear_cancelled(self, key: tuple[str, str]) -> None:
        with self._lifecycle:
            self._cancelled_attempts.discard(key)

    def _is_cancelled(self, key: tuple[str, str]) -> bool:
        with self._lifecycle:
            return key in self._cancelled_attempts


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
