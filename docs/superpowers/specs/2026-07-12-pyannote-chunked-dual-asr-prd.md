# ASR Local：Pyannote 前置分块与双 ASR 后端重构 PRD（已取代）

> Superseded by `docs/superpowers/specs/2026-07-12-qwen-only-single-runtime-design.md`.
> This document describes the removed MOSS dual-backend product scope.

## 1. 文档信息

- 状态：待开发
- 日期：2026-07-12
- 目标版本：Electron 桌面版下一迭代
- 默认后端：Qwen3-ASR-1.7B
- 可选后端：MOSS-Transcribe-Diarize 0.9B
- 目标硬件：Windows 11、NVIDIA RTX 5060 Ti 16GB

## 2. 背景

当前项目存在两条行为不同的本地转录链路：

1. Legacy 链路使用 Pyannote 完成说话人分离，再将片段交给 Qwen3-ASR。
2. MOSS 链路将整段长音频直接交给模型，依赖模型同时完成转录、时间戳和说话人标注。

真实长录音已证明 MOSS 整段推理会产生不可接受的注意力显存开销。一次约 19.24 分钟录音产生 15,293 个输入 token，注意力矩阵的理论开销已接近 14 GiB，最终触发 CUDA OOM。模型参数量较小并不能消除长上下文全注意力的平方增长。

本次重构将说话人检测和安全分块统一前置，Qwen3-ASR 与 MOSS 只负责短片段转录。这样既保留说话人结果，又把显存需求从录音总时长中解耦，并使两个后端可以在同一任务、进度和输出契约下运行。

## 3. 产品目标

### 3.1 核心目标

1. 所有本地转录任务均先执行完整 Pyannote diarization。
2. 使用 Pyannote 结果生成带 speaker、全局时间边界且有最大时长限制的 ASR 分块。
3. Qwen3-ASR-1.7B 成为默认 ASR 后端。
4. MOSS 保留为用户可选后端，并满足与 Qwen 相同的生产级可靠性要求。
5. 两个后端实现统一接口，复用任务控制、进度、错误、导出和重试能力。
6. Pyannote 与 ASR 模型不得同时长期占用 GPU；阶段切换时必须显式释放前一模型资源。
7. 长录音显存消耗由最大分块时长约束，不再随整段录音长度平方增长。

### 3.2 成功标准

- 目标机器能够完成至少 90 分钟的真实录音任务，不发生 CUDA OOM。
- Qwen 和 MOSS 均能处理相同的 Pyannote 分块清单，并输出相同结构的 transcript contract。
- 任意单个片段失败不会丢失已经完成的片段，并提供明确失败信息和可重试依据。
- 默认配置、设置页和新任务默认选择 Qwen3-ASR。
- MOSS 通过本文定义的生产验收门槛后才允许在正式 UI 中标记为“可用”。

## 4. 非目标

本迭代不包含：

- 说话人身份识别或真实姓名绑定。
- MOSS 内部 speaker 标签与 Pyannote speaker 标签融合。
- 多 GPU 并行或多个本地 ASR 模型并发常驻。
- 在线流式识别。
- 新增 MiMo 或其他 ASR 模型。
- 自动根据音频内容选择 Qwen/MOSS。
- 以重叠语音的多路文本替代 Pyannote exclusive diarization 时间轴。

## 5. 用户场景

### 5.1 默认转录

用户上传录音后直接运行。系统使用 Pyannote 分离说话人并安全分块，再用 Qwen3-ASR 转录，最终生成带时间戳和说话人标签的 Markdown。

### 5.2 选择 MOSS

用户在高级设置或任务配置中选择 MOSS。除 ASR 引擎外，音频标准化、Pyannote、分块、进度、任务控制和输出格式均与 Qwen 路径一致。

### 5.3 长录音

用户提交数十分钟或数小时录音。系统只保留完整的分块清单，不把整段音频送入 ASR 模型，因此单次推理的输入规模受最大分块时长约束。

### 5.4 局部失败

某个片段因 CUDA、解码或模型异常失败。系统记录片段 ID、时间范围、speaker、后端和错误码，继续策略按任务错误政策执行；最终结果明确标记缺失片段，并允许重试失败任务。

## 6. 产品行为

### 6.1 后端选项

正式名称：

- `Pyannote + Qwen3-ASR`：默认、推荐。
- `Pyannote + MOSS`：可选；通过生产门槛后显示为稳定选项。

