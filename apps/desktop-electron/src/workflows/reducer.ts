import type { WorkflowEvent, WorkflowSnapshot } from './types'

export class WorkflowEventError extends Error {}

const WORKFLOW_STATUSES = new Set<string>([
  'queued',
  'running',
  'paused',
  'waiting_for_secret',
  'completed',
  'completed_with_warnings',
  'failed',
  'cancelled',
  'interrupted',
])
const WORKFLOW_STAGES = new Set<unknown>([
  'validating',
  'queued',
  'preparing',
  'transcribing',
  'transcript_ready',
  'summarizing',
  'writing_final',
  'completed',
  null,
])

/**
 * Reducer is deliberately pure: no IPC, file reads, timers or ID generation.
 * A late event from a previous attempt is ignored once a newer attempt owns the
 * current snapshot. Sequence gaps are left to the adapter/store to reconcile.
 */
export function reduceWorkflowEvent(
  current: WorkflowSnapshot | undefined,
  event: WorkflowEvent,
): WorkflowSnapshot {
  validateSnapshot(event.state)
  if (event.workflow_id !== event.state.workflow_id) {
    throw new WorkflowEventError('WORKFLOW_ID_MISMATCH')
  }
  if (
    event.attempt_id !== event.state.attempt.attempt_id ||
    event.sequence !== event.state.sequence ||
    event.stage !== event.state.stage
  ) {
    throw new WorkflowEventError('EVENT_STATE_MISMATCH')
  }
  return applyWorkflowSnapshot(current, event.state)
}

export function applyWorkflowSnapshot(
  current: WorkflowSnapshot | undefined,
  candidate: WorkflowSnapshot,
): WorkflowSnapshot {
  validateSnapshot(candidate)
  if (!current) return candidate
  if (candidate.workflow_id !== current.workflow_id) {
    throw new WorkflowEventError('WORKFLOW_ID_MISMATCH')
  }
  if (candidate.sequence === current.sequence) {
    if (
      candidate.attempt.number !== current.attempt.number
      || candidate.attempt.attempt_id !== current.attempt.attempt_id
    ) throw new WorkflowEventError('SNAPSHOT_IDENTITY_CONFLICT')
    return current
  }
  if (candidate.sequence < current.sequence) {
    return current
  }
  if (candidate.attempt.number < current.attempt.number) {
    return current
  }
  if (
    candidate.attempt.number === current.attempt.number &&
    candidate.attempt.attempt_id !== current.attempt.attempt_id
  ) {
    throw new WorkflowEventError('ATTEMPT_ID_MISMATCH')
  }
  return candidate
}

function validateSnapshot(snapshot: WorkflowSnapshot): void {
  if (
    snapshot.snapshot_version !== 2 ||
    !snapshot.workflow_id ||
    !Number.isInteger(snapshot.sequence) ||
    snapshot.sequence < 1 ||
    !snapshot.attempt?.attempt_id ||
    !Number.isInteger(snapshot.attempt.number) ||
    snapshot.attempt.number < 1 ||
    !WORKFLOW_STATUSES.has(snapshot.status) ||
    !WORKFLOW_STAGES.has(snapshot.stage)
  ) {
    throw new WorkflowEventError('INVALID_WORKFLOW_SNAPSHOT')
  }
}

