from __future__ import annotations

from pathlib import Path
import warnings
from contextlib import nullcontext

from app.config import load_models_config, project_root
from app.logging_utils import get_logger
from app.runtime.gpu_lifecycle import release_gpu_resources


LOGGER = get_logger()
QWEN_MODEL_KEY = "qwen3_asr_1_7b"
LOCAL_ASR_MODEL_NAMES = {
    QWEN_MODEL_KEY: "Qwen/Qwen3-ASR-1.7B",
}

# Keep the runtime's normal work size separate from the limit passed to the
# Qwen wrapper.  The normal size is a scheduling choice; the hard maximum is a
# model-loader safety contract and may be raised independently after a real
# workload benchmark.
LOCAL_ASR_OPERATIONAL_BATCH_SIZE = 4
QWEN_HARD_MAX_INFERENCE_BATCH_SIZE = 8


class ModelManager:
    def __init__(
        self,
        *,
        resolved_device: str | None = None,
        dtype: str | None = None,
        execution_gate=None,
    ) -> None:
        if resolved_device not in {None, "cpu", "cuda:0"}:
            raise ValueError(f"unsupported resolved device: {resolved_device}")
        if dtype not in {None, "float16", "float32", "bfloat16"}:
            raise ValueError(f"unsupported torch dtype: {dtype}")
        self._project_root = project_root()
        self._config = load_models_config()
        self._resolved_device = resolved_device
        self._dtype = dtype
        self.execution_gate = execution_gate
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

    def refresh_config(self) -> None:
        previous = self._config
        current = load_models_config()
        if current.qwen3_asr_1_7b.path != previous.qwen3_asr_1_7b.path:
            self._qwen_model = None
        if (
            current.pyannote_speaker_diarization.path
            != previous.pyannote_speaker_diarization.path
        ):
            self._pyannote_pipeline = None
        self._config = current

    def device(self):
        return self.torch.device(self.device_map())

    def device_map(self) -> str:
        if self._resolved_device is not None:
            return self._resolved_device
        return "cuda:0" if self.torch.cuda.is_available() else "cpu"

    def qwen_torch_dtype(self):
        if self._dtype is not None:
            return getattr(self.torch, self._dtype)
        return self.torch.float16 if self.device_map().startswith("cuda") else self.torch.float32

    def torch_dtype(self):
        return self.qwen_torch_dtype()

    def local_asr_model_name(self) -> str:
        return LOCAL_ASR_MODEL_NAMES[QWEN_MODEL_KEY]

    def local_asr_batch_size(self) -> int:
        """Return the default operational batch size for local ASR."""

        return LOCAL_ASR_OPERATIONAL_BATCH_SIZE

    def local_asr_max_batch_size(self) -> int:
        """Return the Qwen loader's hard batch ceiling."""

        return QWEN_HARD_MAX_INFERENCE_BATCH_SIZE

    def local_asr_runtime_key(self):
        """Return the canonical runtime identity used by the GPU dispatcher."""

        from app.pipeline.segment_types import RuntimeKey

        dtype = self._dtype
        if dtype is None:
            dtype = "float16" if self.device_map().startswith("cuda") else "float32"
        return RuntimeKey(
            model_name=QWEN_MODEL_KEY,
            model_path=str(self.qwen_path),
            device=self.device_map(),
            dtype=str(dtype),
        )

    def local_asr_uses_integrated_diarization(self) -> bool:
        """Whether the active local ASR model emits speaker diarization.

        Qwen3-ASR is used with external Pyannote diarization in this runtime;
        keeping this capability explicit prevents accidental whole-recording
        routing through the integrated-diarization path.
        """

        return False

    def get_local_asr_model(self):
        return self.get_qwen_model()

    def get_qwen_model(self):
        if self._qwen_model is None:
            if not self.qwen_path.exists():
                raise FileNotFoundError(f"Qwen model path does not exist: {self.qwen_path}")

            try:
                from qwen_asr import Qwen3ASRModel
            except Exception as exc:
                raise RuntimeError(
                    "QWEN_RUNTIME_UNAVAILABLE: qwen-asr cannot be imported in the main Python runtime."
                ) from exc

            device_map = self.device_map()
            torch_dtype = self.qwen_torch_dtype()
            LOGGER.info(
                "loading Qwen model with resolved runtime | device=%s | dtype=%s",
                device_map,
                torch_dtype,
            )
            self._qwen_model = Qwen3ASRModel.from_pretrained(
                str(self.qwen_path),
                dtype=torch_dtype,
                device_map=device_map,
                max_inference_batch_size=self.local_asr_max_batch_size(),
                max_new_tokens=256,
            )
        return self._qwen_model

    def clear_cuda_cache(self) -> None:
        """Release temporary CUDA allocator blocks without unloading models."""

        try:
            if self.torch.cuda.is_available():
                self.torch.cuda.empty_cache()
        except Exception:
            LOGGER.debug("failed to clear CUDA cache", exc_info=True)

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
            target_device = self.device_map()
            if self._resolved_device is not None or target_device.startswith("cuda"):
                try:
                    self._pyannote_pipeline.to(self.torch.device(target_device))
                    LOGGER.info("Pyannote pipeline moved to resolved runtime | device=%s", target_device)
                except Exception as exc:
                    if self._resolved_device is not None:
                        self._pyannote_pipeline = None
                        raise RuntimeError(
                            f"MODEL_DEVICE_MOVE_FAILED: failed to move Pyannote pipeline to {target_device}"
                        ) from exc
                    LOGGER.warning("failed to move pyannote pipeline to CUDA; continuing on CPU | error=%s", exc)
        return self._pyannote_pipeline

    def close_qwen_model(self) -> None:
        model = self._qwen_model
        self._qwen_model = None
        release_gpu_resources(model, torch_module=self._torch, label="qwen")

    def close_pyannote_pipeline(self) -> None:
        pipeline = self._pyannote_pipeline
        self._pyannote_pipeline = None
        with self._gate_context():
            if pipeline is not None:
                try:
                    move_to = getattr(pipeline, "to", None)
                    if callable(move_to):
                        move_to(self.torch.device("cpu"))
                except Exception:
                    LOGGER.debug("failed to move Pyannote pipeline to CPU during cleanup", exc_info=True)
            release_gpu_resources(pipeline, torch_module=self._torch, label="pyannote")

    def close_local_models(self) -> None:
        self.close_qwen_model()
        self.close_pyannote_pipeline()

    def _gate_context(self):
        return self.execution_gate if self.execution_gate is not None else nullcontext()

    def runtime_summary(self, include_device: bool = True) -> dict:
        summary = {
            "local_asr_model": self.local_asr_model_name(),
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
