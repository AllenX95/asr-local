# ASR Local 文档索引

本目录按“当前参考资料”和“历史归档”区分。历史归档仅用于追溯设计、迁移和验证决策，不代表当前产品架构或操作方式。

## 当前参考资料

- [Electron 迁移实现报告](./Electron_Migration_Implementation_Report.md)：当前 Electron + Vue + Python Runtime 架构及迁移结果。
- [Workflow Runtime v2 合同](./worker-contract-v2.md)：桌面端与 Python Runtime 的当前 JSONL 协议。
- [Workflow v2 Schema 与 Fixture](../contracts/workflow-v2/README.md)：跨层契约资产及兼容性测试说明。
- [代码库稳定化与清理 PRD](./PRD_Codebase_Stabilization_Optimization_Cleanup_2026-08.md)：当前稳定性、优化和清理工作清单。
- [领域词汇表](../CONTEXT.md)：当前领域对象与术语。
- [Electron Desktop README](../apps/desktop-electron/README.md)：开发、验证与打包入口。
- [Python Worker README](../apps/worker-python/README.md)：Runtime 开发与运行说明。

## 设计与验证记录

- [`superpowers/specs/`](./superpowers/specs/)：按日期保存的设计与实施记录；文件中的未来时态和阶段状态以当前代码及上方文档为准。
- [`benchmarks/`](./benchmarks/)：模型、性能和生产门槛记录。
- [`research/`](./research/)：专项技术研究。

## 历史归档

[`legacy/`](./legacy/) 中的资料均为 historical/superseded，不作为当前实现依据：

- [早期 Rust + Slint 产品 PRD](./legacy/PRD_Qwen3_ASR_Local_Desktop.md)
- [早期 Rust + Slint 技术设计](./legacy/Technical_Design_Qwen3_ASR_Local_Desktop.md)
- [早期 Rust + Slint 开发任务拆解](./legacy/Development_Task_Breakdown_Qwen3_ASR_Local_Desktop.md)
- [Tauri + Vue + TypeScript 重构 PRD](./legacy/PRD_Tauri_Vue_TS_Refactor_ASR_Local_Desktop.md)
- [无 Rust / Electron 架构重构 PRD](./legacy/ASR_Local_无Rust架构重构_PRD.md)
- [Workflow Runtime v2 早期 PRD](./legacy/PRD_Workflow_Runtime_V2.md)
- [Workflow Runtime v2 早期实施计划](./legacy/Workflow_Runtime_V2_Implementation_Plan.md)
- [Phase 0 基线与 MOSS 双运行时记录](./legacy/Phase0_Baseline.md)

删除历史文档前应先确认不再需要迁移审计、设计依据或回归背景，并同步修复本索引及所有交叉链接。
