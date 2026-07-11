# PRD：ASR Local 一键工作流与 WorkflowRuntime v2

## 文档信息

- 文档类型：产品与工程重构 PRD
- 版本：v2.0-draft
- 日期：2026-07-10
- 目标平台：Windows 桌面优先
- 初始桌面 adapter：Tauri 2 + Vue 3 + TypeScript
- 核心运行时：Python WorkflowRuntime
- 配套文档：[Domain Glossary](./CONTEXT.md)、[Worker Contract v2](./docs/worker-contract-v2.md)、[分阶段开发计划](./docs/Workflow_Runtime_V2_Implementation_Plan.md)

## Problem Statement

当前产品把转录、Markdown 查看与编辑、总结生成拆成多个需要用户手动衔接的步骤。用户必须先提交转录任务、等待完成、打开转录稿、切换到总结页面、选择总结配置并再次发起请求，才能获得最终总结。这种文件驱动的手工流程增加了操作成本，也无法可靠支持多个录音交错处理。

当前任务身份与内部 Worker lane 绑定，模型选择来自全局可变配置，总结状态和 Markdown 内容则是全局单例。多个任务并发时，任务配置、进度、转录稿和总结响应可能互相覆盖；排队任务无法按任务身份取消；应用重启后也无法恢复队列和运行状态。

产品需要从“多个独立工具页”演进为“一个录音输入、一次配置、一键执行、自动产出最终总结”的工作流产品，同时保留转录稿作为可审计和可重试的检查点。

模型层也需要从默认 Qwen3-ASR + pyannote 组合链路迁移到 MOSS-Transcribe-Diarize。MOSS 0.9B 可以在一次长音频推理中输出时间戳和匿名说话人标签，并支持自定义转录指令和热词；Legacy 转录链路仍需保留用于兼容、回退和结果对照。官方模型卡标注 MOSS-Transcribe-Diarize 0.9B 于 2026-07-09 发布，因此正式设为发布默认前必须完成质量、依赖、CPU/GPU 和并发验收。

## Solution

产品将引入以工作流任务为核心的 WorkflowRuntime。用户在任务草稿中一次性选择录音、转录链路、设备策略、录音背景、热词、总结 Profile、总结模型、总结模板和输出位置；启动后生成不可变任务规格，并自动执行：

```text
准备 → 转录 → 持久化转录稿 → 总结 → 写出最终 Markdown
```

WorkflowRuntime 负责排队、按任务控制、阶段状态、硬件规划、模型生命周期、转录与总结衔接、检查点、重试、恢复和产物登记。桌面 UI 只提交任务草稿；受信任桌面层解析 Profile，supervisor 生成任务规格。UI 订阅任务状态、发送任务级控制命令并展示产物。

MOSS 转录链路与 Legacy 转录链路使用不同 adapter：MOSS 保持长音频端到端转录和说话人一致性；Legacy 链路继续执行 pyannote 分段和 Qwen3-ASR 转录。模型专属 Prompt compiler 将结构化的录音背景和热词编译为可预览、可复现的转录 Prompt。

产品首个并发目标为最多三个处理中任务。工作流并发与模型推理并行度分开管理：三个任务可以同时处于解码、转录、总结或写出阶段，但真正的模型推理并行度由运行计划根据硬件资源决定。

### 核心目标

1. 用户一次配置即可自动获得最终 Markdown 总结。
2. MOSS 转录链路成为目标默认，Legacy 转录链路继续可选。
3. 每个工作流任务拥有不可变任务规格，包含自己的模型身份、Prompt、总结和输出选择。
4. 设备策略支持自动选择、仅 CPU 和仅 GPU，并记录实际运行计划。
5. 支持最多三个处理中任务，且任务状态和产物绝不互相污染。
6. 转录稿作为持久检查点；总结失败可单独重试。
7. 应用重启后可查询排队、失败和被中断的任务，并由用户显式选择建议重试点继续执行。
8. 桌面框架通过稳定 interface 与 WorkflowRuntime 解耦，为后续 Electron adapter 保留可能性。

### 成功标准

