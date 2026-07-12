"""Probe local model imports and short in-memory chunk lifecycle.

This probe never downloads weights and never writes an audio file. It is meant
for validating the phase boundary before running a real recording.
"""

from __future__ import annotations

import argparse
import json
import time

import numpy as np

from app.models.manager import ModelManager
from app.pipeline.pyannote_provider import PyannoteDiarizationProvider
from app.runtime.gpu_lifecycle import gpu_snapshot


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=("qwen3_asr_1_7b", "moss_transcribe_diarize"), default="qwen3_asr_1_7b")
    parser.add_argument("--seconds", type=float, default=1.0)
    args = parser.parse_args()
    sample_rate = 16_000
    audio = np.zeros(max(1, int(sample_rate * args.seconds)), dtype=np.float32)
    manager = ModelManager(active_local_asr_model_override=args.backend)
    started = time.perf_counter()
    result: dict[str, object] = {"backend": args.backend, "before": gpu_snapshot(manager.torch)}
    try:
        turns = PyannoteDiarizationProvider(model_manager=manager).diarize(
            audio=audio,
            sample_rate=sample_rate,
            uri="in-memory-smoke",
            total_ms=int(args.seconds * 1000),
        )
        result["diarization_turns"] = [turn.to_dict() for turn in turns]
        if args.backend == "moss_transcribe_diarize":
            manager.close_pyannote_pipeline()
            output = manager.get_local_asr_model().transcribe(
                audio=[(audio, sample_rate)],
                context=[""],
                language=[None],
                return_time_stamps=False,
            )
            result["text"] = getattr(output[0], "text", "") if output else ""
        else:
            try:
                manager.get_local_asr_model()
                result["qwen_runtime"] = "imported"
            except Exception as exc:
                result["qwen_runtime"] = "unavailable"
                result["qwen_error"] = str(exc)
        result["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        return_code = 0
    except Exception as exc:
        result["error"] = str(exc)
        return_code = 1
    finally:
        manager.close_local_models()
        result["after"] = gpu_snapshot(manager.torch)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
