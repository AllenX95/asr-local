from __future__ import annotations

import unittest

from app.runtime.gpu_lifecycle import gpu_snapshot, release_gpu_resources


class _FakeCuda:
    def __init__(self) -> None:
        self.cleaned = False

    def is_available(self) -> bool:
        return True

    def memory_allocated(self) -> int:
        return 123

    def memory_reserved(self) -> int:
        return 456

    def mem_get_info(self):
        return (789, 1_000)

    def synchronize(self) -> None:
        self.cleaned = True

    def empty_cache(self) -> None:
        self.cleaned = True


class _FakeTorch:
    def __init__(self) -> None:
        self.cuda = _FakeCuda()


class _Closable:
    def __init__(self) -> None:
        self.closed = 0

    def close(self) -> None:
        self.closed += 1


class GpuLifecycleTests(unittest.TestCase):
    def test_snapshot_is_safe_and_reports_cuda(self) -> None:
        snapshot = gpu_snapshot(_FakeTorch())
        self.assertEqual(snapshot["memory_allocated"], 123)
        self.assertEqual(snapshot["memory_reserved"], 456)
        self.assertEqual(snapshot["free"], 789)

    def test_release_is_idempotent_at_helper_boundary(self) -> None:
        resource = _Closable()
        torch = _FakeTorch()
        result = release_gpu_resources(resource, torch_module=torch, label="test")
        self.assertEqual(resource.closed, 1)
        self.assertEqual(result["label"], "test")
        self.assertTrue(torch.cuda.cleaned)


if __name__ == "__main__":
    unittest.main()
