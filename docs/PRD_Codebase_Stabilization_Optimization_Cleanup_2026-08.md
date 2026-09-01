# PRD：ASR Local 代码库稳定性修复、架构优化与冗余清理

## 1. 文档信息

- 文档状态：Draft，待产品决策项确认后进入实施
- 版本：v0.1
- 日期：2026-08-28
- 适用代码库：ASR Local Electron + Vue + TypeScript + Python Workflow Runtime v2
- 文档目标：将 2026-08 代码审计结论转化为可排期、可验收、可回滚的修复与优化需求
- 目标读者：产品负责人、桌面端工程师、Python Runtime 工程师、测试与发布负责人

## 2. 执行摘要

当前主产品链路已经迁移到 Electron 桌面壳、Vue Renderer、TypeScript Desktop Services 和 Python Workflow Runtime v2。现有代码可以通过 TypeScript 检查、前端测试和 Worker 合同测试，但审计发现若干会影响正确性、数据一致性和可维护性的结构性问题：

1. Workflow 合同在 JSON Schema、TypeScript、Python codec、状态机和 Registry 之间发生漂移，其中 completed_with_warnings 已形成真实故障路径。
2. operation 幂等记录与 Workflow 状态变更不在同一事务中，clear、control、retry 等命令存在崩溃窗口、伪成功和敏感快照残留风险。
3. Runtime 启动、握手、恢复和凭据等待存在无法自动收敛的状态，可能导致应用长期停留在 starting 或 waiting_for_secret。
4. Renderer 对完整快照的写入没有统一经过 sequence 保护；延迟的 list 或命令响应可能覆盖较新的事件。
5. 受管产物可以绕过修订、SHA 和 stale 语义被直接覆盖；自定义输出目录授权又无法跨应用重启恢复。
6. 本地 V2 链路仍依赖 legacy job_runner，历史页面存在 Registry 与目录递归扫描双数据源，多个大模块承担过多职责。
7. 测试发现范围会误扫构建产物或打包 Runtime，造成重复测试、超大规模错误收集和错误的质量信号。
8. 已存在一批高置信度冗余代码，但 Cloud ASR、prompt.preview、timeline、旧迁移逻辑等仍需要产品或外部调用决策，不能仅凭静态未使用就删除。

本 PRD 将工作分为三层：

- P0：修复会破坏合同、状态、幂等或生命周期收敛的发布阻断问题。
- P1：收敛状态权威、产物权限、测试边界和可观测性，并清理高置信度冗余。
- P2：拆分大模块、结束迁移态、统一版本与分发策略，并处理需要产品决策的功能链。

## 3. 审计基线

### 3.1 当前架构

主要调用链为：

UI action → Pinia store → Preload bridge → Electron Main → WorkflowRuntimeClient → Python Server/Codec → WorkflowSupervisor → Registry/Pipeline → Event → Store/UI

关键目录：

- apps/desktop-electron/src：Vue UI、Pinia store、Renderer 状态
- apps/desktop-electron/electron：Electron Main、Preload、HostServices、Runtime Client
- apps/worker-python/app：Python Workflow Runtime、Registry、Pipeline、Summary、模型管理
- contracts/workflow-v2：跨语言合同 Schema 与 fixtures

### 3.2 已验证基线

- npm run typecheck：通过。
- npm run build：通过。
- 前端 Vitest：49 个测试通过，但包含 dist-electron 和 release-electron 内生成测试的重复执行，不能作为最终测试边界。
- Worker contract_v2：216 个测试通过，另有 37 个 subtests 通过。
- npm 直接生产依赖均有引用，当前没有可安全移除的直接依赖。
- Python 依赖一致性检查通过；部分推理依赖属于上游运行时依赖，不能仅凭静态 import 删除。
- 当前审计环境中 bundled Python Runtime 本地文件合计约 3.7 GB，release 解包目录约 6.3 GB；数值会随依赖和构建环境变化，只作为 REL-002 的对照基线。

### 3.3 未验证基线

- 真实 GPU、模型权重和代表性长音频端到端执行。
- Electron Builder 完整打包后的首次启动、任务执行、退出和重启恢复。
- 自定义输出目录跨重启后的查看、编辑和修订。
- CPU/GPU Runtime 分发体积优化后的新机器安装。
- Cloud ASR 是否仍属于正式产品范围，以及是否存在仓库外 run_job 调用方。

## 4. 问题陈述

### 4.1 合同不是单一权威

workflow-snapshot.schema.json、TypeScript 类型、Python codec、状态机和 Registry 分别维护枚举或字段约束。completed_with_warnings 已在 Schema、TypeScript 和状态机中合法，也会被 Supervisor 生成，但 codec 和 Registry 终态集合遗漏它。summary-draft Schema 同样未包含 Runtime 实际要求的 policy_snapshot。

直接影响是：任务可能在 Registry 中完成，却无法正确编码最终事件，也无法被 clear。

### 4.2 命令幂等与数据变更不具备原子性

clear 当前先保存成功 operation，再删除 Workflow。若删除失败，重复使用相同 operation_id 会返回成功，但数据仍存在。control、retry 等命令也将状态保存与 operation 结果拆为多个事务。operations 表没有 workflow_id 外键或清理策略，submit 的完整结果还可能长期保留参考文档正文。

### 4.3 生命周期存在“半启动”和“无限等待”

hello 或 recovery 失败后，Main 和 Python 两侧可能已经标记为 started 或 handshaken，却没有完整回滚。凭据请求虽然带 TTL，但等待 Future 没有超时；Main 授权失败也只写日志，不返回明确拒绝。

