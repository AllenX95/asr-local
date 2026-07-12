# Worker Contract v2

本文档定义桌面受信任层与 Python WorkflowRuntime supervisor 之间的第二版协议。它取代 v1 的阻塞式 `run_job` 与 lane 控制模型，以持久工作流任务、异步事件、任务级控制和可恢复快照为核心。

配套文档：[PRD](../PRD_Workflow_Runtime_V2.md)、[Domain Glossary](../CONTEXT.md)、[实施计划](./Workflow_Runtime_V2_Implementation_Plan.md)。

## 1. 规范范围

v2 负责：

- 协议握手与能力发现；
- 工作流任务提交、查询、列表、控制和阶段重试；
- Qwen + Pyannote 与云端转录链路的任务规格；
- 自动总结与最终产物状态；
- 工作流快照和有序事件；
- 断线对账、supervisor 重启和检查点恢复；
- 云端 ASR 与总结凭据的临时授权；
- 优雅停止与中断恢复。

v2 不暴露：

- Worker lane、线程、进程、GPU semaphore 或模型实例；
- renderer 直接可用的 API key；
- 转录正文或最终总结正文；正文通过产物路径读取；
- Qwen、Pyannote 或云端 client 的内部对象和调用参数。

## 2. 设计原则

1. `WorkflowSnapshot` 是状态权威，事件只是变更通知。
2. `workflow_id` 是工作流任务稳定身份；`attempt_id` 是某次执行尝试身份。
3. `request_id` 只关联当前连接上的请求和响应；`operation_id` 保证业务变更跨断线幂等。
4. 所有用户控制都针对 `workflow_id + expected_attempt_id`，不得针对资源槽。
5. 先持久化状态和序号，再发送事件。
6. 每个工作流任务独立维护单调递增 `sequence`；不提供跨任务全局顺序。
7. 任务规格接受后不可变；重试创建新执行尝试，不修改原规格。
8. 凭据不属于任务规格、快照、事件、产物或日志。
9. 不宣称 exactly-once；协议提供持久幂等效果和可对账状态。
10. v1 与 v2 不在同一进程会话中自动猜测或混发。

## 3. 传输

- 传输：UTF-8 JSON Lines over stdin/stdout。
- 每行必须是一个完整 JSON object；JSON 字符串中的换行必须转义。
- stdout 只能写协议消息；日志必须写 stderr 或日志文件。
- 所有 stdout 写入必须通过单一 writer 或 writer lock，避免多线程交错。
- 默认最大单条消息：1 MiB；握手能力可以公布更小限制，但不得静默截断。
- 路径使用平台原生绝对路径字符串；Windows 路径在 JSON 中正常转义。
- 时间使用 UTC ISO-8601 字符串；性能统计可以附加毫秒数。
- 进度值范围为 `0.0..1.0`。

## 4. Envelope

### 4.1 Request

```json
{
  "protocol": "asr-local-workflow",
  "protocol_version": 2,
  "kind": "request",
  "request_id": "req_018f...",
  "operation_id": "op_018f...",
  "method": "workflow.submit",
  "params": {}
}
```

字段：

| 字段 | 必填 | 语义 |
| --- | --- | --- |
| `protocol` | 是 | 固定为 `asr-local-workflow` |
| `protocol_version` | 是 | 固定为 `2` |
| `kind` | 是 | 固定为 `request` |
| `request_id` | 是 | 当前连接上的 request/response correlation ID |
| `operation_id` | 条件 | 第 5.2 节列出的持久业务操作必须提供；实例级 `runtime.shutdown` 和含秘密的 `secret.provide` 不使用 |
| `method` | 是 | 命名空间方法名 |
| `params` | 是 | 方法参数 object |

### 4.2 Response

```json
{
  "protocol": "asr-local-workflow",
  "protocol_version": 2,
  "kind": "response",
  "request_id": "req_018f...",
  "operation_id": "op_018f...",
  "ok": true,
  "result": {}
}
```

错误响应：

```json
{
  "protocol": "asr-local-workflow",
  "protocol_version": 2,
  "kind": "response",
  "request_id": "req_018f...",
  "operation_id": "op_018f...",
  "ok": false,
  "error": {
    "code": "INVALID_REQUEST",
    "message": "The source audio path is required.",
    "retryable": false,
    "field_errors": [
      { "field": "draft.source.path", "message": "Required" }
    ],
    "details": {},
    "diagnostic_id": "diag_018f..."
  }
}
```

每个 request 必须且只能产生一个 response。`result` 与 `error` 必须且只能出现一个。

### 4.3 Event

```json
{
  "protocol": "asr-local-workflow",
  "protocol_version": 2,
  "kind": "event",
  "event": "workflow.event",
  "payload": {
    "workflow_id": "wf_018f...",
    "attempt_id": "att_018f...",
    "sequence": 17,
    "occurred_at": "2026-07-10T12:00:00Z",
    "caused_by_operation_id": "op_018f...",
    "type": "progress",
    "stage": "transcribing",
    "data": {},
    "state": {
      "workflow_id": "wf_018f...",
      "sequence": 17,
      "status": "running",
      "stage": "transcribing",
      "attempt": {
        "attempt_id": "att_018f...",
        "number": 1,
        "stage_attempts": { "transcription": 1, "summary": 0, "writing_final": 0 }
      },
      "progress": {
        "stage_ratio": 0.42,
        "overall_ratio": 0.31,
        "processed_ms": 120000,
        "total_ms": 285000,
        "queue_position": null
      },
      "control": { "pending_action": null },
      "runtime_plan": null,
      "artifacts": [],
      "recovery": { "recommended_retry_stage": null, "interrupted_attempt_id": null },
      "last_error": null,
      "timestamps": {
        "created_at": "2026-07-10T11:58:00Z",
        "updated_at": "2026-07-10T12:04:00Z",
        "started_at": "2026-07-10T12:00:00Z",
        "completed_at": null
      }
    }
  }
}
```

