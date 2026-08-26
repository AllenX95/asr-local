# Qwen3-ASR FlashAttention 2 与 micro-batch 研究笔记

日期：2026-08-26

范围：当前 `apps/worker-python` 的 Qwen3-ASR Transformers 后端、Windows RTX 5060 Ti 16GB 运行环境，以及分段转录的并发/时间线语义。
结论性质：官方资料与本地代码证据分开记录；性能结论中标注的“建议”是针对本项目的工程推断，不是 Qwen 官方承诺。

## 结论先行

1. **当前不需要把官方 `flash-attn`/FlashAttention 2 作为运行前置依赖。** Qwen 官方确实推荐 FA2 在长输入、大 batch 时降低显存并加速，但当前应用运行在 Windows + RTX 5060 Ti（Blackwell，compute capability 12.0），而 Dao-AILab 官方 FA2 README 仍把 NVIDIA CUDA 支持范围写为 Ampere、Ada、Hopper，Windows 也只写成“可能可用、仍需更多测试”。因此在这台机器上直接安装官方 FA2 属于高风险实验，不应作为默认安装步骤。
2. **继续使用并验证 PyTorch 内置 SDPA 更稳妥。** PyTorch 2.x 的 `scaled_dot_product_attention` 会在 CUDA 输入上自动选择可用的 fused backend，其中包含 PyTorch 的 FlashAttention-2 实现；它不等于安装 `flash-attn` Python 扩展。Qwen3-ASR 模型源码声明支持 SDPA 和 FA2。虽然当前应用没有显式传 `attn_implementation`，本机实际加载后的 root/audio/text config 均解析为 `sdpa`。Profiler 同时观察到 efficient-attention 与 math fallback，说明当前已经使用 SDPA，但并非所有 attention 都命中同一种 fused kernel。
3. **micro-batch 不会天然破坏时间线。** 当前应用已经保留 `segment_index`、`segment_id`、权威 `start_ms/end_ms` 和带 padding 的输入起点；批量调用返回后按索引重新关联，并将模型相对时间戳加回输入起点后裁剪到权威分段边界。只要调度器不丢失这些元数据，跨任务 batching 与单任务内部 batching 在时间线语义上等价。
4. **推荐的生产形态是“全局 GPU 队列 + 动态 micro-batch + 每项携带时间线元数据”，而不是多个模型副本并行。** 第一版可以先批量同一录音的相邻分段以降低改造风险；真正面对多个任务时，应跨任务组 batch，并以总音频帧数/最长输入长度作为预算，避免一个长录音独占 GPU。若需要跨段文本前缀或流式连续性，则该段序列不能当作完全独立样本并行；当前离线分段路径不使用前一段文本作为 prompt，因此可以独立 batching。

## 1. 当前本地版本与运行事实

本地只读检查得到：

| 项目 | 当前值 |
| --- | --- |
| GPU | NVIDIA GeForce RTX 5060 Ti 16GB |
| Compute capability | `(12, 0)`（Blackwell） |
| PyTorch | `2.9.0+cu130`，`torch.version.cuda = 13.0` |
| `qwen-asr` | `0.0.6` |
| Transformers | `4.57.6` |
| `flash_attn` Python 包 | 未安装 |
| PyTorch SDPA | 当前实际解析为 `sdpa`；Profiler 同时命中 efficient 与 math 路径 |
| 当前模型加载调用 | `Qwen3ASRModel.from_pretrained(..., dtype=..., device_map=..., max_inference_batch_size=1, max_new_tokens=256)` |

当前 [ModelManager](../../apps/worker-python/app/models/manager.py) 没有传 `attn_implementation`，并且 `local_asr_batch_size()` 固定返回 1。当前 [job_runner](../../apps/worker-python/app/pipeline/job_runner.py) 已有按 `ASR_SEGMENT_BATCH_SIZE` 分组的接口，但被 ModelManager 的 1 覆盖。

本机运行时检查确认加载后的 root、audio、text attention implementation 均为 `sdpa`。一次 30 秒静音、batch 1 的初步对照中，SDPA 与显式 eager 的耗时分别约为 0.74 秒和 0.69 秒，峰值显存相同；该差异处于短探针波动范围，且静音几乎不产生 decode token，不能据此宣称任一路径更快。它只说明当前工作负载下 attention 未表现为明显瓶颈，正式决策仍需真实语音和多 batch 回归。

当前分段策略是最多 30 秒的 ASR 单元，外部 Pyannote diarization 时为输入两端各增加最多 200ms padding；权威输出边界仍为原分段 `start_ms/end_ms`。见 [segment_planner.py](../../apps/worker-python/app/pipeline/segment_planner.py) 与 [segment_types.py](../../apps/worker-python/app/pipeline/segment_types.py)。

