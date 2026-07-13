from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
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

    @mock.patch("qwen_asr.Qwen3ASRModel.from_pretrained")
    def test_forced_cpu_overrides_cuda_for_qwen(self, load_model):
        load_model.return_value = object()
        manager = ModelManager(resolved_device="cpu", dtype="float32")
        manager._torch = SimpleNamespace(
            cuda=SimpleNamespace(is_available=lambda: True),
            float16="float16",
            float32="float32",
            bfloat16="bfloat16",
            device=lambda value: value,
        )

        manager.get_qwen_model()

        self.assertEqual(manager.device_map(), "cpu")
        self.assertEqual(manager.qwen_torch_dtype(), "float32")
        load_model.assert_called_once_with(
            str(manager.qwen_path),
            dtype="float32",
            device_map="cpu",
            max_inference_batch_size=1,
            max_new_tokens=256,
        )

    def test_forced_cpu_keeps_pyannote_on_cpu_when_cuda_is_available(self):
        pipeline = SimpleNamespace(to=mock.Mock())
        pipeline_type = SimpleNamespace(from_pretrained=mock.Mock(return_value=pipeline))
        pyannote_module = ModuleType("pyannote")
        audio_module = ModuleType("pyannote.audio")
        audio_module.Pipeline = pipeline_type
        pyannote_module.audio = audio_module
        manager = ModelManager(resolved_device="cpu", dtype="float32")
        manager._torch = SimpleNamespace(
            cuda=SimpleNamespace(is_available=lambda: True),
            float16="float16",
            float32="float32",
            bfloat16="bfloat16",
            device=lambda value: value,
        )

        with mock.patch.dict(sys.modules, {"pyannote": pyannote_module, "pyannote.audio": audio_module}):
            self.assertIs(manager.get_pyannote_pipeline(), pipeline)

        pipeline.to.assert_called_once_with("cpu")

    def test_forced_cpu_defaults_dtype_from_resolved_device(self):
        manager = ModelManager(resolved_device="cpu")
        manager._torch = SimpleNamespace(
            cuda=SimpleNamespace(is_available=lambda: True),
            float16="float16",
            float32="float32",
            bfloat16="bfloat16",
        )

        self.assertEqual(manager.qwen_torch_dtype(), "float32")


if __name__ == "__main__":
    unittest.main()
