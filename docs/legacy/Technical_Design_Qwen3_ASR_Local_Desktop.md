# 技术设计文档：本地 ASR 语音转文字桌面应用

## 1. 文档信息

- 文档名称：技术设计文档（Technical Design）
- 对应 PRD：`PRD_Qwen3_ASR_Local_Desktop.md`
- 文档版本：v1.0
- 编写日期：2026-04-17
- 目标阶段：MVP 到工程化阶段

## 2. 设计目标

本设计文档用于把 PRD 中已经确定的产品方案，拆解为可实施的系统设计。当前版本的设计目标如下：

1. 在 Windows 本地环境中，基于 `RTX 5060 Ti 16GB` 跑通 `pyannote + Qwen3-ASR-1.7B` 的组合式识别链路。
2. 以 Rust 原生 GUI 作为主应用，提供文件选择、任务配置、进度展示、结果预览和导出能力。
3. 以 Python 推理工作进程承载音频处理、speaker diarization、逐段转写、术语归一化和 Markdown 生成。
4. 保证系统以“单机、单用户、单任务优先”的策略稳定运行，避免过早引入复杂的服务化架构。

## 3. 非目标

当前技术设计不覆盖以下内容：

1. 实时麦克风流式转写。
2. 云端推理、多用户并发和远程服务化部署。
3. `WSL`、`vLLM`、Linux 多后端兼容。
4. 说话人真实身份识别。
5. 内建富文本编辑器。

## 4. 关键设计决策

### 4.1 架构决策

采用“Rust GUI + Python Worker”双进程架构。

原因：

1. `Qwen3-ASR` 和 `pyannote` 的生态主要集中在 Python。
2. Rust 负责 GUI、状态管理和本地集成更合适。
3. 双进程可将模型异常、显存异常、依赖问题与 GUI 进程隔离。

### 4.2 IPC 决策

MVP 采用：`Rust 启动 Python 子进程 + JSON Lines over stdio`

原因：

1. 不依赖本地端口，避免端口占用和防火墙弹窗。
2. 适合单桌面应用与单工作进程配对的模型。
3. 消息结构清晰，便于做进度事件流。
4. 后续如需调试接口，可在不改核心任务模型的情况下扩展为本地 HTTP。

### 4.3 推理决策

采用固定链路：

1. `ffmpeg` 负责解码和重采样。
2. `pyannote.audio` 负责 speaker diarization。
3. 对 diarization 片段做规范化与必要的二次切分。
4. `Qwen3-ASR-1.7B` 负责逐段转写。
5. Python Worker 负责合并 transcript、执行术语归一化并生成 Markdown。

### 4.4 时间戳决策

MVP 中的段级时间戳以 `pyannote` 的片段时间边界为准。

原因：

1. 当前主需求是可读、带 speaker 的 transcript。
2. 使用 diarization 边界即可满足段级时间戳。
3. 这样可以减少额外时间戳模型带来的链路复杂度。

## 5. 系统总览

```text
Rust Desktop App (Slint)
    |
    |-- File Picker
    |-- Task Form
    |-- Job State Store
    |-- Progress View
    |-- Transcript Preview
    |-- Export Actions
    |
    |-- spawn python worker
            |
            |-- JSON Lines RPC / Event Stream
            v
      Python Worker
            |
            |-- config loader
            |-- model manager
            |-- ffmpeg audio decode
            |-- pyannote diarization
            |-- segment normalizer
            |-- Qwen3-ASR transcription
            |-- transcript merger
            |-- terminology post-process
            |-- markdown exporter
```

## 6. 推荐仓库结构

建议在后续实现中采用如下目录结构：

```text
asr-local/
├─ apps/
│  ├─ desktop-rust/
│  │  ├─ src/
│  │  ├─ ui/
│  │  ├─ assets/
│  │  └─ Cargo.toml
│  └─ worker-python/
│     ├─ app/
│     │  ├─ ipc/
│     │  ├─ models/
│     │  ├─ audio/
│     │  ├─ pipeline/
│     │  ├─ export/
│     │  └─ main.py
│     ├─ tests/
│     └─ pyproject.toml
├─ docs/
│  ├─ PRD_Qwen3_ASR_Local_Desktop.md
│  ├─ Technical_Design_Qwen3_ASR_Local_Desktop.md
│  └─ Development_Task_Breakdown_Qwen3_ASR_Local_Desktop.md
├─ samples/
├─ outputs/
└─ scripts/
```