## 2. FlashAttention 2：官方支持与风险

### 2.1 Qwen 官方态度

Qwen3-ASR 官方 README 的 Transformers quickstart：

- 提供 `attn_implementation="flash_attention_2"` 作为可选参数，但示例将其注释掉；
- 明确建议在长输入和大 batch 时使用 FA2 以降低显存和加速；
- 明确要求 FA2 使用 `torch.float16` 或 `torch.bfloat16`；
- `max_inference_batch_size` 是 Qwen 包自己的 batch 上限，较小值可避免 OOM，与 attention backend 是两个独立开关。

来源：[Qwen3-ASR 官方 README，Environment Setup / Quick Inference](https://github.com/QwenLM/Qwen3-ASR#environment-setup) 和 [官方 Transformers 示例](https://github.com/QwenLM/Qwen3-ASR/blob/main/examples/example_qwen3_asr_transformers.py)。

Qwen 官方 Transformers backend 源码的模型基类声明 `_supports_flash_attn = True`、`_supports_sdpa = True`，并根据 config 的 `_attn_implementation` 从 Transformers 的 attention registry 选择实现；音频编码器在 `flash_attention_2` 时不创建普通 4-D mask，而使用变长序列信息。这证明**模型架构层面支持这两类 backend**，但不证明任意操作系统/GPU/版本组合都可用。来源：[Qwen3-ASR 官方 modeling_qwen3_asr.py](https://github.com/QwenLM/Qwen3-ASR/blob/main/qwen_asr/core/transformers_backend/modeling_qwen3_asr.py)。

### 2.2 Windows、Blackwell 和 CUDA 13

Dao-AILab 官方 FA2 README 当前的 NVIDIA 说明是：CUDA 12.0+、fp16/bf16，并列出的 GPU 是 **Ampere、Ada 或 Hopper**；同一 README 的安装说明写明官方主要支持 Linux，Windows “might work” 但编译仍需要更多测试。RTX 5060 Ti 的 compute capability 为 12.0/Blackwell，不在该 FA2 README 的明确支持列表内。

来源：[FlashAttention 官方 README：Installation and features / NVIDIA CUDA Support](https://github.com/Dao-AILab/flash-attention#installation-and-features)。

因此，对本项目的判断是：

- `torch 2.9.0+cu130` 和 fp16 本身满足“PyTorch/CUDA/dtype 版本形态”要求，但**不能弥补官方 FA2 对当前 Blackwell/Windows 组合没有稳定承诺**这一点；
- `pip install flash-attn --no-build-isolation` 在 Windows 上很可能落入本地 C++/CUDA 编译，受 Python、Torch ABI、CUDA Toolkit、MSVC、架构编译目标共同影响；失败或编译出不能在 SM120 上正确运行的扩展都属于可预见风险；
- Qwen 官方 Docker 示例是 Linux CUDA 12.8 容器，并不是 Windows 安装验证，因此不能作为当前桌面运行时的兼容性证明。来源：[Qwen 官方 Dockerfile](https://github.com/QwenLM/Qwen3-ASR/blob/main/docker/Dockerfile-qwen3-asr-cu128)。

官方 FA2 README 的性能图主要展示 A100/H100；没有当前 RTX 5060 Ti 的官方 benchmark。因此“会快多少”不能从官方资料外推到本机。

### 2.3 PyTorch SDPA 是更低风险的第一步

PyTorch 官方 `scaled_dot_product_attention` 文档说明：函数会根据输入自动选择最优实现，CUDA 上可在 FlashAttention-2、memory-efficient attention 和 PyTorch C++ 实现之间选择；如需强制某一实现，可用 `torch.nn.attention.sdpa_kernel()`。来源：[PyTorch SDPA 官方文档](https://docs.pytorch.org/docs/stable/generated/torch.nn.functional.scaled_dot_product_attention.html)。

这条路径的工程含义是：先让 Transformers/Qwen 使用 `attn_implementation="sdpa"`，让 PyTorch 自己判断当前 RTX 5060 Ti 上可用的 kernel；不需要额外安装 Dao-AILab `flash-attn` 扩展，也不会引入 Windows 编译链。需要注意：

- SDPA 是否覆盖 Qwen3-ASR 的文本和音频 attention，要看本地 `qwen-asr`/Transformers 版本的实际 dispatch；Qwen 源码声明支持，但仍需运行 smoke；
- PyTorch 可能因 mask、head dimension、dtype、变长输入等条件选择 math/memory-efficient fallback，不能只看 `flash_sdp_enabled()` 就声称实际使用了 FlashAttention kernel；
- 若 SDPA 结果正确但速度提升很小，当前 30 秒分段、batch 1 的场景没有足够理由承受额外 FA2 依赖。

### 2.4 是否值得安装：决策

对当前版本的建议顺序：

1. 保持不安装 `flash-attn`，先建立 eager/SDPA 两组相同输入 benchmark，记录端到端耗时、`max_memory_allocated/reserved`、输出文本差异和连续运行稳定性。
2. 若 SDPA 在本机被选为 fused backend且有稳定收益，直接采用 SDPA；它已满足“减少 attention 中间张量”的目标。
3. 只有在确有长输入/大 batch 的显存瓶颈、SDPA 无法覆盖且有可复现的 Windows/SM120 wheel 或受控编译产物时，才把 FA2 作为可选实验依赖，不能写进默认安装流程。

以上“先 SDPA、暂不安装 FA2”是结合官方兼容矩阵与当前工作负载的工程推断；Qwen 官方的“长输入、大 batch 推荐 FA2”仍然成立，但不等于当前硬件上必须安装。

## 3. micro-batch 的时间线语义

### 3.1 当前代码已经具备的关联关系

当前 [transcribe_segments](../../apps/worker-python/app/pipeline/job_runner.py) 对每个 batch item 生成：

- `segment_index`：本任务中的稳定序号；
- `segment_id`：如 `segment-0001`；
- 原始权威 `segment.start_ms/end_ms`；
- 实际送给模型的 `input_start_ms/input_end_ms`；
- `chunk_origins[segment_index]`：实际输入起点。

`transcribe_audio_batch()` 将多条 `(audio, sample_rate)`、`context`、`language` 交给模型，随后按返回列表与输入列表 `zip`，构造 `dict[segment_index, transcription]`。因此 batch 返回顺序即使未来由 GPU kernel 或异步调度改变，也可以通过 `segment_index` 重新关联；调度器不能依赖“完成先后”作为时间线。

若模型输出内部相对时间戳，`transcript_segments_from_model_output()` 会把相对时间加上 `model_origin_ms`，再将起止裁剪到权威 `source_segment.start_ms/end_ms`。这使得两端 200ms padding 可用于避免切词，但不会把邻接分段的 padding 音频重复写入最终时间线。该设计与 [PlannedSegment](../../apps/worker-python/app/pipeline/segment_types.py) 的注释一致：只有 `start_ms/end_ms` 可以进入导出器，`input_*` 只属于模型输入边界。

所以，**batch 不是把音频拼成一条长音频**；它是一次 forward/generate 中的多个独立样本。每项必须保留自己的来源和边界，返回后再排序/裁剪/合并。

### 3.2 Qwen 官方实现也采用“批量计算、按原始索引合并”

Qwen 官方 `Qwen3ASRModel.transcribe()` 在长音频时会把输入切成 `AudioChunk(orig_index, chunk_index, offset_sec, ...)`，批量推理后按 `orig_index` 重新聚合；需要 timestamps 时，forced aligner 也按 batch 调用，并对每个结果加上 `offset_sec` 后再合并。

来源：[Qwen 官方 qwen3_asr.py 的 chunk/batch/offset 实现](https://github.com/QwenLM/Qwen3-ASR/blob/main/qwen_asr/inference/qwen3_asr.py)。

这说明只要保留 `orig_index/offset` 这类映射，跨段 batching 与时间线恢复在模型 API 层面是可行的。当前应用的 `segment_index/chunk_origins` 是同一类必要元数据。

### 3.3 什么时候会破坏语义

下面几种做法才会真正造成时间线或内容问题：

- 把不同段的波形在 CPU 端拼成一个数组，再只调用一次单样本 `transcribe`；
- 丢弃 `segment_id`/`segment_index`，按异步完成顺序追加结果；
- 用含 200ms padding 的 `input_start_ms` 直接作为最终输出起点，而不按权威边界裁剪；
- 为了 batching 给每个样本错误地复用另一个任务的 `context` 或强制语言；
- 将同一录音的相邻分段改成带前一段文本前缀的自回归流，而仍按“各段独立样本”处理，导致重复或重叠文本。

当前离线 external-diarization 路径对每段传同一个任务级 `context`，不传上一段识别文本，因此在当前语义下段与段之间是可独立 batching 的。若未来启用 Qwen vLLM streaming，需注意 Qwen 官方明确写明 streaming 不支持 batch、只支持 single stream；此时应按流顺序处理，不应套用离线 micro-batch。来源：[Qwen3-ASR 官方 README：Streaming Inference](https://github.com/QwenLM/Qwen3-ASR#streaming-inference)。

## 4. 单长录音内部 batching vs 多任务 batching

| 方案 | 优点 | 代价/风险 | 适用阶段 |
| --- | --- | --- | --- |
| 同一录音内相邻分段 batch | 最容易接入现有 `transcribe_segments`；天然共享 language/context；容易按 `segment_index` 顺序回收 | 一个长任务可能独占 GPU；相邻段时长差异大时 padding 浪费；单任务没有足够分段时 GPU 仍空闲 | 第一版低风险改造、单任务优先 |
| 多任务共享动态 batch | 多用户/多工作流吞吐和公平性更好；单模型权重只保留一份；可以填满 batch | 需要全局队列、取消/重试/超时、结果路由和 per-item 元数据；不同 context/language/长度需正确处理 | 目标生产形态 |
| 同一录音按前缀连续 decode | 可能利用前文语境，适合真正的连续流 | 依赖强，难以并行；需处理重复、回滚和时间戳；Qwen streaming 官方不支持 batch | 只有流式/强上下文需求 |

**推荐：**

- 如果目标是先让一个长录音更快，先做“单任务内部 batch”，批量相邻、长度相近的分段，完成索引回收和时间线回归；
- 如果目标是三个 workflow 同时服务，最终应做“跨任务动态 batch”。GPU 队列中每个 item 需要至少携带 `workflow_id、attempt_id、segment_id、segment_index、start_ms、end_ms、input_origin_ms、context、language、audio`；batch 返回后按这些字段路由，不能按完成时间写结果；
- 用 `max_batch_items + max_total_audio_ms/frames + max_wait_ms` 三个条件共同封顶，而不是只设固定 item 数。建议按长度分桶，避免一个 30 秒样本把一批 1 秒样本全部 pad 到 30 秒；
- 为公平性设置 per-workflow in-flight 上限或加权轮询，例如每次最多从同一 workflow 取一个 micro-batch，再从其他有等待项的 workflow 取样本；
- batch item 的模型输入可以有各自 `context` 和 `language`。Qwen 官方 batch 示例本身使用 context/language 列表，因此无需把不同任务强行合并为同一 prompt。来源：[Qwen 官方 Transformers 示例](https://github.com/QwenLM/Qwen3-ASR/blob/main/examples/example_qwen3_asr_transformers.py)。

### 推荐的调度层次

```text
workflow A: segment A1 ─┐
workflow A: segment A2 ─┼─> global GPU queue -> dynamic batch -> indexed results
workflow B: segment B1 ─┤                              |
workflow C: segment C1 ─┘                              v
                                   per-item origin/boundary clamp -> per-workflow ordered artifact
```

注意这只是**同一模型的一次批处理**，不是同时加载多个 Qwen 模型副本。后者会线性复制权重和 runtime 缓存，显存效率更差。

## 5. 建议的验证门槛

在放大 batch 或切换 SDPA/FA2 前，至少验证：

- 1/2/4/8（必要时 16）项、时长组合 `[1s, 5s, 30s]` 的峰值 allocated/reserved；
- 同一音频拆分成 1 段、多个相邻段、打乱 batch 完成顺序三种情况，最终导出顺序和 `start_ms/end_ms` 完全一致；
- 外部 diarization 的 200ms padding 不会把邻段内容写出权威边界；
- 每项 `context`、`language` 混合 batch 与各自单项推理结果做文本回归；
- 取消、失败重试、GPU OOM 后，未完成 item 能回到原 workflow，不能污染其他 workflow；
- eager、SDPA（以及未来可复现的 FA2）对同一输入的文本、语言、异常行为和显存数据对比。

在没有这组实测前，不能仅凭“Qwen 官方推荐 FA2”或单次 batch 能运行就提高生产并发上限。

## 参考来源

- [QwenLM/Qwen3-ASR README](https://github.com/QwenLM/Qwen3-ASR)
- [Qwen3-ASR Transformers example](https://github.com/QwenLM/Qwen3-ASR/blob/main/examples/example_qwen3_asr_transformers.py)
- [Qwen3-ASR Transformers model source](https://github.com/QwenLM/Qwen3-ASR/blob/main/qwen_asr/core/transformers_backend/modeling_qwen3_asr.py)
- [Qwen3-ASR inference source](https://github.com/QwenLM/Qwen3-ASR/blob/main/qwen_asr/inference/qwen3_asr.py)
- [Dao-AILab/flash-attention README](https://github.com/Dao-AILab/flash-attention)
- [PyTorch scaled dot product attention](https://docs.pytorch.org/docs/stable/generated/torch.nn.functional.scaled_dot_product_attention.html)
