from __future__ import annotations

from pathlib import Path
import warnings

from app.config import load_models_config, project_root
from app.logging_utils import get_logger


LOGGER = get_logger()


class ModelManager:
    def __init__(self) -> None:
        self._project_root = project_root()
        self._config = load_models_config()
        self._qwen_model = None
        self._pyannote_pipeline = None
        self._torch = None

    @property
    def torch(self):
        if self._torch is None:
            import torch

            self._torch = torch
        return self._torch

    @property
    def qwen_path(self) -> Path:
        return self._config.qwen3_asr_1_7b.resolved_path(self._project_root)

    @property
    def pyannote_path(self) -> Path:
        return self._config.pyannote_speaker_diarization.resolved_path(
            self._project_root
        )

    def device_map(self) -> str:
        return "cuda:0" if self.torch.cuda.is_available() else "cpu"

    def torch_dtype(self):
        return self.torch.float16 if self.torch.cuda.is_available() else self.torch.float32

    def get_qwen_model(self):
        if self._qwen_model is None:
            if not self.qwen_path.exists():
                raise FileNotFoundError(f"Qwen model path does not exist: {self.qwen_path}")

            from qwen_asr import Qwen3ASRModel

            self._qwen_model = Qwen3ASRModel.from_pretrained(
                str(self.qwen_path),
                dtype=self.torch_dtype(),
                device_map=self.device_map(),
                max_inference_batch_size=2,
                max_new_tokens=256,
            )
        return self._qwen_model

    def get_pyannote_pipeline(self):
        if self._pyannote_pipeline is None:
            if not self.pyannote_path.exists():
                raise FileNotFoundError(
                    f"pyannote model path does not exist: {self.pyannote_path}"
                )

            try:
                with warnings.catch_warnings():
                    warnings.filterwarnings(
                        "ignore",
                        category=UserWarning,
                        module=r"pyannote\.audio\.core\.io",
                    )
                    from pyannote.audio import Pipeline
            except ModuleNotFoundError as exc:
                raise RuntimeError(
                    "pyannote.audio is not installed in the current Python environment."
                ) from exc

            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore",
                    category=UserWarning,
                    module=r"pyannote\.audio\.core\.io",
                )
                self._pyannote_pipeline = Pipeline.from_pretrained(str(self.pyannote_path))
            if self.torch.cuda.is_available():
                try:
                    self._pyannote_pipeline.to(self.torch.device("cuda"))
                except Exception as exc:
                    LOGGER.warning(
                        "failed to move pyannote pipeline to CUDA; continuing with default device | error=%s",
                        exc,
                    )
        return self._pyannote_pipeline

    def runtime_summary(self, include_device: bool = True) -> dict:
        summary = {
            "qwen_path": str(self.qwen_path),
            "pyannote_path": str(self.pyannote_path),
        }
        if include_device:
            summary.update(
                {
                    "device_map": self.device_map(),
                    "torch_dtype": str(self.torch_dtype()),
                    "cuda_available": self.torch.cuda.is_available(),
                }
            )
        return summary
