# Phase 0 基线与风险验证记录

日期：2026-07-10  
工作目录：`D:\claude-projects\asr-local`

## 当前工作树

开始实施前工作树已有未提交改动，涉及 Rust commands/config、Vue SettingsView、Tauri client/worker types、Pinia store、Python config/model manager/job runner/runtime，以及 `config/models.toml`。本阶段不清理、不覆盖这些改动；v1 运行链继续作为对照基线。

## 已执行基线命令

| 命令 | 结果 |
| --- | --- |
| `python -m compileall -q app`（`apps/worker-python`） | 通过 |
| `python -m app.main --health-check` | 通过；返回 `worker-contract-v1` |
| `npm run typecheck`（`apps/desktop-tauri`） | 通过 |
| `cargo test`（`apps/desktop-tauri/src-tauri`） | 通过；1 个已有 history 测试 |

## Python/模型环境事实

health snapshot 报告：

- Python：3.13.12；worker stdio 使用 Windows GBK 编码，文件系统编码为 UTF-8。
- `torch`、`transformers`、`qwen_asr`、`pyannote.audio` 当前环境均不可导入。
- cloud ASR 标准库 client 可用。
- MOSS 配置目录 `models/OpenMOSS-Team/MOSS-Transcribe-Diarize` 存在。
- Qwen 配置目录和 pyannote 配置目录当前不存在。
- 当前配置已把新产品默认切到 `qwen3_asr_1_7b`，新任务 profile 为 `pyannote_qwen3_asr`；MOSS 仅作为 `pyannote_moss_asr` 可选后端，发布 gate 仍独立执行。
- 当前 worker 支持命令仍为 `health_check`、`run_job`、`shutdown`，协议仍为 v1。

## 风险结论

1. MOSS native Transformers 路径尚未能在本机执行：缺少 `torch` 和 `transformers`，不能宣称已通过真实推理。
2. MOSS 权重目录存在，但不能仅凭目录存在证明 revision、依赖、remote code 或长音频 speaker 一致性满足发布门槛。
3. 当前实现的设备选择仍主要依据 `torch.cuda.is_available()`，不满足 v2 的硬件探测、内存预算和 warmup 规划要求。
4. 当前 v1 lane/JSONL 运行链可编译、可健康检查，但没有 v2 handshake、workflow identity、attempt、sequence 或持久化恢复能力。
5. 因此 MOSS “发布默认” gate 保持关闭；代码与配置已按目标默认实现，生产 MOSS adapter 验收放在依赖可用后执行。

## Phase 0 关闭条件

MOSS prompt 依据官方 model card 与 prompt recipes：
`https://huggingface.co/OpenMOSS-Team/MOSS-Transcribe-Diarize` 和
`https://github.com/OpenMOSS/MOSS-Transcribe-Diarize/blob/main/examples/prompts.md`。

- [ ] 在受控 Python runtime 中安装并锁定 `torch`、`transformers`、MOSS 所需依赖。
- [ ] 使用锁定模型 revision 完成最小 native Transformers 推理。
- [ ] 完成 30/60/90 分钟代表性音频的长音频和 speaker 一致性报告。
- [ ] 记录 CPU、目标 GPU 的 RTF、峰值 RAM/VRAM、模型加载时间和 1/2/3 工作流策略。
- [ ] 完成 MOSS、Transformers、Torch、Qwen-ASR、pyannote 的依赖兼容矩阵。

在上述条件关闭前，安装包不应宣称真实 MOSS 已验收；开发环境可使用 v2 fake/production feature flag，Qwen 默认链路与 MOSS 可选链路分别验收。

## Native smoke evidence (2026-07-10)

The model was executed in an isolated `.venv-moss313` environment without
changing the application's existing Python environment:

- Python 3.13.12
- torch 2.13.0+cpu
- transformers 5.13.0
- soundfile 0.14.0
- model revision `d7231bbae2587a4af278735eb765b318c4f64edd`
- `config.json` SHA-256 `2b2b7a6e61334152bdd7ecf8a4da3073b4940a097e193d1d2b22093e77535234`
- the audited versions are recorded in [`config/moss-native.lock.toml`](../config/moss-native.lock.toml)
- 1-second CPU native smoke: load 1.71s, inference 4.27s, RTF 4.27, no output truncation
- v2 `--pipeline-mode production` end-to-end smoke: transcript artifact, local mock summary artifact, and completed event sequence all passed
- v2 `--pipeline-mode auto` in the same environment resolved to `production`; the normal application environment still safely resolves to `fake` until its inference runtime is installed

This closes the minimum native load/inference and v2 production wiring check.
It does not close the release gate: the smoke input is synthetic and does not
prove long-form quality, speaker consistency, GPU behavior, or 1/2/3-workflow
stability. The 30/60/90-minute representative corpus and target GPU benchmark
remain required before declaring MOSS the fully validated release default.
