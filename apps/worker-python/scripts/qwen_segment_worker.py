"""JSONL child process for the isolated Qwen3-ASR runtime."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import torch


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-path", required=True)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--dtype", choices=("float32", "float16", "bfloat16"), default="float16")
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--max-new-tokens", type=int, default=256)
    args = parser.parse_args()

    try:
        from qwen_asr import Qwen3ASRModel
        dtype = getattr(torch, args.dtype)
        model = Qwen3ASRModel.from_pretrained(
            args.model_path,
            dtype=dtype,
            device_map=args.device,
            max_inference_batch_size=max(1, args.batch_size),
            max_new_tokens=max(32, args.max_new_tokens),
        )
    except Exception as exc:
        _write({"ok": False, "error": f"QWEN_RUNTIME_LOAD_FAILED: {exc}"})
        return 1

    for raw in sys.stdin:
        try:
            request = json.loads(raw)
            if request.get("shutdown"):
                return 0
            paths = [str(Path(item)) for item in request.get("audio_paths", [])]
            contexts = request.get("context", "")
            languages = request.get("language")
            results = model.transcribe(
                audio=paths,
                context=contexts,
                language=languages,
                return_time_stamps=False,
            )
            _write({
                "ok": True,
                "results": [
                    {"text": getattr(item, "text", ""), "language": getattr(item, "language", None)}
                    for item in results
                ],
            })
        except Exception as exc:
            _write({"ok": False, "error": f"QWEN_SEGMENT_FAILED: {exc}"})
    return 0


def _write(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    raise SystemExit(main())
