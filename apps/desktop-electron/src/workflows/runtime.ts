import type {
  ArtifactRevisionCommand,
  PromptPreviewInput,
  PromptPreviewResult,
  WorkflowCapabilities,
  WorkflowControlCommand,
  WorkflowDraft,
  WorkflowEvent,
  RuntimeStatusEvent,
  WorkflowRetryCommand,
  WorkflowSnapshot,
} from './types'

export type WorkflowEventHandler = (event: WorkflowEvent) => void
export type RuntimeStatusHandler = (event: RuntimeStatusEvent) => void
export type Unsubscribe = () => void

/** Platform-neutral seam implemented by Electron and test adapters. */
export interface WorkflowRuntime {
  capabilities(): Promise<WorkflowCapabilities>
  previewPrompt(input: PromptPreviewInput): Promise<PromptPreviewResult>
  submit(draft: WorkflowDraft): Promise<WorkflowSnapshot>
  list(): Promise<WorkflowSnapshot[]>
  get(workflowId: string): Promise<WorkflowSnapshot>
  clear(workflowId: string): Promise<void>
  control(command: WorkflowControlCommand): Promise<WorkflowSnapshot>
  retry(command: WorkflowRetryCommand): Promise<WorkflowSnapshot>
  registerRevision(command: ArtifactRevisionCommand): Promise<WorkflowSnapshot>
  subscribe(handler: WorkflowEventHandler): Unsubscribe
  subscribeRuntimeStatus(handler: RuntimeStatusHandler): Unsubscribe
}
