from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from pathlib import Path
import re
import warnings

from app.config import DEFAULT_LOCAL_ASR_MODEL, load_models_config, project_root
from app.logging_utils import get_logger


LOGGER = get_logger()
QWEN_MODEL_KEY = "qwen3_asr_1_7b"
MOSS_MODEL_KEY = "moss_transcribe_diarize"
LOCAL_ASR_MODEL_NAMES = {
    QWEN_MODEL_KEY: "Qwen/Qwen3-ASR-1.7B",
    MOSS_MODEL_KEY: "OpenMOSS-Team/MOSS-Transcribe-Diarize",
}
MOSS_DEFAULT_PROMPT = (
    "请将音频转写为文本，每一段需以起始时间戳和说话人编号"
    "（[S01]、[S02]、[S03]…）开头，正文为对应的语音内容，"
    "并在段末标注结束时间戳，以清晰标明该段语音范围。"
)

# Keep the official recipe in UTF-8. The legacy constant above is retained for
# compatibility with older callers, but v2 always uses this canonical prompt.
MOSS_OFFICIAL_PROMPT = (
    "请将音频转写为文本，每一段需以起始时间戳和说话人编号（[S01]、[S02]、[S03]…）开头，"
    "正文为对应的语音内容，并在段末标注结束时间戳，以清晰标明该段语音范围。"
)
MOSS_DEFAULT_PROMPT = MOSS_OFFICIAL_PROMPT


@dataclass(slots=True)
class _MossBatchItem:
    audio: Any
    sample_rate: int
    context: str
    language: str | None


@dataclass(slots=True)
class MossTranscriptSegment:
    start_ms: int
    end_ms: int
    speaker: str
    text: str


