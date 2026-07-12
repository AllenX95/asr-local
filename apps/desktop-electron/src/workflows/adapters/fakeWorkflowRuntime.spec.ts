import { describe, expect, it } from 'vitest'
import { FakeWorkflowRuntime } from './fakeWorkflowRuntime'
import type { WorkflowDraft } from '../types'

const draft = (name: string): WorkflowDraft => ({
  draft_version: 2,
  display_name: name,
  source: { path: `${name}.wav` },
  transcription: { pipeline_profile: 'pyannote_qwen3_asr' },
  summary: { profile_id: 'summary-profile-uuid' },
  output: { directory: 'outputs', base_name: name, collision_policy: 'unique_suffix' },
})

describe('FakeWorkflowRuntime', () => {
  it('creates independent queued workflows and guards expected attempts', async () => {
    const runtime = new FakeWorkflowRuntime({ idFactory: (() => {
      let index = 0
      return (prefix: string) => `${prefix}_${++index}`
    })() })
    const first = await runtime.submit(draft('one'))
    const second = await runtime.submit(draft('two'))
    expect(first.workflow_id).not.toBe(second.workflow_id)
    expect(first.attempt.attempt_id).not.toBe(second.attempt.attempt_id)
    await expect(runtime.control({ workflow_id: first.workflow_id, expected_attempt_id: 'wrong', action: 'cancel' })).rejects.toThrow('STALE_ATTEMPT')
    const cancelled = await runtime.control({ workflow_id: first.workflow_id, expected_attempt_id: first.attempt.attempt_id, action: 'cancel' })
    expect(cancelled.status).toBe('cancelled')
    expect((await runtime.get(second.workflow_id)).status).toBe('queued')
  })

  it('keeps runtime seam independent from the desktop adapter', async () => {
    const runtime = new FakeWorkflowRuntime({ clock: () => '2026-07-10T12:00:00Z' })
    const events: string[] = []
    runtime.subscribe((event) => events.push(`${event.workflow_id}:${event.sequence}`))
    const created = await runtime.submit(draft('preview'))
    const preview = await runtime.previewPrompt({
      pipeline_profile: 'pyannote_qwen3_asr',
      language: { mode: 'auto', value: null },
      prompt_input: { recording_background: 'meeting', hotwords: ['MOSS'] },
    })
    expect(preview.compiled_text).toContain('meeting')
    expect(events).toEqual([`${created.workflow_id}:1`])
  })

  it('clears only terminal workflow records', async () => {
    const runtime = new FakeWorkflowRuntime()
    const created = await runtime.submit(draft('clearable'))
    await expect(runtime.clear(created.workflow_id)).rejects.toThrow('WORKFLOW_NOT_TERMINAL')
    await runtime.control({ workflow_id: created.workflow_id, expected_attempt_id: created.attempt.attempt_id, action: 'cancel' })
    await runtime.clear(created.workflow_id)
    await expect(runtime.get(created.workflow_id)).rejects.toThrow('NOT_FOUND')
  })
})
