# ASR Local Workflow

本上下文描述 ASR Local 将单个录音自动处理为转录稿和最终总结的业务语言。统一术语用于产品、前端、桌面壳、Python worker、测试和文档，避免把用户任务与内部执行资源混为一谈。

## 工作流

**工作流任务（Workflow）**:
一份从单个录音输入到转录稿和最终总结的完整处理意图，具有稳定身份并可被查询、控制和重试。
_Avoid_: 项目、Job、Worker lane

**任务草稿（Workflow Draft）**:
用户尚未启动、仍可自由修改的工作流任务配置。草稿不是已排队或可恢复的工作流任务。
_Avoid_: 未提交任务、临时项目

**任务规格（Workflow Spec）**:
工作流任务启动时确认的输入、转录模型身份、转录选择、总结选择和输出要求的完整快照。任务启动后，规格不随全局设置、模型路径、Profile 或模板变化而改变。
_Avoid_: 当前设置、全局配置

**目录项（Catalog Entry）**:
可被任务规格引用的 Profile、模板或模型定义，具有稳定身份和显式版本；显示名称、默认值或物理路径变化不改变其身份。
_Avoid_: 下拉框文本、当前配置行

**执行尝试（Attempt）**:
工作流任务的一次执行过程；重试会创建新的执行尝试，同时保留此前尝试的结果和失败信息。被应用退出打断的执行尝试已经结束，不能原地继续。
_Avoid_: 新任务、重跑项目

**阶段（Stage）**:
执行尝试当前所处的业务步骤，例如准备、转录、总结或写出最终文件。
_Avoid_: 页面、Worker 状态

**任务状态（Workflow Status）**:
工作流任务面向用户的生命周期状态，例如排队中、运行中、已暂停、已完成、失败、已取消或已中断。
_Avoid_: Stage、lane 状态

**检查点（Checkpoint）**:
一个已经完整产出、可供后续阶段继续使用的中间结果。转录稿是总结阶段的检查点。
_Avoid_: 缓存、临时文件

**失败阶段（Failed Stage）**:
执行尝试发生失败时所在的阶段，用于决定可以从哪里恢复以及是否需要重新转录。
_Avoid_: 错误页面、Worker 错误

**可重试失败（Retryable Failure）**:
在保留已有检查点的前提下，可以创建新执行尝试继续处理的失败。
_Avoid_: 自动忽略的错误

## 转录

**转录链路（Transcription Pipeline）**:
把录音转换为带时间信息和说话人信息的转录稿的一套完整处理方式。
_Avoid_: 模型、后端开关

**链路方案（Pipeline Profile）**:
用户为工作流任务选择的转录链路类型，包括 MOSS 转录链路、Legacy 转录链路或云端转录链路。
_Avoid_: 全局活动模型、enable diarization

**MOSS 转录链路（MOSS Pipeline）**:
使用 MOSS-Transcribe-Diarize 在同一链路中完成长音频转录、时间戳和说话人区分的转录链路。
_Avoid_: MOSS + pyannote

**Legacy 转录链路（Legacy Pipeline）**:
保留的 pyannote 说话人分离与 Qwen3-ASR 转录组合链路，用于兼容和结果对照。
_Avoid_: 默认链路、旧 Worker

**录音背景（Recording Background）**:
帮助转录链路理解录音场景、参与者和主题的上下文信息。
_Avoid_: 总结模板、自由系统 Prompt

**热词（Hotword）**:
希望转录链路优先正确识别的人名、机构名、产品名或专业术语。
_Avoid_: 替换规则、背景全文

**替换规则（Replacement Rule）**:
在转录结果生成后，将明确的错误写法确定性替换为目标写法的规则。
_Avoid_: 热词、Prompt 指令

**转录 Prompt 配方（Transcription Prompt Recipe）**:
将锁定的输出格式要求与录音背景、热词、语言提示组合为最终转录指令的规则。
_Avoid_: 用户随意覆盖的完整 Prompt

