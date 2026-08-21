export interface TaskCenterState {
  diagnosticsWorkflowId: string | null
  resummaryWorkflowId: string | null
}

export function createTaskCenterState(): TaskCenterState {
  return { diagnosticsWorkflowId: null, resummaryWorkflowId: null }
}

export function toggleDiagnostics(state: TaskCenterState, workflowId: string): void {
  state.diagnosticsWorkflowId = state.diagnosticsWorkflowId === workflowId ? null : workflowId
}

export function toggleResummary(state: TaskCenterState, workflowId: string, open?: boolean): void {
  state.resummaryWorkflowId = open === undefined
    ? state.resummaryWorkflowId === workflowId ? null : workflowId
    : open ? workflowId : state.resummaryWorkflowId === workflowId ? null : state.resummaryWorkflowId
}
