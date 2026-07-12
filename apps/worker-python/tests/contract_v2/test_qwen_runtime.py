from __future__ import annotations

import io
from collections import deque
import threading
import unittest

from app.models.manager import ModelManager
from app.pipeline.qwen_subprocess import QwenSubprocessAdapter


class QwenRuntimeTests(unittest.TestCase):
    def test_qwen_uses_serial_segment_batch(self):
        self.assertEqual(
            ModelManager(active_local_asr_model_override="qwen3_asr_1_7b").local_asr_batch_size(),
            1,
        )

    def test_stderr_drain_keeps_a_bounded_tail(self):
        adapter = QwenSubprocessAdapter.__new__(QwenSubprocessAdapter)
        adapter._stderr_tail = deque(maxlen=40)
        adapter._lock = threading.RLock()
        process = type("Process", (), {"stderr": io.StringIO("first warning\nsecond warning\n")})()

        adapter._drain_stderr(process)

        self.assertEqual(list(adapter._stderr_tail), ["first warning", "second warning"])


if __name__ == "__main__":
    unittest.main()
