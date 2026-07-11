from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Protocol
import urllib.error
import urllib.request


class SummaryInputTooLargeError(RuntimeError):
    code = "SUMMARY_INPUT_TOO_LARGE"


class SummaryResultUnknownError(RuntimeError):
    code = "SUMMARY_RESULT_UNKNOWN"


class SecretProvider(Protocol):
    async def provide(self, *, workflow_id: str, attempt_id: str, profile: dict[str, Any], purpose: str) -> str: ...


@dataclass(slots=True)
class SummaryResult:
    text: str
    strategy: str
    provider_request_keys: list[str]


class OpenAICompatibleSummaryGenerator:
    """Provider adapter with explicit context strategy and just-in-time secret access."""

    def __init__(
        self,
        *,
        secret_provider: SecretProvider | None = None,
        request_fn: Callable[[str, dict[str, Any], dict[str, str]], str] | None = None,
        timeout_seconds: int = 600,
    ) -> None:
        self.secret_provider = secret_provider
        self.request_fn = request_fn or _request_json
        self.timeout_seconds = timeout_seconds

    async def summarize(self, spec: dict[str, Any], transcript: dict[str, Any], attempt_id: str) -> dict[str, Any]:
        workflow_id = str(spec.get("workflow_id") or spec.get("display_name") or "workflow")
        summary = spec["summary"]
        transcript_text = _transcript_text(transcript)
        budget = int(summary["input_token_budget"])
        strategy = summary.get("context_strategy", "auto")
        estimated_tokens = _estimate_tokens(transcript_text) + _estimate_tokens(summary["template"]["prompt_snapshot"])
        if strategy == "single_pass" and estimated_tokens > budget:
            raise SummaryInputTooLargeError("transcript exceeds the single-pass input token budget")
        if strategy == "auto":
            strategy = "single_pass" if estimated_tokens <= budget else "hierarchical"
        if strategy == "single_pass":
            text, key = await self._call_provider(spec, attempt_id, summary, transcript_text, chunk_index=0)
            return {"kind": "final_summary_markdown", "text": text, "provider_request_keys": [key], "strategy": strategy}

        chunks = _chunk_text(transcript_text, max(1, budget * 4 - 4000))
        local_summaries: list[str] = []
        keys: list[str] = []
        for index, chunk in enumerate(chunks):
            text, key = await self._call_provider(spec, attempt_id, summary, chunk, chunk_index=index)
            local_summaries.append(text)
            keys.append(key)
        merged, key = await self._call_provider(spec, attempt_id, summary, "\n\n".join(local_summaries), chunk_index=len(chunks))
        keys.append(key)
        return {"kind": "final_summary_markdown", "text": merged, "provider_request_keys": keys, "strategy": strategy}

    async def _call_provider(
        self,
        spec: dict[str, Any],
        attempt_id: str,
        summary: dict[str, Any],
        transcript_text: str,
        *,
        chunk_index: int,
    ) -> tuple[str, str]:
        workflow_id = str(spec.get("workflow_id") or spec.get("display_name") or "workflow")
        request_key = hashlib.sha256(f"{workflow_id}:{attempt_id}:summary:{chunk_index}".encode("utf-8")).hexdigest()
        prompt = f"# Summary Instructions\n{summary['template']['prompt_snapshot']}\n\n# Transcript Markdown\n{transcript_text}"
        headers = {"Content-Type": "application/json", "X-Idempotency-Key": request_key}
        if summary["auth_mode"] == "bearer":
            if self.secret_provider is None:
                raise RuntimeError("CREDENTIAL_REQUIRED")
            secret = await self.secret_provider.provide(workflow_id=workflow_id, attempt_id=attempt_id, profile=summary, purpose="summary_api")
            headers["Authorization"] = f"Bearer {secret}"
        payload = {
            "model": summary["model"],
            "messages": [
                {"role": "system", "content": "Summarize the transcript into Markdown. Follow the template exactly."},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": summary["max_output_tokens"],
        }
        try:
            text = await asyncio.to_thread(self.request_fn, _chat_completions_url(summary["base_url"]), payload, headers)
        except (urllib.error.URLError, TimeoutError) as exc:
            raise SummaryResultUnknownError(str(exc)) from exc
        if not text.strip():
            raise RuntimeError("summary provider returned empty content")
        return text.strip(), request_key


def _transcript_text(transcript: dict[str, Any]) -> str:
    if isinstance(transcript.get("text"), str):
        return transcript["text"]
    path = transcript.get("path")
    if path:
        return Path(path).read_text(encoding="utf-8")
    raise ValueError("transcript artifact has no text or readable path")


def _estimate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


def _chunk_text(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    cursor = 0
    while cursor < len(text):
        end = min(len(text), cursor + max_chars)
        if end < len(text):
            boundary = text.rfind("\n\n", cursor, end)
            if boundary > cursor:
                end = boundary
        chunks.append(text[cursor:end].strip())
        cursor = end
    return [chunk for chunk in chunks if chunk]


def _chat_completions_url(base_url: str) -> str:
    trimmed = base_url.strip().rstrip("/")
    return trimmed if trimmed.endswith("/chat/completions") else f"{trimmed}/chat/completions"


def _request_json(url: str, payload: dict[str, Any], headers: dict[str, str]) -> str:
    request = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"summary provider returned HTTP {exc.code}: {detail}") from exc
    value = json.loads(body)
    content = value.get("choices", [{}])[0].get("message", {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(part.get("text", "") for part in content if part.get("type") == "text")
    return ""