每个 `workflow.event` 必须携带完整的可变状态 `state`，包括 sequence、status、stage、attempt、progress、control、RuntimePlan、ArtifactRefs、recovery、last_error 和 timestamps。event 不重复发送不可变 WorkflowSpecSnapshot；完整任务快照通过 `workflow.get` 获取。因此 event 不包含转录正文、最终总结正文、完整 Prompt、完整总结模板或凭据。

为便于路由，event 顶层 payload 重复携带 `workflow_id`、`attempt_id`、`sequence` 和 `stage`。它们必须分别与 `state.workflow_id`、`state.attempt.attempt_id`、`state.sequence` 和 `state.stage` 完全相等；不一致的消息属于协议错误，host 必须丢弃并通过 `workflow.get` 对账。

## 5. 幂等与顺序

### 5.1 Request correlation

- `request_id` 在单个连接中唯一。
- response 必须原样返回 `request_id`。
- 调用方不得通过 response 类型或到达顺序猜测关联关系。

### 5.2 Operation idempotency

- `workflow.submit`、`workflow.control`、`workflow.retry` 和 `artifact.register_revision` 必须提供 `operation_id`。
- supervisor 必须持久保存变更操作的 canonical payload digest 与逻辑结果。
- 相同 `operation_id` 和相同 canonical payload 重发时，返回同一逻辑结果。
- 相同 `operation_id` 被用于不同 method 或不同 payload 时，返回 `OPERATION_ID_REUSED`。
- response 丢失后，调用方使用同一 `operation_id` 重发，不得重复创建任务或执行控制。

Canonical payload digest 的算法固定如下：

1. 使用对应 JSON Schema 验证 params、拒绝未知字段，并应用文档定义的默认值。
2. 显式 `null` 保留；没有默认值的缺省可选字段保持缺省；不得用语言运行时的隐式默认补齐。
3. 构造 `{ "method": <method>, "params": <normalized params> }`，不包含 envelope、`request_id` 或 `operation_id`。上述持久幂等方法的 params schema 禁止 secret。
4. 按 RFC 8785 JSON Canonicalization Scheme（JCS）编码为 UTF-8，并计算 SHA-256 小写十六进制 digest。

### 5.3 Workflow sequence

- 首个持久事件 `sequence = 1`。
- `sequence` 在同一 `workflow_id` 的全部执行尝试中持续递增，重试不归零。
- 每次对外可见的快照变更与事件必须在同一事务中分配新 sequence。
- supervisor 必须先提交事务，再写 event。
- 客户端收到 `sequence <= last_seen` 时视为重复并忽略。
- 客户端收到 `sequence > last_seen + 1` 时调用 `workflow.get` 对账。
- 多个工作流任务之间不保证事件顺序。

## 6. Handshake

v2 进程启动后的第一个 request 必须是 `runtime.hello`。握手前收到其他方法时返回 `HANDSHAKE_REQUIRED`。在成功写出 hello response 前，supervisor 不得向 stdout 写任何 event envelope；启动恢复可以先持久化状态变化，但 host 通过后续 `workflow.list/get` 对账，supervisor 不补发握手前事件。

Request：

```json
{
  "protocol": "asr-local-workflow",
  "protocol_version": 2,
  "kind": "request",
  "request_id": "req_hello_1",
  "method": "runtime.hello",
  "params": {
    "supported_versions": [2],
    "client": {
      "name": "asr-local-electron",
      "version": "0.2.0",
      "installation_id": "install-stable-uuid"
    }
  }
}
```

Response：

```json
{
  "protocol": "asr-local-workflow",
  "protocol_version": 2,
  "kind": "response",
  "request_id": "req_hello_1",
  "ok": true,
  "result": {
    "selected_version": 2,
    "worker_instance_id": "worker-process-uuid",
    "store_instance_id": "workflow-store-uuid",
    "runtime_version": "0.2.0",
    "capabilities": {
      "methods": [
        "runtime.capabilities",
        "prompt.preview",
        "workflow.submit",
        "workflow.list",
        "workflow.get",
        "workflow.control",
        "workflow.retry",
        "artifact.register_revision",
        "secret.provide",
        "runtime.shutdown"
      ],
      "pipeline_profiles": [
        "pyannote_qwen3_asr",
        "cloud_asr"
      ],
      "max_inflight_workflows": 3,
      "event_recovery": "snapshot_reconcile",
      "secret_transport": "ephemeral_grant",
      "max_message_bytes": 1048576
    }
  }
}
```

如果客户端和 supervisor 没有共同版本，返回 `UNSUPPORTED_PROTOCOL_VERSION` 并退出。一个进程选择版本后不得切换版本。

## 7. 核心类型

### 7.1 WorkflowDraft

`WorkflowDraft` 是受信任 desktop host 调用 `workflow.submit` 的输入。renderer 只能把稳定 Profile ID 和可选模型覆盖交给 desktop host；host 必须从受信任 catalog 解析 endpoint、认证模式、credential ref、默认或覆盖后的模型和 provider binding，再构造本 Draft。supervisor 负责再次归一化、Prompt 编译、源文件指纹、模型 catalog 快照、路径分配和验证，接受后生成不可变 `WorkflowSpecSnapshot`。