1. 从选择录音到生成最终总结只需要一次启动操作，不要求用户切换页面触发总结。
2. 三个工作流任务交错完成时，每个任务的配置、事件、转录稿和最终总结都归属正确。
3. 总结阶段失败后，用户可以在不重新转录的情况下完成重试。
4. 修改全局模板、Profile、模型路径或活动模型不会改变已启动任务的任务规格。
5. 应用异常退出后，运行中任务被识别为已中断，已有检查点仍可使用。
6. MOSS 对代表性录音集的转录质量和说话人区分达到发布验收门槛，且不存在长音频分块导致的说话人标签重置。
7. 自动设备策略能够说明实际选择的设备、精度和推理容量；资源不足时给出可操作的降级原因。
8. API key 不进入 renderer 持久状态、事件、日志、任务数据库或产物文件。
9. 所有 v2 消息都可以按请求和工作流身份关联，迟到事件不会覆盖新执行尝试。
10. Tauri adapter 下完成生产构建、启动、退出清理和新机器安装验证。

## User Stories

1. As a 桌面用户, I want to 创建一个任务草稿, so that 我可以在启动前完整检查录音和处理配置。
2. As a 桌面用户, I want to 选择本地录音文件, so that 系统可以自动完成后续处理。
3. As a 桌面用户, I want to 为任务选择输出位置和名称, so that 产物可以按我的工作习惯归档。
4. As a 桌面用户, I want to 在启动前选择转录链路, so that 每个任务可以使用 MOSS、Legacy 或云端转录。
5. As a 默认用户, I want to 默认看到 MOSS 转录链路, so that 我可以使用更轻量的一体化说话人转录体验。
6. As a 兼容性用户, I want to 继续选择 Legacy 转录链路, so that 我可以回退或对比 Qwen3-ASR + pyannote 的结果。
7. As a 桌面用户, I want to 选择自动、仅 CPU 或仅 GPU 的设备策略, so that 我可以平衡兼容性和性能。
8. As a 桌面用户, I want to 查看系统实际采用的设备和精度, so that 我能理解运行性能和故障原因。
9. As a 桌面用户, I want to 填写录音背景, so that 转录链路能获得会议主题、参与者和场景信息。
10. As a 桌面用户, I want to 填写热词列表, so that 人名、机构名、产品名和专业术语更容易被正确识别。
11. As a 桌面用户, I want to 预览编译后的转录 Prompt, so that 我知道实际发送给模型的完整指令。
12. As a 桌面用户, I want to 继续使用确定性替换规则, so that 已知错误写法可以在转录后稳定纠正。
13. As a 桌面用户, I want to 在启动前选择总结 Profile, so that 系统知道调用哪个服务、凭据和默认模型。
14. As a 桌面用户, I want to 使用 Profile 默认模型或显式覆盖总结模型, so that 每个任务使用的模型是明确且可复现的。
15. As a 桌面用户, I want to 选择现有总结模板, so that 最终总结符合访谈、客户调研或通用纪要等场景。
16. As a 桌面用户, I want to 在启动任务时保存总结模板快照, so that 模板后续变化不会改变已排队任务。
17. As a 桌面用户, I want to 点击一次启动按钮, so that 系统自动完成转录、总结和最终写出。
18. As a 桌面用户, I want to 在任务列表中查看排队和运行状态, so that 我能了解多个录音的处理进度。
19. As a 桌面用户, I want to 同时保有最多三个处理中任务, so that 我可以批量处理录音而不必逐个等待。
20. As a 桌面用户, I want to 取消一个仍在排队的任务, so that 不需要的任务不会占用后续资源。
21. As a 桌面用户, I want to 暂停支持暂停的运行阶段, so that 我可以临时释放或控制处理节奏。
22. As a 桌面用户, I want to 恢复已暂停任务, so that 它能从安全检查点继续。
23. As a 桌面用户, I want to 按工作流任务而不是 Worker lane 终止处理, so that 不会误杀另一个任务。
24. As a 桌面用户, I want to 在转录完成后立即看到转录稿检查点, so that 即使总结失败也不会丢失昂贵的转录结果。
25. As a 桌面用户, I want to 让系统自动开始总结, so that 不需要手动打开总结页面再次操作。
26. As a 桌面用户, I want to 直接获得最终 Markdown 总结, so that 我可以立即审阅、编辑或分享。
27. As a 桌面用户, I want to 在总结失败时看到失败阶段和原因, so that 我知道转录稿仍然可用。
28. As a 桌面用户, I want to 只重试总结阶段, so that 我无需再次消耗本地 ASR 时间和资源。
29. As a 桌面用户, I want to 在转录失败后创建新的执行尝试, so that 历史失败信息仍被保留。
30. As a 桌面用户, I want to 在应用重启后看到此前任务, so that 队列和历史不会因 UI 关闭而丢失。
31. As a 桌面用户, I want to 识别被应用异常退出打断的任务, so that 我可以决定恢复、重试或取消。
32. As a 桌面用户, I want to 在任务详情中查看阶段时间线, so that 我能知道耗时和失败发生在哪里。
33. As a 桌面用户, I want to 查看任务使用的任务规格, so that 结果可以被复现和审计。
34. As a 桌面用户, I want to 查看任务的转录稿和最终总结, so that 我不需要从散落文件中猜测归属。
35. As a 桌面用户, I want to 使用现有 Markdown 编辑与预览能力, so that 我可以修订转录稿和最终总结。
36. As a 桌面用户, I want to 打开任务产物所在位置, so that 我可以使用其他本地工具继续处理。
37. As a 桌面用户, I want to 在输出名称冲突时获得明确策略, so that 并发任务不会覆盖彼此文件。
38. As a 桌面用户, I want to 查看 MOSS 与 Legacy 链路的模型名称和状态, so that 我可以做结果对照和故障排查。
39. As a 桌面用户, I want to 管理总结 Profile 和总结模板 catalog, so that 常用配置可以被多个任务复用。
40. As a 桌面用户, I want to 管理模型路径和默认选择, so that 新任务草稿能获得合理默认值。
41. As a 隐私敏感用户, I want to 在启动前看到总结服务、模型和转录文本将离开本机的提示, so that 选择总结配方并启动任务构成明确授权。
42. As a 隐私敏感用户, I want to 让 API key 保持在受保护的配置层, so that renderer、日志和产物不暴露凭据。
43. As a 支持人员, I want to 查看任务级诊断信息和运行计划, so that 我可以区分模型、硬件、服务和文件错误。
44. As a 开发者, I want to 通过同一个 WorkflowRuntime interface 测试和驱动产品, so that 桌面框架替换不会改变业务行为。
45. As a 开发者, I want to 使用确定版本的 MOSS 依赖和 Prompt 配方, so that 上游变化不会静默改变用户结果。
46. As a 桌面用户, I want to 让超长转录稿使用明确的总结上下文策略, so that 最终总结不会因静默截断而遗漏内容。
47. As a 桌面用户, I want to 区分总结请求失败和结果未知, so that 我可以决定是否承担可能的重复调用或计费。
48. As a 桌面用户, I want to 在凭据真正被需要时由受信任层临时授权, so that API key 不需要跟随任务长期保存。
49. As a 桌面用户, I want to 编辑转录稿或最终总结时创建可追溯修订, so that 原始生成检查点仍可审计。
50. As a 桌面用户, I want to 在转录稿修订后看到旧总结已过期, so that 我不会误把基于旧转录稿的总结当成最新结果。

