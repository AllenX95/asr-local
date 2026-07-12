# Qwen-Only 单 Runtime 逐步实施计划

## 1. 计划信息

- 日期：2026-07-12
- 对应设计：`docs/superpowers/specs/2026-07-12-qwen-only-single-runtime-design.md`
- 迁移方式：一次性硬切换，不保留 MOSS 或旧 profile 兼容
- 最终本地链路：`pyannote_qwen3_asr`
- 保持不变：Cloud ASR、摘要生成、用户模型权重和 `outputs/`
- 交付原则：先证明单环境兼容，再移除旧边界；小步提交，每阶段独立验证
- 本文只规划实施，不修改业务代码或本地虚拟环境

## 2. 完成后的架构

```text
Electron Main
  -> WorkflowRuntimeClient
      -> apps/worker-python/.venv/Scripts/python.exe
          -> Workflow Runtime v2 supervisor
              -> normalize audio
              -> Pyannote diarization
              -> release Pyannote + CUDA cache
              -> in-process Qwen3-ASR
              -> transcript artifacts/events
```

只保留：

- 一个源码环境：`apps/worker-python/.venv`
- 一个打包环境：`apps/desktop-electron/runtime/python`
- 一个本地 profile：`pyannote_qwen3_asr`
- 一个本地 ASR 模型：Qwen3-ASR-1.7B
- 一个说话人模型：Pyannote speaker-diarization-community-1

删除：

- MOSS 模型配置、adapter、profile、Prompt、能力检测和 UI；
- `.venv-qwen` 运行依赖；
- `ASR_LOCAL_QWEN_PYTHON`；
- Qwen JSONL 子进程及双 runtime 打包资源。

## 3. 开工前约束

当前仓库存在未提交用户改动。实施者必须先运行：

```powershell
Set-Location E:\claude-projects\asr-local
git status --short
git diff -- apps/desktop-electron/src/workflows/types.ts `
  apps/worker-python/app/pipeline/chunked_local.py `
  apps/worker-python/app/pipeline/job_runner.py `
  apps/worker-python/app/supervisor/server.py `
  apps/worker-python/tests/contract_v2/test_pipeline_phase3_guards.py
```

这些改动属于用户。不得 reset、checkout、覆盖或把无关变化混入迁移提交。
若实施需要修改同一文件，先理解现有 diff，再做最小合并。

禁止：

- 删除或移动 `models/` 下的模型；
- 广泛扫描或清理 `outputs/`；
- 在单环境 smoke 通过前删除本地 `.venv-qwen`；
- 先改 UI、后验证 Python 依赖；
- 通过 `--system-site-packages`、软链接或 PYTHONPATH 共享两个 venv 的包。

## 4. 阶段与提交总览

| 阶段 | 目标 | 建议提交 |
| --- | --- | --- |
| 0 | 固化基线和清理清单 | 不提交或仅提交基线文档 |
| 1 | 建立单 venv 兼容依赖锁 | `build: lock unified qwen pyannote runtime` |
| 2 | Qwen 改为主进程直接加载 | `refactor: run qwen in workflow runtime` |
| 3 | 删除 MOSS Python 后端 | `refactor: remove moss worker backend` |
| 4 | 收窄 Workflow v2 契约 | `refactor: make qwen the only local profile` |
| 5 | 简化 Electron 配置与 UI | `refactor: remove moss desktop settings` |
| 6 | 合并打包 runtime | `build: package one python runtime` |
| 7 | 清理测试、探针和当前文档 | `docs: finalize qwen-only runtime` |
| 8 | 完整生产 gate | 不在失败状态提交发布标记 |

每个提交只包含本阶段文件。阶段验证失败时，不继续下一个阶段。

## 5. Phase 0：基线和影响面

### 5.1 记录当前基线

从仓库根运行现有测试，不为基线失败修改代码：

```powershell
apps\worker-python\.venv\Scripts\python.exe -m unittest discover `
  -s apps\worker-python\tests -p "test_*.py"