旧名称 `MOSS-Diarize` 和 `Legacy Qwen + pyannote` 应停止用于新任务。历史任务仍按不可变快照展示原名称与配置，不回写历史数据。

### 6.2 默认值

- 新安装和未显式选择后端的现有安装默认使用 Qwen3-ASR。
- 已保存明确后端选择的用户保留选择，但旧 MOSS profile 必须迁移为新的 Pyannote + MOSS profile。
- 迁移失败时回退到 Qwen 默认，并在设置页显示可诊断提示，不允许静默改变已提交任务。

### 6.3 任务阶段

用户可见阶段至少包括：

1. 音频解码与标准化。
2. 加载 Pyannote。
3. 说话人分析。
4. 清理并生成安全分块。
5. 释放 Pyannote 显存。
6. 加载所选 ASR 模型。
7. 按片段转录，显示已完成数、总数和当前 speaker。
8. 合并、后处理和导出。

进度百分比只能表示已完成工作，不得在阻塞加载或推理期间伪造持续增长。长操作继续发送活动心跳和具名阶段。

## 7. 统一架构

```text
Source audio
  -> AudioNormalizer
  -> DiarizationProvider (Pyannote)
  -> SegmentPlanner
  -> release DiarizationProvider GPU resources
  -> SegmentTranscriber (Qwen or MOSS)
  -> TranscriptAssembler
  -> Postprocessor
  -> ArtifactExporter
```

### 7.1 模块职责

#### AudioNormalizer

- 将输入统一为 16 kHz PCM 内部表示。
- 保存单一任务级时间基准。
- 不决定 speaker 或 ASR 分块。

#### DiarizationProvider

- 输入完整标准化音频。
- 输出有序的 `(speaker, start_ms, end_ms)` 列表。
- 第一版唯一实现为 Pyannote community-1。
- 优先使用 `exclusive_speaker_diarization`，避免重叠片段破坏单一输出时间轴。

#### SegmentPlanner

- 校验、排序、裁剪 Pyannote 边界。
- 合并同 speaker 且间隔不超过 300ms 的片段。
- 处理过短片段，并将超长片段切成不超过 30 秒的 ASR 单元。
- 可为 ASR 输入增加最多 250ms 左右上下文 padding，但权威输出边界保持原分块范围。
- 生成稳定 `segment_id`，供进度、日志、重试和产物关联。

#### SegmentTranscriber

两个后端必须实现同一语义接口：

```python
class SegmentTranscriber(Protocol):
    backend_id: str

    def load(self, runtime_plan, model_snapshot, progress) -> None: ...
    def transcribe_batch(self, requests: list[SegmentRequest]) -> list[SegmentResult]: ...
    def close(self) -> None: ...
```

`SegmentRequest` 至少包含：

- `segment_id`
- 音频数组和 sample rate
- 权威 `start_ms`、`end_ms`、`speaker`
- 语言设置
- 编译后的背景、热词和额外指令

`SegmentResult` 至少包含：

- `segment_id`
- `text`
- 可选 detected language
- 可选后端内部相对时间戳
- 可选诊断元数据
- 结构化错误

后端不得覆盖 Pyannote speaker。MOSS 返回的内部 speaker 标签仅允许保留在诊断元数据中，不作为最终 transcript 的权威标签。

#### TranscriptAssembler

- 以 Pyannote 全局时间边界和 speaker 为权威信息。
- 将后端相对时间戳映射到全局时间轴；若后端无可靠时间戳，使用分块边界。
- 去除 padding 造成的相邻重复文本。
- 保证按全局时间稳定排序。

### 7.2 Profile 与模型身份

建议的新 profile 标识：

- `pyannote_qwen3_asr`
- `pyannote_moss_asr`

每个任务的不可变 model snapshot 必须同时记录：

- Pyannote 模型 ID、revision、路径和配置 digest。
- ASR 模型 ID、revision、路径和配置 digest。
- SegmentPlanner 版本和关键参数。
- 运行设备和 dtype。

旧 profile 只用于读取历史记录和一次性配置迁移，不继续作为新代码分支的领域名称。

## 8. 分块规则

第一版固定基线：

- 最短有效片段：800ms。
- 同 speaker 合并最大间隔：300ms。
- 最大 ASR 片段：30,000ms。
- ASR 输入 padding：默认前后各 200ms，受音频边界限制。
- Qwen 初始 batch size：1；显存基准通过后可配置为 2。
- MOSS batch size：1。