### 4.4 Renderer 可能发生快照倒退

事件 reducer 会检查 sequence，但 list、refresh 和命令 response 会直接覆盖 store。先到达的新事件可能被后到达的旧 list 快照覆盖。Runtime 重启后也没有自动完成 capabilities、list 和选中任务详情的重新对账。

### 4.5 产物权威和文件授权不一致

任务中心已经具备 staged file、SHA 和 registerRevision 流程，但 MarkdownView 的保存仍可直接覆盖受管产物，绕过修订和 stale 传播。自定义输出目录授权只保存在 Main 进程内存，应用重启后持久 Workflow 仍在，但产物可能无法读取或编辑。

### 4.6 迁移态和大模块增加变更风险

V2 本地 ASR adapter 仍调用 legacy job_runner；HostServices 同时承担迁移、TOML、Secret、Catalog、可信快照和历史扫描；WorkflowSupervisor、openai_compatible、job_runner、WorkflowView 和 styles.css 体量较大。Cloud ASR、preview、timeline 等能力在合同、Worker、设置和 UI 之间处于不一致的启用状态。

## 5. 产品目标

### 5.1 目标

1. 任何合法 Workflow 终态都能跨 Schema、IPC、Registry、事件和 UI 一致处理。
2. 所有有副作用的 Workflow 命令具备持久幂等效果，状态变更与 operation 结果原子提交。
3. Runtime 启动、失败、重启、凭据超时都能在有限时间内进入明确状态。
4. Renderer 只接受单调前进的 WorkflowSnapshot，并能在 Runtime 重启后自动对账。
5. 受管产物只能通过修订流程变更；自定义输出根在重启后仍可安全访问。
6. 测试只扫描源码测试，发布门禁能覆盖合同、故障注入、重启恢复和真实打包启动。
7. 删除高置信度冗余，并为条件性功能建立可审计的去留决策。
8. 在不改变现有功能行为的前提下逐步拆分大模块和 legacy 兼容层。

### 5.2 非目标

- 本项目不重写为新桌面框架。
- 不在本轮替换 ASR 或总结模型。
- 不移动、删除或重新下载 models 下的模型权重。
- 不直接删除 legacy 数据迁移、DPAPI、旧 summary policy fallback 或 Registry 升级备份。
- 不在产品决策前删除 Cloud ASR、split_stereo、prompt.preview、timeline 或仓库外可能使用的 run_job 接口。
- 不以追求文件行数为目的进行无业务边界的拆分。

## 6. 设计原则

1. 合同优先：先修合同和测试，再改消费者。
2. 快照权威：WorkflowSnapshot 是状态权威，事件和响应只负责传递。
3. 单调收敛：所有写入 store 的完整快照都必须经过同一 sequence 与 attempt 校验。
4. 事务幂等：业务变更和 operation 结果必须属于同一提交。
5. 受管产物不可原地覆盖：用户编辑生成新修订，旧产物继续可审计。
6. 删除需要证据：生产调用图、合同兼容、测试、打包和迁移窗口全部满足后才删除。
7. 渐进迁移：先建立新边界和适配层，再迁移调用，最后删除旧实现。
8. 不牺牲本地优先、安全边界和现有模型配置。

## 7. 目标架构

目标架构保留现有技术栈，并收敛为五个权威边界：

1. Contract Authority
   - contracts/workflow-v2 是方法、状态、字段和 fixture 的唯一语言无关来源。
   - TypeScript 和 Python 类型通过生成或 CI 校验与 Schema 保持一致。

2. Transactional Command Service
   - Supervisor 负责编排，Registry Command Service 负责在单事务中完成 Workflow、Event、Artifact 和 Operation 变更。
   - operation 记录只保存重放所需的最小结果，不复制大段参考文档或秘密。

3. Renderer Snapshot Gateway
   - list、get、event 和 command response 全部进入 applySnapshot。
   - applySnapshot 统一校验 workflow_id、attempt_id、sequence 和不变量。

4. Managed Artifact Service
   - Registry 记录产物身份、修订、SHA、来源和 stale 关系。
   - 文件读写授权由可信 Main 根据持久任务输出根恢复，Renderer 不自行扩大授权。

5. Modular Runtime Services
   - 将合同、命令、生命周期、历史索引、本地转录、云端转录和总结策略分离。
   - 高频进度事件先合并和限频，再持久化和推送。

## 8. 需求总览