当前工作区暂时只有文档文件，后续开始搭项目时再创建上述目录。

## 7. GUI 设计

## 7.1 GUI 模块

Rust GUI 建议拆成以下模块：

1. `app_shell`：窗口、路由、全局菜单、通知。
2. `task_form`：文件选择、输出配置、背景文本、术语表、别名纠正表。
3. `task_store`：当前任务状态、历史任务索引、设置状态。
4. `worker_client`：负责启动 Python Worker、发送命令、接收事件。
5. `progress_panel`：展示阶段、进度、GPU 状态、错误摘要。
6. `preview_panel`：Markdown 预览、纯文本预览、复制和导出动作。
7. `settings_panel`：模型状态、Python 运行时状态、GPU 检测、日志入口。

## 7.2 GUI 关键状态

桌面端应维护以下核心状态：

1. `AppState`
2. `CurrentTaskDraft`
3. `RunningJobState`
4. `TranscriptViewState`
5. `SettingsState`

建议的 `RunningJobState` 字段：

```text
job_id
status
stage
progress
processed_ms
total_ms
current_segment_index
current_speaker_label
gpu_summary
output_md_path
error_message
```

## 7.3 GUI 交互流程

1. 用户选择本地文件。
2. GUI 读取基础文件信息并显示。
3. 用户输入背景文本、术语表、输出目录等配置。
4. 用户点击“开始识别”。
5. GUI 将 `TaskSpec` 发送给 Worker。
6. Worker 持续回传进度事件。
7. GUI 完成进度刷新。
8. Worker 返回结果路径和 transcript 摘要。
9. GUI 加载 Markdown 进行预览。

## 8. Python Worker 设计

## 8.1 Worker 模块拆分

建议将 Python Worker 拆为如下模块：

1. `ipc.protocol`：消息结构定义和编解码。
2. `runtime.env`：运行环境检查、CUDA 可用性检查、依赖版本校验。
3. `runtime.model_manager`：Qwen 模型和 pyannote pipeline 的懒加载与缓存。
4. `audio.decode`：音频解码、重采样、声道规范化。
5. `audio.slice`：按时间段裁切音频、导出临时 wav。
6. `pipeline.diarize`：speaker diarization 调用与结果转换。
7. `pipeline.segment`：片段规范化、合并、二次切分。
8. `pipeline.transcribe`：逐段调用 Qwen3-ASR 进行转写。
9. `pipeline.merge`：合并 transcript，按时间轴排序。
10. `pipeline.normalize`：术语映射、别名纠正、可选清洗。
11. `export.markdown`：产出 `.md` 文件和可选 `.json` 辅助文件。
12. `storage.jobs`：任务目录、临时文件目录和结果文件落盘。

## 8.2 Worker 生命周期

建议的 Worker 生命周期：

1. GUI 启动时不立即加载大模型。
2. Worker 进程启动后先完成环境检查。
3. 第一次任务开始时再懒加载模型。
4. 同一会话中复用模型对象。
5. GUI 退出时发送 `shutdown` 消息，Worker 做资源释放。

## 9. IPC 协议设计

## 9.1 消息格式

采用 JSON Lines，每行一个 JSON 对象。

### 请求消息示例

```json
{
  "type": "run_job",
  "request_id": "req_001",
  "payload": {
    "job_id": "job_20260417_001",
    "source_path": "D:\\Recordings\\project-weekly.m4a",
    "output_dir": "D:\\Outputs",
    "output_file_name": "project-weekly.transcript.md",
    "language_mode": "auto",
    "enable_speaker_diarization": true,
    "context_text": "本次会议讨论 Apollo 项目与 ACME 客户交付。",
    "terms": ["Apollo", "ACME", "Edge Gateway"],
    "replacements": [
      {"wrong": "阿波罗", "correct": "Apollo"}
    ]
  }
}
```

### 事件消息示例

```json
{
  "type": "job_event",
  "job_id": "job_20260417_001",
  "payload": {
    "stage": "transcribing",
    "progress": 0.64,
    "processed_ms": 1840000,
    "total_ms": 2870000,
    "current_segment_index": 18,
    "current_speaker_label": "Speaker 2"
  }
}
```

