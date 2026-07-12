# ASR Local Electron 无 Rust 重构设计（部分取代）

> The Electron migration remains historical context. Its MOSS runtime assumptions
> are superseded by `docs/superpowers/specs/2026-07-12-qwen-only-single-runtime-design.md`.

## 1. 目标

将 ASR Local 从 Tauri、Rust、Vue、Python 的四层结构迁移为 Electron、TypeScript、Vue、Python，并保持 Python Workflow Runtime 作为 ASR、说话人识别、LLM、任务调度、恢复和产物管理的唯一业务核心。

设计优先级依次为：

1. 保持现有主要功能和用户数据可用；
2. 以最小改动复用 Vue 和 Python；
3. 消除重复任务状态和业务调度；
4. 在每个迁移阶段保留可验证的回滚点；
5. 最终删除 Tauri、Rust 和 Cargo 运行路径。

本设计基于 `ASR_Local_无Rust架构重构_PRD.md`、当前 Workflow Contract v2、Python supervisor、Vue stores、Tauri commands 和 Rust worker clients 的现状审计。

## 2. 现状结论

当前代码已经具备低成本迁移所需的两个关键边界：

- Vue 层已有平台无关的 `WorkflowRuntime`、Pinia workflow store、reducer、fake runtime 和 contract types。Tauri 直接依赖集中在少量 client、adapter 和应用启动代码中。预计可原样复用约 85–90% 的前端文件和 90–95% 的前端业务逻辑。
- Python Workflow Runtime v2 已通过 UTF-8 JSONL over stdin/stdout 提供版本握手、请求响应关联、异步事件、幂等操作、SQLite 状态、恢复和 shutdown。Electron Main 可通过 Node `child_process` 直接对接，不需要重写 ASR 或 LLM pipeline。

迁移的主要工作不是重写业务，而是替换可信桌面宿主：进程生命周期、协议路由、文件和路径、配置、密钥、日志、窗口及安装打包。

## 3. 方案选择

### 3.1 采用方案：双壳并行、适配器切换

迁移期保留可运行的 Tauri 基线，在独立 Electron 壳中复用相同 Vue renderer 和 Python v2 runtime。Electron 达到功能、数据、恢复和安装四类验收门槛后，再删除 Tauri 和 Rust。

该方案的约束是：

- 只替换桌面宿主，不重写 Python pipeline；
- 不重新设计 UI；
- 不把 Rust 代码逐行翻译成 Node.js；
- 不在 Electron Main 建立第二套任务队列或状态机；
- v1 只作为临时回滚路径，不再增加能力；
- 不允许同一个任务在 Tauri、Electron、v1、v2 adapter 间切换。

### 3.2 未采用方案

原地把 `apps/desktop-tauri` 改成 Electron 会失去稳定回滚点，并让桌面壳、路径、打包、worker 生命周期问题同时出现。

完全重建 Electron 应用后整体切换可以得到更整洁的目录，但会扩大 renderer 和 Python 的重写范围，延长首个可用版本周期。

## 4. 目标架构

```text
Vue Renderer
  页面、组件、Pinia、Workflow reducer
            │ typed window.asrLocal
Electron Preload
  contextBridge、白名单 API、参数验证、事件订阅
            │ ipcRenderer ↔ ipcMain
Electron Main
  窗口、文件、路径、密钥、日志、Python 生命周期、JSONL 路由
            │ UTF-8 JSONL over stdin/stdout
Python Workflow Runtime v2
  队列、状态机、SQLite、恢复、ASR、Diarization、LLM、产物
```

### 4.1 Vue Renderer

保留现有 Vue 页面、组件、Pinia stores、workflow types、reducer、fake runtime、Markdown 编辑与预览。Renderer 只能通过 `DesktopBridge` 和 `WorkflowRuntime` 使用桌面及运行时能力。

Renderer 不得直接使用 Electron、`ipcRenderer`、Node.js、文件系统、子进程、Python 路径或明文 API key。