Set-Location apps\desktop-electron
npm run typecheck
npm test
Set-Location ..\..
```

记录：

- Python、Torch、CUDA、Transformers、Qwen、Pyannote 版本；
- 当前失败测试及其是否与用户未提交改动有关；
- 一条此前通过的短 Qwen 录音和预期输出位置；
- GPU 空闲、Pyannote 后、Qwen 后和任务结束时显存。

### 5.2 固化 live-reference 搜索

建立清理前后都可复用的命令：

```powershell
rg -n -S "moss_transcribe_diarize|pyannote_moss_asr|MOSS_MODEL_KEY|MossTranscribe|moss-native" `
  apps config contracts scripts `
  -g "!models/**" -g "!outputs/**" -g "!.venv*/**" `
  -g "!**/node_modules/**" -g "!**/dist*/**" -g "!**/__pycache__/**"

rg -n -S "ASR_LOCAL_QWEN_PYTHON|\.venv-qwen|qwen-python|QwenSubprocessAdapter|qwen_segment_worker|qwen_asr_runtime" `
  apps config contracts scripts `
  -g "!.venv*/**" -g "!**/node_modules/**" -g "!**/dist*/**"
```

退出条件：测试基线和完整 live-reference 文件清单已记录。

## 6. Phase 1：证明并锁定单环境依赖

这是迁移的硬门槛。未通过不得删除 Qwen 子进程或 MOSS 代码。

### 6.1 创建干净验证环境

不要直接污染现有 `.venv`。先创建临时验证环境，例如：

```powershell
py -3.12 -m venv tmp\venv-qwen-pyannote-proof
tmp\venv-qwen-pyannote-proof\Scripts\python.exe -m pip install --upgrade pip
```

从 Qwen3-ASR 0.0.6 的官方约束出发，安装 Qwen 与 Pyannote 所需依赖。
不得把旧 `moss-native` pins 合并进来。验证时记录完整命令和包来源。

### 6.2 依赖门槛

从同一解释器执行：

```powershell
tmp\venv-qwen-pyannote-proof\Scripts\python.exe -c `
  "import torch, transformers, qwen_asr, pyannote.audio, soundfile, librosa; print(torch.__version__, transformers.__version__)"

tmp\venv-qwen-pyannote-proof\Scripts\python.exe -m pip check
tmp\venv-qwen-pyannote-proof\Scripts\python.exe -m pip freeze
```

随后运行两类真实 smoke：

1. 加载本地 Pyannote 模型并完成短音频 diarization；
2. 同一进程释放 Pyannote，再加载本地 Qwen 模型并完成转录。

如果导入成功但模型加载失败，先解决依赖/二进制兼容，不能用恢复子进程作为最终方案。

### 6.3 修改依赖声明

修改：

- `apps/worker-python/pyproject.toml`
  - 删除 `moss-native` extra；
  - 将生产 inference extra 收敛为已验证的 Qwen + Pyannote pins；
  - 删除无用途的 MOSS 描述。
- 新增或更新项目采用的依赖锁文件。
  - 锁文件必须能从干净 Python 3.12 环境复现；
  - 记录 Torch/CUDA wheel 获取方式；
  - 不提交 wheel、venv 或 pip cache。
- `apps/worker-python/README.md`
  - 暂时增加“单环境安装”命令；最终文案在 Phase 7 完成。

如果仓库没有既定 lock 工具，首选一份受控 constraints 文件，例如
`apps/worker-python/constraints-inference.txt`，并在 `pyproject.toml` 保留直接依赖。

### 6.4 阶段测试

```powershell
tmp\venv-qwen-pyannote-proof\Scripts\python.exe -m pip check
tmp\venv-qwen-pyannote-proof\Scripts\python.exe -m unittest discover `
  -s apps\worker-python\tests -p "test_*.py"
```

退出条件：干净单环境导入、Pyannote smoke、Qwen smoke 和 `pip check` 全部通过，
可复现 pins 已入库。

## 7. Phase 2：Qwen 改为主进程直接加载

### 7.1 先改测试

更新或新增 ModelManager 测试，证明：

- `get_qwen_model()` 使用当前 `sys.executable` 内的 `qwen_asr`；
- 不读取 `ASR_LOCAL_QWEN_PYTHON`；
- 不启动 subprocess；
- Qwen import、模型不存在和加载失败产生明确主 runtime 错误；
- `close_qwen_model()` 幂等并释放模型资源。

重点文件：

