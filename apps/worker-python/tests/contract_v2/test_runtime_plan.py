from __future__ import annotations

import unittest

from app.workflow.runtime_plan import HardwareSnapshot, RuntimePlanError, resolve_runtime_plan


class RuntimePlanTests(unittest.TestCase):
    def test_auto_uses_cpu_when_cuda_is_unavailable(self) -> None:
        plan = resolve_runtime_plan("auto", HardwareSnapshot(cpu_count=8, cuda_available=False))
        self.assertEqual(plan.resolved_device, "cpu")
        self.assertEqual(plan.dtype, "float32")
        self.assertEqual(plan.workflow_capacity, 3)

    def test_forced_cuda_does_not_silently_fallback(self) -> None:
        with self.assertRaises(RuntimePlanError):
            resolve_runtime_plan("cuda", HardwareSnapshot(cpu_count=8, cuda_available=False))

    def test_auto_warns_when_gpu_headroom_is_insufficient(self) -> None:
        plan = resolve_runtime_plan("auto", HardwareSnapshot(cpu_count=8, cuda_available=True, free_vram_mb=512), estimated_model_memory_mb=2048)
        self.assertEqual(plan.resolved_device, "cpu")
        self.assertTrue(plan.warnings)

    def test_cuda_dtype_uses_bfloat16_when_supported(self) -> None:
        plan = resolve_runtime_plan("auto", HardwareSnapshot(cpu_count=8, cuda_available=True, free_vram_mb=4096, bf16_supported=True))
        self.assertEqual(plan.resolved_device, "cuda:0")
        self.assertEqual(plan.dtype, "bfloat16")


if __name__ == "__main__":
    unittest.main()