## Functional Requirements

### 任务与配置

- **FR-001**：系统必须区分任务草稿与已启动工作流任务。
- **FR-002**：启动任务时必须生成稳定 `workflow_id` 和首个 `attempt_id`。
- **FR-003**：任务规格必须包含录音、转录链路、设备策略、转录 Prompt 输入、总结配方和输出规划的快照。
- **FR-004**：任务启动后，全局设置变化不得改变任务规格。
- **FR-005**：同一输出目标必须通过拒绝、唯一后缀或原子预留策略避免覆盖。

### 转录链路与 Prompt

- **FR-010**：MOSS 转录链路必须保持长音频时间戳和说话人标签语义，不得复用会重置说话人身份的 Legacy 固定分块主干。
- **FR-011**：Legacy 转录链路必须继续支持 pyannote + Qwen3-ASR。
- **FR-012**：链路方案必须按任务选择，不得依赖执行时读取的全局活动模型。
- **FR-013**：MOSS Prompt compiler 必须锁定基础输出格式，并结构化追加录音背景、热词和语言提示。
- **FR-014**：替换规则必须作为转录后处理，不得与热词混为同一机制。
- **FR-015**：编译后的转录 Prompt 和 compiler 版本必须进入任务规格。
- **FR-016**：任务规格必须保存本地 pipeline 的模型稳定 ID、revision、配置 digest 和解析路径；Legacy 链路还必须保存 diarization 模型身份。