class MossTranscribeDiarizeAdapter:
    def __init__(self, path: Path, torch_module, device, dtype, progress=None) -> None:
        self.path = path
        self.torch = torch_module
        self.device = device
        self.dtype = dtype
        self._model = None
        self._processor = None
        self._loaded_device = None
        self._loaded_dtype = None
        self._progress = progress

    def _report(self, phase: str, detail: str) -> None:
        if self._progress:
            self._progress({"phase": phase, "detail": detail})

    def transcribe(
        self,
        audio,
        context,
        language,
        return_time_stamps: bool = False,
    ) -> list[SimpleNamespace]:
        del return_time_stamps
        self._ensure_loaded()
        items = [
            _MossBatchItem(
                audio=audio_chunk,
                sample_rate=sample_rate,
                context=context[index] if index < len(context) else "",
                language=language[index] if index < len(language) else None,
            )
            for index, (audio_chunk, sample_rate) in enumerate(audio)
        ]
        return [self._transcribe_one(item) for item in items]

    def _ensure_loaded(self) -> None:
        if (
            self._model is not None
            and self._processor is not None
            and self._loaded_device == self.device
            and self._loaded_dtype == self.dtype
        ):
            return
        if not self.path.exists():
            raise FileNotFoundError(f"MOSS model path does not exist: {self.path}")

        try:
            from transformers import AutoModelForCausalLM, AutoProcessor
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "transformers is not installed in the current Python environment."
            ) from exc

        if self._model is not None:
            LOGGER.info(
                "reloading MOSS ASR model for runtime plan change | old_device=%s | new_device=%s | old_dtype=%s | new_dtype=%s",
                self._loaded_device,
                self.device,
                self._loaded_dtype,
                self.dtype,
            )
            self._model = None
            self._processor = None

        LOGGER.info(
            "loading MOSS ASR model | path=%s | device=%s | dtype=%s",
            self.path,
            self.device,
            self.dtype,
        )
        self._report("model_loading", "正在从本地磁盘加载 MOSS 权重")
        try:
            model = AutoModelForCausalLM.from_pretrained(
                str(self.path),
                trust_remote_code=True,
                dtype="auto",
            )
        except TypeError:
            model = AutoModelForCausalLM.from_pretrained(
                str(self.path),
                trust_remote_code=True,
                torch_dtype="auto",
            )

        self._report("processor_loading", "正在加载 MOSS 音频处理器")
        try:
            processor = AutoProcessor.from_pretrained(
                str(self.path),
                trust_remote_code=True,
                fix_mistral_regex=True,
            )
        except TypeError:
            processor = AutoProcessor.from_pretrained(
                str(self.path),
                trust_remote_code=True,
            )

        self._report("model_moving_to_device", f"正在将 MOSS 模型迁移到 {self.device}")
        self._model = model.to(dtype=self.dtype).to(self.device).eval()
        self._processor = processor
        self._loaded_device = self.device
        self._loaded_dtype = self.dtype

    def _transcribe_one(self, item: _MossBatchItem) -> SimpleNamespace:
        self._report("feature_extracting", "正在提取音频特征")
        prompt = self._build_prompt(item.context, item.language)
        text = self._processor.apply_chat_template(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "audio", "audio": "segment.wav"},
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            tokenize=False,
            add_generation_prompt=True,
        )
        inputs = self._processor(
            text=text,
            audio=[item.audio],
            return_tensors="pt",
        ).to(self.device)
        prompt_len = int(inputs["attention_mask"][0].sum().item())

        generate_kwargs = {
            "input_ids": inputs["input_ids"],
            "attention_mask": inputs["attention_mask"],
            "input_features": inputs["input_features"],
            "audio_feature_lengths": inputs["audio_feature_lengths"],
            "audio_chunk_mapping": inputs["audio_chunk_mapping"],
            "max_new_tokens": self._max_new_tokens(item),
            "do_sample": False,
        }
        self._report("generating", "正在生成带时间戳与说话人的转录")
        with self.torch.inference_mode(), self._autocast_context():
            output_ids = self._model.generate(**generate_kwargs)[0][prompt_len:]

        raw_text = self._processor.tokenizer.decode(
            output_ids,
            skip_special_tokens=True,
        ).strip()
        segments = _parse_moss_segments(raw_text)
        return SimpleNamespace(
            text=" ".join(segment.text for segment in segments) or _strip_moss_markup(raw_text),
            language=item.language,
            raw_text=raw_text,
            segments=segments,
        )

    def _autocast_context(self):
        if self.device.type == "cuda" and self.dtype in (
            self.torch.float16,
            self.torch.bfloat16,
        ):
            return self.torch.amp.autocast("cuda", dtype=self.dtype)
        return self.torch.no_grad()

    def _build_prompt(self, context: str, language: str | None) -> str:
        parts = [MOSS_OFFICIAL_PROMPT]
        if language:
            parts.append(f"语言提示：{language}")
        if context:
            parts.append(f"上下文、术语和替换规则：\n{context}")
        return "\n\n".join(parts)

    def _max_new_tokens(self, item: _MossBatchItem) -> int:
        duration_seconds = len(item.audio) / max(item.sample_rate, 1)
        return max(2048, min(65536, int(duration_seconds * 24) + 512))


def _strip_moss_markup(text: str) -> str:
    stripped = re.sub(r"\[\d+(?:\.\d+)?\]\[S\d+\]", " ", text or "")
    stripped = re.sub(r"\[\d+(?:\.\d+)?\]", " ", stripped)
    stripped = re.sub(r"\s+", " ", stripped).strip()
    return stripped or (text or "").strip()


_MOSS_SEGMENT_PATTERN = re.compile(
    r"\[(?P<start>\d+(?:\.\d+)?)\]\[(?P<speaker>S\d+)\]"
    r"(?P<text>.*?)"
    r"\[(?P<end>\d+(?:\.\d+)?)\]"
    r"(?=(?:\[\d+(?:\.\d+)?\]\[S\d+\])|$)",
    re.DOTALL,
)


def _parse_moss_segments(text: str) -> list[MossTranscriptSegment]:
    segments: list[MossTranscriptSegment] = []
    for match in _MOSS_SEGMENT_PATTERN.finditer(text or ""):
        start = float(match.group("start"))
        end = float(match.group("end"))
        if end < start:
            continue
        segment_text = re.sub(r"\s+", " ", match.group("text")).strip()
        if not segment_text:
            continue
        segments.append(
            MossTranscriptSegment(
                start_ms=max(0, int(round(start * 1000))),
                end_ms=max(0, int(round(end * 1000))),
                speaker=match.group("speaker"),
                text=segment_text,
            )
        )
    return segments


