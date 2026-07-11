import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { reduceWorkflowEvent } from '../workflows/reducer'
import type { WorkflowRuntime } from '../workflows/runtime'
import type { ArtifactRevisionCommand, WorkflowDraft, WorkflowEvent, WorkflowSnapshot } from '../workflows/types'

/**
 * Workflow state is keyed by workflow identity. It does not know about lanes,
 * files, Tauri commands or summary API calls; adapters are injected at the
 * boundary so the same store can run against fake/Tauri/Electron runtimes.
 */
export const useWorkflowStore = defineStore('workflow', () => {
  const runtime = ref<WorkflowRuntime | null>(null)
  const workflowsById = ref<Record<string, WorkflowSnapshot>>({})
  const selectedWorkflowId = ref<string | null>(null)
  const subscribed = ref(false)
  let unsubscribe: (() => void) | null = null

  const workflows = computed(() => Object.values(workflowsById.value).sort((a, b) => {
    const left = a.timestamps.created_at
    const right = b.timestamps.created_at
    return right.localeCompare(left) || b.workflow_id.localeCompare(a.workflow_id)
  }))

  function requireRuntime(): WorkflowRuntime {
    if (!runtime.value) throw new Error('WorkflowRuntime is not configured')
    return runtime.value
  }

  function reduceEvent(event: WorkflowEvent): void {
    const current = workflowsById.value[event.workflow_id]
    if (!current) {
      workflowsById.value[event.workflow_id] = event.state
      return
    }
    workflowsById.value[event.workflow_id] = reduceWorkflowEvent(current, event)
  }

  async function configure(nextRuntime: WorkflowRuntime): Promise<void> {
    unsubscribe?.()
    runtime.value = nextRuntime
    unsubscribe = nextRuntime.subscribe(reduceEvent)
    subscribed.value = true
    const snapshots = await nextRuntime.list()
    for (const snapshot of snapshots) {
      const current = workflowsById.value[snapshot.workflow_id]
      if (!current || snapshot.sequence >= current.sequence) {
        workflowsById.value[snapshot.workflow_id] = snapshot
      }
    }
  }

  async function submit(draft: WorkflowDraft): Promise<WorkflowSnapshot> {
    const snapshot = await requireRuntime().submit(draft)
    workflowsById.value[snapshot.workflow_id] = snapshot
    selectedWorkflowId.value = snapshot.workflow_id
    return snapshot
  }

  async function refresh(workflowId?: string): Promise<void> {
    const runtimeInstance = requireRuntime()
    if (workflowId) {
      const snapshot = await runtimeInstance.get(workflowId)
      const current = workflowsById.value[workflowId]
      if (!current || snapshot.sequence >= current.sequence) workflowsById.value[workflowId] = snapshot
      return
    }
    const snapshots = await runtimeInstance.list()
    for (const snapshot of snapshots) {
      const current = workflowsById.value[snapshot.workflow_id]
      if (!current || snapshot.sequence >= current.sequence) workflowsById.value[snapshot.workflow_id] = snapshot
    }
  }

  async function control(workflowId: string, expectedAttemptId: string, action: 'pause' | 'resume' | 'cancel'): Promise<WorkflowSnapshot> {
    const snapshot = await requireRuntime().control({ workflow_id: workflowId, expected_attempt_id: expectedAttemptId, action })
    workflowsById.value[workflowId] = snapshot
    return snapshot
  }

  async function retry(workflowId: string, expectedAttemptId: string, expectedSequence: number, fromStage: 'auto' | 'transcribing' | 'summarizing' | 'writing_final', inputArtifactId?: string): Promise<WorkflowSnapshot> {
    const snapshot = await requireRuntime().retry({ workflow_id: workflowId, expected_attempt_id: expectedAttemptId, expected_sequence: expectedSequence, from_stage: fromStage, input_artifact_id: inputArtifactId })
    workflowsById.value[workflowId] = snapshot
    return snapshot
  }

  async function registerRevision(command: ArtifactRevisionCommand): Promise<WorkflowSnapshot> {
    const snapshot = await requireRuntime().registerRevision(command)
    workflowsById.value[command.workflow_id] = snapshot
    return snapshot
  }

  function select(workflowId: string | null): void {
    selectedWorkflowId.value = workflowId
  }

  return {
    runtime,
    workflowsById,
    workflows,
    selectedWorkflowId,
    subscribed,
    configure,
    submit,
    refresh,
    control,
    retry,
    registerRevision,
    select,
    reduceEvent,
  }
})
