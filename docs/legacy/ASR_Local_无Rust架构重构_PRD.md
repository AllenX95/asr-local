# ASR Local 无 Rust 架构重构 PRD

> **历史归档（implemented）**：本文是 Tauri/Rust 到 Electron 的迁移决策依据，迁移已经完成。当前架构以 [Electron 迁移实现报告](../Electron_Migration_Implementation_Report.md) 为准。

## 1. 文档信息

| 项目 | 内容 |
|---|---|
| 产品名称 | ASR Local |
| 文档类型 | 产品重构需求文档 |
| 文档版本 | V1.0 |
| 重构类型 | 桌面应用架构重构 |
| 目标技术栈 | Electron + TypeScript + Vue 3 + Python |
| 核心原则 | 去除 Rust；Python 作为唯一业务内核；Electron 仅承担桌面运行时能力 |
| 适用平台 | 第一阶段以 Windows 为主，预留 macOS 和 Linux 适配能力 |
| 当前状态 | Tauri + Vue + TypeScript + Rust + Python，旧版任务体系与 Workflow Runtime v2 并存 |

---

## 2. 产品背景

ASR Local 是一款本地音视频转写与内容处理软件，主要通过本地 Python 环境调用 CUDA、PyTorch、Transformers、ASR 模型及说话人识别模型，完成音视频转写、说话人区分、文本整理、摘要生成和结果导出。

当前产品主要采用以下技术栈：

```text
Vue 3 + TypeScript
        ↓
Tauri
        ↓
Rust 桌面后端
        ↓
Python Worker
        ↓
CUDA / PyTorch / Transformers
```

随着产品功能演进，当前架构逐渐形成以下问题：

1. Rust 与 Python 之间存在业务职责重叠；
2. 旧版 Rust 任务调度体系与新版 Python Workflow Runtime 并存；
3. 部分配置、任务状态和历史记录分散在 Rust、前端和 Python 中；
4. 桌面框架、业务逻辑和模型运行逻辑耦合较深；
5. Python、CUDA、模型和应用安装包尚未形成清晰的产品化分发体系；
6. 桌面端每新增一项业务能力，可能同时涉及 Vue、Rust 和 Python 三层修改；
7. Rust 对项目核心价值贡献有限，但显著提高了开发、调试和维护成本。

因此，需要在保留现有 Vue 前端和 Python 推理能力的基础上，移除 Rust，并重构为职责边界更加清晰的桌面应用架构。

---

## 3. 重构目标

### 3.1 总体目标

将当前产品重构为：

```text
Vue 3 + TypeScript 前端
          ↓
Electron Preload 安全桥接层
          ↓
Electron Main 桌面运行时
          ↓
JSONL / 标准输入输出
          ↓
Python Workflow Runtime
          ↓
CUDA / PyTorch / Transformers
```

其中：

- Vue 负责用户界面和交互；
- Electron 负责窗口、文件、进程和操作系统能力；
- Python 负责全部任务、模型和工作流业务；
- Rust 代码全部移除；
- Workflow Runtime v2 成为唯一任务运行时；
- 桌面壳层不再承担 ASR、总结、模型调度等核心业务。

### 3.2 具体目标

#### 产品目标

1. 保留现有主要产品功能和用户操作习惯；
2. 提升任务执行稳定性和错误可恢复能力；
3. 形成统一的任务、阶段、状态和产物管理机制；
4. 支持本地模型、云端模型及混合工作流；
5. 降低后续新增工作流和模型的开发成本；
6. 支持后续扩展命令行、浏览器和服务端运行模式；
7. 建立可安装、可诊断、可升级的本地 AI 软件基础设施。

#### 工程目标

1. 完全移除 Rust 和 Tauri；
2. Vue 页面及组件复用率原则上不低于 80%；
3. Python 推理与模型代码原则上不做无必要重写；
4. 以 Workflow Contract v2 作为前后端唯一业务通信协议；
5. 桌面端不重复实现任务队列、GPU 调度和工作流状态机；
6. 建立标准化日志、崩溃恢复、运行环境检测和数据迁移机制；
7. 支持应用、Python 运行时和模型文件独立管理。

---

## 4. 非目标

本次重构不以以下事项为首要目标：

1. 不重新设计全部 UI；
2. 不更换 Vue 3、TypeScript 和 Pinia；
3. 不将 Python 推理代码改写为 TypeScript；
4. 不将应用改造成纯浏览器产品；
5. 不在第一阶段实现多用户或远程协作；
6. 不在第一阶段实现云端任务调度平台；
7. 不在第一阶段支持移动端；
8. 不对现有模型效果做大规模算法升级；
9. 不将 Rust 代码逐行翻译为 Node.js；
10. 不要求将所有模型权重打入主安装包。