迁移期保持现有公开方法和 DTO，优先替换实现而不是修改页面调用。

### 4.2 Electron Preload

Preload 通过 `contextBridge` 暴露窄接口：

```ts
window.asrLocal = {
  app: {},
  dialog: {},
  files: {},
  paths: {},
  config: {},
  history: {},
  secrets: {},
  workerV1: {},
  workflowV2: {},
  diagnostics: {}
}
```

要求：

- `contextIsolation: true`；
- `nodeIntegration: false`；
- 不直接暴露 `ipcRenderer`；
- channel 使用固定白名单；
- invoke 输入和输出均做运行时校验；
- event subscribe 同步返回 `unsubscribe`；
- renderer 只能得到密钥状态和掩码；
- sender、frame 和导航来源必须校验。

### 4.3 Electron Main

建议目录：

```text
src/main/
├── app/
│   ├── lifecycle.ts
│   ├── windows.ts
│   └── paths.ts
├── ipc/
│   ├── channels.ts
│   ├── validators.ts
│   └── registerHandlers.ts
├── runtime/
│   ├── PythonProcessManager.ts
│   ├── JsonlTransport.ts
│   ├── WorkflowRuntimeClient.ts
│   └── LegacyWorkerPool.ts
├── services/
│   ├── FileService.ts
│   ├── ConfigRepository.ts
│   ├── HistoryService.ts
│   ├── SecretStore.ts
│   ├── LogService.ts
│   └── DiagnosticsService.ts
└── security/
    ├── navigationPolicy.ts
    └── pathPolicy.ts
```

Electron Main 可以保存 Python 存活状态、worker instance、pending requests、heartbeat 和 shutdown 状态，但不得保存第二套 workflow 业务状态。

### 4.4 Python Workflow Runtime

Python v2 是任务状态、调度、恢复、模型资源和产物的唯一事实来源。保留现有 JSONL protocol、`runtime.hello`、operation idempotency、workflow events、SQLite registry、list/get reconciliation 和 retry 语义。

只允许实施桌面宿主适配所必需的改动：

- 支持显式 `STATE_DIR`、`CONFIG_DIR`、`MODELS_DIR` 和 `OUTPUTS_DIR`；
- 生成真实的 worker/store instance ID；
- 修正 capability 声明；
- 统一 provider-binding 摘要算法；
- production 模式禁止静默降级到 fake。

不改写 ASR、Diarization、LLM 或模型加载实现。

## 5. 数据流和生命周期

### 5.1 启动

```text
Electron Main 启动
→ 注册 IPC 和 workflow event dispatcher
→ 创建早期可见窗口和启动状态页
→ 解析 runtime/config/models/state/output 路径
→ spawn Python v2
→ runtime.hello
→ runtime.capabilities
→ workflow.list 查询非终态任务
→ 对已知任务和 sequence gap 调 workflow.get
→ renderer store 完成同步
→ 应用 ready
```

事件监听必须先于耗时初始化注册。生产版必须启动 `pipeline-mode=production`，并断言 runtime capability 的 resolved mode 为 production；不满足时进入明确的 degraded/failed 状态，不能使用 fake 结果冒充生产结果。

### 5.2 请求和事件

Renderer 发起操作时，Preload 只转发已验证的 DTO。Electron Main 为 protocol request 生成 request ID，并为持久业务操作传递 operation ID。`JsonlTransport` 用单一 stdout reader 将 response 交给 pending map，将 event 分发给 renderer。

Renderer reducer 处理事件时：

- `incoming.sequence <= current.sequence`：丢弃旧事件；
- `incoming.sequence === current.sequence + 1`：正常归并；
- `incoming.sequence > current.sequence + 1`：调用 `workflow.get` 对账。

Renderer 不依赖事件回放重建权威状态。

### 5.3 退出和崩溃

正常退出先发送 `runtime.shutdown`，等待 stdout EOF；超过 grace period 后终止整个 Python 进程树。退出流程必须幂等，避免窗口 close 和 `before-quit` 重复执行。

