# Pyannote 前置分块与双 ASR 后端逐文件实施计划（已取代）

> Superseded by `docs/superpowers/specs/2026-07-12-qwen-only-single-runtime-implementation-plan.md`.
> This document describes the removed MOSS dual-backend migration.

## 1. 计划信息

- 日期：2026-07-12
- 对应 PRD：`docs/superpowers/specs/2026-07-12-pyannote-chunked-dual-asr-prd.md`
- 实施原则：测试先行、小步迁移、Qwen 先回归、MOSS 后接入、最后切默认值
- 本计划范围：Python Worker、Workflow v2 contract、Electron UI/配置、运行时验证和文档
- 本计划不直接实施业务代码

## 2. 目标架构

```text
WorkflowSupervisor
  -> ProfileRoutingTranscriber
  -> ChunkedLocalTranscriber
       -> AudioNormalizer
       -> PyannoteDiarizationProvider
       -> SegmentPlanner
       -> release Pyannote GPU resources
       -> QwenSegmentTranscriber | MossSegmentTranscriber
       -> TranscriptAssembler
       -> artifact text
```

新任务 profile：

- `pyannote_qwen3_asr`：默认。
- `pyannote_moss_asr`：正式可选，发布前需独立通过生产 gate。
- `cloud_asr`：保持不变。

旧 profile `qwen3_asr_with_pyannote`、`moss_transcribe_diarize` 仅用于历史读取与配置迁移，不接受新任务写入。

## 3. 实施顺序总览

| 阶段 | 目标 | 退出条件 |
| --- | --- | --- |
| 0 | 固化基线与 fixture | 现有 Python/Electron 测试结果已记录 |
| 1 | 建立分块领域模型和纯函数 | 新单元测试通过，不触碰真实模型 |
| 2 | 提取 Pyannote provider 与 GPU 生命周期 | Qwen 新路径能先释放 Pyannote 再加载 ASR |
| 3 | Qwen 接入统一接口 | 结果不低于现有 Legacy 基线 |
| 4 | MOSS 接入统一接口 | 不再整段推理，通过相同 adapter contract |
| 5 | Workflow contract/profile 迁移 | 新写新 profile，历史旧 profile 可读 |
| 6 | Electron 默认值与 UI | Qwen 默认、MOSS 同级可选、状态文案正确 |
| 7 | 真实 GPU 和长录音验收 | 两后端分别通过生产 gate |
| 8 | 清理旧入口与更新文档 | 无新调用依赖旧整段/Legacy adapter |

## 4. Phase 0：基线与测试资产

### 4.1 读取但暂不修改

- `apps/worker-python/app/pipeline/job_runner.py`
  - 记录现有 `MIN_SEGMENT_MS=800`、`MERGE_GAP_MS=300`、`MAX_SEGMENT_MS=30000`、batch size 和 exporter 行为。
- `apps/worker-python/app/models/manager.py`
  - 记录 Qwen、MOSS、Pyannote 的加载方式、缓存引用和设备选择。
- `apps/worker-python/app/pipeline/moss_v2.py`
  - 固化当前整段 MOSS adapter 的解析兼容 fixture，后续只保留可复用解析函数。
- `apps/worker-python/app/pipeline/legacy_v2.py`
  - 固化 Qwen + Pyannote 输出作为回归基线。
- `apps/worker-python/app/workflow/supervisor.py`
  - 记录 progress callback、attempt staging 和 artifact promotion 边界。

### 4.2 新增验证资产

- 新增 `apps/worker-python/tests/fixtures/segmentation/`。
  - 保存不含模型权重的 Pyannote 边界 JSON fixture：连续同 speaker、短片段、重叠、长片段、空结果、多 speaker。
- 新增 `apps/worker-python/tests/fixtures/transcription/`。
  - 保存 Qwen/MOSS adapter 的纯文本、结构化时间戳、空结果和格式异常 fixture。