```json
{
  "draft_version": 2,
  "display_name": "客户访谈 2026-07-10",
  "source": {
    "path": "D:\\recordings\\customer-interview.wav"
  },
  "transcription": {
    "pipeline_profile": "pyannote_qwen3_asr",
    "pipeline_profile_version": 1,
    "device_policy": "auto",
    "language": {
      "mode": "auto",
      "value": null
    },
    "prompt_input": {
      "recording_background": "这是一次客户访谈，讨论 ASR Local 产品使用情况。",
      "hotwords": ["MOSS", "ASR Local"],
      "extra_instruction": ""
    },
    "postprocess": {
      "replacements": [
        { "wrong": "ASRLocal", "correct": "ASR Local" }
      ],
      "keep_fillers": true,
      "auto_punctuation": true
    },
    "cloud_profile": null
  },
  "summary": {
    "profile_id": "summary-profile-uuid",
    "profile_version": 4,
    "base_url": "https://example.com/v1",
    "auth_mode": "bearer",
    "model": "summary-model-name",
    "model_source": "profile_default",
    "credential_ref": "credential://summary/summary-profile-uuid",
    "provider_binding_sha256": "hex-digest",
    "template": {
      "id": "summary-template-uuid",
      "version": 3,
      "name": "客户访谈",
      "prompt_snapshot": "请根据转录稿生成结构化客户访谈纪要……"
    },
    "context_strategy": "auto",
    "input_token_budget": 100000,
    "max_output_tokens": 8192
  },
  "output": {
    "directory": "D:\\outputs",
    "base_name": "customer-interview",
    "collision_policy": "unique_suffix"
  }
}
```

#### WorkflowDraft validation

| 字段 | 规则 |
| --- | --- |
| `display_name` | 1..200 个 Unicode 字符 |
| `source.path` | Draft 只提交存在且可读的文件路径；supervisor 接受时解析规范路径并计算 size/mtime，可按策略增加 SHA-256，执行前再次验证 |
| `pipeline_profile` | `pyannote_qwen3_asr`、`cloud_asr` |
| `device_policy` | `auto`、`cpu`、`cuda`；云端链路必须为 `auto` |
| `language.mode` | `auto` 或 `fixed`；`fixed` 时 `value` 必填 |
| `recording_background` | 最多 4000 字符 |
| `hotwords` | 最多 200 项；去空、大小写无关去重；每项最多 64 字符 |
| `extra_instruction` | 最多 1000 字符，不得覆盖锁定输出格式 |
| `replacements` | 最多 500 条；`wrong` 和 `correct` 均不可为空 |
| `summary.profile_id` | 必填稳定 UUID；desktop host 必须从有效且启用的 Summary Profile 解析，supervisor 校验 version、字段结构和 binding 一致性 |
| `summary.base_url` | desktop host 从 Profile 解析；HTTPS 或明确允许的本地开发 URL；不得包含 userinfo 或 query secret |
| `summary.auth_mode` | `none` 或 `bearer` |
| `summary.model` | Profile 默认模型或 Summary Recipe 的显式覆盖；`model_source` 必须说明来源 |
| `summary.credential_ref` | `bearer` 时必填 opaque identity，`none` 时必须为 `null` |
| `summary.provider_binding_sha256` | 必须等于按键名字典序序列化的 `{auth_mode, base_url, model, profile_id, profile_version}` 紧凑 JSON UTF-8 SHA-256 |
| `summary.template.prompt_snapshot` | 必填，最多 32000 字符 |
| `summary.context_strategy` | `auto`、`single_pass` 或 `hierarchical` |
| `input_token_budget` | 正整数，由 Summary Profile 默认并可在能力范围内覆盖 |
| `output.directory` | 必须可创建或可写 |
| `collision_policy` | `reject` 或 `unique_suffix`；v2 不允许静默覆盖 |

`cloud_profile` 是必填 nullable 字段。`cloud_asr` 必须提供该对象，其结构与 summary provider 相同：稳定 `profile_id`、`profile_version`、非敏感 `base_url`、`auth_mode`、resolved `model`、条件性 `credential_ref` 和 `provider_binding_sha256`；本地链路必须令 `cloud_profile = null`。`auth_mode=none` 的 provider 不触发 `credentials_required`。

Provider binding 使用精确的规范化 URL：解析后把 scheme/host 转小写，移除 userinfo、query、fragment 和默认端口，pathname 除根路径外去除末尾 `/`；随后对 `{ "profile_id", "base_url", "auth_mode" }` 按 RFC 8785 JCS 编码并计算 SHA-256。desktop host 与 supervisor 都必须独立计算并比对。

### 7.2 WorkflowSpecSnapshot

supervisor 接受任务时生成并持久化：