- `apps/worker-python/tests/contract_v2/test_pipeline_phase3_guards.py`
- 可新增 `apps/worker-python/tests/contract_v2/test_model_manager.py`
- 删除或改写所有 Qwen subprocess 专属测试。

### 7.2 修改 ModelManager

修改 `apps/worker-python/app/models/manager.py`：

- `get_qwen_model()` 直接 import `Qwen3ASRModel`；
- 使用既有 workflow device/dtype/batch 参数；
- 删除 `resolve_qwen_python()` 和 `QwenSubprocessAdapter` 分支；
- 保留 Qwen 模型缓存和幂等清理；
- 统一异常文案为当前 runtime 缺依赖或模型加载失败。

修改 `apps/worker-python/app/runtime/env.py`：

- 删除 Qwen 解释器路径探测；
- 删除 `qwen_asr_runtime`；
- readiness 只检查当前解释器能否 import `qwen_asr`。

删除：

- `apps/worker-python/app/pipeline/qwen_subprocess.py`
- `apps/worker-python/scripts/qwen_segment_worker.py`

### 7.3 验证生命周期

确认 `apps/worker-python/app/pipeline/job_runner.py` 和
`apps/worker-python/app/pipeline/chunked_local.py` 中：

- Pyannote 分段完成后调用 `close_pyannote_pipeline()`；
- Qwen 只在清理完成后首次加载；
- cancel、异常和正常完成都会调用 `close_local_models()`；
- 本地 GPU lane 容量仍为 1；
- 不因移除子进程而扩大模型常驻周期。

### 7.4 阶段验证

```powershell
apps\worker-python\.venv\Scripts\python.exe -m unittest `
  apps.worker-python.tests.contract_v2.test_pipeline_phase3_guards

apps\worker-python\.venv\Scripts\python.exe -m unittest discover `
  -s apps\worker-python\tests -p "test_*.py"
```

按实际包路径从 `apps/worker-python` 运行测试，避免目录名连字符造成模块错误。

退出条件：Qwen 在主进程工作，代码和日志中没有子进程启动路径。

## 8. Phase 3：删除 MOSS Python 后端

### 8.1 配置和模型管理

修改：

- `config/models.toml`
  - 删除 `[moss_transcribe_diarize]`；
  - 删除 `active_local_asr_model`；
  - 保留 Qwen 与 Pyannote 模型路径。
- `apps/worker-python/app/config.py`
  - 删除 MOSS 默认配置和 dataclass 字段；
  - 删除 active model 解析；
  - TOML 中未知旧 MOSS section 只忽略，不回写。
- `apps/worker-python/app/schemas.py`
  - 删除 MOSS model snapshot metadata 和 MOSS-specific schema 分支。
- `apps/worker-python/app/workflow/model_snapshot.py`
  - `pyannote_qwen3_asr` 返回 Qwen + Pyannote；
  - `cloud_asr` 保持原行为；
  - 删除所有 MOSS 和旧 alias 分支。
- `apps/worker-python/app/models/manager.py`
  - 删除 MOSS adapter、path、dtype、batch、load、close 和 active-model 分支；
  - `get_local_asr_model()` 可删除，调用方改用显式 `get_qwen_model()`。

删除：

- `apps/worker-python/app/pipeline/moss_v2.py`
- `config/moss-native.lock.toml`
- `apps/worker-python/tests/contract_v2/test_moss_v2_adapter.py`

### 8.2 路由和 supervisor

修改：

- `apps/worker-python/app/pipeline/router.py`
  - 删除 MOSS map；
  - 如果只剩单一 local adapter，简化为显式 Qwen local route；
  - 保留未知 profile 的明确拒绝。
- `apps/worker-python/app/pipeline/chunked_local.py`
  - 删除 `model_key`/`backend_id` 中只为多后端存在的参数；
  - 直接取得 Qwen 模型；
  - 保持分块恢复、warning 和 assembler 行为。
- `apps/worker-python/app/supervisor/server.py`
  - 只构造一个 Qwen local transcriber；
  - 删除 MOSS readiness、capability、Prompt compiler 和 backend 分支。
- `apps/worker-python/app/workflow/supervisor.py`
  - 删除 MOSS Prompt/version 和 profile 分支；
  - Cloud ASR 分支保持不变。
- `apps/worker-python/app/ipc/v2/codec.py`
  - 只接受 `pyannote_qwen3_asr`、`cloud_asr`。

