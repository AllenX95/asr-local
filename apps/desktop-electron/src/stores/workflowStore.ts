import { computed, ref, shallowRef } from 'vue'
import { defineStore } from 'pinia'
import { applyWorkflowSnapshot, reduceWorkflowEvent } from '../workflows/reducer'
import type { WorkflowRuntime } from '../workflows/runtime'
import type { ArtifactRevisionCommand, RuntimeStatusEvent, WorkflowDraft, WorkflowEvent, WorkflowResummarizeCommand, WorkflowSnapshot } from '../workflows/types'

/**
 * Workflow state is keyed by workflow identity. It does not know about lanes,
 * files, desktop commands or summary API calls; adapters are injected at the
 * boundary so the same store can run against fake/Electron runtimes.
 */
export const useWorkflowStore = defineStore('workflow', () => {
  const runtime = shallowRef<WorkflowRuntime | null>(null)
  const workflowsById = ref<Record<string, WorkflowSnapshot>>({})
  const selectedWorkflowId = ref<string | null>(null)
  const capabilities = ref<Awaited<ReturnType<WorkflowRuntime['capabilities']>> | null>(null)
  const runtimeStatus = ref<RuntimeStatusEvent | null>(null)
  const configured = ref(false)
  let unsubscribe: (() => void) | null = null
  let unsubscribeRuntimeStatus: (() => void) | null = null
  let reconcileTask: { runtime: WorkflowRuntime; promise: Promise<void> } | null = null
  const clearedWorkflowIds = new Set<string>()

  const workflows = computed(() => Object.values(workflowsById.value).sort((a, b) => {
    const left = a.timestamps.created_at
    const right = b.timestamps.created_at
    return right.localeCompare(left) || b.workflow_id.localeCompare(a.workflow_id)
  }))
  const isReady = computed(() => configured.value && (
    runtimeStatus.value === null || runtimeStatus.value.state === 'ready'
  ))

  function requireRuntime(): WorkflowRuntime {
    if (!runtime.value) throw new Error('WorkflowRuntime is not configured')
    return runtime.value
  }

  function applySnapshot(snapshot: WorkflowSnapshot): WorkflowSnapshot {
    const current = workflowsById.value[snapshot.workflow_id]
    const applied = applyWorkflowSnapshot(current, snapshot)
    if (current && applied === current && snapshot.sequence === current.sequence) {
      if (snapshot.timeline !== undefined) {
        const enriched = { ...current, timeline: snapshot.timeline }
        workflowsById.value[snapshot.workflow_id] = enriched
        return enriched
      }
      return current
    }
    if (applied === current) return current
    const withTimeline = current?.timeline !== undefined && applied.timeline === undefined
      ? { ...applied, timeline: current.timeline }
      : applied
    workflowsById.value[snapshot.workflow_id] = withTimeline
    return withTimeline
  }

  function snapshotSequences(): Map<string, number> {
    return new Map(Object.values(workflowsById.value).map((snapshot) => [snapshot.workflow_id, snapshot.sequence]))
  }

  function applySnapshotList(snapshots: WorkflowSnapshot[], baseline: Map<string, number>): void {
    const incomingIds = new Set(snapshots.map((snapshot) => snapshot.workflow_id))
    for (const snapshot of snapshots) {
      clearedWorkflowIds.delete(snapshot.workflow_id)
      applySnapshot(snapshot)
    }
    for (const [workflowId, baselineSequence] of baseline) {
      const current = workflowsById.value[workflowId]
      if (!incomingIds.has(workflowId) && current?.sequence === baselineSequence) {
        delete workflowsById.value[workflowId]
      }
    }
    if (selectedWorkflowId.value && !workflowsById.value[selectedWorkflowId.value]) {
      selectedWorkflowId.value = workflows.value[0]?.workflow_id ?? null
    }
  }

  function reconcileWorkflow(workflowId: string): void {
    const runtimeInstance = runtime.value
    if (!runtimeInstance || clearedWorkflowIds.has(workflowId)) return
    void runtimeInstance.get(workflowId)
      .then((snapshot) => {
        if (runtime.value === runtimeInstance && !clearedWorkflowIds.has(workflowId)) {
          applySnapshot(snapshot)
        }
      })
      .catch(() => {
        // Runtime status and the next ready/list reconciliation remain the
        // source of user-facing diagnostics.
      })
  }

  function reduceEvent(event: WorkflowEvent): void {
    if (clearedWorkflowIds.has(event.workflow_id)) return
    const current = workflowsById.value[event.workflow_id]
    const hasGap = Boolean(current && event.sequence > current.sequence + 1)
    try {
      const snapshot = reduceWorkflowEvent(current, event)
      applySnapshot(snapshot)
    } catch {
      reconcileWorkflow(event.workflow_id)
      return
    }
    if (hasGap) reconcileWorkflow(event.workflow_id)
  }

  function reconcileRuntime(nextRuntime: WorkflowRuntime): Promise<void> {
    if (reconcileTask?.runtime === nextRuntime) return reconcileTask.promise
    const baseline = snapshotSequences()
    const promise = Promise.all([nextRuntime.capabilities(), nextRuntime.list()])
      .then(async ([runtimeCapabilities, snapshots]) => {
        if (runtime.value !== nextRuntime) return
        capabilities.value = runtimeCapabilities
        applySnapshotList(snapshots, baseline)
        configured.value = true
        const selectedId = selectedWorkflowId.value
        if (!selectedId) return
        try {
          const selectedSnapshot = await nextRuntime.get(selectedId)
          if (runtime.value === nextRuntime && selectedWorkflowId.value === selectedId) applySnapshot(selectedSnapshot)
        } catch {
          // A concurrent clear/list reconciliation is authoritative here.
        }
      })
      .finally(() => {
        if (reconcileTask?.promise === promise) reconcileTask = null
      })
    reconcileTask = { runtime: nextRuntime, promise }
    return promise
  }

  async function configure(nextRuntime: WorkflowRuntime): Promise<void> {
    unsubscribe?.()
    unsubscribeRuntimeStatus?.()
    runtime.value = nextRuntime
    configured.value = false
    runtimeStatus.value = null
    unsubscribe = nextRuntime.subscribe(reduceEvent)
    unsubscribeRuntimeStatus = nextRuntime.subscribeRuntimeStatus((status) => {
      if (runtime.value !== nextRuntime) return
      runtimeStatus.value = status
      if (status.state === 'ready') {
        void reconcileRuntime(nextRuntime).catch(() => {
          configured.value = false
        })
      } else if (status.state === 'unavailable' || status.state === 'error') {
        configured.value = false
      }
    })
    await reconcileRuntime(nextRuntime)
  }

  async function submit(draft: WorkflowDraft): Promise<WorkflowSnapshot> {
    const snapshot = await requireRuntime().submit(draft)
    clearedWorkflowIds.delete(snapshot.workflow_id)
    const applied = applySnapshot(snapshot)
    selectedWorkflowId.value = snapshot.workflow_id
    return applied
  }

  async function refresh(workflowId?: string): Promise<void> {
    const runtimeInstance = requireRuntime()
    if (workflowId) {
      const snapshot = await runtimeInstance.get(workflowId)
      applySnapshot(snapshot)
      return
    }
    const baseline = snapshotSequences()
    const snapshots = await runtimeInstance.list()
    applySnapshotList(snapshots, baseline)
  }

  async function control(workflowId: string, expectedAttemptId: string, action: 'pause' | 'resume' | 'cancel'): Promise<WorkflowSnapshot> {
    const snapshot = await requireRuntime().control({ workflow_id: workflowId, expected_attempt_id: expectedAttemptId, action })
    return applySnapshot(snapshot)
  }

  async function retry(workflowId: string, expectedAttemptId: string, expectedSequence: number, fromStage: 'auto' | 'transcribing' | 'summarizing' | 'writing_final', inputArtifactId?: string): Promise<WorkflowSnapshot> {
    const snapshot = await requireRuntime().retry({ workflow_id: workflowId, expected_attempt_id: expectedAttemptId, expected_sequence: expectedSequence, from_stage: fromStage, input_artifact_id: inputArtifactId })
    return applySnapshot(snapshot)
  }

  async function resummarize(command: WorkflowResummarizeCommand): Promise<WorkflowSnapshot> {
    const snapshot = await requireRuntime().resummarize(command)
    clearedWorkflowIds.delete(snapshot.workflow_id)
    const applied = applySnapshot(snapshot)
    selectedWorkflowId.value = snapshot.workflow_id
    return applied
  }

  async function registerRevision(command: ArtifactRevisionCommand): Promise<WorkflowSnapshot> {
    const snapshot = await requireRuntime().registerRevision(command)
    return applySnapshot(snapshot)
  }

  async function clear(workflowId: string): Promise<void> {
    await requireRuntime().clear(workflowId)
    clearedWorkflowIds.add(workflowId)
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
    isReady,
    configure,
    submit,
    refresh,
    control,
    retry,
    resummarize,
    registerRevision,
    clear,
    select,
  }
})
