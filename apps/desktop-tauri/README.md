# ASR Local Tauri Desktop

这是 PRD `PRD_Tauri_Vue_TS_Refactor_ASR_Local_Desktop.md` 对应的新桌面端。新版只通过 Tauri commands 与 `apps/worker-python` 通信。

## 开发

```powershell
npm install
npm run tauri:dev
```

## 构建

```powershell
npm run tauri:build
```

## 边界

- Python ASR pipeline、模型加载、speaker diarization 和导出格式不在前端重构中改写。
- Worker 协议见 `docs/worker-contract-v1.md`。
- 暂停、恢复和终止沿用 `outputs/.jobs/{job_id}/control.*` 文件。