- 新增 `docs/benchmarks/pyannote-dual-asr-baseline.md`。
  - 记录基线机器、依赖版本、现有测试结果和后续真实录音样本清单；不提交录音和模型。

### 4.3 命令

```powershell
apps\desktop-electron\runtime\python\python.exe -m unittest discover -s apps\worker-python\tests -p "test_*.py"
cd apps\desktop-electron
npm run typecheck
npm test
```

## 5. Phase 1：分块领域模型与纯函数

### 5.1 新增 `apps/worker-python/app/pipeline/segment_types.py`

定义稳定、与具体模型无关的数据结构：

- `DiarizationTurn`
- `PlannedSegment`
- `SegmentRequest`
- `SegmentResult`
- `SegmentFailure`
- `ChunkedTranscriptResult`

要求：

- 时间统一使用整数毫秒。
- `PlannedSegment` 同时保存权威边界和带 padding 的输入边界。
- speaker 只能来自 diarization provider。
- 所有类型可序列化，用于 staging 和诊断。

### 5.2 新增 `apps/worker-python/app/pipeline/segment_planner.py`

从 `job_runner.py` 提取并增强：

- 边界排序、裁剪和无效段过滤。
- 同 speaker、间隔不超过 300ms 的合并。
- 800ms 以下片段的邻接合并策略。
- 最大 30 秒切分。
- 前后默认 200ms padding。
- 稳定 segment ID。
- 预留静音优先切点接口；第一提交可使用固定切点，随后增加静音切点实现。

该模块不得 import torch、Pyannote、Qwen 或 MOSS。

### 5.3 新增 `apps/worker-python/tests/contract_v2/test_segment_planner.py`

测试：

- 乱序、越界、零长度。
- 相邻同 speaker 合并。
- 不同 speaker 不合并。
- 30 秒整除与尾段。
- padding 不改变权威边界。
- 过短片段不静默丢失。
- 输出 ID 和排序确定性。

### 5.4 修改 `apps/worker-python/app/schemas.py`

- 暂时保留旧 `SpeakerSegment`/`TranscriptSegment`，避免一次性破坏 v1 exporter。
- 增加从新类型到旧 exporter 类型的显式转换函数，转换点只能存在于 assembler/exporter 边界。
- 不在此阶段删除旧结构。

### 5.5 阶段验证

```powershell
apps\desktop-electron\runtime\python\python.exe -m unittest apps.worker-python.tests.contract_v2.test_segment_planner
```

如果模块路径不适合带连字符目录，则从 `apps/worker-python` 工作目录运行 `python -m unittest tests.contract_v2.test_segment_planner`。

## 6. Phase 2：Provider、统一接口和 GPU 生命周期

### 6.1 新增 `apps/worker-python/app/pipeline/interfaces.py`

定义 Protocol：

- `DiarizationProvider.load/run/close`
- `SegmentTranscriber.load/transcribe_batch/close`
- `GpuLane.acquire/release`

接口不暴露具体模型类。`close()` 必须幂等。

### 6.2 新增 `apps/worker-python/app/pipeline/pyannote_provider.py`

从 `job_runner.build_speaker_segments` 提取：

- 加载 snapshot 中 role=`diarization` 的模型。
- 调用 `exclusive_speaker_diarization`，缺失时使用兼容结果。
- 输出 `DiarizationTurn`。
- 空结果使用单 speaker fallback，并产生 warning code。
- `close()` 将 pipeline 移至 CPU（若安全）、删除强引用、触发 GC 和 CUDA cache 清理。
- 记录清理前后 allocated/reserved/free VRAM。

Pyannote provider 不得加载 ASR 模型。

### 6.3 新增 `apps/worker-python/app/runtime/gpu_lifecycle.py`

集中实现：

