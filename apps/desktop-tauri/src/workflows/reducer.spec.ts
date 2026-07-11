import { describe, expect, it } from 'vitest'
import { reduceWorkflowEvent, WorkflowEventError } from './reducer'
import type { WorkflowEvent, WorkflowSnapshot } from './types'

function snapshot(overrides: Partial<WorkflowSnapshot> = {}): WorkflowSnapshot {
  const now = '2026-07-10T12:00:00Z'
  return {
    snapshot_version: 2,
    workflow_id: 'wf_001',
    sequence: 1,
    spec: { spec_version: 2, display_name: 'test', source: {}, transcription: {}, summary: {}, output: {} },
    status: 'queued',
    stage: 'queued',
    attempt: { attempt_id: 'att_001', number: 1, stage_attempts: {} },
    progress: { stage_ratio: 0, overall_ratio: 0 },
    control: { pending_action: null },
    runtime_plan: null,
    artifacts: [],
    recovery: { recommended_retry_stage: null, interrupted_attempt_id: null },
    last_error: null,
    timestamps: { created_at: now, updated_at: now, started_at: null, completed_at: null },
    ...overrides,
  }
}

function event(state: WorkflowSnapshot): WorkflowEvent {
  return {
    workflow_id: state.workflow_id,
    attempt_id: state.attempt.attempt_id,
    sequence: state.sequence,
    occurred_at: state.timestamps.updated_at,
    type: 'state_changed',
    stage: state.stage,
    data: {},
    state,
  }
}

describe('workflow event reducer', () => {
  it('accepts a newer event and ignores duplicate sequence', () => {
    const current = snapshot()
    const next = snapshot({ sequence: 2, status: 'running', stage: 'preparing' })
    expect(reduceWorkflowEvent(current, event(next))).toBe(next)
    expect(reduceWorkflowEvent(next, event(next))).toBe(next)
  })

  it('ignores late events from an older attempt', () => {
    const current = snapshot({ sequence: 5, status: 'queued', attempt: { attempt_id: 'att_002', number: 2, stage_attempts: {} } })
    const late = snapshot({ sequence: 6, status: 'completed', stage: 'completed', attempt: { attempt_id: 'att_001', number: 1, stage_attempts: {} } })
    expect(reduceWorkflowEvent(current, event(late))).toBe(current)
  })

  it('rejects duplicated routing fields that disagree with state', () => {
    const current = snapshot()
    const next = snapshot({ sequence: 2, stage: 'preparing' })
    const malformed = { ...event(next), stage: 'queued' as const }
    expect(() => reduceWorkflowEvent(current, malformed)).toThrow(WorkflowEventError)
  })
})
