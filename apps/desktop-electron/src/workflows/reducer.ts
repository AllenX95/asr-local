import type { WorkflowEvent, WorkflowSnapshot } from './types'

export class WorkflowEventError extends Error {}

/**
 * Reducer is deliberately pure: no IPC, file reads, timers or ID generation.
 * A late event from a previous attempt is ignored once a newer attempt owns the
 * current snapshot. Sequence gaps are left to the adapter/store to reconcile.
 */
export function reduceWorkflowEvent(
  current: WorkflowSnapshot,
  event: WorkflowEvent,
): WorkflowSnapshot {
  if (event.workflow_id !== current.workflow_id || event.state.workflow_id !== current.workflow_id) {
    throw new WorkflowEventError('WORKFLOW_ID_MISMATCH')
  }
  if (
    event.attempt_id !== event.state.attempt.attempt_id ||
    event.sequence !== event.state.sequence ||
    event.stage !== event.state.stage
  ) {
    throw new WorkflowEventError('EVENT_STATE_MISMATCH')
  }
  if (event.sequence <= current.sequence) {
    return current
  }
  if (event.attempt_id !== current.attempt.attempt_id) {
    return current
  }
  return event.state
}

