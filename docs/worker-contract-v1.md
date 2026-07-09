# Worker Contract v1

本文档冻结 Tauri/Vue 前端、Tauri shell 与现有 Python worker 之间的第一版协议。目标是让 GUI 重构不改变 ASR pipeline、模型加载、speaker diarization 和导出格式。

## 传输

- 通信方式：JSON Lines over stdio。
- 编码：UTF-8。
- 请求基础结构：

```json
{
  "type": "run_job",
  "payload": {}
}
```

- 响应基础结构：

```json
{
  "type": "job_event",
  "job_id": "tauri_1780000000000",
  "payload": {}
}
```

## Commands

### health_check

请求：

```json
{ "type": "health_check", "payload": {} }
```

响应：

```json
{
  "type": "health_check_ok",
  "payload": {
    "contract_version": "worker-contract-v1",
    "supported_commands": ["health_check", "run_job", "shutdown"]
  }
}
```

### run_job

请求 payload：

```json
{
  "job_id": "tauri_1780000000000",
  "source_path": "E:/recordings/meeting.wav",
  "output_dir": "E:/claude-projects/asr-local/outputs",
  "output_file_name": "meeting.transcript.md",
  "asr_backend": "local",
  "cloud_asr_profile": null,
  "language_mode": "auto",
  "fixed_language": null,
  "enable_speaker_diarization": true,
  "context_text": "",
  "terms": ["Qwen", "pyannote"],
  "replacements": [{ "wrong": "错词", "correct": "正确词" }],
  "keep_fillers": true,
  "auto_punctuation": true
}
```

`asr_backend` 可选值：

- `local`：使用本地 Qwen3-ASR 模型。
- `cloud`：调用 OpenAI-compatible `/audio/transcriptions` 云端 ASR API；此时必须提供 `cloud_asr_profile`。

`cloud_asr_profile` 结构：

```json
{
  "name": "example",
  "base_url": "https://api.example.com/v1",
  "model": "whisper-1",
  "api_key": "..."
}
```

进度事件 payload：

```json
{
  "stage": "transcribing",
  "progress": 0.52,
  "processed_ms": 120000,
  "total_ms": 360000,
  "current_segment_index": 8,
  "segment_count": 24,
  "current_speaker_label": "Speaker 1"
}
```

完成响应：

```json
{
  "type": "job_completed",
  "job_id": "tauri_1780000000000",
  "payload": {
    "md_path": "E:/claude-projects/asr-local/outputs/meeting.transcript.md",
    "transcript_json_path": "E:/claude-projects/asr-local/outputs/meeting.transcript.json",
    "job_json_path": "E:/claude-projects/asr-local/outputs/.jobs/tauri_1780000000000/job.json",
    "job_dir": "E:/claude-projects/asr-local/outputs/.jobs/tauri_1780000000000",
    "source_path": "E:/recordings/meeting.wav",
    "segments": 24,
    "speakers": 2,
    "total_ms": 360000,
    "detected_languages": ["zh"],
    "asr_backend": "local",
    "asr_profile_name": null,
    "asr_model": "Qwen/Qwen3-ASR-1.7B"
  }
}
```

失败响应：

```json
{
  "type": "job_failed",
  "job_id": "tauri_1780000000000",
  "payload": {
    "reason": "Source file does not exist",
    "user_message": "转写任务失败。",
    "diagnostic_detail": "Source file does not exist",
    "stage": "failed"
  }
}
```

### shutdown

请求：

```json
{ "type": "shutdown", "payload": {} }
```

响应：

```json
{ "type": "shutdown_ack", "payload": {} }
```

## Stage

当前 worker 允许以下 stage：

- `preparing`
- `decoding`
- `diarizing`
- `segmenting`
- `transcribing`
- `merging`
- `normalizing`
- `exporting`
- `paused`
- `resumed`
- `terminating`
- `failed`

## 控制信号

暂停、恢复、终止不通过 stdio command 修改 Python pipeline，仍沿用旧版控制文件：

- 暂停：`outputs/.jobs/{job_id}/control.pause`
- 恢复：删除 `control.pause`
- 终止：`outputs/.jobs/{job_id}/control.cancel`

Tauri shell 负责将 lane 操作映射到当前 active `job_id`，Python worker 在 pipeline 检查点读取控制文件。