| ID | 优先级 | 主题 | 发布属性 |
| --- | --- | --- | --- |
| FIX-001 | P0 | 修复 completed_with_warnings 与 summary draft 合同漂移 | 发布阻断 |
| FIX-002 | P0 | operation 幂等、事务原子性和数据保留 | 发布阻断 |
| FIX-003 | P0 | Runtime 启动、握手、恢复回滚 | 发布阻断 |
| FIX-004 | P0 | 凭据请求超时与拒绝闭环 | 发布阻断 |
| FIX-005 | P1 | Renderer 快照单调合并与重启对账 | 稳定版门禁 |
| FIX-006 | P1 | 受管产物修订与 stale 语义 | 稳定版门禁 |
| FIX-007 | P1 | 自定义输出根跨重启授权 | 稳定版门禁 |
| ARCH-001 | P1 | Contract 单一来源与版本策略 | 稳定版门禁 |
| ARCH-002 | P1 | Registry Command Service 与 retention | 稳定版门禁 |
| ARCH-003 | P1 | 历史与产物单一数据源 | 稳定版门禁 |
| ARCH-004 | P1 | 进度事件背压与 UI 更新节流 | 性能门禁 |
| ARCH-005 | P2 | 拆分大模块和迁移 local pipeline | 后续演进 |
| ARCH-006 | P1 | 统一 Runtime 路径、能力和版本信息 | 稳定版门禁 |
| CLEAN-001 | P1 | 高置信度无消费者代码清理 | 稳定版门禁 |
| CLEAN-002 | P2 | 条件性功能去留与兼容层退役 | 需决策 |
| CLEAN-003 | P2 | 文档、脚本和本地生成物治理 | 仓库治理 |
| TEST-001 | P0 | Vitest 与 pytest 发现边界 | 发布阻断 |
| TEST-002 | P0 | 合同与 fault-injection 回归矩阵 | 发布阻断 |
| TEST-003 | P1 | 重启、路径授权、产物修订 E2E | 稳定版门禁 |
| REL-001 | P1 | 完整 Electron package 启动验收 | 发布阻断 |
| REL-002 | P2 | Runtime 分发体积与按需方案 | 发布优化 |
| MIG-001 | P1 | Registry Schema 与 operation 数据迁移 | 稳定版门禁 |
| MIG-002 | P2 | legacy job_runner 兼容链退役 | 需决策 |

## 9. 详细需求

### FIX-001：合同漂移修复

动机：

- completed_with_warnings 是合法且可实际产生的终态，但 Python codec 和 Registry 的终态集合不完整。
- summary-draft Schema 与 Runtime 的 policy_snapshot 要求不一致。

需求：

1. 将 completed_with_warnings 纳入 codec 编解码、Registry 终态、clear、retry、UI 展示和合同 fixtures。
2. 明确 policy_snapshot 属于可信 Main 注入后的 accepted draft，或拆分 Renderer Draft 与 Trusted Draft 两个 Schema；不得继续让同一 Schema 同时表达两种结构。
3. CI 必须逐一验证 Schema、fixtures、Python codec 和 TypeScript 类型的一致性。
4. 非法状态必须在信任边界返回结构化合同错误，不得静默丢事件。

验收标准：

- warning 任务能够编码最终事件、在 UI 中显示、重启后恢复并成功 clear。
- completed_with_warnings fixture 同时通过 JSON Schema、Python codec 和 TypeScript 合同测试。
- summary draft 的 Renderer 输入和 Worker 输入各有无歧义的 Schema。
- 搜索状态与方法 allowlist 时，不再存在消费者自建但未经一致性测试的重复枚举。

依赖与风险：

- 需要先决定采用代码生成还是 CI 对照；本需求不强制具体生成工具。
- Schema 变更需要合同版本兼容测试，不得破坏已有 Registry 快照。

### FIX-002：operation 原子性、幂等与保留策略

动机：

- clear 的 operation 成功记录可能早于真实删除。
- control、retry 等存在状态变更与 operation 记录分离的崩溃窗口。
- operation 可能永久保存完整 submit 快照。

需求：

1. 对 submit、clear、control、retry、resummarize、registerRevision 建立统一事务模板。
2. 同一事务内完成预期 attempt 校验、状态或产物变更、事件序号推进和 operation 结果写入。
3. operations 表增加 workflow_id 关联、payload digest、状态、创建时间和必要索引。
4. operation 结果只保存幂等重放所需的最小响应；禁止保存明文凭据，默认不得复制参考文档正文。
5. 明确 Workflow clear 后 operation 的级联删除、脱敏或短期保留策略。
6. Renderer 在请求超时或连接中断后，必须能使用原 operation_id 重试同一逻辑操作。
7. 相同 operation_id 但 canonical payload 不同必须返回冲突错误。

验收标准：

- 在 Registry commit 前后、operation insert 前后和 response 写出前后注入故障，不产生重复 Workflow 或伪成功。
- 丢失 submit response 后以原 operation_id 重试，只得到同一 workflow_id。
- clear 失败不会留下成功 operation；clear 成功后按策略清除或脱敏关联 operation。
- 数据库检查确认 operation 中不存在 secret，参考文档正文不被无期限复制。

依赖与风险：

- 依赖 MIG-001 数据库迁移。
- 必须保存升级前备份，并验证旧数据库可升级、升级失败可回滚。

### FIX-003：Runtime 生命周期回滚

需求：

1. WorkflowRuntimeClient 采用明确状态机：stopped、starting、ready、stopping、failed。
2. hello、进程启动或初始 capabilities 失败时，必须拒绝 pending 请求、清理监听器、终止子进程并回到可重试状态。
3. Python Server 仅在 Supervisor start 与 recovery 全部成功后设置 handshaken。
4. Supervisor 仅在恢复完成后设置 started；失败时释放已分配资源。
5. Runtime 意外退出后，Main 发送结构化 runtime-status，Store 自动进入重连与对账流程。

验收标准：

- 注入 hello、Supervisor start 和 recovery 异常后，下一次 capabilities 请求可以干净重启。
- 不存在 child 仍存活但 Client 误认为已就绪的半启动状态。
- 退出时 pending request 均收到确定错误，进程和模型关闭链没有悬挂。

### FIX-004：凭据等待闭环

需求：

1. SecretProvider 的 Future 必须由超时控制，TTL 到期后主动结束等待。
2. Main 在授权失败、binding 不匹配、用户取消或安全存储错误时，向 Worker 返回结构化拒绝。
3. Workflow 根据错误类型进入可重试 failed、保持 waiting 并提示操作，或被取消；不得无限等待。
4. 凭据错误和 secret 内容不得进入事件、Registry 或日志。