Python 异常退出时，Main 记录 exit code 和脱敏 stderr，将 runtime 标记为 unavailable，重启并重新 hello/list/get。执行中的 attempt 按 Python registry 恢复为 interrupted；Electron 不自动创建新 attempt。

Electron 崩溃后重新启动也按相同 reconciliation 流程恢复。

## 6. 数据和目录

目标分层：

```text
安装目录
├── Electron 应用
└── Python Core Runtime

用户数据目录
├── config
├── db
├── logs
├── cache
└── temp

外置模型目录
├── asr
├── diarization
└── llm

用户输出目录
└── workflows/<workflow-id>
```

迁移初期继续读取现有 `config/`、`models/` 和 `outputs/`，不立即移动数据。Electron runtime 验证稳定后，再通过显式目录参数和幂等迁移器切换到产品化目录。

Python runtime、FFmpeg、DLL、原生依赖和模型不得进入 ASAR。安装目录按只读处理；配置、数据库和日志不得写入 `process.resourcesPath`。

数据迁移要求：

- 迁移前备份；
- 支持 dry-run；
- 重复运行结果一致；
- 输出结构化迁移报告；
- 失败不修改旧数据；
- legacy outputs 只读导入，不修改用户原文件；
- 卸载默认不删除模型、输出和用户数据库。

## 7. v1/v2 迁移边界

迁移期间存在两条明确分离的链路：

- v2：单 Python supervisor，事件为 `workflow-event-v2`，Python SQLite 为权威状态；
- v1：临时双 lane legacy worker，事件为 `worker-event`，仅用于功能回退。

不得合并两套 event semantics，不得把 lane 状态映射成 v2 workflow 状态，不得允许同一任务跨 adapter。v2 完成核心能力验收后停止发布 v1，随后删除 legacy host、lane UI 和相关 contract。

## 8. Rust 到 Electron 映射

| 当前 Rust/Tauri | Electron 目标 | 迁移要求 |
|---|---|---|
| `main.rs` | `app/lifecycle.ts` | 保留窗口和退出行为 |
| `tauri.conf.json` | BrowserWindow 和 builder config | 保留尺寸、最小尺寸、最大化 |
| `commands.rs` | `ipcMain.handle` handlers | 暂时保持 DTO 和错误语义 |
| `workflow_v2_client.rs` | `WorkflowRuntimeClient.ts` | 保持 JSONL v2 |
| `workflow_v2_commands.rs` | workflow IPC handlers | renderer 不感知 stdio |
| `worker_client.rs` | 临时 `LegacyWorkerPool.ts` | 只作回滚，不扩展 |
| `config.rs` | `ConfigRepository.ts` | 保持 TOML 和默认值 |
| profile/template modules | typed repositories | 保持稳定 ID 和相对路径语义 |
| `summary_api.rs` | 短期 TS service，长期 Python | 第一阶段行为等价迁移 |
| `history.rs` | `HistoryService.ts` | 先兼容扫描，后切 SQLite |
| `session_log.rs` | `LogService.ts` | Main、runtime stderr 分流 |
| `rfd` | Electron `dialog` | 仅 Main 调用 |
| explorer integration | Electron `shell` | 路径校验和显式错误 |

## 9. 七阶段迁移

### Phase 0：契约和基线

冻结 v1，完成 command/event inventory、v2 golden fixtures、路径基线、provider-binding 统一和 production/fake gate。退出条件是 Tauri 仍可运行，平台无关测试通过，所有 IPC 和 event 有明确 owner、输入、输出及错误语义。

### Phase 1：DesktopBridge

提取共享类型，建立 DesktopBridge、Tauri bridge 和 fake bridge，让 stores 只依赖接口，并由入口显式注入 runtime。退出条件是 Vue 页面测试不依赖 Tauri，renderer 除迁移 adapter 外没有 Tauri import，Tauri 产品路径仍可运行。

### Phase 2：Electron 最小壳