### 硬件与运行计划

- **FR-020**：设备策略必须支持 `auto`、`cpu` 和 `cuda`。
- **FR-021**：运行时必须记录实际设备、精度、推理容量和选择原因。
- **FR-022**：`auto` 必须考虑设备可用性、内存预算和模型加载探测，而不能只检查 CUDA 是否存在。
- **FR-023**：资源不足时必须降低推理并行度或返回明确的可重试/不可重试错误。
- **FR-024**：用户强制 `cuda` 而 GPU 不可用时不得静默改用 CPU。

### 自动总结与产物

- **FR-030**：转录成功后必须原子写出转录稿检查点，再进入总结阶段。
- **FR-031**：总结阶段必须使用任务规格中的总结 Profile、模型和模板快照。
- **FR-032**：总结成功后必须写出最终 Markdown 和结构化元数据。
- **FR-033**：总结失败不得删除或覆盖转录稿检查点。
- **FR-034**：系统必须支持从总结阶段重试，而不重新执行转录。
- **FR-035**：任务详情必须能关联全部产物和诊断信息。
- **FR-036**：总结 adapter 必须根据明确的 input token budget 选择 single-pass 或 hierarchical 策略，禁止静默截断转录稿。
- **FR-037**：鉴权、限流、超时和 provider 结果未知必须使用不同错误码和重试策略。
- **FR-038**：生成的转录和总结检查点必须不可变；用户编辑必须创建新的产物修订。
- **FR-039**：转录稿产生新修订时，所有基于旧转录稿的最终总结必须标记为 stale。
- **FR-040**：用户可以选择某个转录稿修订，从总结阶段创建新的执行尝试。

### 并发、控制与恢复

- **FR-050**：系统必须支持最多三个处理中任务；仍在待处理队列中的第四个及后续任务不占工作流执行容量。
- **FR-051**：模型推理并行度必须由运行计划独立控制，不得等同于三个模型进程。
- **FR-052**：所有暂停、恢复、取消和重试命令必须按 `workflow_id` 和预期 `attempt_id` 执行。
- **FR-053**：待处理队列中的任务必须可以取消。
- **FR-054**：迟到事件和旧执行尝试事件不得覆盖当前任务快照。
- **FR-055**：应用启动时必须恢复任务注册表，并把未正常结束的运行中尝试标记为已中断。
- **FR-056**：启动协调不得自动创建新执行尝试；用户必须基于建议恢复点显式选择重试或取消。

### UI 与历史

- **FR-060**：一级导航应以任务、历史和设置为主，不再要求用户按页面手工串联转录和总结。
- **FR-061**：新建任务页面必须一次提供录音、转录、Prompt、总结和输出配置。
- **FR-062**：任务列表必须按任务显示状态，内部资源槽仅作为诊断信息。
- **FR-063**：任务详情必须展示阶段时间线、任务规格、运行计划、错误和产物。
- **FR-064**：Markdown 编辑器和预览必须绑定到选中任务的具体产物修订，不得使用跨任务全局内容。
- **FR-065**：历史必须来源于任务注册表和产物关系，不再仅按文件名后缀推断。

### 安全与兼容

- **FR-070**：renderer 只使用总结 Profile 身份和可选模型覆盖，不得持有或提交明文 API key。
- **FR-071**：桌面受信任层必须把 Profile 解析为带 Profile version、endpoint、credential ref、provider binding 和 resolved model 的非敏感授权快照后再提交 worker，并保留仍被 workflow 引用的历史版本。
- **FR-072**：worker 只保存凭据引用；真正需要凭据时发出临时请求，由桌面受信任层按提交时授权快照核对 workflow、attempt、Profile version、purpose、credential ref 和 endpoint binding 后通过一次性授权消息提供。
- **FR-073**：新建任务页面必须在启动前明确显示总结 provider host 和模型，并提示转录文本将发送到该服务。
- **FR-074**：worker contract 必须在握手时验证主版本兼容性。
- **FR-075**：每条命令响应必须关联请求；每个任务事件必须关联工作流、执行尝试和单调序号。
- **FR-076**：v1 产物必须继续可读；v1 与 v2 运行消息不得混用。
- **FR-077**：MOSS 上游代码、依赖和 Prompt 配方必须锁定可审计版本。