评估 `apps/worker-python/app/pipeline/legacy_v2.py`：若只服务已删除 alias，删除；
若仍有被当前 Qwen链路调用的纯转换逻辑，先移动到命名准确的当前模块，再删除 legacy wrapper。

### 8.3 音频和探针

修改：

- `apps/worker-python/app/audio.py`
  - 将“安装 moss-native runtime”的错误提示改为统一 inference runtime；
  - 不改变 ffmpeg 解析/归一化行为。
- `apps/worker-python/scripts/probe_chunked_runtime.py`
  - 删除 `--backend` 选择；
  - 固定探测 Pyannote + Qwen；
  - 同时输出 Pyannote 释放前后与 Qwen 结束后的 GPU snapshot。

### 8.4 Python 测试调整

删除或重写：

- `test_model_snapshot.py` 中 MOSS cases；
- `test_chunked_router.py` 中 MOSS route；
- `test_supervisor.py` 中 MOSS提交；
- `test_pipeline_phase3_guards.py` 中 MOSS manager；
- 所有 capability/Prompt 测试中的 MOSS预期。

新增拒绝测试：三个被删除的 profile 均返回不支持，而不是静默映射到 Qwen。

退出条件：Python live code 中不存在 MOSS identifier，完整 Python 测试通过。

## 9. Phase 4：收窄 Workflow v2 契约

### 9.1 Schema 和 fixtures

修改：

- `contracts/workflow-v2/schemas/transcription-draft.schema.json`
  - profile enum 只保留 `pyannote_qwen3_asr`、`cloud_asr`。
- `contracts/workflow-v2/fixtures/`
  - capabilities fixture 只声明一个 local profile；
  - Prompt fixture 使用 Qwen compiler/version；
  - 移除或替换 MOSS-only fixture；
  - 保持事件 sequence、attempt 和 artifact contract 不变。
- `contracts/workflow-v2/README.md`
  - 删除双后端说明和旧 alias。

### 9.2 TypeScript contract 类型

修改：

- `apps/desktop-electron/src/ipc/workerTypes.ts`
  - `PipelineProfile` 收窄为 `pyannote_qwen3_asr | cloud_asr`；
  - 删除 `LocalAsrModelKey` 或收窄后评估是否仍有价值；
  - 删除 MOSS config/status 字段。
- `apps/desktop-electron/src/workflows/types.ts`
  - 同步 profile 与 capability 类型；
  - 保留用户已有未提交改动。
- `apps/desktop-electron/src/workflows/adapters/fakeWorkflowRuntime.ts`
  - capability 只返回 Qwen local + Cloud；
  - Prompt preview 不再返回 MOSS compiler。

更新对应 Vitest 和 reducer fixture。不得改变 unrelated workflow 状态机语义。

### 9.3 契约验证

```powershell
apps\worker-python\.venv\Scripts\python.exe -m unittest discover `
  -s apps\worker-python\tests -p "test_*.py"

