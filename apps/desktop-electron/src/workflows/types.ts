import type { PipelineProfile } from '../ipc/workerTypes';

export type WorkflowStatus =
  | 'queued'
  | 'running'
  | 'paused'
  | 'waiting_for_secret'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'interrupted'

export type WorkflowStage =
  | 'validating'
  | 'queued'
  | 'preparing'
  | 'transcribing'
  | 'transcript_ready'
  | 'summarizing'
  | 'writing_final'
  | 'completed'
  | null

export interface WorkflowSpec {
  spec_version: 2
  display_name: string
  source: Record<string, unknown>
  transcription: Record<string, unknown> & { pipeline_profile?: PipelineProfile }
  summary: Record<string, unknown>
  output: Record<string, unknown>
}

export interface WorkflowArtifact {
  artifact_id: string
  kind: string
  revision: number
  origin: 'generated' | 'user_edited'
  derived_from_artifact_id: string | null
  input_artifact_ids: string[]
  stale: boolean
  path: string
  size_bytes: number
  sha256: string
  created_at: string
}

export interface WorkflowAttempt {
  attempt_id: string
  number: number
  stage_attempts: Record<string, number>
}

export interface WorkflowSnapshot {
  snapshot_version: 2
  workflow_id: string
  sequence: number
  spec: WorkflowSpec
  status: WorkflowStatus
  stage: WorkflowStage
  attempt: WorkflowAttempt
  progress: WorkflowProgress
  control: { pending_action: 'pause' | 'cancel' | null }
  runtime_plan: Record<string, unknown> | null
  artifacts: WorkflowArtifact[]
  recovery: {
    recommended_retry_stage: Exclude<WorkflowStage, null> | null
    interrupted_attempt_id: string | null
    input_artifact_id?: string | null
  }
  last_error: WorkflowFailure | null
  timestamps: {
    created_at: string
    updated_at: string
    started_at: string | null
    completed_at: string | null
  }
  timeline?: WorkflowTimelineEntry[]
}

export interface WorkflowProgress {
  stage_ratio?: number | null
  overall_ratio?: number | null
  queue_position?: number | null
  processed_ms?: number | null
  total_ms?: number | null
  detail?: string | null
  phase?: string | null
  phase_started_at?: string | null
  heartbeat_at?: string | null
}

export interface WorkflowTimelineEntry {
  sequence: number
  attempt_id: string
  type: string
  stage: WorkflowStage
  occurred_at: string
  detail?: string
}

export interface WorkflowFailure {
  code: string
  message: string
  retryable: boolean
  field_errors: Array<{ field: string; message: string }>
  details: Record<string, unknown>
  diagnostic_id: string
}

export interface WorkflowEvent {
  workflow_id: string
  attempt_id: string
  sequence: number
  occurred_at: string
  caused_by_operation_id?: string | null
  type: string
  stage: WorkflowStage
  data: Record<string, unknown>
  state: WorkflowSnapshot
}

export interface RuntimeStatusEvent {
  state: 'starting' | 'ready' | 'stopping' | 'stopped' | 'unavailable' | 'error'
  occurred_at: string
  detail?: string
  pid?: number
}

export interface WorkflowDraft extends Record<string, unknown> {
  draft_version: 2
  display_name: string
  source: Record<string, unknown>
  transcription: Record<string, unknown>
  summary: Record<string, unknown>
  output: Record<string, unknown>
}

export type WorkflowControlAction = 'pause' | 'resume' | 'cancel'

export interface WorkflowControlCommand {
  workflow_id: string
  expected_attempt_id: string
  action: WorkflowControlAction
}

export interface WorkflowRetryCommand {
  workflow_id: string
  expected_attempt_id: string
  expected_sequence: number
  from_stage: 'auto' | 'transcribing' | 'summarizing' | 'writing_final'
  input_artifact_id?: string
}

export interface ArtifactRevisionCommand {
  workflow_id: string
  expected_attempt_id: string
  expected_sequence: number
  source_artifact_id: string
  kind: 'transcript_markdown' | 'final_summary_markdown'
  staged_path: string
  size_bytes: number
  sha256: string
}

export interface WorkflowCapabilities {
  max_inflight_workflows: number
  pipeline_profiles: string[]
  methods: string[]
}

export interface PromptPreviewInput {
  pipeline_profile: string
  language: Record<string, unknown>
  prompt_input: Record<string, unknown>
}

export interface PromptPreviewResult {
  compiler_id: string
  compiler_version: number
  base_template_version: string
  compiled_text: string
  sha256: string
  warnings: string[]
}
