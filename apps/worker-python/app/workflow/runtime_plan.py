from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any


class RuntimePlanError(RuntimeError):
    code = "RESOURCE_EXHAUSTED"


@dataclass(frozen=True, slots=True)
class HardwareSnapshot:
    cpu_count: int
    cuda_available: bool
    cuda_name: str | None = None
    free_memory_mb: int | None = None
    free_vram_mb: int | None = None
    bf16_supported: bool = False


@dataclass(frozen=True, slots=True)
class RuntimePlan:
    resolved_device: str
    dtype: str
    workflow_capacity: int
    asr_inference_capacity: int
    model_replicas: int
    reason: str
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "resolved_device": self.resolved_device,
            "dtype": self.dtype,
            "workflow_capacity": self.workflow_capacity,
            "asr_inference_capacity": self.asr_inference_capacity,
            "model_replicas": self.model_replicas,
            "reason": self.reason,
            "warnings": list(self.warnings),
        }


def profile_hardware(torch_module: Any | None = None) -> HardwareSnapshot:
    if torch_module is None:
        try:
            import torch as torch_module  # type: ignore[no-redef]
        except Exception:
            torch_module = None
    cuda_available = False
    cuda_name = None
    free_vram_mb = None
    bf16_supported = False
    if torch_module is not None:
        cuda_available = bool(torch_module.cuda.is_available())
        if cuda_available:
            cuda_name = str(torch_module.cuda.get_device_name(0))
            bf16_supported = bool(getattr(torch_module.cuda, "is_bf16_supported", lambda: False)())
            try:
                free_bytes, _total_bytes = torch_module.cuda.mem_get_info(0)
                free_vram_mb = int(free_bytes / (1024 * 1024))
            except Exception:
                free_vram_mb = None
    return HardwareSnapshot(cpu_count=max(1, os.cpu_count() or 1), cuda_available=cuda_available, cuda_name=cuda_name, free_vram_mb=free_vram_mb, bf16_supported=bf16_supported)


def resolve_runtime_plan(
    device_policy: str,
    hardware: HardwareSnapshot,
    *,
    workflow_capacity: int = 3,
    estimated_model_memory_mb: int = 2048,
) -> RuntimePlan:
    if device_policy not in {"auto", "cpu", "cuda"}:
        raise RuntimePlanError(f"unsupported device policy: {device_policy}")
    if workflow_capacity < 1:
        raise RuntimePlanError("workflow capacity must be positive")
    if device_policy == "cuda" and not hardware.cuda_available:
        raise RuntimePlanError("CUDA was forced but no CUDA device is available")

    warnings: list[str] = []
    if device_policy == "cuda":
        device = "cuda:0"
        reason = "forced_cuda"
    elif device_policy == "cpu":
        device = "cpu"
        reason = "forced_cpu"
    elif hardware.cuda_available and (hardware.free_vram_mb is None or hardware.free_vram_mb >= estimated_model_memory_mb):
        device = "cuda:0"
        reason = "auto_cuda_headroom"
    else:
        device = "cpu"
        reason = "auto_cpu_fallback"
        if hardware.cuda_available:
            warnings.append("CUDA was available but did not meet the configured memory headroom.")

    if device.startswith("cuda"):
        dtype = "bfloat16" if hardware.bf16_supported else "float16"
    else:
        dtype = "float32"
    asr_capacity = 1
    return RuntimePlan(device, dtype, workflow_capacity, asr_capacity, 1, reason, tuple(warnings))