## Non-Functional Requirements

### 可靠性

1. 任一阶段失败不得破坏已经成功写出的检查点和产物。
2. 任务状态更新和产物登记必须具备原子性或可恢复性。
3. Worker 或桌面异常退出后不得把任务错误标记为成功。
4. 重复提交同一 `operation_id` 和相同 canonical payload 不得创建重复任务。
5. 任务事件允许重复投递，但消费结果必须幂等。

### 性能与资源

1. 桌面主窗口启动不得等待模型加载。
2. 模型必须按需加载并由统一运行时管理，不得因工作流并发无条件创建三份模型副本。
3. 三任务在途时 UI 必须保持可交互。
4. 正式发布前必须记录单任务及 1/2/3 任务场景下的吞吐、延迟、峰值 RAM/VRAM 和失败率。
5. CPU 模式和 GPU 模式必须分别建立代表性录音基准。

### 可维护性

1. WorkflowRuntime interface 是产品工作流的主要测试 seam。
2. MOSS、Legacy、云端转录和总结提供方通过内部 adapter 变化。
3. TypeScript、Rust 和 Python 使用同一组 contract fixtures 验证消息兼容性。
4. 业务状态不得依赖页面是否打开或当前选中的任务。
5. 内部资源槽、进程和模型实例不得成为 UI 的任务身份。

### 隐私与安全

1. 本地录音默认不上传；只有用户选择云端转录时才发送音频。
2. 标准工作流选择总结配方并点击启动，即表示用户授权向启动页明确显示的 provider host 和 resolved model 发送转录内容；未完成该提示和确认不得提交任务。
3. 凭据不得进入任务规格持久快照、日志、错误详情或产物。
4. 使用 `trust_remote_code` 的模型代码必须锁定 revision 并纳入发布审计。

## Implementation Decisions

1. 建立深 module `WorkflowRuntime`，其 interface 只暴露 capabilities、Prompt preview、提交、任务级控制、查询/列表、阶段重试、产物修订登记和事件订阅；Profile、模板和模型的 catalog CRUD 属于独立 `WorkflowCatalog` module。
2. WorkflowRuntime 的首个生产 implementation 放在单一 Python supervisor 中，集中管理状态机、调度、模型生命周期、总结衔接、持久化和恢复。
3. Tauri 保持为首个桌面 adapter；本 PRD 不同时执行 Electron 全量迁移。
4. worker contract v2 继续使用 UTF-8 JSON Lines over stdio，但加入握手、请求关联、任务身份、执行尝试、事件序号和可查询快照。
5. MOSS 转录链路与 Legacy 转录链路是两个独立 adapter，共同返回规范化转录稿。
6. MOSS 优先采用官方消息构造和输出解析能力，锁定上游 revision；自定义代码只负责产品需要的 Prompt compiler、运行计划和产物规范化。
7. 任务规格在提交时形成不可变快照；其中包含 pipeline、转录与 diarization 模型的稳定 ID、revision、配置 digest、解析路径，以及总结 Profile、resolved model、模板和 Prompt 快照。凭据值不属于快照，只记录安全引用和非敏感服务信息。
8. 工作流任务注册表使用 SQLite；大型产物继续保存在文件系统，并在注册表中记录路径、摘要和状态。
9. 每个任务使用独占产物目录，文件先写临时路径再原子替换。
10. Summary Profile 拥有 endpoint、认证模式、credential ref 和默认模型；Summary Recipe 可以显式覆盖模型。桌面受信任层在提交时保留版本化的非敏感授权快照，并通过绑定工作流、执行尝试、Profile version、用途、credential ref、provider endpoint 和过期时间的临时授权提供凭据；Profile 编辑只产生新版本，不改写旧任务授权。
11. 并发采用阶段级资源槽：工作流并发目标为 3，ASR、CPU 预处理和网络总结拥有独立容量。
12. 初始 native Transformers MOSS runtime 默认单模型实例；是否增加推理并行或 serving backend 由基准结果决定。
13. UI 状态按 `workflow_id` 归一化存储，任务草稿、任务快照、catalog 和 artifact editor state 分离。
14. 初始化恢复采用“先订阅事件，再拉取任务快照，按序号合并”的流程。启动恢复只把旧 attempt 标记为 `interrupted` 并计算建议重试阶段，不自动创建新 attempt；用户必须显式重试或取消。
15. 历史视图改为任务视图；散落的 Markdown 文件仍可作为 legacy 产物导入或浏览。
16. MOSS 是目标默认，但只有通过发布门槛后才修改新安装默认；Legacy 链路至少保留一个稳定发布周期。
17. Profile、模板和本地模型 catalog 在 Phase 1 即采用稳定 UUID/ID 与显式 version；后续改名、路径调整或默认值变化不得改变已提交任务规格。
18. 生成产物是不可变检查点；编辑器保存时创建带 `derived_from` 的产物修订。转录稿修订会把基于旧修订的总结标记为 `stale`，重新总结必须显式选择输入修订。