建立 main/preload/renderer build、BrowserWindow、IPC registry、dialog/files/shell 和 fake workflow smoke。退出条件是 Electron 可安全启动现有 Vue UI，renderer 无 Node 权限，fake workflow 能走完整 UI 流程，Tauri 未受影响。

### Phase 3：Python v2 垂直切片

实现 PythonProcessManager、JsonlTransport、WorkflowRuntimeClient、preload bridge、startup reconciliation、shutdown 和 process-tree cleanup。先通过 fake v2 contract，再做真实 MOSS production smoke。退出条件包括无遗留进程、sequence gap 可恢复、崩溃后不自动创建 attempt、真实短音频转写成功。

### Phase 4：可信宿主服务

按配置、profiles/templates、history、files、logging、SecretStore、credential broker、diagnostics 的顺序迁移 Rust 桌面能力。退出条件包括配置无损 round-trip、稳定 ID、密钥不进入 renderer 和日志、历史结果一致、中文/空格路径通过、云端认证路径通过。

### Phase 5：Vue 切换 Electron

注入 Electron bridge/runtime，保留先监听再初始化，分别处理 v1/v2 事件，增加启动与 degraded UI，覆盖创建、进度、暂停、恢复、取消、retry、revision、总结、输出和重启恢复。退出条件是 Electron 完成全部 P0 流程，三任务隔离正确，并成为默认开发入口；Tauri 仍是发布回滚版本。

### Phase 6：Windows 产品化

完成 runtime resource layout、显式数据目录、幂等迁移器、legacy history import、installer、升级/卸载、诊断包和干净环境 smoke。退出条件是无源码仓库环境可运行，升级不覆盖模型/输出，迁移可回滚，production 不可能使用 fake。

### Phase 7：清理

删除 v1 host、Tauri adapter、Rust/Cargo、Tauri capabilities 和旧脚本；将 Electron 工程正式设为 `apps/desktop`，更新 CI、文档和仓库检查。只有 Phase 6 全部通过后才执行。

## 10. 测试矩阵

### 10.1 Contract

同一组 fixtures 必须同时通过 TypeScript 和 Python：hello、version negotiation、request correlation、operation idempotency、event envelope、sequence、submit/list/get/control/retry、artifact revision、credentials、shutdown、malformed JSON、stdout pollution、oversize、timeout 和 EOF。

### 10.2 Electron Main

覆盖 executable 选择、开发/打包路径、环境变量、pending map、timeout、event routing、stderr 脱敏、shutdown 幂等、异常退出、path policy、atomic config write、secret store 和 IPC sender validation。

### 10.3 Renderer

覆盖 reducer、store 初始化、先订阅再 list、sequence gap、多任务和 attempt 隔离、runtime unavailable、credentials required、interrupted retry、artifact revision 和 stale summary。

### 10.4 集成和故障注入

用 fake Python runtime 做 Electron Main→Preload→Renderer→Runtime 的完整闭环。注入 Python 启动失败、hello 不兼容、timeout、乱序、丢事件、中途退出、中文 stderr、shutdown 无响应、配置损坏和输出不可写。

### 10.5 真实运行

至少覆盖 CPU 和 CUDA 短音频、MOSS production、总结 no-auth/bearer、缺少凭据、模型缺失、CUDA OOM、取消、retry、Electron/Python 崩溃恢复、中文和空格路径、三 workflow 加第四个排队，以及安装环境无源码运行。

## 11. 发布阻断条件

以下任一成立时，不得删除 Tauri：

- production runtime 可能使用 fake；
- Electron 退出遗留 Python 或 FFmpeg；
- workflow 恢复依赖 renderer 本地状态；
- API key 能进入 renderer；
- 安装包依赖源码目录；
- 用户数据迁移不可回滚；
- 主要工作流未通过真实推理 smoke；
- Tauri 和 Electron 对同一 v2 fixture 产生不同业务状态。

## 12. 风险登记