```json
{
  "spec_version": 2,
  "display_name": "客户访谈 2026-07-10",
  "source": {
    "path": "D:\\recordings\\customer-interview.wav",
    "fingerprint": {
      "size_bytes": 12345678,
      "modified_ms": 1780000000000,
      "sha256": null
    }
  },
  "transcription": {
    "pipeline_profile": "pyannote_qwen3_asr",
    "pipeline_profile_version": 1,
    "device_policy": "auto",
    "audio": { "channel_strategy": "mixdown" },
    "language": { "mode": "auto", "value": null },
    "prompt_input": {
      "recording_background": "这是一次客户访谈，讨论 ASR Local 产品使用情况。",
      "hotwords": ["MOSS", "ASR Local"],
      "extra_instruction": ""
    },
    "prompt_snapshot": {
      "compiler_id": "qwen-prompt",
      "compiler_version": 1,
      "base_template_version": "qwen-segment-v1",
      "compiled_text": "请准确转写音频内容……热词提示：MOSS, ASR Local",
      "sha256": "hex-digest"
    },
    "postprocess": {
      "replacements": [
        { "wrong": "ASRLocal", "correct": "ASR Local" }
      ],
      "keep_fillers": true,
      "auto_punctuation": true
    },
    "cloud_profile": null,
    "model_snapshot": {
      "components": [
        {
          "role": "transcriber",
          "model_id": "Qwen/Qwen3-ASR-1.7B",
          "revision": "locked-upstream-revision",
          "config_sha256": "hex-digest",
          "resolved_path": "D:\\models\\Qwen3-ASR-1.7B"
        },
        {
          "role": "diarization",
          "model_id": "pyannote/speaker-diarization-community-1",
          "revision": "locked-upstream-revision",
          "config_sha256": "hex-digest",
          "resolved_path": "D:\\models\\speaker-diarization-community-1"
        }
      ]
    }
  },
  "summary": {
    "profile_id": "summary-profile-uuid",
    "profile_version": 4,
    "base_url": "https://example.com/v1",
    "auth_mode": "bearer",
    "model": "summary-model-name",
    "model_source": "profile_default",
    "credential_ref": "credential://summary/summary-profile-uuid",
    "provider_binding_sha256": "hex-digest",
    "template": {
      "id": "summary-template-uuid",
      "version": 3,
      "name": "客户访谈",
      "prompt_snapshot": "请根据转录稿生成结构化客户访谈纪要……",
      "sha256": "hex-digest"
    },
    "context_strategy": "auto",
    "input_token_budget": 100000,
    "max_output_tokens": 8192
  },
  "output": {
    "directory": "D:\\outputs\\customer-interview--wf_018f",
    "base_name": "customer-interview",
    "collision_policy": "unique_suffix"
  }
}
```

规则：

- Snapshot 一旦接受不可变。
- Snapshot 不含 secret。
- Prompt 与模板保存正文、版本和 digest，用于复现。
- 本地 pipeline 的 `model_snapshot.components` 保存稳定模型 ID、revision、配置 digest 和解析路径；两个新本地 profile 都必须同时包含 `transcriber` 与 `diarization` component。旧 profile 仅为历史兼容。Cloud pipeline 的组件列表为空，身份由 `cloud_profile` 快照保存。
- Summary Profile 拥有 endpoint、认证模式、credential ref 和默认模型；Summary Recipe 只有显式配置时才覆盖模型，`model_source` 记录最终来源。
- `device_policy` 是用户意图，实际设备属于 RuntimePlan。
- 任务执行前源文件 fingerprint 不一致时失败为 `SOURCE_CHANGED`。

### 7.3 WorkflowStatus

```text
queued
running
paused
waiting_for_secret
completed
failed
cancelled
interrupted
```

- `completed`、`failed`、`cancelled` 是当前执行结果的终态；其中 `failed` 和 `completed` 仍可通过显式 retry 创建新 attempt。
- `interrupted` 表示 supervisor 异常结束；旧 attempt 已是终态，但 workflow 保持可恢复，等待用户显式 retry 或 cancel。启动不得自动创建新 attempt。
- `waiting_for_secret` 表示 supervisor 已请求临时凭据但尚未获得有效 grant。

### 7.4 WorkflowStage

```text
validating
queued
preparing
transcribing
transcript_ready
summarizing
writing_final
completed
```

`status` 表示生命周期，`stage` 表示处理步骤，两者不得互换。

### 7.5 RuntimePlan

```json
{
  "resolved_device": "cuda:0",
  "dtype": "bfloat16",
  "workflow_capacity": 3,
  "asr_inference_capacity": 1,
  "model_replicas": 1,
  "reason": "auto_cuda_warmup_succeeded",
  "warnings": []
}
```

- RuntimePlan 由 supervisor 在执行尝试中生成。
- `workflow_capacity=3` 表示最多三个已经取得工作流执行容量的 in-flight workflow；已接受但仍等待该容量的 backlog workflow 不计入，且必须可以取消。
- 同一工作流任务重试时可以因硬件变化生成新的 RuntimePlan。
- `auto` 可以在转录开始前从 GPU 受控降级到 CPU；不得在已经产生部分转录后静默切换设备并拼接结果。
- 用户强制 `cuda` 而不可用时必须失败，不得回退 CPU。

### 7.6 ArtifactRef

```json
{
  "artifact_id": "artifact-uuid",
  "kind": "transcript_markdown",
  "revision": 1,
  "origin": "generated",
  "derived_from_artifact_id": null,
  "input_artifact_ids": [],
  "stale": false,
  "path": "D:\\outputs\\customer-interview--wf_018f\\transcript--r1.md",
  "size_bytes": 18234,
  "sha256": "hex-digest",
  "created_at": "2026-07-10T12:10:00Z"
}
```

支持的首版 `kind`：

```text
workflow_manifest
transcript_markdown
transcript_json
final_summary_markdown
final_summary_json
summary_checkpoint_json
diagnostic_log
```

只有文件成功写入临时路径并原子替换后，才能发布 ArtifactRef。已发布 artifact 不可原地修改或复用同一路径；用户编辑必须通过 `artifact.register_revision` 创建 `origin=user_edited` 的新 revision，并用 `derived_from_artifact_id` 指向直接来源。总结 artifact 的 `input_artifact_ids` 必须包含所使用的 transcript artifact ID；输入 transcript 出现更新修订时，基于旧修订的总结标记为 `stale=true`，但不得删除。

### 7.7 WorkflowSnapshot