验收标准：

- 授权拒绝和超过 TTL 后，Workflow 在规定时间内离开 waiting_for_secret。
- 迟到 grant 被拒绝且不会唤醒错误 attempt。
- 日志与数据库扫描未发现 secret 内容。

### FIX-005：Renderer 单调快照与重启对账

需求：

1. 新建 applySnapshot 作为 Store 写入完整 WorkflowSnapshot 的唯一入口。
2. event、list、get、submit、control、retry、resummarize 和 registerRevision response 全部使用该入口。
3. 拒绝 sequence 较小的快照；对 attempt 不匹配、顶层路由字段不一致和非法状态记录诊断并触发 get 对账。
4. Runtime ready 或 restarted 后自动刷新 capabilities、list 和当前选中任务详情。
5. configure 完成前不得仅以 runtime 对象非空表示可用。

验收标准：

- 人工让 event N 先于 list N-1 到达，最终 Store 保持 N。
- Runtime 重启后无需用户刷新即可恢复任务、能力和选中详情。
- 首次收到未知 Workflow 事件时也执行完整不变量校验。

### FIX-006：受管产物修订

需求：

1. MarkdownView 识别 Registry 管理的 Artifact，不允许直接覆盖原文件。
2. 编辑保存采用 staged file → SHA → registerRevision。
3. 转录稿新修订必须使基于旧修订的总结标记为 stale。
4. 非 Registry 管理的普通 Markdown 可保留直接保存，但 UI 必须明确区分。
5. Registry 与文件写入失败时不得留下指向不存在文件的修订记录。

验收标准：

- 编辑任务转录稿后生成新 revision，旧文件和旧 SHA 保持不变。
- 旧总结在 UI 和 Registry 中均显示 stale。
- 普通 Markdown 的现有编辑体验不受影响。

### FIX-007：持久输出根授权

需求：

1. 自定义输出根由可信 Main 持久记录，或从受信任 WorkflowSpec/Registry 重建最小授权。
2. 启动时只恢复仍被现有 Workflow/Artifact 引用的根。
3. 所有读写仍需做规范化路径和 root containment 校验。
4. 用户移除授权时，不删除文件；现有任务显示需要重新授权。

验收标准：

- 在默认目录之外完成任务，重启后仍可查看、编辑、修订和定位产物。
- 路径穿越、符号链接逃逸和未经授权的新路径被拒绝。
- 不扩大到整个磁盘或用户主目录。

### ARCH-001：Contract 单一来源

需求：

1. 指定 contracts/workflow-v2/schemas 为合同权威。
2. 建立生成或验证流程，覆盖状态、方法、请求、响应、事件和能力声明。
3. 合同版本采用一套明确规则，区分 protocol version、runtime implementation version、应用版本和打包资源版本。
4. 删除前先替换散落的手写 allowlist，保留信任边界的运行时校验。

验收标准：

- CI 能在任一语言遗漏合法状态或字段时失败。
- 合同 fixture 是 TS 与 Python 测试的共同输入。
- 版本信息在 About、hello 和构建元数据中来源明确且可追踪。

### ARCH-002：Registry Command Service

需求：

1. 从 Supervisor 提取事务命令边界，Supervisor 不直接拼接多个独立 Registry 操作。
2. Command Service 返回已持久化的 Snapshot/Event/Operation 结果。
3. 操作需要明确预置条件、冲突码、重试语义和审计字段。
4. 为 events、operations、artifacts 定义保留和清理策略。

验收标准：

- Supervisor 测试可通过 fake command service 覆盖调度逻辑。
- Registry 集成测试覆盖所有业务命令的一致提交和回滚。

### ARCH-003：历史与产物单一数据源

需求：

1. 新 Workflow 历史以 Registry/Artifact index 为权威。
2. 旧 Markdown 历史通过一次性或增量导入进入索引，不在每次打开页面时递归扫描全部目录。
3. HistoryView 统一展示转录、总结、修订和来源类型。
4. 从历史打开 transcript 时进入 transcript 视图，不得默认进入 summary。

验收标准：

- 历史页面不再同时维护 Registry list 和全量递归扫描两份可冲突数据。
- 大量历史文件时，首次导入和后续增量加载有可观测耗时。
- transcript/summary 导航目标正确。

### ARCH-004：进度背压

需求：

1. Worker 对高频 progress 进行合并、时间节流或变化阈值过滤。
2. 持久化进度与 UI 动画频率分离，不为每个底层 callback 创建独立持久化任务。
3. Renderer 对排序、过滤和深度 watch 做最小化更新。
4. 最终阶段、错误、检查点和终态事件不得被节流丢失。

验收标准：

- 代表性长音频执行时，progress 持久化和渲染次数有明确上限。
- UI 保持可交互，终态与关键检查点零丢失。

### ARCH-005：大模块拆分与 local pipeline 迁移

拆分对象：

- apps/worker-python/app/summary/openai_compatible.py
- apps/worker-python/app/pipeline/job_runner.py
- apps/worker-python/app/workflow/supervisor.py
- apps/desktop-electron/electron/hostServices.ts
- apps/desktop-electron/src/features/workflow/WorkflowView.vue
- apps/desktop-electron/src/styles.css

需求：