---

## 5. 核心设计原则

### 5.1 Python 是唯一业务内核

所有与任务执行相关的业务均归属于 Python，包括：

- 工作流定义；
- 任务队列；
- 阶段编排；
- ASR 转写；
- 说话人识别；
- 文本后处理；
- 总结生成；
- 模型加载和卸载；
- GPU 和显存资源规划；
- 失败重试；
- 检查点；
- 任务恢复；
- 结果和产物索引；
- 任务历史；
- 运行时业务配置校验。

Electron 不得重新实现上述逻辑。

### 5.2 Electron 是薄桌面壳

Electron 仅负责：

- 创建和管理窗口；
- 系统菜单和托盘；
- 文件选择；
- 文件打开和保存；
- 系统路径；
- Python 子进程生命周期；
- Workflow Contract 请求转发；
- 操作系统安全存储；
- 自动更新；
- 日志收集；
- 崩溃诊断；
- 应用级配置；
- 系统通知。

### 5.3 前端与运行时解耦

Vue 页面不得直接依赖：

- `electron`；
- `ipcRenderer`；
- Node.js 文件系统；
- Python 进程；
- Tauri API；
- 具体操作系统路径。

前端统一通过 `DesktopBridge` 使用桌面能力。

### 5.4 协议优先

Electron 与 Python 之间必须通过明确、可版本化的协议通信。

协议应满足：

- 请求和响应可追踪；
- 支持异步事件；
- 支持状态快照；
- 支持任务恢复；
- 支持版本协商；
- 支持超时和错误码；
- 支持测试替身；
- 不依赖 Electron 特有实现。

### 5.5 应用、运行时、模型和用户数据分离

软件必须将以下四类资源分开管理：

1. Electron 应用代码；
2. Python 运行时及业务代码；
3. 模型权重和模型依赖；
4. 用户配置、任务数据库和输出文件。

任何单一组件升级均不应无必要地覆盖其他组件。

---

## 6. 目标用户与使用场景

### 6.1 目标用户

主要目标用户为：

- 需要在本地处理会议、访谈、课程和视频内容的个人用户；
- 对数据隐私有要求的专业用户；
- 需要 GPU 加速的音视频转写用户；
- 需要将转写、说话人识别、总结和 Markdown 输出串联处理的用户；
- 具备 Windows 电脑和 NVIDIA GPU 的用户。

### 6.2 核心使用场景

#### 场景一：本地音频转写

用户选择一个或多个音频文件，选择 ASR 模型和输出目录，创建转写任务并查看实时进度。

#### 场景二：音视频转写和说话人识别

用户导入会议录音，开启说话人识别，系统自动输出带说话人标签的文本。

#### 场景三：转写后自动总结

用户创建完整工作流：

```text
音视频导入
→ 音频预处理
→ ASR 转写
→ 说话人识别
→ 文本整理
→ 内容总结
→ Markdown 导出
```

#### 场景四：任务失败后恢复

应用或 Python 进程异常退出后，用户重新打开应用，系统识别未完成任务，并从有效检查点恢复。

#### 场景五：模型管理

用户查看已安装模型、模型大小、版本、所需硬件和状态，并选择下载、校验、更新或删除模型。

#### 场景六：无独立显卡运行

用户设备没有可用 CUDA 环境时，软件允许使用 CPU 模式、云端 API 模式或提示安装相应运行环境。

---

## 7. 总体信息架构

产品主要包含以下一级模块：

```text
ASR Local
├── 首页
├── 新建任务
├── 任务中心
├── 任务详情
├── 历史记录
├── 模型管理
├── 工作流配置
├── 提示词与模板
├── 设置
├── 运行环境诊断
└── 日志与问题反馈
```

---

## 8. 目标技术架构

### 8.1 整体架构

```text
┌──────────────────────────────────────┐
│ Vue Renderer                         │
│ 页面、组件、Pinia、表单、任务展示      │
└──────────────────┬───────────────────┘
                   │ window.desktop
┌──────────────────▼───────────────────┐
│ Electron Preload                     │
│ contextBridge、安全接口白名单          │
└──────────────────┬───────────────────┘
                   │ IPC
┌──────────────────▼───────────────────┐
│ Electron Main                        │
│ 窗口、文件、进程、路径、密钥、更新、日志 │
└──────────────────┬───────────────────┘
                   │ JSONL / stdio
┌──────────────────▼───────────────────┐
│ Python Workflow Runtime              │
│ 队列、状态机、模型、GPU、恢复、产物      │
└──────────────────┬───────────────────┘
                   │
┌──────────────────▼───────────────────┐
│ PyTorch / CUDA / Transformers        │
│ ASR / Diarization / LLM              │
└──────────────────────────────────────┘
```