```json
{
  "snapshot_version": 2,
  "workflow_id": "wf_018f...",
  "sequence": 17,
  "spec": {},
  "status": "running",
  "stage": "transcribing",
  "attempt": {
    "attempt_id": "att_018f...",
    "number": 1,
    "stage_attempts": {
      "transcription": 1,
      "summary": 0,
      "writing_final": 0
    }
  },
  "progress": {
    "stage_ratio": 0.42,
    "overall_ratio": 0.31,
    "processed_ms": 120000,
    "total_ms": 285000,
    "queue_position": null
  },
  "control": {
    "pending_action": null
  },
  "runtime_plan": {},
  "artifacts": [],
  "recovery": {
    "recommended_retry_stage": null,
    "interrupted_attempt_id": null,
    "input_artifact_id": null
  },
  "last_error": null,
  "timestamps": {
    "created_at": "2026-07-10T11:58:00Z",
    "updated_at": "2026-07-10T12:04:00Z",
    "started_at": "2026-07-10T12:00:00Z",
    "completed_at": null
  }
}
```

规则：

- Snapshot 是 UI 和恢复逻辑的唯一权威状态。
- 同一 `attempt_id` 内 stage progress 不得倒退；新 attempt 可以重新从低进度开始。
- `control.pending_action` 可为 `pause` 或 `cancel`；pause/cancel 接受不等于立即生效。
- `artifacts` 是按 revision 保留的 ArtifactRef 列表；同一 kind 可以存在多个 revision。
- `recovery.recommended_retry_stage` 只提供建议，不会自动创建 attempt 或调用模型/provider。
- `recovery.input_artifact_id` 仅在用户显式选择 transcript revision 进行 summary retry 时保存，worker 必须验证该 revision 属于本 workflow 且未标记 stale。
- `last_error` 使用第 12 节错误模型且不得包含 secret。

## 8. 合法状态迁移

正常主路径：

```text
queued/validating
→ queued/queued
→ running/preparing
→ running/transcribing
→ running/transcript_ready
→ running/summarizing
→ running/writing_final
→ completed/completed
```

控制与异常：

- 支持暂停的阶段收到 pause：`control.pending_action=pause`，到安全点后 `status=paused`，stage 保持不变。
- resume：`status=running`，清空 pending action，从原 stage 继续。
- cancel：先设置 `control.pending_action=cancel`，到安全点后进入 `cancelled`。
- 排队任务 cancel 可以直接进入 `cancelled`。
- 云端阶段需要凭据时，stage 保持不变并进入 `waiting_for_secret`；有效临时授权到达后恢复为 `running`，授权超时则进入 `failed`。
- 任一非终态可以进入 `failed` 或 `interrupted`。
- 总结失败后转录 ArtifactRef 必须保留。
- retry 创建新 `attempt_id`；旧 attempt 不能再改变当前 Snapshot。
- `workflow.submit` 成功时在同一事务中创建 number=1 的初始 queued attempt。`attempt_started` 只在 scheduler 获得工作流执行容量并真正开始使用运行资源时产生，不等同于 submit accepted。
- `failed`、`completed` 或 `interrupted` 接受 retry 后，在同一事务中创建新 attempt 并进入 `queued/queued`；取得执行容量后进入 `running/preparing`，验证所选检查点与 RuntimePlan 后再进入 `transcribing`、`summarizing` 或 `writing_final`。三层状态机 fixture 必须使用该固定迁移。

## 9. WorkflowEvent

核心 `type`：

```text
submitted
attempt_started
runtime_plan_resolved
state_changed
progress
artifact_ready
credentials_required
paused
resumed
completed
failed
cancelled
interrupted
recovered
```

规则：

- 每个 event 携带 `workflow_id`、当前 `attempt_id`、sequence、stage 和完整可变 state，但不重复发送不可变 Spec；重复路由字段必须与 state 完全相等。
- `credentials_required` 不携带 secret，只携带临时请求 ID、credential ref、用途、provider binding 和过期时间。
- 客户端不得仅依赖 event 构建真实状态；事件间隙通过 `workflow.get` 对账。
- supervisor 重启后 `worker_instance_id` 变化，但 workflow sequence 继续递增。
- `recovered` 只可在用户显式 retry 创建新 attempt 后产生；启动扫描本身不得把 workflow 改回 running。

## 10. Methods

### 10.1 `runtime.capabilities`

查询运行时、模型和硬件能力。

```json
{
  "include_hardware": true,
  "include_model_readiness": true
}
```

Response 至少包含：

- 支持的 pipeline profiles；
- 模型路径是否可用；
- Prompt compiler 与输入限制；
- CPU、CUDA、BF16/FP16 能力；
- 最大处理中任务数；
- 当前 stage resource capacities；
- Summary context strategies；
- 支持的控制动作和阶段。

### 10.2 `prompt.preview`

对结构化 Prompt 输入进行服务端编译，供提交前预览。

```json
{
  "pipeline_profile": "pyannote_qwen3_asr",
  "language": { "mode": "auto", "value": null },
  "prompt_input": {
    "recording_background": "这是一次产品评审会议。",
    "hotwords": ["MOSS", "ASR Local"],
    "extra_instruction": ""
  }
}
```

Response：

```json
{
  "compiler_id": "qwen-prompt",
  "compiler_version": 1,
  "base_template_version": "qwen-segment-v1",
  "compiled_text": "请将音频转写为文本……",
  "sha256": "hex-digest",
  "warnings": []
}
```

`workflow.submit` 必须重新编译并验证，不得信任客户端 preview 结果。

### 10.3 `workflow.submit`

需要 `operation_id`。

Params：

```json
{
  "draft": {}
}
```

Result：

```json
{
  "created": true,
  "deduplicated": false,
  "snapshot": {}
}
```

语义：

- submit response 表示任务已验证到可接受程度并持久化，不表示模型已加载或任务已开始。
- workflow、不可变 Spec 和 number=1 的 queued attempt 必须在同一事务中持久化；response 必须发生在 scheduler 开始执行前。
- response 成功后才允许发送该操作引起的事件。
- 在事务提交后、response 发送前崩溃时，相同 operation ID 重发必须返回原任务。
- desktop host 在发出 submit 前以 operation ID 暂存版本化 Provider Authorization Snapshot，成功或去重响应后绑定返回的 workflow ID；host crash 后以同一 operation ID 重发并对账。缺少该受信任快照时不得为后续 `credentials_required` 释放 secret。

