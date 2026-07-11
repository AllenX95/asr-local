import { electronBridge } from '../../ipc/desktopBridge'
import type { WorkflowEventHandler, WorkflowRuntime } from '../runtime'
import type { ArtifactRevisionCommand, PromptPreviewInput, PromptPreviewResult, WorkflowCapabilities, WorkflowControlCommand, WorkflowDraft, WorkflowRetryCommand, WorkflowSnapshot } from '../types'

const operationId = (prefix: string) => `${prefix}_${crypto.randomUUID()}`

export class ElectronWorkflowRuntime implements WorkflowRuntime {
  private get bridge() { const bridge = electronBridge(); if (!bridge) throw new Error('Electron desktop bridge is unavailable'); return bridge }
  capabilities(): Promise<WorkflowCapabilities> { return this.bridge.invoke('workflow_v2_capabilities') }
  previewPrompt(input: PromptPreviewInput): Promise<PromptPreviewResult> { return this.bridge.invoke('workflow_v2_prompt_preview', { input }) }
  async submit(draft: WorkflowDraft): Promise<WorkflowSnapshot> { const result = await this.bridge.invoke<{ snapshot: WorkflowSnapshot }>('workflow_v2_submit', { operationId: operationId('op_submit'), draft }); return result.snapshot }
  async list(): Promise<WorkflowSnapshot[]> { const result = await this.bridge.invoke<{ items: WorkflowSnapshot[] }>('workflow_v2_list', { statuses: null }); return result.items }
  async get(workflowId: string): Promise<WorkflowSnapshot> { const result = await this.bridge.invoke<{ snapshot: WorkflowSnapshot; timeline?: WorkflowSnapshot['timeline'] }>('workflow_v2_get', { workflowId, timelineLimit: 200 }); return { ...result.snapshot, timeline: result.timeline ?? [] } }
  async clear(workflowId: string): Promise<void> { await this.bridge.invoke('workflow_v2_clear', { operationId: operationId('op_clear'), workflowId }) }
  async control(command: WorkflowControlCommand): Promise<WorkflowSnapshot> { const result = await this.bridge.invoke<{ snapshot: WorkflowSnapshot }>('workflow_v2_control', { operationId: operationId('op_control'), workflowId: command.workflow_id, expectedAttemptId: command.expected_attempt_id, action: command.action }); return result.snapshot }
  async retry(command: WorkflowRetryCommand): Promise<WorkflowSnapshot> { const result = await this.bridge.invoke<{ snapshot: WorkflowSnapshot }>('workflow_v2_retry', { operationId: operationId('op_retry'), workflowId: command.workflow_id, expectedAttemptId: command.expected_attempt_id, expectedSequence: command.expected_sequence, fromStage: command.from_stage, inputArtifactId: command.input_artifact_id ?? null }); return result.snapshot }
  async registerRevision(command: ArtifactRevisionCommand): Promise<WorkflowSnapshot> { const result = await this.bridge.invoke<{ snapshot: WorkflowSnapshot }>('workflow_v2_register_revision', { operationId: operationId('op_revision'), params: command }); return result.snapshot }
  subscribe(handler: WorkflowEventHandler): () => void { return this.bridge.onWorkflowEvent(handler) }
}