### 8.2 推荐项目目录

```text
asr-local/
├── apps/
│   ├── desktop-electron/
│   │   ├── src/
│   │   │   ├── main/
│   │   │   ├── preload/
│   │   │   ├── renderer/
│   │   │   └── shared/
│   │   ├── resources/
│   │   └── package.json
│   │
│   └── worker-python/
│       ├── src/
│       │   ├── ipc/
│       │   ├── runtime/
│       │   ├── workflow/
│       │   ├── models/
│       │   ├── pipeline/
│       │   ├── artifacts/
│       │   ├── storage/
│       │   ├── diagnostics/
│       │   └── main.py
│       └── pyproject.toml
│
├── packages/
│   ├── workflow-contract/
│   ├── desktop-bridge/
│   ├── shared-types/
│   └── ui/
│
├── schemas/
│   ├── workflow-request.schema.json
│   ├── workflow-response.schema.json
│   ├── workflow-event.schema.json
│   └── model-manifest.schema.json
│
├── scripts/
│   ├── build/
│   ├── package/
│   ├── migrate/
│   └── diagnostics/
│
├── docs/
└── tests/
```

---

## 9. 功能需求

### 9.1 桌面应用启动

#### 9.1.1 启动流程

应用启动后按以下顺序执行：

1. 启动 Electron 主进程；
2. 加载应用级配置；
3. 检查 Python Runtime 是否存在；
4. 启动 Python Workflow Runtime；
5. 完成协议握手；
6. 检查任务数据库；
7. 加载未完成任务状态；
8. 将运行时状态同步至前端；
9. 打开主窗口。

#### 9.1.2 启动状态

前端至少展示以下状态：

- 正在启动桌面应用；
- 正在检查 Python 环境；
- 正在启动工作流运行时；
- 正在检测 GPU；
- 正在加载任务状态；
- 启动完成；
- 启动失败。

#### 9.1.3 启动失败处理

出现以下问题时，应用不得直接退出：

- Python 不存在；
- Python Runtime 文件损坏；
- Worker 启动失败；
- 协议版本不兼容；
- 数据库损坏；
- CUDA 不可用；
- 模型目录不可访问。

系统应展示明确问题说明，并提供：

- 重试；
- 打开诊断页面；
- 查看日志；
- 修复运行环境；
- 切换 CPU 或云端模式；
- 重新选择 Runtime 路径。

### 9.2 新建任务

#### 9.2.1 输入文件

支持：

- 单个音频文件；
- 单个视频文件；
- 多文件批量导入；
- 文件夹批量导入；
- 拖拽导入。

第一阶段建议支持格式：

- WAV；
- MP3；
- M4A；
- AAC；
- FLAC；
- MP4；
- MOV；
- MKV。

#### 9.2.2 输入校验

系统应校验：

- 文件是否存在；
- 文件是否可读；
- 文件类型是否支持；
- 文件大小；
- 文件时长；
- 输出路径是否可写；
- 是否存在同名任务；
- 是否已经处理过；
- 所需模型是否安装。

#### 9.2.3 任务参数

用户可配置：

- 工作流模板；
- ASR 模型；
- 语言；
- 是否启用说话人识别；
- 说话人数量范围；
- 是否启用自动总结；
- 总结模型；
- 总结模板；
- 输出格式；
- 输出目录；
- 是否保留中间文件；
- 是否任务完成后自动打开结果。

#### 9.2.4 任务创建

用户点击“开始处理”后：

1. 前端生成任务草稿；
2. Python 校验任务配置；
3. Python 返回规范化任务配置；
4. Python 创建任务；
5. 任务进入队列；
6. 前端跳转至任务详情。

任务 ID 必须由 Python Runtime 生成。

### 9.3 工作流系统

#### 9.3.1 标准工作流阶段

```text
PREPARE
→ EXTRACT_AUDIO
→ PREPROCESS
→ TRANSCRIBE
→ DIARIZE
→ ALIGN
→ POSTPROCESS
→ SUMMARIZE
→ EXPORT
→ COMPLETE
```

不同任务可跳过部分阶段。

#### 9.3.2 阶段状态

每个阶段支持：

- pending；
- queued；
- preparing；
- running；
- paused；
- retrying；
- succeeded；
- failed；
- cancelled；
- skipped。

#### 9.3.3 任务状态

任务总体状态支持：

- draft；
- queued；
- running；
- paused；
- succeeded；
- failed；
- cancelled；
- recovering；
- interrupted。

#### 9.3.4 阶段输出

每个阶段必须明确：

- 输入；
- 输出；
- 开始时间；
- 结束时间；
- 执行尝试次数；
- 使用模型；
- 使用设备；
- 日志引用；
- 错误信息；
- 检查点；
- 产物清单。