1. 先按稳定职责提取模块和接口，再迁移调用，不做行为重写式拆分。
2. local V2 adapter 逐步改为调用共享 domain pipeline，最后才退役 legacy job_runner 本地兼容入口。
3. Summary 按 provider transport、上下文策略、trace/audit、repair 和 artifact 输出拆分。
4. HostServices 按 config、catalog、secret、trusted draft、history 分离。
5. WorkflowView 按 draft、task list、detail、artifact actions 和 timeline 分离。

验收标准：

- 拆分前后合同 fixture、核心测试和代表性 E2E 行为一致。
- 模块依赖方向不形成 Renderer → Worker internal 或 Pipeline → UI 的反向依赖。
- MIG-002 完成前，现有 local V2 链仍保持可用。

### ARCH-006：Runtime 能力、路径与版本统一

需求：

1. Main 只解析一次 Python executable，并显式传给 WorkflowRuntimeClient。
2. worker_health_check 与 workflow_v2_capabilities 合并为一个权威能力源。
3. capabilities 必须声明真实可用的 methods、pipeline profiles、audio strategies 和 prompt preview；UI 由能力数据决定显示与可提交项，不再用硬编码开关宣称或隐藏能力。
4. Settings 与 Workflow Store 不再各自缓存互相漂移的健康状态。
5. model_root 必须二选一：真正成为模型相对路径根，或从配置和 UI 移除。
6. package version、Python project version、hello runtime_version 和 bundled runtime version 建立映射。

验收标准：

- 缺少项目 .venv 时不会静默选择不可控的系统 Python，除非配置明确允许。
- UI 中显示的能力、版本和实际启动资源一致。
- 未声明的能力不可提交；已声明的能力必须存在至少一条可通过的端到端执行链。

## 10. 清理需求与分级

### 10.1 CLEAN-001：高置信度清理候选

以下项目在当前仓库生产调用图中没有有效消费者，允许进入删除 PR，但每一项仍需通过 typecheck、相关测试和 package 启动门禁：

Renderer 与 Desktop：

- appStore.markdown.search。
- appStore.summary.prompt 及 applyTemplate 中仅写不读的赋值。
- workflowStore.subscribed。
- reduceEvent 的公共暴露；内部 reducer 保留。
- App.vue 中当前不可达的 transcript/summary currentView 分支。
- 没有实际差异的 .markdown-editor.large CSS。
- Renderer IPC 命令 workflow_v2_shutdown；Python runtime.shutdown 和 Main before-quit 清理必须保留。
- HostServices.secretForProfile；生产路径使用 credentialGrant。

Python：

- 未使用的 SummaryResult。
- _section_has_table_like_line、_conflict_table_rows、_repair_untraceable_conflict_rows、_chunk_text。
- clear_job_control。
- _NoopInferenceHook 与 _LOCAL_INFERENCE_LANE 测试兼容空实现；删除时同步更新测试 seam。
- close_wait_timeout_seconds 兼容别名。
- pipeline/interfaces.py 中无消费者的 DiarizationProvider 与 SegmentTranscriber Protocol。
- workflow.get 固定为空且无消费者的 attempt_history。
- ~~空的根 tests/__init__.py。~~ 已在 2026-09-02 清理。

条件性缩减：

- AppInfo 中 Renderer 未读取的字段，需先确认没有仓库外 Preload consumer。
- model_root 不得直接删除，必须按 ARCH-006 完成“实现或移除”决策。

验收标准：

- 每批删除提供 rg 调用证据和受影响测试清单。
- typecheck、源测试、Worker tests、build 和 packaged smoke 全部通过。
- 不删除用于测试隔离的 FakeWorkflowRuntime。

### 10.2 CLEAN-002：需要决策后才能清理

以下项目不得在本 PRD Draft 状态下删除：

1. prompt.preview 全链路：生产 UI 未调用，但合同、fake、fixture 和 Server 仍存在。
2. workflow.get/timeline：UI 能渲染 timeline，但现有 refresh 未请求详情。
3. Cloud ASR：合同、Profile、Credential 和 Worker 存在，Workflow UI 当前隐藏。
4. split_stereo：合同和音频层接受该策略，但当前 V2 local adapter 实际采用 mixdown；必须选择补齐多声道语义或撤销能力声明。
5. job_runner 的旧 CloudAsrClient 分支与 TaskSpec.cloud_asr_profile：桌面 V2 不可达，但可能存在仓库外 run_job 调用。
6. worker_health_check：只有在 Settings 迁移到统一 capabilities 后删除。
7. --contract v2：当前只有一个实现，但启动约定和文档仍公开使用。
8. Host/Registry legacy migration：迁移窗口结束前保留。
9. summary policy legacy fallback：历史 Registry 快照仍可能依赖。
10. apps/worker-python/scripts/probe_chunked_runtime.py：确认无运维用途后归档。
11. electron/beforeBuild.cjs：完成 Electron Builder 对照验证后处理。

每项决策必须记录：

- 保留、补齐 UI、标记 deprecated 或删除。
- 产品负责人、截止版本、外部调用验证方式。
- 数据和配置迁移方案。
- 删除后的回滚方式。

### 10.3 CLEAN-003：文档、脚本与生成物治理

需求：

1. docs/legacy 和标记 superseded/historical 的旧设计文档默认归档，不默认永久删除。
2. 根目录旧 Tauri/Workflow PRD 在建立文档索引后归档，保留审计历史。
3. 多个启动脚本先统一到一个共享启动入口，再删除重复包装。
4. output/playwright 等生成物加入合适的 ignore 规则；删除现有未跟踪文件需用户单独确认。
5. apps/desktop-tauri 本地忽略目录已在 2026-09-02 经用户明确授权清理；本地 exclude 规则同步移除，避免以后隐藏架构回退残留。

