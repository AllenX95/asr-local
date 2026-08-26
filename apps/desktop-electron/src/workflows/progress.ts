import type { WorkflowProgress } from './types'

export function chunkProgressLabel(progress: WorkflowProgress): string | null {
  const completed = progress.completed_chunks
  const total = progress.total_chunks
  if (
    typeof completed !== 'number'
    || !Number.isFinite(completed)
    || !Number.isInteger(completed)
    || completed < 0
    || typeof total !== 'number'
    || !Number.isFinite(total)
    || !Number.isInteger(total)
    || total <= 0
  ) {
    return null
  }

  return `${Math.min(completed, total)} / ${total} 个分块`
}