### 9.4 任务中心

任务中心展示：

- 当前执行任务；
- 排队任务；
- 暂停任务；
- 失败任务；
- 最近完成任务。

每个任务展示：

- 文件名；
- 工作流名称；
- 当前阶段；
- 总体进度；
- 当前阶段进度；
- 开始时间；
- 已运行时间；
- 使用模型；
- GPU 或 CPU 状态；
- 错误状态。

支持操作：

- 查看详情；
- 暂停；
- 恢复；
- 取消；
- 重试；
- 打开结果；
- 打开输出目录；
- 删除任务记录；
- 复制任务配置；
- 重新运行。

### 9.5 任务详情

任务详情页面包含：

#### 基本信息

- 任务名称；
- 输入文件；
- 输出目录；
- 创建时间；
- 工作流；
- 模型；
- 运行设备。

#### 阶段时间线

展示每个阶段：

- 状态；
- 进度；
- 耗时；
- 重试次数；
- 错误信息；
- 产物。

#### 实时日志

支持：

- 按级别过滤；
- 按阶段过滤；
- 搜索；
- 复制；
- 导出。

#### 结果预览

支持预览：

- 原始转写；
- 说话人识别结果；
- 修订文本；
- 摘要；
- Markdown；
- JSON；
- SRT/VTT 字幕。

### 9.6 任务暂停、恢复和取消

#### 暂停

暂停请求由前端发送至 Python Runtime。

暂停应优先发生在安全检查点，不能通过 Electron 强制挂起 Python 线程。

#### 恢复

恢复时：

- 读取最新检查点；
- 校验输入文件和模型状态；
- 从可恢复阶段继续；
- 不重复执行已完成且产物有效的阶段。

#### 取消

取消后：

- 停止当前阶段；
- 释放模型和 GPU 资源；
- 保留已有日志；
- 根据用户配置保留或删除中间产物；
- 任务状态更新为 cancelled。

#### 强制终止

仅在 Python 无法响应时，由 Electron 提供“强制终止运行时”功能。

强制终止后必须：

- 重启 Python Runtime；
- 将受影响任务标记为 interrupted；
- 提示用户尝试恢复。

### 9.7 Python 运行时管理

Electron Main 负责：

- 启动 Python；
- 传入工作目录和配置路径；
- 连接 stdin、stdout、stderr；
- 监测退出码；
- 监测心跳；
- 发送优雅关闭命令；
- 异常退出后重启；
- 防止重复启动多个 Runtime。

Python Runtime 负责：

- 协议握手；
- 任务恢复；
- 任务队列；
- 模型状态；
- 业务日志；
- 心跳响应；
- 优雅关闭。

### 9.8 模型管理

#### 9.8.1 模型清单

模型清单至少包含：

- 模型 ID；
- 模型名称；
- 模型类型；
- 版本；
- 来源；
- 模型大小；
- 所需显存；
- 所需依赖包；
- 支持语言；
- 下载地址；
- 文件校验值；
- 当前状态。

#### 9.8.2 模型状态

支持：

- not_installed；
- downloading；
- verifying；
- installed；
- update_available；
- corrupted；
- incompatible；
- disabled。

#### 9.8.3 模型操作

支持：

- 下载；
- 暂停下载；
- 继续下载；
- 校验；
- 更新；
- 删除；
- 设置默认模型；
- 打开模型目录。

#### 9.8.4 模型兼容性

系统应根据以下信息判断兼容性：

- 操作系统；
- Python 版本；
- PyTorch 版本；
- CUDA Runtime；
- GPU 型号；
- 显存；
- 磁盘空间；
- 模型依赖。

### 9.9 运行环境诊断

#### 系统信息

- 操作系统；
- CPU；
- 内存；
- GPU；
- 显存；
- 磁盘空间。

#### Python 信息

- Python 版本；
- Runtime 路径；
- 虚拟环境；
- 核心依赖版本；
- Worker 版本。

#### CUDA 信息

- NVIDIA 驱动；
- CUDA 可用状态；
- PyTorch CUDA 版本；
- GPU 可见状态；
- 基础张量测试结果。

#### 模型信息

- 模型目录；
- 已安装模型；
- 文件完整性；
- 模型加载测试。

#### 诊断操作

支持：

- 一键诊断；
- 复制诊断信息；
- 导出诊断包；
- 打开日志目录；
- 修复建议；
- 重启 Runtime。

### 9.10 配置管理

配置分为两类。

#### Electron 管理的应用配置

包括：

- 窗口尺寸和位置；
- 主题；
- 语言；
- 最近访问目录；
- 自动更新；
- 是否开机启动；
- Runtime 路径；
- 模型根目录；
- 日志级别；
- API 密钥。

