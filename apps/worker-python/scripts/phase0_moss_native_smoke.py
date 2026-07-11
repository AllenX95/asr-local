"""Run a single full-audio MOSS native Transformers smoke test.

This script deliberately bypasses the v1 job runner's 30-second segment loop.
It is a Phase 0 evidence tool, not the production WorkflowRuntime adapter.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import time
from typing import Any


SEGMENT_RE = re.compile(
    r"\[(?P<start>\d+(?:\.\d+)?)\]\[(?P<speaker>S\d+)\](?P<text>.*?)"
    r"\[(?P<end>\d+(?:\.\d+)?)\](?=(?:\[\d+(?:\.\d+)?\]\[S\d+\])|$)",
    re.DOTALL,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--background", default="")
    parser.add_argument("--hotword", action="append", default=[])
    parser.add_argument("--max-new-tokens", type=int, default=65536)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    started_at = time.time()
    model_path = args.model.resolve()
    audio_path = args.audio.resolve()
    if not model_path.is_dir():
        raise SystemExit(f"MOSS model directory does not exist: {model_path}")
    if not audio_path.is_file():
        raise SystemExit(f"Audio file does not exist: {audio_path}")

    try:
        import soundfile as sf
        import torch
        from transformers import AutoModelForCausalLM, AutoProcessor
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "Phase 0 native smoke requires soundfile, torch and transformers in the dedicated runtime. "
            f"Missing: {exc.name}"
        ) from exc

    if args.device == "cuda" and not torch.cuda.is_available():
        raise SystemExit("--device cuda requested but torch.cuda.is_available() is false")

    audio, sample_rate = sf.read(str(audio_path), always_2d=False)
    if getattr(audio, "ndim", 1) > 1:
        audio = audio.mean(axis=1)
    duration_seconds = len(audio) / max(sample_rate, 1)
    device = torch.device(args.device)
    dtype = torch.bfloat16 if args.device == "cuda" and torch.cuda.is_bf16_supported() else (torch.float16 if args.device == "cuda" else torch.float32)

    load_started = time.perf_counter()
    model = AutoModelForCausalLM.from_pretrained(
        str(model_path),
        trust_remote_code=True,
        torch_dtype="auto",
    ).to(dtype=dtype).to(device).eval()
    processor = AutoProcessor.from_pretrained(
        str(model_path),
        trust_remote_code=True,
        fix_mistral_regex=True,
    )
    load_seconds = time.perf_counter() - load_started

    prompt_parts = [
        "请将音频转写为文本，每一段需以起始时间戳和说话人编号（[S01]、[S02]、[S03]…）开头，正文为对应的语音内容，并在段末标注结束时间戳，以清晰标明该段语音范围。",
    ]
    if args.background.strip():
        prompt_parts.append(f"录音背景：\n{args.background.strip()}")
    if args.hotword:
        prompt_parts.append("热词提示：" + "、".join(word.strip() for word in args.hotword if word.strip()))
    prompt = "\n\n".join(prompt_parts)
    chat_text = processor.apply_chat_template(
        [{"role": "user", "content": [{"type": "audio", "audio": audio_path.name}, {"type": "text", "text": prompt}]}],
        tokenize=False,
        add_generation_prompt=True,
    )
    inputs = processor(text=chat_text, audio=[audio], return_tensors="pt").to(device)
    prompt_len = int(inputs["attention_mask"][0].sum().item())

    inference_started = time.perf_counter()
    with torch.inference_mode():
        output_ids = model.generate(
            input_ids=inputs["input_ids"],
            attention_mask=inputs["attention_mask"],
            input_features=inputs["input_features"],
            audio_feature_lengths=inputs["audio_feature_lengths"],
            audio_chunk_mapping=inputs["audio_chunk_mapping"],
            max_new_tokens=args.max_new_tokens,
            do_sample=False,
        )[0][prompt_len:]
    inference_seconds = time.perf_counter() - inference_started
    raw_text = processor.tokenizer.decode(output_ids, skip_special_tokens=True).strip()
    segments = parse_segments(raw_text)
    result: dict[str, Any] = {
        "script": "phase0_moss_native_smoke",
        "python_version": __import__("sys").version,
        "torch_version": torch.__version__,
        "transformers_version": __import__("transformers").__version__,
        "model_path": str(model_path),
        "model_revision": read_revision(model_path),
        "model_config_sha256": sha256_file(model_path / "config.json"),
        "audio_path": str(audio_path),
        "sample_rate": sample_rate,
        "duration_seconds": duration_seconds,
        "device": str(device),
        "dtype": str(dtype),
        "prompt": prompt,
        "raw_text": raw_text,
        "segments": segments,
        "output_truncated_suspected": len(output_ids) >= args.max_new_tokens,
        "load_seconds": load_seconds,
        "inference_seconds": inference_seconds,
        "rtf": inference_seconds / max(duration_seconds, 0.001),
        "total_seconds": time.time() - started_at,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("model_revision", "duration_seconds", "device", "dtype", "load_seconds", "inference_seconds", "rtf", "output_truncated_suspected")}, ensure_ascii=False))
    return 0


def parse_segments(raw_text: str) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    for match in SEGMENT_RE.finditer(raw_text):
        start = float(match.group("start"))
        end = float(match.group("end"))
        text = re.sub(r"\s+", " ", match.group("text")).strip()
        if end < start or not text:
            continue
        segments.append({"start_ms": round(start * 1000), "end_ms": round(end * 1000), "speaker": match.group("speaker"), "text": text})
    return segments


def read_revision(model_path: Path) -> str | None:
    for path in (
        model_path / ".cache" / "huggingface" / "refs" / "main",
        model_path / "refs" / "main",
        model_path / ".cache" / "huggingface" / "download" / "config.json.metadata",
    ):
        if path.is_file():
            return path.read_text(encoding="utf-8").splitlines()[0].strip() or None
    return None


def sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