### 完成消息示例

```json
{
  "type": "job_completed",
  "job_id": "job_20260417_001",
  "payload": {
    "md_path": "D:\\Outputs\\project-weekly.transcript.md",
    "segments": 42,
    "speakers": 3
  }
}
```

## 9.2 命令集合

MVP 建议支持如下命令：

1. `health_check`
2. `load_models`
3. `run_job`
4. `cancel_job`
5. `open_output_dir`
6. `shutdown`

## 10. 核心数据结构

## 10.1 TaskSpec

```text
job_id: str
source_path: str
output_dir: str
output_file_name: str
language_mode: auto | source | fixed
fixed_language: Optional[str]
enable_speaker_diarization: bool
context_text: str
terms: list[str]
replacements: list[ReplacementRule]
keep_fillers: bool
auto_punctuation: bool
```

## 10.2 SpeakerSegment

```text
segment_id: str
speaker: str
start_ms: int
end_ms: int
duration_ms: int
source_audio_path: str
slice_audio_path: Optional[str]
```

## 10.3 TranscriptSegment

```text
segment_id: str
speaker: str
start_ms: int
end_ms: int
text: str
normalized_text: str
confidence: Optional[float]
```

## 10.4 TranscriptDocument

```text
job_id: str
source_path: str
language: str
segments: list[TranscriptSegment]
speaker_count: int
context_snapshot: str
terms_snapshot: list[str]
generated_at: str
```

## 11. 音频处理流水线

## 11.1 输入规范化

统一把输入文件解码为中间格式：

1. 单声道或双声道保留策略需在验证期确认。
2. 统一采样率建议为 `16kHz`。
3. 中间文件格式使用 `wav`，便于下游处理。

推荐做法：

1. 保留原始文件路径。
2. 在任务临时目录生成标准化 wav。
3. 所有后续操作基于标准化 wav 进行。

## 11.2 Diarization 阶段

输入：标准化音频。

输出：原始 diarization 片段列表。

此阶段职责：

1. 执行 speaker diarization。
2. 生成 `(speaker, start_ms, end_ms)` 列表。
3. 记录估算的 speaker 数量。

## 11.3 片段规范化阶段

此阶段用于提升可读性和稳定性。

规则建议：

1. 过滤极短片段。
2. 合并相邻同 speaker 片段。
3. 对过长片段进行二次切分。
4. 纠正越界时间和负时间。

建议阈值：

1. 最小片段时长：`400ms ~ 800ms`
2. 相邻同 speaker 合并间隔：`<= 300ms`
3. 单段最大转写时长：`30s ~ 90s`

以上阈值应在实现中做成配置项。

## 11.4 转写阶段

输入：规范化后的片段音频。

输出：每个片段的原始文本。

实现要点：

1. 模型使用 `cuda:0`。
2. 同时只处理 1 个任务。
3. 片段内部按串行方式转写，优先保证稳定性。
4. 背景文本和术语表通过 prompt adaptor 传入。

## 11.5 合并阶段

将 transcript 结果按时间轴合并。

规则：

1. 按 `start_ms` 升序排序。
2. 时间重叠时优先保留较早开始的片段顺序。
3. 若同一 speaker 的相邻结果可读性较差，可在导出前做可选文本拼接。

## 12. 术语与上下文增强设计

## 12.1 设计原则

上下文增强采用“两段式实现”：

1. 识别前：把背景文本、术语、专有名词注入转写 prompt。
2. 识别后：用规则化映射做文本归一化。

## 12.2 Prompt Adaptor

建议在 Worker 内实现一个 `PromptAdaptor`，职责如下：

1. 把 `context_text`、`terms`、`replacements` 组装成稳定格式。
2. 控制 prompt 长度，避免输入过长。
3. 在无上下文时退化为默认转写 prompt。

## 12.3 Post Processor

建议后处理拆成三个步骤：

1. 规范化空白字符和标点。
2. 执行术语替换和别名纠正。
3. 做可选的句首大小写或中英文空格修正。

## 13. Markdown 导出设计

## 13.1 输出文件

MVP 建议输出以下文件：

1. `<name>.transcript.md`
2. `<name>.transcript.json`
3. `<name>.job.json`

其中：

1. `.md` 用于用户阅读与分享。
2. `.transcript.json` 用于结构化回放和调试。
3. `.job.json` 用于任务追踪和问题复现。