#### Python 管理的业务配置

包括：

- ASR 模型配置；
- 说话人识别配置；
- 总结模型配置；
- 工作流模板；
- 提示词模板；
- 重试策略；
- 检查点策略；
- 并发策略；
- 模型生命周期策略；
- 输出模板。

业务配置必须由 Python 负责校验和规范化。

### 9.11 API 密钥管理

云端模型所需 API 密钥不得：

- 存入前端 LocalStorage；
- 通过明文配置文件持久化；
- 写入日志；
- 发送至无关组件。

API 密钥应通过 Electron Main 调用操作系统安全存储。

前端只能获得：

- 是否已配置；
- 密钥名称；
- 密钥掩码；
- 最近验证结果。

Python 需要使用密钥时，由 Electron 在受控请求中提供，或通过安全的进程环境注入。

### 9.12 历史记录

历史记录不再通过扫描输出目录作为唯一数据来源。

采用 SQLite 保存：

- 任务；
- 阶段；
- 执行尝试；
- 产物；
- 事件；
- 模型使用记录；
- 错误记录。

用户可按照以下维度筛选：

- 日期；
- 文件名；
- 状态；
- 工作流；
- 模型；
- 输入类型；
- 是否有摘要。

支持：

- 查看；
- 搜索；
- 重跑；
- 删除记录；
- 打开输出；
- 复制配置；
- 导出任务信息。

### 9.13 产物管理

每个任务可以产生：

- 音频中间文件；
- 原始转写 JSON；
- 文本转写；
- 说话人识别结果；
- 字幕文件；
- 摘要；
- Markdown；
- 日志；
- 任务元数据。

产物必须包含：

- artifact_id；
- workflow_id；
- stage_id；
- 类型；
- 路径；
- 文件大小；
- 校验值；
- 创建时间；
- 是否为最终产物；
- 是否可安全删除。

### 9.14 日志系统

日志分为：

1. Electron 主进程日志；
2. Python Runtime 日志；
3. Workflow 业务日志；
4. 模型日志；
5. 前端错误日志。

日志要求：

- 统一时间格式；
- 带进程来源；
- 带 workflow_id；
- 带 stage_id；
- 支持日志轮转；
- 默认不记录敏感信息；
- 支持一键打包导出。

Python stdout 仅用于协议消息，不得混入普通日志。

Python 普通日志统一写入 stderr 或日志文件。

### 9.15 自动更新

应用更新分为：

#### Electron 应用更新

包括：

- Vue 前端；
- Electron Main；
- Preload；
- 静态资源。

#### Python Runtime 更新

包括：

- Python 业务代码；
- 依赖清单；
- Runtime 启动器。

#### 模型更新

包括：

- 模型权重；
- 模型配置；
- 模型清单。

三类更新应尽量独立。

更新过程应支持：

- 版本检查；
- 下载进度；
- 文件校验；
- 安装；
- 失败回滚；
- 更新日志；
- 跳过版本。

---

## 10. DesktopBridge 设计

前端统一调用：

```ts
interface DesktopBridge {
  app: AppService;
  files: FileService;
  workflow: WorkflowService;
  settings: SettingsService;
  models: ModelService;
  diagnostics: DiagnosticsService;
  credentials: CredentialService;
  updates: UpdateService;
}
```

### AppService

```ts
interface AppService {
  getVersion(): Promise<string>;
  getPlatform(): Promise<string>;
  openExternal(url: string): Promise<void>;
  restart(): Promise<void>;
  quit(): Promise<void>;
}
```

### FileService

```ts
interface FileService {
  pickFiles(options: PickFileOptions): Promise<string[]>;
  pickDirectory(): Promise<string | null>;
  openPath(path: string): Promise<void>;
  revealInFolder(path: string): Promise<void>;
  saveFile(options: SaveFileOptions): Promise<string | null>;
  readTextFile(path: string): Promise<string>;
  writeTextFile(path: string, content: string): Promise<void>;
}
```

### WorkflowService

```ts
interface WorkflowService {
  submit(input: WorkflowSubmitInput): Promise<Workflow>;
  get(id: string): Promise<Workflow>;
  list(query?: WorkflowQuery): Promise<Workflow[]>;
  pause(id: string): Promise<void>;
  resume(id: string): Promise<void>;
  cancel(id: string): Promise<void>;
  retry(id: string, stageId?: string): Promise<void>;
  subscribe(listener: WorkflowEventListener): Unsubscribe;
}
```

前端不得绕过 DesktopBridge 直接调用 IPC。

---

## 11. Workflow Contract v2

### 11.1 消息结构