### 10.4 `workflow.list`

```json
{
  "statuses": ["queued", "running", "paused", "waiting_for_secret", "interrupted"],
  "cursor": null,
  "limit": 50
}
```

Result：

```json
{
  "items": [],
  "next_cursor": null
}
```

- `limit` 范围 1..100。
- 排序固定为 `created_at DESC, workflow_id DESC`。
- 列表项使用轻量 summary，不返回完整 Prompt 文本。

### 10.5 `workflow.get`

```json
{
  "workflow_id": "wf_018f...",
  "timeline_limit": 200
}
```

Result：

```json
{
  "snapshot": {},
  "timeline": [
    {
      "sequence": 17,
      "attempt_id": "att_018f...",
      "type": "artifact_ready",
      "stage": "transcript_ready",
      "occurred_at": "2026-07-10T12:10:00Z",
      "detail": "Transcript checkpoint is ready."
    }
  ],
  "attempt_history": []
}
```

- `timeline_limit` 范围 0..500，默认 200。
- timeline 是持久事件的轻量读模型，用于任务详情；不包含 secret、正文或完整 Prompt。
- `attempt_history` 至少包含 attempt ID、序号、开始/结束时间、结束状态和失败阶段。
- 客户端仍以 Snapshot 为当前状态权威，不通过 timeline 重放构建状态。

### 10.6 `workflow.control`

需要 `operation_id`。

```json
{
  "workflow_id": "wf_018f...",
  "expected_attempt_id": "att_018f...",
  "action": "pause"
}
```

`action`：`pause`、`resume`、`cancel`。

Result：

```json
{
  "accepted": true,
  "snapshot": {}
}
```

规则：

- `expected_attempt_id` 不匹配时返回 `STALE_ATTEMPT`。
- response 只表示控制意图被接受；实际状态由后续 snapshot/event 表示。
- `resume` 只适用于同一 attempt 的 `paused` 状态；`interrupted` attempt 不可 resume，必须显式调用 `workflow.retry` 创建新 attempt。
- 正在执行的单次模型 `generate` 可能只能在安全点响应暂停或取消。
- 对已取消任务重复 cancel 返回当前 snapshot，保持幂等。
- 对不支持该动作的阶段返回 `CONTROL_NOT_SUPPORTED`。

### 10.7 `workflow.retry`

需要 `operation_id`。

```json
{
  "workflow_id": "wf_018f...",
  "expected_attempt_id": "att_018f...",
  "expected_sequence": 24,
  "from_stage": "auto",
  "input_artifact_id": "transcript-artifact-uuid"
}
```

`from_stage`：`auto`、`transcribing`、`summarizing`、`writing_final`。

规则：

- retry 创建新的 `attempt_id`，`workflow_id` 不变。
- `auto` 从失败阶段和最近有效检查点决定。
- summary retry 保留 transcript artifacts；`input_artifact_id` 缺省时使用最新非 stale transcript，显式提供时必须引用本 workflow 的有效 transcript revision。
- writing retry 保留总结结果；若首版不持久化总结中间结果，则必须降级为 summary retry 并明确返回。
- transcription retry 必须版本化或清除所有下游产物引用，不能把旧 summary 误标为新结果。
- 旧 attempt 的迟到事件必须忽略。

### 10.8 `artifact.register_revision`

需要 `operation_id`。desktop editor 先把新内容写到 workflow 专属 staging 目录；supervisor 验证路径边界、大小与 digest 后原子登记为新产物修订。

```json
{
  "workflow_id": "wf_018f...",
  "expected_attempt_id": "att_018f...",
  "expected_sequence": 24,
  "source_artifact_id": "transcript-artifact-uuid",
  "kind": "transcript_markdown",
  "staged_path": "D:\\outputs\\customer-interview--wf_018f\\.staging\\edit-uuid.md",
  "size_bytes": 18320,
  "sha256": "hex-digest"
}
```

Result：

```json
{
  "artifact": {},
  "snapshot": {}
}
```

规则：

- 首版只允许为 `transcript_markdown` 或 `final_summary_markdown` 创建同 kind 修订；`source_artifact_id` 必须属于本 workflow。
- supervisor 分配新的 `artifact_id` 和递增 revision，设置 `origin=user_edited` 与 `derived_from_artifact_id`；原文件和原 ArtifactRef 保持不变。
- staged file 必须位于 supervisor 为该 workflow 预留的 staging 目录，且 digest/size 匹配；登记后原子移动到不可变 revision 路径。
- transcript 修订会把所有未使用该新 revision 的下游 summary artifacts 标记为 `stale=true`，并产生一个事务性事件。
- 本方法只登记人工编辑，不创建 attempt；基于新 transcript 重新总结必须另行调用 `workflow.retry` 并传入该 `input_artifact_id`。

### 10.9 `secret.provide`

秘密只通过本方法临时提供，不进入 WorkflowDraft。该方法不使用持久 `operation_id`；`secret_request_id` 在当前 supervisor 实例内提供一次性幂等身份，避免为了去重而持久保存 secret 或其可离线猜测的 digest。

`credentials_required` event data：

```json
{
  "secret_request_id": "secret_req_018f...",
  "profile_id": "summary-profile-uuid",
  "profile_version": 4,
  "credential_ref": "credential://summary/summary-profile-uuid",
  "purpose": "summary_api",
  "provider_binding_sha256": "hex-digest",
  "expires_at": "2026-07-10T12:05:00Z"
}
```

`purpose` 枚举固定为：