- GPU 指标快照。
- 幂等资源关闭 helper。
- `gc.collect()` 和 `torch.cuda.empty_cache()`。
- 可选 `torch.cuda.synchronize()`，仅在测量或安全收尾时使用。
- 清理失败日志，不吞掉原始业务异常。

### 6.4 新增 `apps/worker-python/app/runtime/gpu_lane.py`

- 在单 Worker 进程内提供容量 1 的本地 GPU lane。
- diarization 和 local ASR 均通过同一 lane。
- cloud、summary 和 CPU-only 工作不占用 GPU lane。
- 取消等待 lane 时能正确退出。

### 6.5 修改 `apps/worker-python/app/workflow/runtime_plan.py`

- 增加 diarization/ASR 串行阶段的显存预算字段。
- 将 Qwen、MOSS 的估算显存从调用点传入，不继续使用单一 2048MB 假设。
- 保持 `asr_inference_capacity=1`。
- CUDA 强制模式在安全余量不足时给出结构化警告或拒绝。

### 6.6 修改 `apps/worker-python/app/models/manager.py`

- 拆分“模型定位/构造”和“模型常驻缓存”。
- 禁止全局 `_MODEL_MANAGER` 同时持有 Pyannote 与 ASR 的 GPU 引用。
- 给 Qwen、MOSS、Pyannote 增加独立显式 unload 方法。
- 保留现有解析 helpers 和模型包装，避免重写已验证的官方调用。

### 6.7 新增测试

- `apps/worker-python/tests/contract_v2/test_gpu_lifecycle.py`
  - 使用 fake torch 验证 close 次序、幂等和异常路径。
- `apps/worker-python/tests/contract_v2/test_pyannote_provider.py`
  - fake annotation 覆盖 exclusive、兼容、空结果和异常。
- `apps/worker-python/tests/contract_v2/test_gpu_lane.py`
  - 验证容量 1、取消等待和释放。

## 7. Phase 3：Qwen 统一后端

### 7.1 新增 `apps/worker-python/app/pipeline/qwen_segment_transcriber.py`

- 实现 `SegmentTranscriber`。
- 从 model snapshot role=`transcriber` 加载 Qwen3-ASR-1.7B。
- 将 SegmentRequest 映射为现有 `qwen-asr` 调用。
- 初始 batch size 固定为 1；配置允许的 batch 2 在硬件 gate 后启用。
- 返回纯文本、检测语言和诊断元数据。
- 不生成或修改 speaker。
- OOM 转换为稳定错误码，清理后单段重试一次。

### 7.2 新增 `apps/worker-python/app/pipeline/transcript_assembler.py`

- 将 SegmentResult 映射到 Pyannote 权威 speaker 和全局时间轴。
- 后端相对时间戳必须裁剪在权威分块边界内。
- 实现 padding 边界的轻量相邻重复文本去除。
- 转换为现有 `TranscriptSegment` 或统一 Markdown 输入。
- 生成失败片段占位和 warning 摘要。

### 7.3 新增 `apps/worker-python/app/pipeline/chunked_local.py`

作为两个本地 profile 的共享 orchestration：

1. 标准化音频。
2. 获取 GPU lane 并运行 Pyannote。
3. 保存 diarization 和 segment plan staging JSON。
4. 在 `finally` 关闭 Pyannote并释放 lane。
5. 重新获取 lane，加载所选 transcriber。
6. 逐段推理、写入中间 SegmentResult、发送真实进度。
7. 在 `finally` 关闭 transcriber。
8. assembler、后处理和 artifact 输出。

暂停、取消检查应在每个模型加载前、每个 segment 前后及重试前执行。

### 7.4 修改 `apps/worker-python/app/pipeline/job_runner.py`

- 复用音频、control、export 和后处理 helper。
- 将 diarization、segment planning、local transcription 逻辑迁到新模块。
- 暂时保留兼容入口供旧历史/测试使用，但禁止新 profile 进入旧路径。
- 删除 `_MODEL_MANAGER` 对新路径的全局模型所有权。