**编译后转录 Prompt（Compiled Transcription Prompt）**:
某个工作流任务实际使用的完整转录指令快照，可用于预览和复现结果。
_Avoid_: 当前 Prompt、模板名称

**设备策略（Device Policy）**:
用户对转录执行设备的偏好，可为自动选择、仅 CPU 或仅 GPU；它不等同于实际采用的设备。
_Avoid_: resolved device、CUDA available

**运行计划（Runtime Plan）**:
系统针对某次执行尝试实际选定的设备、精度和可用推理容量的结果。
_Avoid_: 设备策略、硬件检测结果

## 总结与产物

**总结配方（Summary Recipe）**:
某个工作流任务使用的总结 Profile、解析后的总结模型和总结模板的完整选择；任务可以使用 Profile 默认模型或显式模型覆盖。
_Avoid_: 仅模板、仅 Profile

**总结 Profile（Summary Profile）**:
一个可复用且有版本的总结服务配置身份，指向服务地址、认证方式、安全保存的凭据引用和默认模型；默认模型可以在任务的总结配方中被显式覆盖，编辑 Profile 会产生新版本。
_Avoid_: 总结模板、API key

**总结模板（Summary Template）**:
规定最终总结内容结构、关注重点和输出要求的可复用指令。
_Avoid_: 转录 Prompt、Summary Profile

**总结上下文策略（Summary Context Strategy）**:
当转录稿接近或超过总结模型输入容量时，决定单次总结或分块归并总结的规则。
_Avoid_: 静默截断、输出长度

**凭据引用（Credential Reference）**:
指向安全保存凭据的稳定身份，本身不包含 API key 或其他秘密。
_Avoid_: Profile 名称、明文 API key

**临时凭据授权（Ephemeral Credential Grant）**:
在特定工作流、执行尝试、用途、凭据引用和服务目标内短暂提供给云端转录或总结阶段的秘密，使用后即失效。
_Avoid_: 任务配置、持久 Token

**服务目标绑定（Provider Binding）**:
把临时凭据授权限制到任务提交时已确认的 Profile 版本和服务目标，避免 Profile 后续编辑或恶意请求把凭据释放给另一个地址。
_Avoid_: Profile 显示名称、任意 URL

**转录稿（Transcript）**:
转录阶段输出的、带说话人和时间信息的 Markdown 与结构化数据，是总结阶段的输入检查点。
_Avoid_: 最终总结、草稿

**最终总结（Final Summary）**:
总结阶段根据转录稿和总结配方生成的最终 Markdown 结果。
_Avoid_: 转录稿、摘要预览

**产物集（Artifact Set）**:
属于同一工作流任务的转录稿、最终总结、结构化数据和诊断信息的集合。
_Avoid_: outputs 文件列表、历史文件

**产物修订（Artifact Revision）**:
由用户编辑生成、可追溯到原始生成产物的新版本；原始检查点和生成产物保持不变。
_Avoid_: 覆盖原文件、修改检查点

**过期总结（Stale Summary）**:
基于旧转录稿修订生成、在出现新转录稿修订后不再代表最新输入的总结；它仍可审计，但不得呈现为当前结果。
_Avoid_: 失败总结、已删除总结

## 并发与调度

**处理中任务（In-flight Workflow）**:
已经取得工作流执行容量、但尚未进入最终状态的工作流任务。仍在等待执行容量的任务属于待处理队列，不计入处理中任务。
_Avoid_: 全部非终态任务、活动 Worker、忙碌 lane

**待处理队列（Workflow Backlog）**:
已经被接受、但尚未取得工作流执行容量的任务集合。
_Avoid_: 处理中任务、模型推理队列

**工作流并发（Workflow Concurrency）**:
系统允许同时处于不同处理阶段的工作流任务数量；产品首个目标为最多三个处理中任务。
_Avoid_: 三模型并行、三 Worker 进程

**模型推理并行度（Inference Parallelism）**:
同一时刻真正执行模型推理的请求数量，由运行计划和硬件资源决定，可以低于工作流并发。
_Avoid_: 工作流并发、队列长度
