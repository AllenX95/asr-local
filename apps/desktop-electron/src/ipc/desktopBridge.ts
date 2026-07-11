import type { WorkflowEvent } from '../workflows/types'

export interface ElectronDesktopBridge {
  invoke<T>(command: string, args?: Record<string, unknown>): Promise<T>
  onWorkflowEvent(handler: (event: WorkflowEvent) => void): () => void
}

declare global {
  interface Window { asrLocal?: ElectronDesktopBridge }
}

export const electronBridge = (): ElectronDesktopBridge | null =>
  typeof window !== 'undefined' && window.asrLocal ? window.asrLocal : null