### 7.5 修改 `apps/worker-python/app/pipeline/legacy_v2.py`

- 第一提交将其变为旧 profile 兼容适配器。
- 新 `pyannote_qwen3_asr` 直接路由到 `ChunkedLocalTranscriber(QwenSegmentTranscriber)`。
- 迁移稳定后删除内部 `run_job` 重复 orchestration，只保留旧 profile 到新实现的只读映射。

### 7.6 新增/修改测试

- 新增 `test_qwen_segment_transcriber.py`。
- 新增 `test_transcript_assembler.py`。
- 新增 `test_chunked_local_pipeline.py`。
- 修改 `test_pipeline_phase3_guards.py`：新本地 profile 均必须 split long，MOSS 不再拥有整段豁免。

退出条件：Qwen fake/真实短音频、取消、片段错误、artifact 回归全部通过。

## 8. Phase 4：MOSS 生产级统一后端

### 8.1 重构 `apps/worker-python/app/pipeline/moss_v2.py`

- `MossTranscriber` 改为 `MossSegmentTranscriber`，实现统一接口。
- 删除整段 `_normalize_for_moss` orchestration，由共享 pipeline 提供短音频。
- 保留官方 processor/model 调用和 `_parse_moss_segments` 兼容能力。
- MOSS 内部 speaker 只进入 diagnostics；最终 speaker 始终使用 SegmentRequest speaker。
- 处理结构化 segment、纯文本 fallback、空输出、格式异常和相对时间戳越界。
- batch size 固定 1。
- 每段记录输入时长、token 数、推理耗时和峰值显存。

### 8.2 修改 `apps/worker-python/app/models/manager.py`

- 将 `MossTranscribeDiarizeAdapter` 调整为短片段模型包装器。
- 允许模型只加载一次后处理多个 segment，但任务结束必须 close。
- 删除“整段输入才能保持 speaker 一致性”的代码假设和注释。

### 8.3 修改 `apps/worker-python/tests/contract_v2/test_moss_v2_adapter.py`

- 改用 SegmentRequest fixture。
- 验证 MOSS speaker 不覆盖 Pyannote。
- 验证相对时间映射、纯文本 fallback、空输出、格式异常、OOM 和 close。
- 删除“full audio stream”作为正确行为的断言。

### 8.4 新增真实 smoke

- 新增 `apps/worker-python/scripts/smoke_chunked_moss.py`。
- 输入短音频和预制 segment plan，不执行长上下文。
- 输出每段耗时/token/显存及最终 Markdown。
- 默认不下载模型，只接受本地配置路径。

退出条件：MOSS 通过与 Qwen 相同的 adapter contract、任务控制和 artifact 测试。

## 9. Phase 5：Workflow v2 contract 与迁移

### 9.1 修改 contract schema

- `contracts/workflow-v2/schemas/transcription-draft.schema.json`
  - 新任务 enum 改为 `pyannote_qwen3_asr`、`pyannote_moss_asr`、`cloud_asr`。
- `contracts/workflow-v2/schemas/workflow-snapshot.schema.json`
  - 若 profile 有枚举，同步支持历史旧值读取和新值写入。
- `contracts/workflow-v2/schemas/error.schema.json`
  - details 允许 segment ID、时间范围、backend 和 GPU snapshot。

### 9.2 修改 fixtures

- `contracts/workflow-v2/fixtures/workflow-submit.request.json`
- `contracts/workflow-v2/fixtures/prompt-preview.request.json`
- `contracts/workflow-v2/fixtures/prompt-preview.response.json`
- 所有含旧本地 profile 的 fixture

默认 fixture 使用 `pyannote_qwen3_asr`；另增 MOSS fixture，不能只替换字符串而缺少 diarization snapshot。

### 9.3 修改 `apps/worker-python/app/ipc/v2/codec.py`