硬切 30 秒前应优先在附近静音或 Pyannote 子边界切分。找不到合适边界时才做固定时长切分。切分不得产生零长度片段。

过短片段处理顺序：

1. 优先合并到相邻同 speaker 片段。
2. 无同 speaker 邻居时保留，防止漏字。
3. 仅对确认无语音或无效边界的片段丢弃，并记录原因。

## 9. GPU 与模型生命周期

单任务按阶段串行使用 GPU：

1. 加载 Pyannote。
2. 完成整段 diarization 并把结果转为 CPU 数据结构。
3. 调用 provider `close()`，删除 pipeline 引用，执行必要的垃圾回收和 `torch.cuda.empty_cache()`。
4. 记录释放前后 `allocated`、`reserved` 和设备空闲显存。
5. 仅在释放完成后加载 Qwen 或 MOSS。
6. ASR 完成、取消或失败时均调用 transcriber `close()`。

不能只依赖 Python 局部变量离开作用域释放模型。清理必须位于 `finally` 路径，并能在取消、OOM 和非预期异常下执行。

同一 GPU 默认只允许一个本地推理阶段运行。任务并发仍可用于非 GPU 阶段或云端任务，但本地模型加载和推理必须经过 GPU lane 调度。

## 10. 后端要求

### 10.1 Qwen3-ASR

- 作为默认后端。
- 使用官方或项目已验证的 `qwen-asr` 接口。
- 支持自动语言识别和固定语言。
- 支持背景及热词上下文，遵守后端输入限制。
- 第一版单段推理；通过显存和质量测试后允许 batch size 2。

### 10.2 MOSS

- 不再执行整段长音频推理。
- 每次只处理 SegmentPlanner 生成的短音频。
- 使用与 Qwen 相同的 SegmentRequest/Result 适配层。
- 内部 diarization、speaker 和时间戳不得覆盖 Pyannote 权威字段。
- 必须处理无结构化 segment、纯文本结果、空结果和格式异常。
- 必须支持取消、超时诊断、OOM 分类、加载阶段心跳和逐段进度。
- 不能因为是可选项而降低测试、错误处理或产物完整性标准。

## 11. 错误处理与恢复

### 11.1 错误分类

至少提供稳定错误码：

- `AUDIO_DECODE_FAILED`
- `DIARIZATION_MODEL_UNAVAILABLE`
- `DIARIZATION_FAILED`
- `DIARIZATION_EMPTY_FALLBACK`
- `SEGMENT_PLAN_INVALID`
- `ASR_MODEL_UNAVAILABLE`
- `ASR_MODEL_LOAD_FAILED`
- `ASR_SEGMENT_FAILED`
- `CUDA_OUT_OF_MEMORY`
- `GPU_RESOURCE_RELEASE_FAILED`
- `TASK_CANCELLED`

### 11.2 片段失败策略

- 默认对同一片段进行一次降级重试：清理 CUDA cache 后以 batch size 1 重试。
- 再次失败时记录失败占位，继续后续片段；若模型进程或 CUDA 上下文已不可用，则终止任务。
- 最终产物必须列出缺失片段，不得把不完整结果伪装成完整成功。
- 任务状态区分 `completed`、`completed_with_warnings`、`failed` 和 `cancelled`。

### 11.3 中间状态

任务 staging 目录保存：

- Pyannote 原始结果。
- 规范化分块清单。
- 已完成 SegmentResult。
- 失败片段和错误信息。

中间文件按 attempt 隔离，重试不得覆盖已发布 revision。第一版可只支持整任务重试，但数据结构必须允许未来按失败片段恢复。

## 12. UI 与配置

### 12.1 工作流页面

- 默认选择 `Pyannote + Qwen3-ASR`。
- `Pyannote + MOSS` 作为同级可选项。
- 后端不可用时显示缺失模型或运行依赖，不允许提交必然失败的任务。
- MOSS 未通过生产 gate 的构建中标记为实验性或隐藏。

### 12.2 设置页

- 分别配置 Qwen、MOSS 和 Pyannote 模型路径。
- “默认本地 ASR”只选择 transcriber，不暗示是否启用 Pyannote；本版本本地 profile 固定使用 Pyannote。
- 显示三个模型的路径存在性和运行时依赖检查结果。

