import { describe, expect, it } from 'vitest'
import { createTaskCenterState, toggleDiagnostics, toggleResummary } from './taskCenterState'

describe('task center secondary panels', () => {
  it('toggles the same workflow closed and keeps panels independent', () => {
    const state = createTaskCenterState()

    toggleDiagnostics(state, 'wf_a')
    expect(state.diagnosticsWorkflowId).toBe('wf_a')
    toggleDiagnostics(state, 'wf_a')
    expect(state.diagnosticsWorkflowId).toBeNull()

    toggleDiagnostics(state, 'wf_a')
    toggleDiagnostics(state, 'wf_b')
    expect(state.diagnosticsWorkflowId).toBe('wf_b')

    toggleResummary(state, 'wf_b')
    expect(state.resummaryWorkflowId).toBe('wf_b')
    toggleResummary(state, 'wf_b', false)
    expect(state.resummaryWorkflowId).toBeNull()
    toggleResummary(state, 'wf_a')
    expect(state.resummaryWorkflowId).toBe('wf_a')
    expect(state.diagnosticsWorkflowId).toBe('wf_b')
  })

  it('does not close a different workflow when explicit false is received', () => {
    const state = createTaskCenterState()
    toggleResummary(state, 'wf_a', true)
    toggleResummary(state, 'wf_b', false)
    expect(state.resummaryWorkflowId).toBe('wf_a')
  })
})