- 新任务仅接受新 profile 与 cloud。
- 历史 snapshot decode 继续接受旧 profile。
- 将 draft 校验和历史读取校验分开，避免旧值继续写入。

### 9.4 修改 `apps/worker-python/app/workflow/model_snapshot.py`

- 两个新本地 profile都返回 `diarization` + `transcriber` 两个 component。
- 增加 planner component/version 或在 snapshot metadata 中记录 planner 参数 digest。
- 旧 MOSS profile 解析仅用于历史，不再生成只有 transcriber 的新 snapshot。

### 9.5 修改 `apps/worker-python/app/pipeline/router.py`

- 新 Qwen/MOSS profile 都进入共享 `ChunkedLocalTranscriber`。
- cloud 保持独立。
- 旧 profile 只在明确兼容模式中映射，记录 migration warning。

### 9.6 修改 `apps/worker-python/app/workflow/supervisor.py`

- prompt compiler 根据 transcriber 后端选择，不再根据是否包含 Pyannote选择。
- model snapshot 包含两个本地模型与 planner 版本。
- 最终状态支持 `completed_with_warnings`；若状态机改动过大，第一版可保持 completed 并在 snapshot warnings 明确标记，但发布前必须完成正式状态。
- staging 保存 segment plan、逐段结果和失败清单。

### 9.7 修改 `apps/worker-python/app/workflow/state_machine.py`

- 增加 `completed_with_warnings` 终态及允许转换。
- 明确 retry、clear 和历史过滤行为。

### 9.8 修改 `apps/worker-python/app/supervisor/server.py`

- production supervisor 注入共享 chunked pipeline 和两个 backend factory。
- capabilities 返回新 profile，Qwen 排在默认首位。
- `auto` production readiness 必须检查 Qwen + Pyannote 默认链路，而非只检查 MOSS。
- 预加载依赖不得在主线程实例化或常驻三个 GPU 模型。
- 更新生产启动错误文案，不再写“MOSS production runtime”。

### 9.9 测试文件

- 修改 `test_codec.py`。
- 修改 `test_model_snapshot.py`。
- 修改 `test_pipeline_mode.py`。
- 修改 `test_supervisor.py`。
- 修改 `test_state_machine.py`。
- 修改 `test_server_startup.py`。
- 修改 `test_registry_migration.py`。

测试必须覆盖：新写新值、旧历史可读、MOSS snapshot 包含 Pyannote、默认 capabilities 为 Qwen profile。

## 10. Phase 6：Electron 与配置迁移

### 10.1 修改 `config/models.toml`

- `active_local_asr_model` 默认改为 `qwen3_asr_1_7b`。
- 保留三个模型路径。
- 不把 profile 和具体模型路径混为同一个配置字段；后续可引入 `default_local_pipeline_profile`，缺失时由 active model 推导一次。

### 10.2 修改 `apps/desktop-electron/src/ipc/workerTypes.ts`

- 保留 `LocalAsrModelKey` 表示权重选择。
- 新增严格 `PipelineProfile` union：两个新本地 profile + cloud。
- 不再用任意 string 传递 pipeline profile。
- 增加 segment warning 和 `completed_with_warnings` 类型。

### 10.3 修改 `apps/desktop-electron/src/workflows/types.ts`

- 使用统一 `PipelineProfile`。
- snapshot/timeline 支持新终态和阶段诊断字段。

### 10.4 修改 `apps/desktop-electron/electron/hostServices.ts`

- 默认 active model 改为 Qwen。
- 配置加载执行幂等迁移：明确 MOSS 选择保留，否则默认 Qwen。
- 不改写历史 workflow registry。
- 非法值回退时写 session 日志和可展示 warning。

### 10.5 修改 `apps/desktop-electron/src/ipc/desktopClient.ts`

- fake/default model config 改为 Qwen。
- profile 和 model key 分离传递。