### 12.3 高级参数

最大分块时长、padding 和 batch size 第一版使用受控配置，不作为普通用户选项。开发模式可以展示，但必须校验安全范围。

## 13. 数据与兼容性迁移

迁移映射：

| 旧值 | 新值 |
| --- | --- |
| `qwen3_asr_with_pyannote` | `pyannote_qwen3_asr` |
| `moss_transcribe_diarize` | `pyannote_moss_asr` |
| `active_local_asr_model=qwen3_asr_1_7b` | 默认 profile 为 `pyannote_qwen3_asr` |
| `active_local_asr_model=moss_transcribe_diarize` | 保留明确选择并迁移到 `pyannote_moss_asr` |

要求：

- 历史任务快照保持原样可读。
- 新任务只能写入新 profile。
- 配置迁移幂等，可重复启动。
- 缺失或非法配置回退到 Qwen 默认并产生诊断日志。

## 14. 质量评估

### 14.1 目标

在不计算说话人正确率的前提下，比较两个后端的纯文本质量，并验证 Qwen 作为默认值的选择。

### 14.2 测试集

至少包含：

- 普通话单人清晰录音。
- 普通话多人会议。
- 中英混说。
- 粤语或项目常见方言。
- 远场、回声和噪声音频。
- 专有名词和热词密集内容。
- 10 分钟、30 分钟和 90 分钟长录音。

同一音频必须复用同一份 Pyannote 分块清单，避免切分差异污染后端比较。

### 14.3 指标

- 中文 CER。
- 英文及空格语言 WER。
- 数字、实体和热词准确率。
- 漏转、幻觉和重复文本次数。
- 实时率 RTF。
- 峰值 GPU allocated/reserved memory。
- 任务成功率与单段失败率。

speaker 标签从评分文本中移除；标点和大小写采用统一归一化规则。MOSS 与 Qwen 使用相同音频、分块、语言设置和上下文。

### 14.4 默认后端判断

Qwen 保持默认，除非真实测试集证明 MOSS 在主要业务语料上同时满足：

- 核心 CER/WER 有稳定且实质性的改善。
- 90 分钟任务稳定性不低于 Qwen。
- 峰值显存不超过目标机器安全阈值。
- 性能和依赖复杂度没有抵消质量收益。

MOSS 即使不成为默认，也必须满足生产 gate 才能作为正式可选项。

## 15. 生产验收门槛

Qwen 与 MOSS 分别验收，不允许用一个后端的结果替代另一个。

### 15.1 功能

- 相同分块清单产生合法统一 transcript contract。
- pause、resume、cancel、retry 和应用退出清理有效。
- 历史、总结、Markdown 和 JSON 产物兼容。
- 中文路径、空格路径和常见音视频格式可用。

### 15.2 稳定性

- 10、30、90 分钟真实音频各连续运行至少 3 次。
- 无 CUDA OOM、无僵尸 Python 进程、无长期显存泄漏。
- 连续完成 5 个任务后，空闲显存与首次任务前的差异处于可解释范围。
- 单段异常、模型返回空文本和用户取消均能正确收尾。

### 15.3 性能与资源

- RTX 5060 Ti 16GB 上峰值显存保留至少 1.5 GiB 安全余量。
- UI 心跳在模型加载和逐段推理期间持续可见。
- 90 分钟任务的内存占用不随已处理片段数持续线性增长。

### 15.4 质量

- Qwen 不低于现有 Legacy Qwen + Pyannote 基线。
- MOSS 在项目测试集上不得出现系统性漏转、跨片段重复或格式污染。
- 两个后端均完成盲听抽检和 CER/WER 报告。

## 16. 测试策略

### 16.1 单元测试

- Pyannote 边界排序、裁剪、合并、过短片段和 30 秒切分。
- padding 与权威边界分离。
- 后端结果映射和重复文本消除。
- profile 迁移和历史兼容。
- 错误码、重试决策和最终状态。

### 16.2 契约测试

- Qwen/MOSS adapter 使用相同 fixture。
- 两个 adapter 均覆盖正常文本、空结果、结构异常和后端异常。
- model snapshot 必须同时包含 diarization 与 transcriber。

### 16.3 集成测试

- Fake Pyannote + fake ASR 验证完整状态机。
- 真实 Pyannote + 短音频。
- 真实 Qwen 与真实 MOSS 分别完成 GPU smoke。
- Electron 提交、进度事件、取消、历史和产物读取。