### 10.4 明确保留

以下内容不得作为冗余删除：

- contracts/workflow-v2 schemas 与 fixtures。
- FakeWorkflowRuntime。
- Workflow 和 runtime-status 事件监听。
- job_runner、audio、exporters、schemas 中仍被 local V2 使用的核心链路。
- runtime.shutdown、Main before-quit、GPU dispatcher 与 transcriber close。
- credentialGrant、SecretBroker 和 summary credential 流程。
- legacy config/output/DPAPI migration。
- summary policy 旧快照 fallback。
- Registry .pre-v1.bak 保护逻辑。
- models 下的模型权重和本地配置假设。

## 11. 测试与质量门禁

### TEST-001：测试发现边界

1. Vitest 只 include 源 TypeScript/Vue 测试，排除 dist、dist-electron、release-electron、runtime 和 node_modules。
2. pytest 在 apps/worker-python/pyproject.toml 声明 testpaths，或 CI 使用唯一固定命令。
3. 根目录执行测试不得递归扫描打包 Runtime。

验收标准：

- 前端测试不再执行生成的 credentialGrant.spec.js 副本。
- pytest 收集数量与 apps/worker-python/tests 源测试规模一致。

### TEST-002：合同和故障注入

必须新增：

- completed_with_warnings encode、event、restart、clear。
- summary Renderer Draft 与 Trusted Draft Schema。
- operation 同 ID 同 payload 重放。
- operation 同 ID 不同 payload 冲突。
- Registry commit、operation insert、response write 故障注入。
- hello、Supervisor start、recovery 失败后的再次启动。
- secret grant 拒绝、超时和迟到授权。
- list/event 乱序与 Runtime 重启对账。

### TEST-003：端到端矩阵

| 场景 | CPU | GPU | 开发模式 | 打包模式 |
| --- | --- | --- | --- | --- |
| 本地 ASR 完成 | 必测 | 必测 | 必测 | 必测 |
| completed_with_warnings | 必测 | 抽测 | 必测 | 必测 |
| 总结成功与失败重试 | 必测 | 不适用 | 必测 | 必测 |
| 应用中断与恢复 | 必测 | 抽测 | 必测 | 必测 |
| 自定义输出根重启访问 | 必测 | 不适用 | 必测 | 必测 |
| 受管产物修订与 stale | 必测 | 不适用 | 必测 | 必测 |
| 凭据拒绝与超时 | 必测 | 不适用 | 必测 | 必测 |
| 三任务交错和进度压力 | 必测 | 必测 | 必测 | 抽测 |

## 12. 数据迁移与兼容

### MIG-001：Registry 迁移

需求：

1. 升级前生成明确命名的数据库备份，不覆盖现有 .pre-v1.bak。
2. operations 增加 workflow_id、payload_digest、created_at、必要状态和索引。
3. 迁移旧 operation 时对完整 snapshot 或 reference content 做删除、脱敏或受限保留。
4. 删除 Workflow 时根据已批准 retention 策略处理 event、artifact 和 operation。
5. 迁移脚本可重复执行，失败不改变原数据库。

验收标准：

- 使用旧版本真实结构样本完成升级、启动、查询、重试和 clear。
- 升级失败后旧版本或恢复工具仍可读取备份。

### MIG-002：legacy job_runner 退役

前置条件：

1. 生产入口调用图确认无仓库外依赖，或提供兼容 CLI。
2. local V2 已迁移到共享 domain pipeline。
3. 代表性本地 ASR 的产物、进度、取消、暂停和错误码与迁移前等价。
4. Cloud ASR 已完成产品去留决策。

只有满足全部条件后，才删除 legacy 分支和相关兼容字段。

## 13. 发布与回滚

### REL-001：发布门禁

候选版本必须同时满足：

1. 所有 P0 需求验收通过。
2. npm run typecheck、npm run build、源范围 Vitest 和 Worker contract_v2 全部通过。
3. Electron Builder 产物包含 Renderer、Main、Preload 和外部 Python Runtime。
4. 在干净或等价新机器环境完成启动、一次本地转录、一次总结、退出和重启恢复。
5. 真实模型/GPU 测试没有新增阻断；torchcodec 警告需要记录影响和处置，不得被静默忽略。
6. 数据库升级样本、失败回滚和旧产物读取通过。

### REL-002：分发优化

当前 bundled Runtime 和 release unpacked 体积较大。P2 评估：

- CPU 与 GPU Runtime 分包。
- 模型与 Runtime 按需安装。
- Electron asar 策略和外部资源清单。
- 版本 manifest、校验和与断点恢复。

本项不得在没有离线安装、回滚和模型可用性方案时直接缩减发布资源。

### 回滚要求

1. P0/P1 每个 PR 应保持单一主题和可独立回滚。
2. Registry 迁移必须先备份、后切换，应用不得覆盖唯一旧库。
3. 合同变更在兼容窗口内支持读取旧快照。
4. 架构拆分先保留 adapter；新实现验证稳定后再删除旧入口。
5. 清理 PR 不与行为变更混合，便于快速恢复误删内容。

## 14. 实施里程碑

### M0：质量边界与基线冻结

- 完成 TEST-001。
- 固化现有 typecheck、build、源测试和 Worker 测试命令。
- 保存合同、数据库和代表性产物样本。