### 10.6 修改 `apps/desktop-electron/src/features/workflow/WorkflowView.vue`

- 默认 `pyannote_qwen3_asr`。
- 选项名称改为“Pyannote + Qwen3-ASR（推荐）”和“Pyannote + MOSS”。
- 两个选项都提示需要 Pyannote。
- MOSS 只有 capabilities 和 readiness 都通过时可提交；未通过时显示原因。
- 阶段文案统一为 diarization、显存释放、模型加载、片段进度。
- MOSS 不再显示 split-stereo 或整段 diarization 专属承诺；如 split-stereo 仍保留，必须在共享分块前定义双通道与 speaker 的明确语义，否则暂时隐藏。

### 10.7 修改 `apps/desktop-electron/src/features/settings/SettingsView.vue`

- 默认本地 ASR 选择 Qwen。
- 路径配置仍展示 Qwen、MOSS、Pyannote。
- readiness 分别显示依赖与模型状态。
- 文案说明两种本地方案均先执行 Pyannote。

### 10.8 修改 stores/adapters

- `apps/desktop-electron/src/stores/appStore.ts`
  - 保存默认 ASR 和迁移 warning。
- `apps/desktop-electron/src/stores/workflowStore.ts`
  - 支持 warning 终态与逐段诊断。
- `apps/desktop-electron/src/workflows/adapters/fakeWorkflowRuntime.ts`
  - capabilities 使用新 profile，默认 Qwen。
- `apps/desktop-electron/src/workflows/adapters/fakeWorkflowRuntime.spec.ts`
  - 更新 fixture 与默认断言。
- `apps/desktop-electron/src/workflows/reducer.spec.ts`
  - 增加 completed_with_warnings 和片段失败事件覆盖。

### 10.9 UI 验证

```powershell
cd apps\desktop-electron
npm run typecheck
npm test
npm run build
npm run electron:debug
```

视觉检查：默认选项、缺失模型提示、MOSS 可用状态、长任务逐段进度、warning 终态和历史展示。

## 11. Phase 7：生产验证脚本与报告

### 11.1 新增 `apps/worker-python/scripts/benchmark_chunked_dual_asr.py`

- 读取本地 manifest，不扫描 `outputs` 或 `models`。
- 一次 Pyannote 分块结果可锁定并被两个后端复用。
- 采集 CER/WER、热词、重复、漏转、RTF、segment failures 和 GPU 峰值。
- 支持仅 Qwen、仅 MOSS 或 A/B。
- 不自动下载权重。

### 11.2 新增 `apps/worker-python/scripts/probe_gpu_lifecycle.py`

- 记录启动、Pyannote load/run/close、ASR load/run/close 后的 allocated/reserved/free VRAM。
- 连续执行 5 个任务，输出是否存在阶梯式泄漏。

### 11.3 新增 `docs/benchmarks/pyannote-dual-asr-production-gate.md`

分别记录 Qwen 和 MOSS：

- 环境与 revision。
- 10/30/90 分钟各 3 次结果。
- 质量指标。
- 峰值显存和任务结束回落。
- 已知问题和是否通过 gate。

MOSS 未通过时保持实验性或隐藏，但不得阻止 Qwen 默认链路发布。

## 12. Phase 8：清理与文档

### 12.1 条件式删除/收缩

- `apps/worker-python/app/pipeline/legacy_v2.py`
  - 仅在旧 profile 已由 router 兼容映射且测试通过后收缩。
- `apps/worker-python/app/pipeline/moss_v2.py`
  - 删除整段 normalizer 和整段 adapter 入口。
- `apps/worker-python/app/pipeline/job_runner.py`
  - 删除已迁出的本地模型 orchestration，保留共享 control/export helpers 或进一步拆分。

任何删除都必须先通过 `rg` 确认无新路径引用。不得删除模型权重或用户产物。

### 12.2 更新文档

- `apps/worker-python/README.md`
  - 描述统一 Pyannote 前置和双后端启动要求。