```text
summary_api
cloud_asr
```

Request：

```json
{
  "workflow_id": "wf_018f...",
  "expected_attempt_id": "att_018f...",
  "secret_request_id": "secret_req_018f...",
  "profile_id": "summary-profile-uuid",
  "profile_version": 4,
  "credential_ref": "credential://summary/summary-profile-uuid",
  "purpose": "summary_api",
  "provider_binding_sha256": "hex-digest",
  "secret": "ephemeral-plaintext-secret",
  "lease_scope": "attempt"
}
```

规则：

- grant 只存在于 supervisor 进程内存，作用域限定到 workflow、attempt、Profile ID/version、credential ref、purpose 和 provider binding。
- grant 使用后、attempt 结束、超时或 supervisor 重启时立即失效。
- 相同 `secret_request_id` 的重复授权返回当前接受结果；已过期或已经绑定到其他身份的请求被拒绝。
- supervisor 不得记录原始 request line 或 secret 字段。
- secret request 过期或身份不匹配时返回 `CREDENTIAL_REJECTED`。
- Summary 和 cloud ASR 共用本机制。
- desktop host 必须使用任务提交时持久化的非敏感 Provider Authorization Snapshot 进行比对，而不是读取 Profile 当前值。该快照至少包含 workflow ID、Profile ID/version、规范化 endpoint、auth mode、credential ref、purpose 与 provider binding；workflow、attempt、Profile version、purpose、credential ref 或 binding 任一不匹配都不得释放 secret。
- Profile 编辑创建新版本，不能改写或删除仍被 workflow 引用的历史授权快照。删除 Profile 只阻止新任务选择；只有用户显式撤销 credential 才会使旧任务授权失败，此时返回 `CREDENTIAL_REJECTED`，但不修改不可变 Spec。
- `auth_mode=none` 时 `credential_ref=null`，supervisor 不得发出 `credentials_required` 或接受 `secret.provide`。
- Python 字符串无法保证可靠内存清零；本地 supervisor 进程内存属于受信任范围。

### 10.10 `runtime.shutdown`

不使用持久 `operation_id`。shutdown 只作用于当前 hello 返回的 `worker_instance_id`；同一进程内重复调用返回当前 shutdown state，进程重启后不存在跨实例 shutdown 去重语义。

```json
{
  "mode": "interrupt",
  "grace_ms": 10000
}
```

`mode`：

- `drain`：停止接受新任务，等待当前任务完成后退出。
- `interrupt`：把运行中任务持久化为可恢复中断，协作停止后退出。

Result：

```json
{
  "state": "interrupting",
  "active_workflow_ids": ["wf_018f..."]
}
```

response 表示停止策略已生效；进程实际结束以 stdout EOF 为准。host 在 grace 超时后可以强制结束进程。

## 11. Summary context strategy

总结 adapter 不得静默截断转录稿。

- `single_pass`：转录稿超过 `input_token_budget` 时失败为 `SUMMARY_INPUT_TOO_LARGE`。
- `hierarchical`：按稳定段落边界拆分，生成局部总结后再进行归并；所有分块和归并尝试共享同一 attempt，但需要独立诊断 ID。
- `auto`：预算内使用 single pass，超出预算时使用 hierarchical。
- 每次外部总结调用必须有稳定 provider request key；provider 支持幂等 header 时必须使用。
- 如果外部 provider 已处理请求但 response 丢失，系统不得宣称 exactly-once；应记录 `SUMMARY_RESULT_UNKNOWN` 并要求用户确认是否重试，以避免无提示重复计费。
- 鉴权失败不自动重试；限流和临时网络错误最多自动重试两次，并采用带抖动的指数退避。

## 12. Error model

Command error 与 workflow failure 共享：

```json
{
  "code": "RESOURCE_EXHAUSTED",
  "message": "Not enough GPU memory for the selected runtime plan.",
  "retryable": true,
  "field_errors": [],
  "details": {
    "required_action": "use_cpu_or_reduce_concurrency"
  },
  "diagnostic_id": "diag_018f..."
}
```

workflow failure 额外记录 `workflow_id`、`attempt_id`、`stage` 和 `occurred_at`。

首版错误码：

```text
INVALID_REQUEST
HANDSHAKE_REQUIRED
UNSUPPORTED_PROTOCOL_VERSION
UNSUPPORTED_METHOD
OPERATION_ID_REUSED
NOT_FOUND
INVALID_TRANSITION
CONTROL_NOT_SUPPORTED
STALE_ATTEMPT
SEQUENCE_CONFLICT
SOURCE_CHANGED
OUTPUT_CONFLICT
MODEL_NOT_AVAILABLE
MODEL_LOAD_FAILED
RESOURCE_EXHAUSTED
CREDENTIAL_REQUIRED
CREDENTIAL_REJECTED
SUMMARY_INPUT_TOO_LARGE
SUMMARY_RESULT_UNKNOWN
PROVIDER_AUTH_FAILED
PROVIDER_RATE_LIMITED
PROVIDER_TIMEOUT
WORKER_INTERRUPTED
SHUTTING_DOWN
INTERNAL
```

`message` 面向用户；traceback、HTTP response body 和原始 provider payload 只进入本地诊断日志，通过 `diagnostic_id` 关联。错误中不得出现 secret、完整转录稿或未经用户允许的完整 Prompt。

## 13. Secret handling