每条消息至少包含：

```json
{
  "protocol_version": "2.0",
  "message_type": "request",
  "request_id": "uuid",
  "timestamp": "ISO-8601",
  "method": "workflow.submit",
  "params": {}
}
```

响应：

```json
{
  "protocol_version": "2.0",
  "message_type": "response",
  "request_id": "uuid",
  "success": true,
  "result": {}
}
```

事件：

```json
{
  "protocol_version": "2.0",
  "message_type": "event",
  "event_id": "uuid",
  "event": "workflow.stage.progress",
  "timestamp": "ISO-8601",
  "payload": {}
}
```

### 11.2 核心方法

至少包括：

- runtime.hello；
- runtime.health；
- runtime.shutdown；
- runtime.getCapabilities；
- workflow.submit；
- workflow.get；
- workflow.list；
- workflow.pause；
- workflow.resume；
- workflow.cancel；
- workflow.retry；
- workflow.delete；
- workflow.getArtifacts；
- model.list；
- model.install；
- model.verify；
- model.remove；
- diagnostics.run；
- settings.getBusinessConfig；
- settings.updateBusinessConfig。

### 11.3 核心事件

至少包括：

- runtime.ready；
- runtime.warning；
- runtime.error；
- runtime.restarting；
- workflow.created；
- workflow.queued；
- workflow.started；
- workflow.updated；
- workflow.paused；
- workflow.resumed；
- workflow.failed；
- workflow.completed；
- workflow.cancelled；
- workflow.stage.started；
- workflow.stage.progress；
- workflow.stage.completed；
- workflow.stage.failed；
- artifact.created；
- model.loading；
- model.loaded；
- model.unloaded；
- model.download.progress；
- diagnostics.updated。

### 11.4 协议要求

- 每个请求必须有唯一 request_id；
- 每个请求必须有超时策略；
- 同一请求不得产生多个最终响应；
- 事件必须允许重复接收；
- 前端处理事件时应具备幂等性；
- 运行时重启后，前端通过状态快照重新同步；
- 协议版本不兼容时应拒绝运行并展示升级提示；
- TypeScript 和 Python 类型应基于同一份 Schema 生成或校验。

---

## 12. SQLite 数据设计

建议至少包含以下表。

### workflows

保存：

- id；
- name；
- status；
- input_uri；
- workflow_template_id；
- config_json；
- created_at；
- started_at；
- completed_at；
- updated_at；
- current_stage_id；
- error_code；
- error_message。

### workflow_stages

保存：

- id；
- workflow_id；
- stage_type；
- status；
- progress；
- attempt_count；
- started_at；
- completed_at；
- checkpoint_uri；
- error_json。

### workflow_events

保存：

- id；
- workflow_id；
- stage_id；
- event_type；
- event_data；
- created_at。

### artifacts

保存：

- id；
- workflow_id；
- stage_id；
- artifact_type；
- file_path；
- checksum；
- size_bytes；
- created_at；
- is_final。

### model_installations

保存：

- model_id；
- version；
- install_path；
- status；
- checksum；
- installed_at；
- verified_at。

### execution_attempts

保存：

- id；
- workflow_id；
- stage_id；
- attempt_number；
- device；
- model_id；
- started_at；
- completed_at；
- exit_status；
- error_json。

---

## 13. 文件目录设计

### 应用安装目录

```text
ASR Local/
├── app/
├── resources/
├── runtime-launcher/
└── defaults/
```

### 用户数据目录

```text
userData/
├── config/
├── db/
├── logs/
├── cache/
├── runtime/
├── downloads/
└── temp/
```

### 模型目录

```text
models/
├── manifests/
├── asr/
├── diarization/
├── llm/
└── cache/
```

### 用户输出目录

```text
outputs/
├── task-id/
│   ├── transcript/
│   ├── summary/
│   ├── subtitles/
│   ├── metadata/
│   └── intermediate/
```

路径不得继续依赖源码仓库或 Cargo 工程目录。

---

## 14. Python Runtime 分发方案

### 14.1 基本原则

第一阶段不依赖用户自行安装 Python。

软件应提供受控 Python Runtime，至少包含：

- Python 解释器；
- Workflow Runtime；
- 基础依赖；
- Runtime 版本信息；
- 环境校验脚本。

### 14.2 依赖分层

建议拆分：

```text
Core Runtime
├── Python
├── Workflow Runtime
├── SQLite
├── 基础工具包
└── 诊断工具

ASR Runtime Pack
├── PyTorch
├── Transformers
├── ASR 依赖
└── 音视频处理依赖

Diarization Runtime Pack
├── Pyannote
└── 对应依赖

Model Pack
├── 模型权重
├── 配置
└── Manifest
```