- `apps/desktop-electron/README.md`
  - 更新热调试和模型 readiness。
- `docs/worker-contract-v2.md`
  - 更新 profile、snapshot、阶段、warning 终态和错误 details。
- `docs/Workflow_Runtime_V2_Implementation_Plan.md`
  - 标注旧“MOSS 整段且不走 30 秒切片”决策已被本 PRD 取代。
- `CONTEXT.md`
  - 更新领域语言，删除“避免 MOSS + pyannote”的旧约束，明确 speaker 权威来源为 Pyannote。
- 根目录启动/开发脚本说明
  - 保持 Electron 热调试无需重复打包的流程。

## 13. 提交拆分建议

每个提交必须可测试、可回滚，不混入无关改动：

1. `test: add dual ASR segmentation fixtures`
2. `refactor: extract deterministic segment planner`
3. `feat: add explicit GPU lifecycle and lane`
4. `refactor: adapt Qwen to chunked transcriber interface`
5. `refactor: adapt MOSS to chunked transcriber interface`
6. `feat: migrate workflow profiles to chunked local ASR`
7. `feat: make Qwen the default Electron ASR backend`
8. `test: add dual backend production probes`
9. `docs: update chunked dual ASR runtime guidance`

不得在计划实施期间提交 `.mimocode/`、模型、录音、`outputs/` 或用户现有未跟踪 PRD。

## 14. 每阶段通用验证

### Python 快速测试

```powershell
cd apps\worker-python
..\desktop-electron\runtime\python\python.exe -m unittest discover -s tests\contract_v2 -p "test_*.py"
```

若 bundled runtime 相对路径不成立，使用 `E:\claude-projects\asr-local\apps\desktop-electron\runtime\python\python.exe`。

### Electron

```powershell
cd apps\desktop-electron
npm run typecheck
npm test
npm run build
```

### 热调试

```powershell
cd apps\desktop-electron
npm run electron:debug
```

Python Worker 与 renderer 的普通修改优先使用热调试验证；只有 installer、extraResources 或正式发布验证才运行 `npm run electron:package`/`electron:dist`。

### 最终发布验证

```powershell
cd apps\desktop-electron
npm run runtime:build
npm run electron:build
npm run electron:package:fast
```

正式安装包验收再运行 `npm run electron:dist`。

## 15. 停止条件与回退策略

遇到以下情况停止切默认值，但保留已通过的基础重构：

- Qwen 新路径的 CER/WER 或漏转明显劣于 Legacy 基线。
- Pyannote close 后显存仍不足以加载 Qwen。
- 新旧 profile 迁移破坏历史读取。
- 90 分钟 Qwen 路径出现 OOM 或持续内存增长。

MOSS 单独失败时：

- 不回退到整段 MOSS。
- 保持 Qwen 默认正常发布。
- 将 MOSS 标记实验性或隐藏，保留 adapter 和诊断证据继续修复。

## 16. 最终完成清单

- [ ] 新任务只写新 profile。
- [ ] Qwen 是配置、capabilities、fake runtime 和 UI 的一致默认值。
- [ ] 两个本地 profile 的 snapshot 均含 Pyannote 与 ASR component。
- [ ] 两个后端使用同一 SegmentRequest/Result contract。
- [ ] MOSS 不再接收整段长音频。
- [ ] Pyannote speaker 是最终 transcript 的唯一权威 speaker。
- [ ] Pyannote 与 ASR 之间存在可测量的显式 GPU 清理。
- [ ] pause/resume/cancel/retry 和失败收尾通过。
- [ ] Qwen 通过 90 分钟生产 gate。
- [ ] MOSS 独立通过生产 gate 后才作为稳定选项发布。
- [ ] 历史旧 profile 可读，配置迁移幂等。
- [ ] Python、Electron、真实 GPU、A/B 质量报告齐备。