1. WorkflowDraft、WorkflowSpecSnapshot、WorkflowSnapshot、WorkflowEvent、artifact metadata、operation payload digest 和日志都不得保存 secret。
2. 只保存 opaque `credential_ref`。
3. Profile 使用稳定 UUID 和不可变 version；可改名的 Profile name 不得作为 credential identity。Summary Profile 拥有 endpoint、认证模式、credential ref 和默认模型，Summary Recipe 只能显式覆盖模型。
4. worker 重启后 grant 消失；任务再次发出 `credentials_required`。
5. renderer 只提交 Profile identity 和可选模型覆盖；桌面受信任层负责解析并持久化版本化的非敏感 Provider Authorization Snapshot、校验 provider binding，并从 DPAPI 或未来系统 keychain 解析秘密。
6. Profile 编辑和删除不得清除仍被 workflow 引用的非敏感历史版本；credential 的显式撤销独立处理并可阻止旧任务再次取得 secret。
7. 日志层必须执行字段级 redaction，不得记录收到的完整 JSONL request。

## 14. Reconnect 与恢复

标准连接流程：

1. host 注册 stdout reader 和事件分发器。
2. host 启动 supervisor。
3. 调用 `runtime.hello`，记录新的 `worker_instance_id`。
4. 调用 `workflow.list` 查询非终态任务。
5. 对本地已知或 sequence 不连续的任务调用 `workflow.get`。
6. 以 Snapshot sequence upsert 本地状态。
7. 对 `waiting_for_secret` 任务重新完成临时授权。
8. 持续消费 event；发现 sequence gap 时再次对账。

supervisor 启动恢复规则：

- 数据库中的 `running` 或 `waiting_for_secret` attempt 在启动事务中标记为 terminal `interrupted`，workflow snapshot 进入 recoverable `interrupted`。
- supervisor 根据不可变检查点计算 `recovery.recommended_retry_stage`：完整 transcript 存在时建议 `summarizing`，只有完整 summary checkpoint 时可建议 `writing_final`，否则建议 `transcribing`；首版不承诺帧级断点。
- 启动扫描不得自动创建新 attempt、加载模型或调用 provider。只有用户显式调用 `workflow.retry` 且 expected attempt/sequence 匹配时，才在单一事务中创建新 attempt 并继续增加 workflow sequence。
- 旧 attempt 的所有晚到结果不得写入当前快照或产物引用。

## 15. 必须避免的竞态

1. 不得把资源槽或 lane 当作用户身份或控制目标。
2. 不得把 `request_id`、`operation_id`、`workflow_id` 和 `attempt_id` 混用。
3. 不得在不同层发现 ID 缺失时各自生成任务 ID。
4. 不得在事务提交前 emit event。
5. 不得让多个线程直接写 stdout。
6. 不得把 submit accepted 展示成 workflow started 或 completed。
7. 不得让旧 attempt 的迟到 completion 覆盖 retry 后的新 attempt。
8. 不得假定 pause/cancel response 表示模型已立即停止。
9. 不得让 Profile、模板或全局模型后续修改改变已提交 Spec。
10. 不得在三个并发任务之间共享可变 Prompt、输出文件名或总结 state。
11. 不得让并发任务静默覆盖同一输出路径。
12. 不得把 event 当作可靠事件日志或唯一状态来源。
13. 不得在 worker 忙于推理时停止读取 stdin；命令循环与执行调度必须解耦。
14. 不得通过 kill 共享模型进程取消单个任务，以免影响其他任务。
15. 不得把三任务在途解释为三个模型副本或固定三路 GPU `generate`。
16. 不得让 renderer 自行提交 endpoint、credential ref 或 provider binding，也不得按 Profile 当前值覆盖提交时授权快照，或向与任务规格不一致的 endpoint 释放 secret。
17. 不得原地覆盖已登记 artifact；编辑和派生输出必须创建新 revision。

## 16. v1 迁移

1. 冻结 v1 文档和现有 JSONL fixture，不继续扩展 v1 字段。
2. Python 增加显式 `--contract v2` supervisor 启动模式；v1 保留原入口。
3. 桌面建立 `WorkflowRuntime` interface，保留 v1 adapter 并新增 v2 adapter。
4. v2 先用 fake transcriber、fake summary provider 和无秘密测试凭据完成包含 mandatory summary 的完整状态机 vertical slice，再替换生产 adapter 和 secret broker；不得引入 `summary=null` 的临时协议分支。
5. 使用显式 feature flag 选择 adapter；禁止根据返回内容自动猜测版本。
6. 旧历史继续作为 legacy history 展示，不伪造成完整 v2 快照。
7. 在生产 UI 接入前把 Summary/ASR Profile、Summary Template 和本地模型 catalog 迁移为稳定 UUID/ID 与显式 version，同时保留现有 DPAPI 密文并新增 credential ref 映射。
8. 并行期一个工作流任务只能使用一个 adapter，不允许从 v1 中途切换到 v2。
9. 通过 submit 幂等、supervisor crash、事件 gap、旧 attempt 迟到、任务级控制、summary retry 和 secret 重新授权测试后，v2 才能成为默认。
10. v2 稳定一个发布周期后，移除 v1 lane interface；Legacy 转录链路本身继续保留。

## 17. Contract fixtures

实现阶段必须维护语言无关的 JSON fixtures，至少覆盖：

```text
hello.request.json
hello.response.json
prompt-preview.request.json
prompt-preview.response.json
workflow-submit.request.json
workflow-submit.response.json
workflow-list.response.json
workflow-get.response.json
workflow-progress.event.json
workflow-summary-credentials-required.event.json
workflow-cloud-asr-credentials-required.event.json
workflow-control.request.json
workflow-retry.request.json
artifact-register-revision.request.json
artifact-register-revision.response.json
secret-provide-summary.request.json
secret-provide-cloud-asr.request.json
workflow-failed.event.json
workflow-completed.event.json
error.response.json
shutdown.request.json
```

TypeScript、Rust 和 Python 必须对同一 fixture 执行解析、必填字段、枚举、未知字段和 round-trip 测试。
