import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useWorkflowStore } from './workflowStore'
import type { WorkflowEventHandler, WorkflowRuntime, RuntimeStatusHandler } from '../workflows/runtime'
import type {
  ArtifactRevisionCommand,
  PromptPreviewInput,
  RuntimeStatusEvent,
  WorkflowControlCommand,
  WorkflowDraft,
  WorkflowEvent,
  WorkflowResummarizeCommand,
  WorkflowRetryCommand,
  WorkflowSnapshot,
} from '../workflows/types'

function snapshot(sequence: number, overrides: Partial<WorkflowSnapshot> = {}): WorkflowSnapshot {
  const now = '2026-08-28T00:00:00Z'
  return {
    snapshot_version: 2,
    workflow_id: 'wf_store',
    sequence,
    spec: {
      spec_version: 2,
      display_name: 'store test',
      source: {},
      transcription: {},
      summary: {},
      output: {},
    },
    status: sequence === 1 ? 'queued' : 'running',
    stage: sequence === 1 ? 'queued' : 'transcribing',
    attempt: { attempt_id: 'att_store', number: 1, stage_attempts: {} },
    progress: {},
    control: { pending_action: null },
    runtime_plan: null,
    artifacts: [],
    recovery: { recommended_retry_stage: null, interrupted_attempt_id: null },
    last_error: null,
    timestamps: {
      created_at: now,
      updated_at: now,
      started_at: null,
      completed_at: null,
    },
    ...overrides,
  }
}

function event(state: WorkflowSnapshot): WorkflowEvent {
  return {
    workflow_id: state.workflow_id,
    attempt_id: state.attempt.attempt_id,
    sequence: state.sequence,
    occurred_at: state.timestamps.updated_at,
    type: 'progress',
    stage: state.stage,
    data: {},
    state,
  }
}

class ControlledRuntime implements WorkflowRuntime {
  snapshots: WorkflowSnapshot[] = [snapshot(1)]
  getSnapshot: WorkflowSnapshot = snapshot(1)
  controlPromise: Promise<WorkflowSnapshot> = Promise.resolve(snapshot(1))
  listOverride: (() => Promise<WorkflowSnapshot[]>) | null = null
  private eventHandlers = new Set<WorkflowEventHandler>()
  private statusHandlers = new Set<RuntimeStatusHandler>()
  getCalls: string[] = []

  capabilities() {
    return Promise.resolve({
      max_inflight_workflows: 3,
      pipeline_profiles: ['pyannote_qwen3_asr'],
      methods: [],
    })
  }
  previewPrompt(_input: PromptPreviewInput) {
    return Promise.resolve({
      compiler_id: 'test',
      compiler_version: 1,
      base_template_version: 'test',
      compiled_text: '',
      sha256: '',
      warnings: [],
    })
  }
  submit(_draft: WorkflowDraft) { return Promise.resolve(snapshot(1)) }
  list() { return this.listOverride ? this.listOverride() : Promise.resolve(this.snapshots) }
  get(workflowId: string) { this.getCalls.push(workflowId); return Promise.resolve(this.getSnapshot) }
  clear(_workflowId: string) { return Promise.resolve() }
  control(_command: WorkflowControlCommand) { return this.controlPromise }
  retry(_command: WorkflowRetryCommand) { return Promise.resolve(snapshot(2)) }
  resummarize(_command: WorkflowResummarizeCommand) { return Promise.resolve(snapshot(1)) }
  registerRevision(_command: ArtifactRevisionCommand) { return Promise.resolve(snapshot(2)) }
  subscribe(handler: WorkflowEventHandler) {
    this.eventHandlers.add(handler)
    return () => this.eventHandlers.delete(handler)
  }
  subscribeRuntimeStatus(handler: RuntimeStatusHandler) {
    this.statusHandlers.add(handler)
    return () => this.statusHandlers.delete(handler)
  }
  emitWorkflowEvent(next: WorkflowEvent) {
    for (const handler of this.eventHandlers) handler(next)
  }
  emitStatus(state: RuntimeStatusEvent['state']) {
    const status: RuntimeStatusEvent = {
      state,
      occurred_at: '2026-08-28T00:00:00Z',
    }
    for (const handler of this.statusHandlers) handler(status)
  }
}

describe('workflow store snapshot convergence', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('does not let a delayed initial list overwrite a newer event', async () => {
    const runtime = new ControlledRuntime()
    let resolveList: (items: WorkflowSnapshot[]) => void = () => undefined
    runtime.listOverride = () => new Promise((resolve) => { resolveList = resolve })
    const store = useWorkflowStore()

    const configuring = store.configure(runtime)
    runtime.emitWorkflowEvent(event(snapshot(2)))
    resolveList([snapshot(1)])
    await configuring

    expect(store.workflowsById.wf_store.sequence).toBe(2)
    expect(store.isReady).toBe(true)
  })

  it('does not let a delayed command response overwrite a newer event', async () => {
    const runtime = new ControlledRuntime()
    const store = useWorkflowStore()
    await store.configure(runtime)
    let resolveControl: (value: WorkflowSnapshot) => void = () => undefined
    runtime.controlPromise = new Promise((resolve) => { resolveControl = resolve })

    const command = store.control('wf_store', 'att_store', 'pause')
    runtime.emitWorkflowEvent(event(snapshot(3)))
    resolveControl(snapshot(2))

    const result = await command
    expect(result.sequence).toBe(3)
    expect(store.workflowsById.wf_store.sequence).toBe(3)
  })

  it('reconciles capabilities and snapshots after runtime ready', async () => {
    const runtime = new ControlledRuntime()
    const store = useWorkflowStore()
    await store.configure(runtime)
    runtime.emitStatus('unavailable')
    expect(store.isReady).toBe(false)

    runtime.snapshots = [snapshot(4)]
    runtime.getSnapshot = snapshot(5, { timeline: [{
      sequence: 5,
      attempt_id: 'att_store',
      type: 'state_changed',
      stage: 'transcribing',
      occurred_at: '2026-08-28T00:00:00Z',
    }] })
    store.select('wf_store')
    runtime.emitStatus('ready')

    await vi.waitFor(() => {
      expect(store.workflowsById.wf_store.sequence).toBe(5)
      expect(store.workflowsById.wf_store.timeline).toHaveLength(1)
      expect(runtime.getCalls).toContain('wf_store')
      expect(store.isReady).toBe(true)
    })
  })

  it('prunes missing list items without deleting workflows updated during the request', async () => {
    const runtime = new ControlledRuntime()
    const store = useWorkflowStore()
    await store.configure(runtime)
    let resolveList: (items: WorkflowSnapshot[]) => void = () => undefined
    runtime.listOverride = () => new Promise((resolve) => { resolveList = resolve })

    const refreshing = store.refresh()
    runtime.emitWorkflowEvent(event(snapshot(2)))
    resolveList([])
    await refreshing
    expect(store.workflowsById.wf_store.sequence).toBe(2)

    runtime.listOverride = null
    runtime.snapshots = []
    await store.refresh()
    expect(store.workflowsById.wf_store).toBeUndefined()
  })
})
