import type {
  ArtifactRevisionCommand,
  PromptPreviewInput,
  PromptPreviewResult,
  WorkflowArtifact,
  WorkflowCapabilities,
  WorkflowControlCommand,
  WorkflowDraft,
  WorkflowEvent,
  WorkflowRetryCommand,
  WorkflowSnapshot,
} from '../types'
import type { RuntimeStatusHandler, WorkflowEventHandler, WorkflowRuntime } from '../runtime'

export interface FakeRuntimeOptions {
  idFactory?: (prefix: string) => string
  clock?: () => string
}

/** Deterministic in-memory adapter for state-machine and UI integration tests. */
export class FakeWorkflowRuntime implements WorkflowRuntime {
  private readonly snapshots = new Map<string, WorkflowSnapshot>()
  private readonly listeners = new Set<WorkflowEventHandler>()
  private readonly runtimeStatusListeners = new Set<RuntimeStatusHandler>()
  private readonly idFactory: (prefix: string) => string
  private readonly clock: () => string
  private idCounter = 0

  constructor(options: FakeRuntimeOptions = {}) {
    this.idFactory = options.idFactory ?? ((prefix) => `${prefix}_${++this.idCounter}`)
    this.clock = options.clock ?? (() => new Date().toISOString())
  }

  async capabilities(): Promise<WorkflowCapabilities> {
    return {
      max_inflight_workflows: 3,
      pipeline_profiles: ['pyannote_qwen3_asr', 'cloud_asr'],
      methods: ['workflow.submit', 'workflow.list', 'workflow.get', 'workflow.clear', 'workflow.control', 'workflow.retry', 'artifact.register_revision'],
    }
  }

  async previewPrompt(input: PromptPreviewInput): Promise<PromptPreviewResult> {
    const background = String(input.prompt_input.recording_background ?? '').trim()
    const hotwords = Array.isArray(input.prompt_input.hotwords) ? input.prompt_input.hotwords.join(', ') : ''
    const compiled_text = ['Qwen3-ASR transcript format', background, hotwords ? `Hotwords: ${hotwords}` : '']
      .filter(Boolean)
      .join('\n\n')
    return {
      compiler_id: 'fake-qwen-prompt',
      compiler_version: 1,
      base_template_version: 'fake',
      compiled_text,
      sha256: 'fake-digest',
      warnings: [],
    }
  }

  async submit(draft: WorkflowDraft): Promise<WorkflowSnapshot> {
    const now = this.clock()
    const workflow_id = this.idFactory('wf')
    const attempt_id = this.idFactory('att')
    const snapshot: WorkflowSnapshot = {
      snapshot_version: 2,
      workflow_id,
      sequence: 1,
      spec: { ...draft, spec_version: 2 } as WorkflowSnapshot['spec'],
      status: 'queued',
      stage: 'queued',
      attempt: { attempt_id, number: 1, stage_attempts: { transcription: 0, summary: 0, writing_final: 0 } },
      progress: { stage_ratio: 0, overall_ratio: 0, queue_position: null },
      control: { pending_action: null },
      runtime_plan: null,
      artifacts: [],
      recovery: { recommended_retry_stage: null, interrupted_attempt_id: null },
      last_error: null,
      timestamps: { created_at: now, updated_at: now, started_at: null, completed_at: null },
    }
    this.snapshots.set(workflow_id, snapshot)
    this.emit(snapshot, 'submitted')
    return snapshot
  }

  async list(): Promise<WorkflowSnapshot[]> {
    return [...this.snapshots.values()]
  }

  async get(workflowId: string): Promise<WorkflowSnapshot> {
    const snapshot = this.snapshots.get(workflowId)
    if (!snapshot) throw new Error('NOT_FOUND')
    return snapshot
  }

  subscribeRuntimeStatus(handler: RuntimeStatusHandler): () => void {
    this.runtimeStatusListeners.add(handler)
    handler({ state: 'ready', occurred_at: this.clock(), detail: 'Fake Runtime 已就绪' })
    return () => this.runtimeStatusListeners.delete(handler)
  }

  async clear(workflowId: string): Promise<void> {
    const snapshot = await this.get(workflowId)
    if (!['completed', 'completed_with_warnings', 'failed', 'cancelled', 'interrupted'].includes(snapshot.status)) {
      throw new Error('WORKFLOW_NOT_TERMINAL')
    }
    this.snapshots.delete(workflowId)
  }