退出条件：测试只扫描预期源码，后续变化可被可靠比较。

### M1：P0 正确性修复

- FIX-001、FIX-002、FIX-003、FIX-004。
- MIG-001。
- TEST-002。

退出条件：合同、事务、启动和凭据等待全部有限收敛；故障注入通过。

### M2：状态与产物权威

- FIX-005、FIX-006、FIX-007。
- ARCH-001、ARCH-002、ARCH-003。
- TEST-003 中重启、输出根和修订场景。

退出条件：状态不倒退，受管产物不可原地覆盖，自定义输出根可跨重启。

### M3：性能、清理与模块化

- ARCH-004。
- CLEAN-001。
- ARCH-005 的第一阶段提取。
- 历史索引增量化。

退出条件：高频进度不拖慢 UI；清理后完整质量门禁通过；行为保持一致。

### M4：产品决策与迁移收口

- 完成 Cloud ASR、preview、timeline、model_root、legacy 外部调用和 Runtime 分发决策。
- 执行 CLEAN-002、CLEAN-003、ARCH-006、MIG-002、REL-002 中获批部分。

退出条件：每条兼容链都有明确 owner、保留期限或删除证据。

## 15. 成功指标

稳定性：

- 合法 Workflow 状态在 Schema、TS、Python、Registry 和 UI 间零漂移。
- 相同 operation_id 重放不产生重复任务。
- hello、recovery 和 secret 超时场景零永久悬挂。
- Store 中同一 Workflow 的 sequence 零倒退。

数据完整性：

- 受管产物原文件零直接覆盖。
- stale 总结漏标率为零。
- clear 后敏感 operation 内容符合批准的 retention 策略。

质量：

- Vitest 生成测试误扫描数量为零。
- pytest 打包 Runtime 误扫描数量为零。
- 所有发布构建均有 packaged startup 记录。

性能与维护：

- 进度事件持久化和 Renderer 更新频率有明确上限与监控。
- 历史页面日常加载不再递归扫描全部产物。
- 大模块拆分后的职责、接口和依赖方向有文档与测试约束。

## 16. 开放决策

| 决策 ID | 问题 | 可选方向 | 未决时规则 |
| --- | --- | --- | --- |
| DEC-001 | Cloud ASR 是否属于正式产品 | 恢复 UI、仅保留底层、完全退役 | 不删除 |
| DEC-002 | prompt.preview 是否提供给用户 | 接入任务草稿 UI、保留内部调试、删除 | 不删除 |
| DEC-003 | timeline 是否成为任务详情能力 | 补齐 get/refresh、移除 UI 和合同 | 不删除 |
| DEC-004 | model_root 语义 | 真正作为模型根、移除配置面 | 不宣称生效 |
| DEC-005 | operation retention 周期 | 随 Workflow 删除、限时脱敏保留 | 不保留大正文 |
| DEC-006 | legacy run_job 外部兼容窗口 | 兼容 CLI、版本弃用、直接退役 | 不删除 |
| DEC-007 | 历史设计文档保留方式 | archive、索引后保留、删除 | 默认 archive |
| DEC-008 | Runtime 分发方式 | 单包、CPU/GPU 分包、按需下载 | 保持现状 |
| DEC-009 | large-file mode 产品标准 | 虚拟化、分段预览、仅编辑限制 | 现有样式不算完成 |
| DEC-010 | split_stereo 的产品语义 | 实现完整多声道链路、从能力和 Draft 中撤销 | 不宣称已支持 |

## 17. Definition of Done

单个需求只有在以下条件全部满足时才算完成：

1. 需求实现、数据迁移和错误路径已覆盖。
2. 验收标准有自动测试或可复现的人工验收记录。
3. typecheck、build、源测试和正确范围的 Worker 测试通过。
4. 涉及 Electron 生产行为时，完成 packaged smoke；涉及 GUI 时完成 DOM 或截图检查。
5. 涉及 Registry 时，验证升级、失败回滚和旧数据读取。
6. 涉及清理时，提供生产调用图、条件决策和回滚提交。
7. 文档、合同、版本说明和用户可见提示同步更新。
8. 不修改或删除 models、用户 outputs 和未经授权的本地生成物。

## 18. 主要证据索引

- 合同状态：contracts/workflow-v2/schemas/workflow-snapshot.schema.json
- Renderer 类型：apps/desktop-electron/src/workflows/types.ts
- Python codec：apps/worker-python/app/ipc/v2/codec.py
- 状态机：apps/worker-python/app/workflow/state_machine.py
- Supervisor：apps/worker-python/app/workflow/supervisor.py
- Registry：apps/worker-python/app/workflow/registry.py
- Python Server 与 SecretProvider：apps/worker-python/app/supervisor/server.py
- SecretBroker：apps/worker-python/app/workflow/secrets.py
- Runtime Client：apps/desktop-electron/electron/workflowRuntimeClient.ts
- Electron Main：apps/desktop-electron/electron/main.ts
- Host Services：apps/desktop-electron/electron/hostServices.ts
- Workflow Store：apps/desktop-electron/src/stores/workflowStore.ts
- App Store：apps/desktop-electron/src/stores/appStore.ts
- Workflow View：apps/desktop-electron/src/features/workflow/WorkflowView.vue
- Markdown View：apps/desktop-electron/src/features/markdown/MarkdownView.vue
- History View：apps/desktop-electron/src/features/history/HistoryView.vue
- 本地 V2 Adapter：apps/worker-python/app/pipeline/chunked_local.py
- Legacy Pipeline：apps/worker-python/app/pipeline/job_runner.py
- Summary 主模块：apps/worker-python/app/summary/openai_compatible.py