class ModelManager:
    def __init__(self, *, active_local_asr_model_override: str | None = None) -> None:
        self._project_root = project_root()
        self._config = load_models_config()
        self._active_local_asr_model_override = active_local_asr_model_override
        self._qwen_model = None
        self._moss_model = None
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
    def moss_path(self) -> Path:
        return self._config.moss_transcribe_diarize.resolved_path(self._project_root)

    @property
    def pyannote_path(self) -> Path:
        return self._config.pyannote_speaker_diarization.resolved_path(
            self._project_root
        )

    @property
    def active_local_asr_model(self) -> str:
        return self._active_local_asr_model_override or self._config.active_local_asr_model or DEFAULT_LOCAL_ASR_MODEL

    def refresh_config(self) -> None:
        previous = self._config
        current = load_models_config()
        if current.qwen3_asr_1_7b.path != previous.qwen3_asr_1_7b.path:
            self._qwen_model = None
        if current.moss_transcribe_diarize.path != previous.moss_transcribe_diarize.path:
            self._moss_model = None
        if (
            current.pyannote_speaker_diarization.path
            != previous.pyannote_speaker_diarization.path
        ):
            self._pyannote_pipeline = None
        if current.active_local_asr_model != previous.active_local_asr_model:
            LOGGER.info(
                "active local ASR model changed | previous=%s | current=%s",
                previous.active_local_asr_model,
                current.active_local_asr_model,
            )
        self._config = current

    def device(self):
        return self.torch.device(self.device_map())

    def device_map(self) -> str:
        return "cuda:0" if self.torch.cuda.is_available() else "cpu"

    def qwen_torch_dtype(self):
        return self.torch.float16 if self.torch.cuda.is_available() else self.torch.float32

    def moss_torch_dtype(self):
        if not self.torch.cuda.is_available():
            return self.torch.float32
        if getattr(self.torch.cuda, "is_bf16_supported", lambda: False)():
            return self.torch.bfloat16
        return self.torch.float16

    def torch_dtype(self):
        if self.active_local_asr_model == MOSS_MODEL_KEY:
            return self.moss_torch_dtype()
        return self.qwen_torch_dtype()

    def local_asr_model_name(self) -> str:
        return LOCAL_ASR_MODEL_NAMES.get(
            self.active_local_asr_model,
            self.active_local_asr_model,
        )

    def local_asr_batch_size(self) -> int:
        if self.active_local_asr_model == MOSS_MODEL_KEY:
            return 1
        return 2

    def local_asr_uses_integrated_diarization(self) -> bool:
        return self.active_local_asr_model == MOSS_MODEL_KEY

    def get_local_asr_model(self):
        if self.active_local_asr_model == MOSS_MODEL_KEY:
            return self.get_moss_model()
        return self.get_qwen_model()

    def get_qwen_model(self):
        if self._qwen_model is None:
            if not self.qwen_path.exists():
                raise FileNotFoundError(f"Qwen model path does not exist: {self.qwen_path}")

            from qwen_asr import Qwen3ASRModel

            self._qwen_model = Qwen3ASRModel.from_pretrained(
                str(self.qwen_path),
                dtype=self.qwen_torch_dtype(),
                device_map=self.device_map(),
                max_inference_batch_size=2,
                max_new_tokens=256,
            )
        return self._qwen_model

    def get_moss_model(self):
        if self._moss_model is None:
            self._moss_model = MossTranscribeDiarizeAdapter(
                path=self.moss_path,
                torch_module=self.torch,
                device=self.device(),
                dtype=self.moss_torch_dtype(),
            )
        return self._moss_model

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
            "active_local_asr_model": self.active_local_asr_model,
            "local_asr_model": self.local_asr_model_name(),
            "qwen_path": str(self.qwen_path),
            "moss_path": str(self.moss_path),
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