  async control(command: WorkflowControlCommand): Promise<WorkflowSnapshot> {
    const current = await this.get(command.workflow_id)
    if (current.attempt.attempt_id !== command.expected_attempt_id) throw new Error('STALE_ATTEMPT')
    const next = this.clone(current)
    next.sequence += 1
    next.timestamps.updated_at = this.clock()
    if (command.action === 'cancel' && current.status === 'queued') {
      next.status = 'cancelled'
      next.stage = 'queued'
      next.control.pending_action = null
    } else if (command.action === 'pause' && current.status === 'running') {
      next.status = 'paused'
      next.control.pending_action = null
    } else if (command.action === 'resume' && current.status === 'paused') {
      next.status = 'running'
      next.control.pending_action = null
    } else {
      throw new Error('CONTROL_NOT_SUPPORTED')
    }
    this.snapshots.set(next.workflow_id, next)
    this.emit(next, command.action)
    return next
  }

  async retry(command: WorkflowRetryCommand): Promise<WorkflowSnapshot> {
    const current = await this.get(command.workflow_id)
    if (current.attempt.attempt_id !== command.expected_attempt_id) throw new Error('STALE_ATTEMPT')
    if (current.sequence !== command.expected_sequence) throw new Error('SEQUENCE_CONFLICT')
    if (!['failed', 'completed', 'completed_with_warnings', 'interrupted'].includes(current.status)) throw new Error('INVALID_TRANSITION')
    const next = this.clone(current)
    next.sequence += 1
    next.status = 'queued'
    next.stage = 'queued'
    next.attempt = { attempt_id: this.idFactory('att'), number: current.attempt.number + 1, stage_attempts: { transcription: 0, summary: 0, writing_final: 0 } }
    next.progress = { stage_ratio: 0, overall_ratio: 0, queue_position: null }
    next.recovery = { recommended_retry_stage: command.from_stage === 'auto' ? 'summarizing' : command.from_stage, interrupted_attempt_id: null }
    next.last_error = null
    next.timestamps.updated_at = this.clock()
    this.snapshots.set(next.workflow_id, next)
    this.emit(next, 'retry_started')
    return next
  }

  async registerRevision(command: ArtifactRevisionCommand): Promise<WorkflowSnapshot> {
    const current = await this.get(command.workflow_id)
    if (current.attempt.attempt_id !== command.expected_attempt_id) throw new Error('STALE_ATTEMPT')
    if (current.sequence !== command.expected_sequence) throw new Error('SEQUENCE_CONFLICT')
    const source = current.artifacts.find((artifact) => artifact.artifact_id === command.source_artifact_id)
    if (!source || source.kind !== command.kind) throw new Error('INVALID_REQUEST')
    const next = this.clone(current)
    const now = this.clock()
    const revision: WorkflowArtifact = {
      artifact_id: this.idFactory('artifact'),
      kind: command.kind,
      revision: source.revision + 1,
      origin: 'user_edited',
      derived_from_artifact_id: source.artifact_id,
      input_artifact_ids: source.input_artifact_ids,
      stale: false,
      path: command.staged_path,
      size_bytes: command.size_bytes,
      sha256: command.sha256,
      created_at: now,
    }
    next.artifacts = next.artifacts.map((artifact) =>
      artifact.kind === 'final_summary_markdown' && artifact.input_artifact_ids.includes(source.artifact_id)
        ? { ...artifact, stale: true }
        : artifact,
    )
    next.artifacts.push(revision)
    next.sequence += 1
    next.timestamps.updated_at = now
    this.snapshots.set(next.workflow_id, next)
    this.emit(next, 'artifact_ready')
    return next
  }

  subscribe(handler: WorkflowEventHandler): () => void {
    this.listeners.add(handler)
    return () => this.listeners.delete(handler)
  }

  /** Test-only state injection for out-of-order and failure scenarios. */
  emitSnapshot(snapshot: WorkflowSnapshot, type = 'state_changed'): void {
    this.snapshots.set(snapshot.workflow_id, this.clone(snapshot))
    this.emit(snapshot, type)
  }

  private emit(snapshot: WorkflowSnapshot, type: string): void {
    const event: WorkflowEvent = {
      workflow_id: snapshot.workflow_id,
      attempt_id: snapshot.attempt.attempt_id,
      sequence: snapshot.sequence,
      occurred_at: snapshot.timestamps.updated_at,
      type,
      stage: snapshot.stage,
      data: {},
      state: this.clone(snapshot),
    }
    for (const listener of this.listeners) listener(event)
  }

  private clone<T>(value: T): T {
    return structuredClone(value)
  }
}
