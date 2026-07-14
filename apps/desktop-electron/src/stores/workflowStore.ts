import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { reduceWorkflowEvent } from '../workflows/reducer'
import type { WorkflowRuntime } from '../workflows/runtime'
import type { ArtifactRevisionCommand, RuntimeStatusEvent, WorkflowDraft, WorkflowEvent, WorkflowResummarizeCommand, WorkflowSnapshot } from '../workflows/types'

/**
 * Workflow state is keyed by workflow identity. It does not know about lanes,
 * files, desktop commands or summary API calls; adapters are injected at the
 * boundary so the same store can run against fake/Electron runtimes.
 */
export const useWorkflowStore = defineStore('workflow', () => {
  const runtime = ref<WorkflowRuntime | null>(null)
  const workflowsById = ref<Record<string, WorkflowSnapshot>>({})
  const selectedWorkflowId = ref<string | null>(null)
  const capabilities = ref<Awaited<ReturnType<WorkflowRuntime['capabilities']>> | null>(null)
  const subscribed = ref(false)
  const runtimeStatus = ref<RuntimeStatusEvent | null>(null)
  let unsubscribe: (() => void) | null = null
  let unsubscribeRuntimeStatus: (() => void) | null = null

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
    unsubscribeRuntimeStatus?.()
    runtime.value = nextRuntime
    unsubscribe = nextRuntime.subscribe(reduceEvent)
    unsubscribeRuntimeStatus = nextRuntime.subscribeRuntimeStatus((status) => { runtimeStatus.value = status })
    subscribed.value = true
    const [runtimeCapabilities, snapshots] = await Promise.all([nextRuntime.capabilities(), nextRuntime.list()])
    capabilities.value = runtimeCapabilities
    workflowsById.value = Object.fromEntries(snapshots.map((snapshot) => [snapshot.workflow_id, snapshot]))
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
    workflowsById.value = Object.fromEntries(snapshots.map((snapshot) => [snapshot.workflow_id, snapshot]))
    if (selectedWorkflowId.value && !workflowsById.value[selectedWorkflowId.value]) {
      selectedWorkflowId.value = workflows.value[0]?.workflow_id ?? null
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

  async function resummarize(command: WorkflowResummarizeCommand): Promise<WorkflowSnapshot> {
    const snapshot = await requireRuntime().resummarize(command)
    workflowsById.value[snapshot.workflow_id] = snapshot
    selectedWorkflowId.value = snapshot.workflow_id
    return snapshot
  }

  async function registerRevision(command: ArtifactRevisionCommand): Promise<WorkflowSnapshot> {
    const snapshot = await requireRuntime().registerRevision(command)
    workflowsById.value[command.workflow_id] = snapshot
    return snapshot
  }

  async function clear(workflowId: string): Promise<void> {
    await requireRuntime().clear(workflowId)
    delete workflowsById.value[workflowId]
    if (selectedWorkflowId.value === workflowId) {
      selectedWorkflowId.value = workflows.value[0]?.workflow_id ?? null
    }
  }

  function select(workflowId: string | null): void {
    selectedWorkflowId.value = workflowId
  }

  return {
    runtime,
    workflowsById,
    workflows,
    selectedWorkflowId,
    capabilities,
    runtimeStatus,
    subscribed,
    configure,
    submit,
    refresh,
    control,
    retry,
    resummarize,
    registerRevision,
    clear,
    select,
    reduceEvent,
  }
})