避免将所有模型和依赖塞入一个安装包。

---

## 15. 安全要求

### Electron 安全

必须：

- 开启 contextIsolation；
- 关闭 nodeIntegration；
- 通过 Preload 暴露白名单接口；
- 限制导航和新窗口；
- 限制外部链接；
- 校验所有 IPC 参数；
- 禁止渲染进程执行任意系统命令；
- 禁止渲染进程访问完整文件系统。

### Python 进程安全

必须：

- 仅接受受支持的协议方法；
- 校验文件路径；
- 防止目录穿越；
- 不执行来自前端的任意 Python 代码；
- 不执行未经校验的 shell 命令；
- 对输出路径和输入路径进行权限检查。

### 敏感信息

API 密钥、令牌和用户隐私数据不得写入普通日志。

---

## 16. 性能要求

### 启动性能

- 窗口应尽早展示；
- Python Runtime 启动不应阻塞界面；
- 模型不得在应用启动时无条件加载；
- 任务执行时按需加载模型；
- 空闲时根据策略卸载模型。

### 内存和显存

- Electron 不保存大型音频二进制副本；
- 文件通过路径传递；
- 大型任务结果应分页或按需读取；
- Python 负责显存回收；
- 模型切换时应可观测显存状态。

### 任务并发

第一阶段默认限制为：

- GPU 重任务单任务执行；
- 轻量阶段可串行执行；
- 批量文件进入队列；
- 后续再根据显存和模型能力扩展并发。

---

## 17. 异常恢复

### Electron 崩溃

重新启动后：

- Python Runtime 若已退出则重新启动；
- 读取数据库；
- 恢复未完成任务状态；
- 校验检查点和产物；
- 提示用户恢复。

### Python 崩溃

Electron 应：

1. 记录退出码和 stderr；
2. 标记 Runtime 状态为 unavailable；
3. 将执行中的任务标记为 interrupted；
4. 尝试重启 Runtime；
5. 完成握手；
6. 请求状态恢复；
7. 更新前端。

### 数据库异常

应支持：

- 启动前完整性检查；
- 自动备份；
- 数据库迁移；
- 损坏后只读恢复；
- 导出可恢复任务信息。

---

## 18. 兼容和迁移要求

### 18.1 用户数据迁移

需要迁移：

- 原有应用配置；
- 模型路径；
- ASR Profile；
- 总结 Profile；
- 总结模板；
- 历史输出路径；
- 已有任务结果。

### 18.2 历史任务迁移

对于旧版本输出目录：

- 扫描已有 Markdown、JSON、字幕和日志；
- 生成历史任务记录；
- 标记为 imported；
- 不要求生成完整阶段事件；
- 不修改用户原文件。

### 18.3 配置迁移

首次启动新版时：

1. 检测旧版配置；
2. 展示迁移提示；
3. 生成迁移备份；
4. 执行转换；
5. 输出迁移报告；
6. 迁移失败时保留旧配置。

---

## 19. 重构实施阶段

### 阶段一：协议和边界收敛

目标：

- 确定 Workflow Contract v2；
- 明确 Electron 与 Python 职责；
- 停止扩展旧版 Rust Lane；
- 补齐协议 Schema；
- 建立 Fake Runtime。

交付物：

- 协议文档；
- JSON Schema；
- TypeScript 类型；
- Python 类型；
- 协议测试；
- Fake Runtime。

### 阶段二：前端运行时解耦

目标：

- 移除 Vue 组件中的 Tauri 直接依赖；
- 建立 DesktopBridge；
- 保留 Tauri 适配器用于迁移期测试；
- 建立 Electron 和 Fake 适配器。

交付物：

- DesktopBridge 接口；
- ElectronBridge；
- TauriBridge；
- FakeBridge；
- 前端组件测试。

### 阶段三：Electron 最小桌面壳

目标：

- 实现 Electron Main；
- 实现 Preload；
- 实现窗口和基础文件能力；
- 启动 Python Runtime；
- 完成 Workflow v2 请求和事件通信。

交付物：

- Electron 应用可运行版本；
- Python 进程管理器；
- JSONL 客户端；
- 启动和退出机制；
- 基础日志。

### 阶段四：业务能力迁移

目标：

- 新建任务；
- 任务中心；
- 转写；
- 说话人识别；
- 总结；
- 导出；
- 暂停、恢复、取消和重试；
- 历史记录。

交付物：

- 完整核心工作流；
- SQLite 任务数据库；
- 产物管理；
- 崩溃恢复。

### 阶段五：移除旧架构

目标：

- 停止使用旧版 Rust Worker Client；
- 停止使用旧版 Lane；
- 删除 Tauri；
- 删除 Rust；
- 移除重复配置和历史逻辑。

