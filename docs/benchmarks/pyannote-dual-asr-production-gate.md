# Pyannote 双 ASR 生产 Gate 记录

## 当前环境

- GPU：NVIDIA GeForce RTX 5060 Ti 16GB
- Python：3.12.2（`apps/worker-python/.venv`）
- Torch：2.9.0+cu130
- Transformers：5.13.0
- Pyannote Audio：4.0.4
- 模型目录：Qwen、MOSS、Pyannote 均存在

## 已完成的短 smoke

### Pyannote provider

- 1 秒内存零音频：通过。
- 无 speaker 输出时回退为 `Speaker 1`：通过。
- GPU allocated 从约 42MB 回落到约 10MB 基线：通过。

### MOSS chunk adapter

- 1 秒内存零音频：加载和生成通过。
- 单段显存峰值约 1.83GB；未触发长上下文 OOM。
- `close_local_models()` 后 GPU allocated 约 9.7MB：通过。
- 当前结果为合成静音输入，不代表识别质量。

### Qwen runtime

- 已创建独立 `.venv-qwen`，复用 CUDA/Torch 基础包但固定 Qwen 兼容依赖。
- `qwen-asr 0.0.6` 固定 Transformers 4.57.6；MOSS 已验证 runtime 固定 Transformers 5.13.0。
- 已验证将 qwen 包强行装入 MOSS runtime 会导致导入错误，因此主 runtime 保持干净。
- Qwen 1 秒内存零音频通过隔离子进程 smoke；输出为空符合静音输入预期，GPU 回落至 0 基线。

## 发布 gate 状态

| Gate | Qwen | MOSS |
| --- | --- | --- |
| Provider/分块契约 | 代码与单元测试通过 | 代码与单元测试通过 |
| Pyannote 前置与显存释放 | 1 秒隔离 Qwen smoke 通过 | 短 smoke 通过 |
| 10/30/90 分钟真实录音 | 未执行 | 未执行 |
| CER/WER 与热词质量 | 未执行 | 未执行 |
| 生产可选项 | 待长录音/质量 gate | 待长录音/质量 gate |

当前可以合并代码重构，但不能宣称双后端均已完成生产验收；还需要真实长录音质量和稳定性数据。
