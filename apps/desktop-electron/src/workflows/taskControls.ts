import type { WorkflowControlAction, WorkflowStatus } from './types'

const actionsByStatus: Partial<Record<WorkflowStatus, readonly WorkflowControlAction[]>> = {
  queued: ['cancel'],
  running: ['pause', 'cancel'],
  paused: ['resume', 'cancel'],
  waiting_for_secret: ['cancel'],
}

export function taskControlActions(status: WorkflowStatus): readonly WorkflowControlAction[] {
  return actionsByStatus[status] ?? []
}