Set-Location apps\desktop-electron
npm test
npm run typecheck
Set-Location ..\..
```

退出条件：Python 和 TypeScript 对可接受 profile 的集合完全一致。

## 10. Phase 5：简化 Electron 配置与 UI

### 10.1 Electron Main/host services

修改 `apps/desktop-electron/electron/hostServices.ts`：

- 模型配置只解析 Qwen、Pyannote；
- 删除 active model 选择和校验；
- 删除 MOSS path/readiness；
- 保存设置时不再写回旧 MOSS section；
- 读取含旧 MOSS键的 TOML 时忽略该键。

修改 `apps/desktop-electron/src/ipc/desktopClient.ts`，删除 fake/default MOSS 字段。
如 Preload/IPC method 签名含 `mossPath` 或 active model，同步收窄并更新测试。

### 10.2 Settings UI

修改 `apps/desktop-electron/src/features/settings/SettingsView.vue`：

- 删除 ASR backend selector；
- 删除 MOSS path 输入和状态；
- 保留 Qwen、Pyannote path；
- 保存方法参数只传必要字段；
- 明确显示“本地链路：Pyannote + Qwen3-ASR”。

### 10.3 Workflow UI

修改 `apps/desktop-electron/src/features/workflow/WorkflowView.vue`：

- 删除 MOSS label、ready computed、提交 guard 和 `<option>`；
- 本地任务固定生成 `pyannote_qwen3_asr`；
- 如果 UI 仍需本地/云端选择，只保留这两项；
- 不改变 hotwords、录音背景、语言、设备策略和后处理字段。

### 10.4 UI 验证

```powershell
Set-Location apps\desktop-electron
npm run typecheck
npm test
npm run build
```

随后运行开发或构建应用进行 DOM/截图检查：

- Settings 无 MOSS、无 active model selector；
- Workflow 无 MOSS选项；
- Qwen/Pyannote 缺失状态准确；
- 本地任务提交 payload 是 `pyannote_qwen3_asr`；
- Cloud ASR 仍可选择和提交。

退出条件：UI 和 IPC 中无 MOSS live reference，视觉验证通过。

## 11. Phase 6：合并构建与打包 runtime

### 11.1 重写 runtime builder

修改 `scripts/build/build_python_runtime.ps1`：

- 删除 `$QwenVenv`、`$QwenRuntimeDir`、Qwen overlay 和双版本文件；
- 更新 `$ExpectedVersion`，使依赖或布局变化可触发重建；
- 只复制主 `.venv/Lib/site-packages`；
- portable import gate 增加 `qwen_asr`；
- 在 copied runtime 中运行 `pip check` 等价验证（若 embeddable runtime 无 pip，至少执行明确 import/version gate）；
- 运行 runtime hello/capabilities smoke；
- 保持删除路径的绝对路径边界检查，不能扩大 Remove-Item 范围。

期望 import gate：

```powershell
runtime\python\python.exe -X utf8 -c `
  "import sqlite3, torch, transformers, qwen_asr, pyannote.audio, soundfile; print('runtime-ok', torch.__version__, transformers.__version__)"
```

### 11.2 Electron Builder

修改 `apps/desktop-electron/package.json`：

- 从 `extraResources` 删除 `runtime/qwen-python`；
- 保留一个 `runtime/python`；
- 确认 worker script filter 不再包含已删除 Qwen child script；
- 不添加 MOSS config/lock/model资源。

检查 `apps/desktop-electron/electron/runtimePaths.ts` 和
`workflowRuntimeClient.ts`：只解析主 runtime；删除任何 packaged Qwen path/env 注入。

### 11.3 打包验证

```powershell
Set-Location apps\desktop-electron
npm run runtime:build -- --Force
npm run electron:build
npm run electron:package
```

必须检查实际输出：

- `runtime-root/runtime/python/python.exe` 存在；
- `runtime-root/runtime/qwen-python` 不存在；
- bundled Python 能 import Qwen 和 Pyannote；
- 应用启动后 runtime hello/capabilities 成功；
- 应用退出后无残留 Python 子进程。

退出条件：目录版安装产物完成一次真实 Pyannote + Qwen 转录。

## 12. Phase 7：清理测试、探针和文档

### 12.1 测试清理

确认测试名称和 fixture 不再暗示双后端：

- 删除 MOSS-only 测试文件；
- 重命名 dual-ASR 测试为 Qwen local pipeline 测试；
- 保留分块、speaker、warning、retry、cancel、artifact contract 测试；
- 新增单解释器 readiness 和无 child process 测试；
- 新增配置读取旧 MOSS键但保存时丢弃的测试。

### 12.2 当前文档

更新：

- `apps/worker-python/README.md`
- `docs/worker-contract-v2.md`
- `docs/Phase0_Baseline.md`（标记已被新架构取代或更新当前状态）
- Electron 构建/发布文档中所有双 runtime 描述。

为以下历史规格/benchmark 顶部增加 superseded note，链接到已批准设计：

- 双 ASR PRD/实施计划；
- dual-ASR production gate；
- MOSS Phase 0 或迁移文档。

历史文档正文可保留，不做伪造式重写。

### 12.3 最终静态搜索

对 live code/config/contracts/scripts 执行 Phase 0 的两组 `rg`。预期零结果。
对 docs 的剩余结果逐个确认只存在于标记为 historical/superseded 的记录中。

退出条件：不存在可执行 MOSS 或双 runtime 路径。

## 13. Phase 8：生产验收

### 13.1 Gate A：静态和单元测试