## 19. 建议的首个实施批次

首批只处理发布阻断项，避免与大范围清理混合：

1. TEST-001：固定 Vitest 和 pytest 测试边界。
2. FIX-001：补齐 completed_with_warnings，拆清两类 summary draft Schema。
3. FIX-002 与 MIG-001：建立 transaction command 和 operation 最小保留。
4. FIX-003、FIX-004：修复启动回滚与凭据超时。
5. TEST-002：用合同 fixture 与故障注入验收以上改动。

首批完成并稳定后，再进入 Renderer 快照、产物修订、持久授权和清理工作。

## 20. 实施状态（更新至 2026-09-02）

本轮已完成：

- M0 / TEST-001：新增 Vitest 源测试发现边界；根目录和 Worker 目录 pytest 均限定到 apps/worker-python/tests。
- FIX-001：补齐 completed_with_warnings codec、Registry、fixture 和完成后清理路径；Trusted Summary Draft Schema 正式要求 policy_snapshot。
- FIX-002 / MIG-001：Registry 升级到 Schema v2，operation 关联 workflow；control、retry、registerRevision 的 Snapshot/Event/Operation 同事务提交；clear 同事务删除关联 operation 并保留精简幂等结果。
- FIX-003：失败 hello 会回滚 Electron Runtime 子进程；Server 只在 Supervisor 恢复成功后提交握手；Supervisor 启动失败可干净重试。
- FIX-004：Secret 等待遵守 TTL；desktop 授权失败会通过不含 secret 的 secret.reject 唤醒 Worker。
- FIX-005：Workflow Store 新增单调 applySnapshot 入口；event/list/get/submit/control/retry/resummarize/registerRevision 全部经统一合并；乱序响应不再回退 sequence，Runtime ready 后自动对账 capabilities、列表和当前选中任务详情。
- FIX-006：受管 transcript/summary 的编辑保存统一改为 staged file → UTF-8 SHA-256 → artifact.register_revision；Main 缓存受信 Snapshot 中的 Artifact 规范路径并拒绝直接覆盖；历史 transcript 会进入 transcript 视图。
- FIX-007：自定义输出根由 Main 持久记录并依据 Workflow Snapshot 重建/裁剪；读写路径先解析真实父目录再校验 root containment，覆盖路径穿越和目录链接逃逸。
- CLEAN-003：删除本地 Tauri 缓存、旧 staging、空根测试包和损坏的 Rust/Slint 恢复诊断；旧 Tauri/Workflow/MOSS 文档统一移入 `docs/legacy`，并新增 `docs/README.md` 索引。
- BUILD-CLEAN：Electron Main 编译前清空生成目录并排除 `*.spec.ts`；打包清单只保留一次 `dist-electron/**/*`，避免测试源码和重复 TOML 依赖进入发布包。
- TMP-CLEAN：清理约 613.7 MiB 的旧 Electron/npm、Runtime 下载缓存和 smoke 现场，仅保留当前调试入口使用的 `tmp/electron-debug`；Prompt 评估脚本迁入 `scripts/eval/prompt`，MOSS smoke 只归档脱敏工程指标。

架构需求推进状态：

- ARCH-001：合同 fixture 已同时进入 TS/Python 回归，Schema/codec 漂移的首批缺口已补齐；合同生成器、完整 CI 跨语言验证和版本元数据统一仍待实施。
- ARCH-002：Registry 已提供原子 command 边界并定义 clear operation 最小保留；独立 Command Service 提取及 fake command service 测试仍待实施。
- ARCH-003：受管 Artifact 读写改由 Registry Snapshot 识别，transcript/summary 导航已纠正；历史页仍保留目录扫描，增量 Artifact 索引尚未实施。

本轮验收结果：

- 前端源码测试：17 个文件、63 个测试通过，生成目录副本不再被发现。
- Worker contract_v2：228 个测试、39 个 subtests 通过；保留 1 条既有 torchcodec/FFmpeg DLL 警告。
- npm run typecheck、npm run build、npm run electron:compile 通过。
- 从仓库根目录 pytest collect-only 只收集 228 个 Worker 源测试，未扫描打包 Runtime。
- M2 聚焦测试覆盖 list/event 与 command/event 乱序、Runtime ready 对账、受管 Artifact staging/SHA、输出授权持久化、路径穿越及 junction/symlink 逃逸。
- Playwright DOM/截图检查通过：Markdown 当前文件框为只读，工具栏与编辑/预览布局正常；纯浏览器环境仅出现预期的 Electron Bridge 不可用提示。
- npm run electron:package:fast 完整通过；打包 Runtime 可导入 Pyannote/Qwen 并解析内置 FFmpeg。安全打包脚本改为唯一 staging + 同盘原子目录切换，避免文件锁导致部分发布和不完整回滚。
- 打包程序使用隔离 config/state/output 实际启动，10 秒后仍存活；日志确认 Python Runtime 进入 ready，并在关闭时完成 stopped，Electron/Worker 无残留进程。

后续仍待实施：

- M2：ARCH-001 的合同生成/版本来源、ARCH-002 的独立 Command Service、ARCH-003 的增量历史索引，以及 TEST-003 的真实重启 E2E。
- M3/M4：进度背压、历史索引、模块拆分、确认性清理和产品开放决策。
- 真实 GPU/模型、长音频、自定义输出根跨进程重启和受管修订完整 E2E。
