from __future__ import annotations

from unittest import mock
import unittest

from app.models.manager import ModelManager


class QwenRuntimeTests(unittest.TestCase):
    def test_qwen_uses_serial_segment_batch(self):
        self.assertEqual(
            ModelManager().local_asr_batch_size(),
            1,
        )

    @mock.patch("qwen_asr.Qwen3ASRModel.from_pretrained")
    def test_qwen_loads_in_the_main_runtime(self, load_model):
        sentinel = object()
        load_model.return_value = sentinel
        manager = ModelManager()

        self.assertIs(manager.get_qwen_model(), sentinel)
        load_model.assert_called_once_with(
            str(manager.qwen_path),
            dtype=manager.qwen_torch_dtype(),
            device_map=manager.device_map(),
            max_inference_batch_size=1,
            max_new_tokens=256,
        )


if __name__ == "__main__":
    unittest.main()