```powershell
apps\worker-python\.venv\Scripts\python.exe -m pip check
apps\worker-python\.venv\Scripts\python.exe -m unittest discover `
  -s apps\worker-python\tests -p "test_*.py"

Set-Location apps\desktop-electron
npm run typecheck
npm test
npm run build
Set-Location ..\..
```

### 13.2 Gate B：单解释器证明

在 source `.venv` 和 packaged runtime 分别打印：

- `sys.executable`；
- Torch、Transformers、Qwen、Pyannote 版本；
- CUDA availability/device；
- capabilities local readiness。

日志不得出现第二 Python executable、Qwen child PID 或 JSONL child protocol。

### 13.3 Gate C：短真实录音

使用已通过的短多说话人样本：

- Pyannote 得到非空 speaker turns；
- Qwen 得到非空文本；
- speaker/time 合并正确；
- progress 与 warning 顺序正确；
- Pyannote 清理发生在 Qwen model loading 前；
- 正常完成后 GPU 显存接近基线。

### 13.4 Gate D：10/30/90 分钟录音

逐条运行，不并行：

- 无 OOM、死锁、worker restart loop；
- chunk 数、失败恢复和最终文本完整；
- cancel 后 GPU lane 可被下一任务获取；
- retry 不重复占用模型；
- 输出与此前 Qwen 成功基线无明显回退。

音频和生成结果不提交 Git。

### 13.5 Gate E：正式安装包

运行 `npm run electron:package`，在非源码路径启动实际产物：

- 不依赖系统 Python；
- 不依赖 `.venv-qwen`；
- 不依赖全局 ffmpeg；
- Settings 与 Workflow 只显示 Qwen local；
- 完成真实本地转录；
- 应用退出后 Python 进程清理干净。

所有 Gate 通过后，才允许手动删除本地 `.venv-qwen` 和 MOSS模型目录。
代码和脚本不得代替用户执行模型删除。

## 14. 失败处理和回滚点

| 失败 | 停止位置 | 处理 |
| --- | --- | --- |
| Qwen + Pyannote 无法共存 | Phase 1 | 不继续迁移，保留现有双环境 Qwen 架构 |
| 主进程 Qwen smoke 失败 | Phase 2 | 回退该阶段提交，保留 Qwen child |
| profile/contract 测试失败 | Phase 4 | 修正 Python/TS enum 一致性，不做兼容 alias |
| UI build 失败 | Phase 5 | 修正类型和 IPC，不放宽 schema |
| portable import 失败 | Phase 6 | 修正复制/锁文件，不回退到 overlay runtime |
| 长录音 OOM | Phase 8 | 检查 Pyannote 强引用、Qwen关闭和 GPU lane，不恢复 MOSS |
| packaged app 失败 | Phase 8 | 修复打包资源与 runtime path，不以源码 smoke 代替 |

迁移提交保持可逐阶段回退。不要使用 `git reset --hard`；使用常规 revert 或
在未提交阶段修复当前改动。

## 15. 最终 Definition of Done

- `pyannote_qwen3_asr` 是唯一 local profile，Cloud ASR 保持可用；
- live code、配置、契约、构建和 UI 中没有 MOSS；
- live code 和构建中没有 `.venv-qwen`、`qwen-python`、
  `ASR_LOCAL_QWEN_PYTHON` 或 Qwen subprocess；
- 同一 `sys.executable` 成功 import 并运行 supervisor、Pyannote、Qwen；
- Pyannote 在 Qwen加载前释放，任务结束释放 Qwen；
- Python、TypeScript、Vue build 和 contract 测试通过；
- 10/30/90 分钟真实录音通过；
- 实际 Electron安装产物完成本地转录并正常退出；
- 未删除模型权重、用户输出或用户已有工作区改动；
- 当前文档只描述单 runtime，历史双后端文档已标记 superseded。

## 16. 推荐执行提示词

```text
请按 docs/superpowers/specs/2026-07-12-qwen-only-single-runtime-implementation-plan.md
从 Phase 0 开始实施。保留当前工作区已有改动；每阶段先执行对应测试，验证通过后
再进入下一阶段。不要删除 models、outputs 或本地 .venv-qwen，直到 packaged Gate E
通过。主线程拥有代码编辑和最终集成。
```