## Testing Decisions

1. 测试只断言通过 WorkflowRuntime interface 可观察到的任务快照、事件、控制结果和产物，不依赖内部线程、进程或资源槽数量。
2. 建立共享 contract fixtures，确保 TypeScript、Rust 和 Python 对每个命令、响应、事件和错误的编码结果一致。
3. 使用 fake Transcriber 和 fake SummaryGenerator 测试完整状态机，包括成功、阶段失败、暂停、取消、重试和应用恢复。
4. 测试三个任务事件交错、逆序完成、重复事件和迟到事件，确保任务状态不互相污染。
5. 测试旧 `attempt_id` 和较小 `sequence` 的事件被安全忽略。
6. 测试排队任务取消、运行任务取消和不支持暂停阶段的明确错误。
7. 测试总结失败后保留转录稿，并只从总结阶段创建新执行尝试。
8. 测试同名输出的原子冲突处理和多任务隔离目录。
9. 测试应用重启后 `running` 转为 `interrupted` 并给出建议重试阶段，但在用户显式调用 retry 前不得创建新 attempt 或调用模型/provider。
10. 对 MOSS Prompt compiler 做快照测试，覆盖无背景、背景、热词去重、语言提示和长度限制。
11. 对 MOSS 与 Legacy adapter 使用固定短音频做集成 smoke test，验证统一转录格式。
12. 对长音频验证 MOSS speaker 标签不因固定 30 秒分块重置，并检测输出截断。
13. 对设备策略测试自动选择、强制 CPU、强制 CUDA 不可用、模型加载失败和 OOM 降级。
14. 对总结凭据做安全测试，确认数据库、日志、事件和产物中不存在 API key，并拒绝 workflow、attempt、Profile version、purpose、credential ref 或 provider endpoint binding 不一致的授权；Profile 编辑后旧任务仍按提交版本授权，显式凭据撤销则返回可理解错误。
15. 测试任务入队后修改全局模型路径、Profile 默认模型和模板正文，已提交任务仍使用原模型身份与快照。
16. 测试编辑转录稿会创建新 Artifact Revision、保留生成检查点、使旧总结 stale，并可基于指定修订只重试总结。
17. 对 UI reducer 测试任务快照合并、选中任务切换和多任务 artifact editor 隔离。
18. 在生产构建上执行安装、首次启动、任务运行、退出清理和卸载 smoke test。

现有仓库只有少量 Rust 测试，前端尚无测试脚本；因此 contract fixture、状态机和 reducer 测试必须作为功能开发的前置基础，而不是发布前补做。

## Out of Scope

