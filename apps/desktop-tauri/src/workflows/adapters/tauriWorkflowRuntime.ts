import { invoke } from '@tauri-apps/api/core'
import { listen } from '@tauri-apps/api/event'
import type { WorkflowEventHandler, WorkflowRuntime } from '../runtime'
import type {
  ArtifactRevisionCommand,
  PromptPreviewInput,
  PromptPreviewResult,
  WorkflowCapabilities,
  WorkflowControlCommand,
  WorkflowDraft,
  WorkflowEvent,
  WorkflowRetryCommand,
  WorkflowSnapshot,
} from '../types'

const operationId = (prefix: string) => `${prefix}_${crypto.randomUUID()}`

/** Tauri adapter; Vue and Pinia depend only on WorkflowRuntime. */
export class TauriWorkflowRuntime implements WorkflowRuntime {
  async capabilities(): Promise<WorkflowCapabilities> {
    return invoke<WorkflowCapabilities>('workflow_v2_capabilities')
  }

  async previewPrompt(input: PromptPreviewInput): Promise<PromptPreviewResult> {
    return invoke<PromptPreviewResult>('workflow_v2_prompt_preview', { input })
  }

  async submit(draft: WorkflowDraft): Promise<WorkflowSnapshot> {
    const result = await invoke<{ snapshot: WorkflowSnapshot }>('workflow_v2_submit', { operationId: operationId('op_submit'), draft })
    return result.snapshot
  }

  async list(): Promise<WorkflowSnapshot[]> {
    const result = await invoke<{ items: WorkflowSnapshot[] }>('workflow_v2_list', { statuses: null })
    return result.items
  }

  async get(workflowId: string): Promise<WorkflowSnapshot> {
    const result = await invoke<{ snapshot: WorkflowSnapshot; timeline?: WorkflowSnapshot['timeline'] }>('workflow_v2_get', { workflowId, timelineLimit: 200 })
    return { ...result.snapshot, timeline: result.timeline ?? [] }
  }

  async clear(workflowId: string): Promise<void> {
    await invoke('workflow_v2_clear', { operationId: operationId('op_clear'), workflowId })
  }

  async control(command: WorkflowControlCommand): Promise<WorkflowSnapshot> {
    const result = await invoke<{ snapshot: WorkflowSnapshot }>('workflow_v2_control', {
      operationId: operationId('op_control'),
      workflowId: command.workflow_id,
      expectedAttemptId: command.expected_attempt_id,
      action: command.action,
    })
    return result.snapshot
  }

  async retry(command: WorkflowRetryCommand): Promise<WorkflowSnapshot> {
    const result = await invoke<{ snapshot: WorkflowSnapshot }>('workflow_v2_retry', {
      operationId: operationId('op_retry'),
      workflowId: command.workflow_id,
      expectedAttemptId: command.expected_attempt_id,
      expectedSequence: command.expected_sequence,
      fromStage: command.from_stage,
      inputArtifactId: command.input_artifact_id ?? null,
    })
    return result.snapshot
  }

  async registerRevision(command: ArtifactRevisionCommand): Promise<WorkflowSnapshot> {
    const result = await invoke<{ snapshot: WorkflowSnapshot }>('workflow_v2_register_revision', {
      operationId: operationId('op_revision'),
      params: {
        workflow_id: command.workflow_id,
        expected_attempt_id: command.expected_attempt_id,
        expected_sequence: command.expected_sequence,
        source_artifact_id: command.source_artifact_id,
        kind: command.kind,
        staged_path: command.staged_path,
        size_bytes: command.size_bytes,
        sha256: command.sha256,
      },
    })
    return result.snapshot
  }

  subscribe(handler: WorkflowEventHandler): () => void {
    let disposed = false
    let unlisten: (() => void) | undefined
    void listen<WorkflowEvent>('workflow-event-v2', (event) => {
      const payload = event.payload
      handler(payload)
      if (payload.type === 'credentials_required') {
        void invoke('workflow_v2_provide_secret', {
          workflowId: payload.workflow_id,
          expectedAttemptId: payload.attempt_id,
          requestData: payload.data,
        }).catch(() => undefined)
      }
    }).then((stop) => {
      if (disposed) stop()
      else unlisten = stop
    })
    return () => {
      disposed = true
      unlisten?.()
    }
  }
}