## 13.2 Markdown 模板

Markdown 生成器至少应支持：

1. YAML Front Matter
2. 任务元信息
3. 上下文摘要
4. 术语表
5. 正文 transcript

正文行格式：

```text
- [00:12:31 - 00:12:42] Speaker 2：我们先确认 Apollo 项目的上线窗口。
```

## 14. 文件系统与缓存设计

## 14.1 目录建议

运行期建议采用以下目录：

```text
<app_data>/
├─ logs/
├─ jobs/
│  └─ <job_id>/
│     ├─ normalized.wav
│     ├─ segments/
│     ├─ transcript.json
│     ├─ transcript.md
│     └─ job.json
├─ models/
└─ temp/
```

## 14.2 临时文件策略

1. 中间切片文件默认保留到任务完成。
2. 任务成功后允许按设置决定是否清理切片。
3. 任务失败时默认保留中间文件，便于排障。

## 15. 任务状态机

建议定义如下状态机：

```text
draft
queued
preparing
decoding
diarizing
segmenting
transcribing
merging
normalizing
exporting
completed
failed
canceled
```

状态切换原则：

1. 只允许前向推进。
2. `failed` 和 `canceled` 为终态。
3. 每次状态切换都要写日志并回传事件。

## 16. 错误处理设计

MVP 应覆盖以下错误类别：

1. 文件不存在或无法读取。
2. `ffmpeg` 不可用。
3. CUDA 不可用。
4. 模型未下载或加载失败。
5. diarization 返回空结果。
6. 某片段转写失败。
7. 导出路径无写权限。

错误处理策略：

1. 可恢复错误尽量落为当前任务失败，不崩溃 GUI。
2. Worker 崩溃时 GUI 需提示“推理进程异常退出”。
3. 单片段转写失败时，默认记录错误并继续后续片段，最终在结果中标记缺失段。

## 17. 性能与显存策略

建议采用以下默认策略：

1. 模型懒加载。
2. 单任务串行执行。
3. 单卡单进程。
4. 片段长度受控。
5. 避免多模型同时高峰并行。

可观测指标：

1. 模型加载耗时。
2. diarization 耗时。
3. 单片段平均转写耗时。
4. 总任务耗时。
5. 峰值显存占用。

## 18. 日志与可观测性设计

建议日志分为三层：

1. GUI 操作日志
2. Worker 结构化日志
3. 任务级审计日志

任务日志至少记录：

1. `job_id`
2. 输入文件路径
3. 各阶段开始与结束时间
4. 片段数量
5. speaker 数量
6. 输出文件路径
7. 错误摘要

## 19. 测试设计

## 19.1 Rust 侧测试

1. 状态管理单测。
2. IPC 编解码单测。
3. 任务表单校验单测。

## 19.2 Python 侧测试

1. 音频解码单测。
2. 片段规范化单测。
3. transcript 合并单测。
4. 术语归一化单测。
5. Markdown 导出快照测试。

## 19.3 集成测试

1. 从 GUI 触发任务到 Worker 返回结果的端到端测试。
2. 使用小样本音频进行稳定性测试。
3. 使用多人音频测试 diarization 合并效果。

## 20. 打包与发布建议

MVP 阶段建议：

1. Rust GUI 独立打包。
2. Python Worker 与虚拟环境随应用一起分发，或在首次启动时引导安装。
3. `ffmpeg` 作为应用依赖一并提供。

工程化阶段建议：

1. 提供安装器。
2. 提供环境诊断页面。
3. 提供模型下载状态检查。

## 21. 主要技术风险

1. Windows 下新一代 GPU 与 `PyTorch/CUDA` 组合兼容性存在波动。
2. diarization 质量会直接影响 transcript 可读性。
3. 长音频处理时，切片策略不当会造成速度慢或显存抖动。
4. 本地依赖较多，安装体验容易成为用户阻塞点。

## 22. 当前假设

1. MVP 允许使用匿名 `Speaker 1/2/3` 标签。
2. MVP 默认只保留段级时间戳。
3. MVP 不做编辑器，只做预览和导出。
4. MVP 使用 stdio JSON Lines 作为 GUI 和 Worker 的通信方式。
5. 模型授权、模型缓存路径和 pyannote 下载方式在工程验证阶段进一步确认。