1. 本 PRD 不包含 Electron 全量迁移；只要求 interface 不依赖 Tauri。
2. 不承诺首版实现三个同时执行的模型 `generate` 请求；首版承诺三个处理中任务。
3. 不把 SGLang Omni、vLLM 或其他 serving runtime 设为首版生产依赖。
4. 不重写 MOSS、Qwen3-ASR 或 pyannote 的模型算法。
5. 不实现实时麦克风流式转录。
6. 不实现跨机器分布式任务队列或远程 Worker。
7. 不实现账号、云同步和多人协作。
8. 不在主安装器中内置模型权重；模型包和应用包继续分层。
9. 不实现模型自动下载市场或模型版本管理平台。
10. 不实现完整所见即所得 Markdown 编辑器。
11. 不把多个录音聚合为一个长期“项目”；首版一个工作流任务对应一个录音。
12. 不在本期引入本地总结大模型；“总结模型”指现有 OpenAI-compatible Summary Profile 的默认模型或 Summary Recipe 的显式覆盖。
13. 不在自动主路径中插入“等待人工校对”检查点；任务完成后仍可通过 Artifact Revision 修订转录稿并显式重新总结。
14. 不增强现有 cloud ASR 能力，只通过 v2 adapter 保持兼容。
15. 不承诺多 GPU 调度；首版只认证单 CPU 或单 GPU RuntimePlan。
16. 不保证共享模型正在执行的单次 `generate` 能即时暂停或取消；控制在安全点生效。

## Further Notes

### 本草案固定的产品决策

为使计划可以直接实施，本草案采用以下默认决定，后续若改变需同步修改 PRD、contract 和实施计划：

1. “三并发”指最多三个处理中任务，不保证三路模型推理同时执行。
2. 标准工作流强制包含总结；缺少有效 Summary Profile、模型或模板时阻止启动。Transcript-only 模式不在本期。
3. Summary Profile 提供 OpenAI-compatible endpoint、认证模式、credential ref 和默认模型；任务可在 Summary Recipe 中显式覆盖模型。本地总结模型不在本期。
4. 主路径是自动总结，不设置人工校对检查点。
5. `device_policy=auto` 可以在转录开始前从 GPU 受控选择或降级为 CPU，并必须记录原因；转录开始后不静默切换设备。
6. MOSS 失败不自动切换 Legacy 链路；用户需要显式选择 Legacy。
7. 同名输出默认创建唯一任务目录，不覆盖已有产物。
8. 长转录稿默认使用 `context_strategy=auto`：预算内单次总结，超出预算后 hierarchical 总结。
9. 鉴权失败不自动重试；临时网络错误和限流最多自动重试两次；结果未知需要用户确认。
10. 首版一个工作流任务只处理一份录音。
11. supervisor 启动只中断旧 attempt 并建议安全重试点，不自动恢复执行；创建新 attempt 必须来自用户显式 retry。
12. 生成的 transcript/summary artifact 不可原地修改；编辑始终产生可追溯 revision，转录修订会使旧总结 stale。
13. Profile、模板和模型 catalog 的稳定身份与版本在 Phase 1 冻结，数据迁移在生产 UI 接线前完成，Phase 7 仅做兼容清理和发布验证。

### MOSS 官方依据

- [MOSS-Transcribe-Diarize 模型卡](https://huggingface.co/OpenMOSS-Team/MOSS-Transcribe-Diarize)
- [MOSS 官方仓库](https://github.com/OpenMOSS/MOSS-Transcribe-Diarize)
- [MOSS Prompt Recipes](https://github.com/OpenMOSS/MOSS-Transcribe-Diarize/blob/main/examples/prompts.md)

官方资料确认 0.9B 模型支持长音频转录、匿名说话人标签、自定义指令和热词，并给出了 native Transformers 与并发 serving 示例。官方并发数据来自 H100，不作为 Windows 消费级 GPU 三路推理的直接承诺。

### 发布默认门槛

MOSS 从“可选”切换为“发布默认”前，必须完成：

1. 代表性中文访谈、会议和客户调研录音集对比。
2. 转录错误率、说话人一致性和热词命中评估。
3. 30、60、90 分钟录音的长上下文和截断测试。
4. CPU 与目标 GPU 的实时因子、峰值 RAM/VRAM 和稳定性测试。
5. 1/2/3 工作流并发及不同阶段交错测试。
6. MOSS、Transformers、Torch、Qwen-ASR 和 pyannote 依赖兼容矩阵。
7. 上游 revision、remote code 和许可证审计。

### 桌面框架决策点

完成 WorkflowRuntime、contract v2、一键 UI 和恢复能力后，再以同一个 interface 实现 Electron adapter PoC。只有 Electron 在团队开发效率、安装、冷启动、RSS、退出清理和安全审查上显示明确综合收益时，才进入迁移决策。
