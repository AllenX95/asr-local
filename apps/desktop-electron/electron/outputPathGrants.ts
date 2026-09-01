import { mkdir, readFile, rename, stat, unlink, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { canonicalizeAccessPath } from './pathAccess.js'

const GRANTS_VERSION = 1

interface OutputPathGrantDocument {
  version: typeof GRANTS_VERSION
  roots: string[]
}

function snapshotSpec(snapshot: unknown): Record<string, unknown> | null {
  if (typeof snapshot !== 'object' || snapshot === null) return null
  const spec = (snapshot as { spec?: unknown }).spec
  return typeof spec === 'object' && spec !== null ? spec as Record<string, unknown> : null
}

export function workflowOutputRoot(snapshot: unknown): string | null {
  const output = snapshotSpec(snapshot)?.output
  if (typeof output !== 'object' || output === null) return null
  const directory = (output as { directory?: unknown }).directory
  return typeof directory === 'string' && directory.length > 0 ? canonicalizeAccessPath(directory) : null
}

export function workflowArtifactPaths(snapshot: unknown): string[] {
  if (typeof snapshot !== 'object' || snapshot === null) return []
  const artifacts = (snapshot as { artifacts?: unknown }).artifacts
  if (!Array.isArray(artifacts)) return []
  return normalizeRoots(artifacts.map((artifact) => (
    typeof artifact === 'object' && artifact !== null ? (artifact as { path?: unknown }).path : null
  )).filter((artifactPath): artifactPath is string => typeof artifactPath === 'string'))
}

function normalizeRoots(roots: Iterable<string>): string[] {
  return [...new Set(
    [...roots]
      .filter((root) => typeof root === 'string' && root.trim().length > 0)
      .map((root) => canonicalizeAccessPath(root)),
  )].sort((left, right) => left.localeCompare(right))
}

export async function loadOutputPathGrants(storagePath: string): Promise<Set<string>> {
  let document: unknown
  try {
    document = JSON.parse(await readFile(storagePath, 'utf8'))
  } catch {
    return new Set()
  }
  if (
    typeof document !== 'object'
    || document === null
    || (document as Partial<OutputPathGrantDocument>).version !== GRANTS_VERSION
    || !Array.isArray((document as Partial<OutputPathGrantDocument>).roots)
  ) return new Set()

  const existingDirectories: string[] = []
  for (const root of normalizeRoots((document as OutputPathGrantDocument).roots)) {
    try {
      if ((await stat(root)).isDirectory()) existingDirectories.push(root)
    } catch {
      // Missing or inaccessible roots require the user to authorize them again.
    }
  }
  return new Set(existingDirectories)
}

export async function saveOutputPathGrants(storagePath: string, roots: Iterable<string>): Promise<void> {
  const document: OutputPathGrantDocument = { version: GRANTS_VERSION, roots: normalizeRoots(roots) }
  await mkdir(path.dirname(storagePath), { recursive: true })
  const temporaryPath = `${storagePath}.${process.pid}.${Date.now()}.tmp`
  try {
    await writeFile(temporaryPath, `${JSON.stringify(document, null, 2)}\n`, 'utf8')
    await rename(temporaryPath, storagePath)
  } catch (error) {
    await unlink(temporaryPath).catch(() => undefined)
    throw error
  }
}