交付物：

- 无 Rust 代码仓库；
- 单一 Python Runtime；
- 单一任务状态来源；
- 清理后的工程结构。

### 阶段六：产品化分发

目标：

- Python Runtime 分发；
- 模型管理；
- CUDA 诊断；
- 安装包；
- 自动更新；
- 用户数据迁移；
- 日志导出。

交付物：

- Windows 安装包；
- 模型安装器；
- 运行环境诊断；
- 更新和回滚机制；
- 用户迁移工具。

---

## 20. 优先级

### P0：必须完成

- Electron 主进程和 Preload；
- DesktopBridge；
- Python Runtime 启动；
- Workflow Contract v2；
- 新建任务；
- ASR 转写；
- 任务队列；
- 进度事件；
- 暂停、取消和重试；
- SQLite 任务记录；
- 结果导出；
- 基础日志；
- Windows 安装运行；
- 移除 Rust。

### P1：重要能力

- 说话人识别；
- 自动总结；
- 模型管理；
- 环境诊断；
- 检查点恢复；
- 历史数据迁移；
- API 密钥安全存储；
- 自动更新；
- 日志诊断包。

### P2：后续优化

- 工作流可视化编辑；
- 插件式工作流阶段；
- 命令行模式；
- 浏览器模式；
- FastAPI 服务模式；
- macOS 和 Linux；
- 多 GPU；
- 远程模型运行；
- 局域网访问。

---

## 21. 验收标准

### 架构验收

- 代码仓库中不再包含运行时 Rust 代码；
- Electron Main 不包含 ASR 和模型业务；
- Python 是任务状态的唯一事实来源；
- Vue 不直接引用 Electron 和 Node.js；
- 所有桌面能力通过 DesktopBridge 调用；
- 所有任务请求通过 Workflow Contract v2。

### 功能验收

用户可以：

- 启动应用；
- 导入音视频；
- 创建任务；
- 查看实时进度；
- 暂停和恢复；
- 取消任务；
- 失败后重试；
- 查看转写结果；
- 查看总结；
- 导出 Markdown 和字幕；
- 查看历史任务；
- 打开输出目录。

### 稳定性验收

以下场景不得导致任务不可恢复：

- 关闭应用；
- Electron 异常退出；
- Python 异常退出；
- 模型加载失败；
- CUDA 显存不足；
- 输出目录不可写；
- 输入文件被移动；
- 总结 API 请求失败。

### 安全验收

- 渲染进程无完整 Node.js 权限；
- API 密钥不进入前端持久化；
- IPC 参数经过校验；
- Python 不执行任意命令；
- 日志不泄露密钥。

### 数据验收

- 旧配置可迁移；
- 旧输出文件不被修改；
- 数据库升级可回滚；
- 卸载应用时默认不删除模型和用户输出。

---

## 22. 主要风险与应对措施

| 风险 | 影响 | 应对措施 |
|---|---|---|
| v1 与 v2 长期并存 | 状态和配置持续分裂 | 明确 v2 为唯一长期运行时 |
| 将 Rust 逐行改写为 Node.js | 重复保留旧架构问题 | 仅迁移操作系统能力 |
| Python Runtime 分发复杂 | 安装失败和体积过大 | 运行时、依赖和模型分层 |
| CUDA 兼容问题 | 用户无法启动模型 | 增加诊断、兼容矩阵和降级模式 |
| JSONL stdout 混入日志 | 协议解析失败 | stdout 仅输出协议，日志使用 stderr |
| Electron 权限过大 | 本地安全风险 | contextIsolation + Preload 白名单 |
| 数据库损坏 | 历史任务丢失 | 备份、完整性检测和迁移机制 |
| 模型体积过大 | 安装和升级困难 | 模型独立下载和校验 |
| 应用退出导致任务中断 | 用户体验差 | 检查点、优雅关闭和恢复机制 |
| 前端依赖运行时细节 | 后续难以扩展 | DesktopBridge 和运行时适配器 |

---

## 23. 最终目标状态

重构完成后，产品应形成如下稳定边界：

```text
Vue
只关心：
页面、交互、状态展示

Electron
只关心：
桌面系统、文件、窗口、进程、安全、更新

Python
只关心：
任务、模型、GPU、工作流、结果、恢复
```

最终项目应具备以下特征：

1. 没有 Rust；
2. 没有重复任务调度；
3. 没有多套任务状态来源；
4. 前端不绑定具体桌面框架；
5. Python Runtime 可以独立测试；
6. Workflow Contract 可以被 Electron、命令行或 Web 服务复用；
7. 应用、运行时和模型可以独立升级；
8. 软件具备可安装、可诊断、可恢复和可维护的产品化能力。