### 16.4 硬件验收

- 使用目标 16GB GPU 运行真实 10/30/90 分钟样本。
- 每个阶段采集 GPU allocated、reserved、device free 和进程显存。
- 任务结束和取消后验证资源回落。

## 17. 实施阶段

### Phase 1：统一领域模型与分块器

- 新增 profile、SegmentRequest/Result 和 SegmentTranscriber 接口。
- 从现有 `job_runner.py` 提取 Pyannote provider 与 SegmentPlanner。
- 保持现有 Qwen 输出行为，建立回归基线。

退出条件：Qwen 通过现有测试及新增分块测试，输出无回归。

### Phase 2：显存生命周期

- 实现 provider/transcriber 显式 `close()`。
- 增加 GPU lane、阶段资源日志和 finally 清理。
- 验证 Pyannote 卸载后再加载 Qwen。

退出条件：连续任务无显存阶梯式增长。

### Phase 3：MOSS 适配

- 将 MOSS 从整段 adapter 改为 SegmentTranscriber。
- 忽略内部 speaker 权威性，映射纯文本及可选相对时间戳。
- 完成空结果、格式错误、OOM 和取消处理。

退出条件：MOSS 与 Qwen 通过相同契约和真实短音频 smoke。

### Phase 4：Electron 与配置迁移

- 更新类型、设置页、工作流选择、默认值和迁移逻辑。
- 保持历史 profile 可读。
- 更新阶段文案与诊断信息。

退出条件：开发模式完整提交两种后端任务，历史和产物正常。

### Phase 5：生产验收与默认发布

- 执行质量 A/B、90 分钟稳定性和连续任务显存测试。
- Qwen 默认发布。
- MOSS 仅在独立生产 gate 通过后取消实验标记。

## 18. 低迁移成本原则

- 复用现有 `job_runner.py` 中已经验证的 Pyannote、片段规范化、逐段转录和 exporter 行为。
- 先提取接口再替换实现，避免一次性重写 supervisor。
- transcript contract、历史产物路径和 Electron IPC 尽量保持稳定。
- 首先让 Qwen 走新接口并保持基线，再接入 MOSS，避免同时调试两个变量。
- 不删除旧 profile 解析逻辑，直到历史兼容测试完成。

## 19. 风险与缓解

| 风险 | 影响 | 缓解措施 |
| --- | --- | --- |
| Pyannote 切分过碎 | 上下文不足、识别质量下降 | 同 speaker 合并、短片段保留、padding、真实语料调参 |
| 30 秒硬切截断词语 | 边界漏字或重复 | 优先静音切点、前后 padding、相邻去重 |
| Pyannote 显存未释放 | ASR 加载 OOM | 显式 close、finally 清理、GPU 指标和连续任务测试 |
| MOSS 短片段表现弱于长音频 | 可选后端质量不足 | 独立 A/B 与生产 gate，不以可运行代替可发布 |
| 两后端依赖冲突 | Worker 启动或加载失败 | 延迟导入；必要时后续拆分运行环境，但不作为第一版前提 |
| Profile 改名破坏历史 | 历史任务无法打开 | 新旧双读、新写新值、迁移幂等 |
| 单段失败导致不完整产物 | 用户误判结果完整 | `completed_with_warnings`、失败占位和结构化诊断 |

## 20. 开发完成定义

只有同时满足以下条件，需求才算完成：

1. Qwen 默认后端在新统一接口中通过功能、质量和 90 分钟硬件验收。
2. MOSS 在相同接口中通过独立生产验收，不依赖整段推理。
3. Pyannote 到 ASR 的阶段切换具备可验证的显存释放证据。
4. Electron 默认值、选项、配置迁移、历史兼容和任务状态全部完成。
5. 自动测试、真实 GPU smoke、质量 A/B 和资源报告齐备。
6. 开发、调试和正式启动文档更新，用户无需重新构建 Electron 即可在热调试模式验证 Python 与前端改动。

## 21. 后续开发文档

本 PRD 批准后，下一份文档应是逐文件实施计划，至少列出：

- Python 模块提取与接口变更顺序。
- contract/schema 与 Electron TypeScript 类型迁移。
- 测试先后顺序及真实硬件验证脚本。
- 旧 profile 兼容的删除门槛。
- MOSS 生产 gate 证据模板。