| 风险 | 优先级 | 控制措施 |
|---|---:|---|
| 打包后 Python 根路径失效 | P0 | 显式目录环境变量，不依赖源码 parents |
| ASAR 内原生依赖不可执行 | P0 | runtime、FFmpeg、DLL、模型 external/unpack |
| production 静默降级 fake | P0 | 固定 production 参数和 capability 断言 |
| 退出遗留进程树 | P0 | Windows Job Object 或等价 tree cleanup，加 E2E |
| workflow 双事实来源 | P0 | Python SQLite 权威，Main 只做 transport |
| secret 泄露 | P0 | OS 安全存储、短期 grant、日志脱敏 |
| provider binding 不一致 | P0 | 单一规范和跨语言 golden fixtures |
| v1/v2 事件混合 | P1 | 分 channel、分 adapter、禁止跨 adapter |
| 配置迁移损坏 | P1 | backup、dry-run、幂等和迁移报告 |
| IPC 权限过宽 | P1 | context isolation、schema、sender 校验 |

## 13. 工作包

### E0 契约和基线

- IPC command/event inventory；
- Workflow v2 golden fixtures；
- provider-binding 统一；
- production/fake capability gate；
- 路径和数据基线。

### E1 DesktopBridge

- DesktopBridge types；
- Tauri 和 fake bridge；
- store 显式依赖注入；
- 移除 renderer runtime sniffing。

### E2 Electron 骨架

- main/preload/renderer build；
- BrowserWindow lifecycle；
- IPC registry 和 validation；
- dialog/files/shell；
- Electron smoke。

### E3 Python v2

- PythonProcessManager；
- JsonlTransport；
- WorkflowRuntimeClient；
- preload 和 Vue workflow adapter；
- startup reconciliation；
- shutdown/process-tree cleanup；
- MOSS production smoke。

### E4 宿主服务

- config repositories；
- profiles/templates；
- history；
- logging；
- SecretStore 和 credential broker；
- diagnostics。

### E5 产品切换

- 核心用户流程 E2E；
- 故障注入；
- 三任务稳定性；
- Electron 默认开发入口；
- Tauri 回滚脚本。

### E6 打包和数据迁移

- runtime resource layout；
- 显式目录；
- 配置迁移器和 legacy import；
- Windows installer；
- 安装/升级/卸载 smoke；
- 诊断包。

### E7 清理

- 移除 v1 host 和 Tauri adapter；
- 删除 Rust/Cargo；
- 更新启动脚本、CI 和文档；
- 无 Tauri/Rust 仓库检查；
- 最终验收报告。

每个 issue 必须包含输入输出、不允许改变的行为、fixtures、测试命令、退出标准、回滚方式、数据路径以及密钥/真实模型影响。

## 14. 建议排期和人员边界

建议按纵向切片推进，避免 TypeScript、Rust、Python 三支队伍各自完成后再集成。

- 第 1 周：E0、E1；
- 第 2 周：E2；
- 第 3–4 周：E3；
- 第 4–5 周：E4、E5；
- 第 6–7 周：E6；
- 稳定发布验证后：E7。

每一时刻只允许一个生产 adapter 成为某个任务的 owner。主集成线程负责跨层 contract、数据兼容和发布门槛；UI、Electron host、Python runtime 可做独立只读调查和非重叠实现，但共享 schema 和入口文件由主集成线程维护。

## 15. 完成定义

重构完成必须同时满足：

1. 生产仓库和构建链不再包含 Rust/Tauri runtime；
2. Electron Main 不包含 ASR、LLM、GPU、任务队列或 workflow 状态机；
3. Python 是 workflow 状态唯一事实来源；
4. Vue renderer 不直接引用 Electron、Node 或 Python；
5. 所有桌面能力通过 typed DesktopBridge；
6. 所有任务操作通过 Workflow Contract v2；
7. Windows 安装包可在无源码环境运行；
8. 应用、Python runtime、模型和用户数据独立管理；
9. 旧配置、历史和输出完成无损兼容验证；
10. 真实推理、崩溃恢复、数据迁移、安全和进程清理验收全部通过。
