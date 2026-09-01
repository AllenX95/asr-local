import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import { loadOutputPathGrants, saveOutputPathGrants, workflowArtifactPaths, workflowOutputRoot } from '../electron/outputPathGrants'

const temporaryRoots: string[] = []

afterEach(async () => {
  await Promise.all(temporaryRoots.splice(0).map((root) => rm(root, { recursive: true, force: true })))
})

async function temporaryRoot(): Promise<string> {
  const root = await mkdtemp(path.join(os.tmpdir(), 'asr-local-path-grants-'))
  temporaryRoots.push(root)
  return root
}

describe('persistent output path grants', () => {
  it('round-trips unique existing output directories', async () => {
    const root = await temporaryRoot()
    const output = path.join(root, 'custom-output')
    const storage = path.join(root, 'state', 'output-path-grants.json')
    await mkdir(output)

    await saveOutputPathGrants(storage, [output, path.join(output, '..', 'custom-output')])

    await expect(loadOutputPathGrants(storage)).resolves.toEqual(new Set([path.resolve(output)]))
    await expect(readFile(storage, 'utf8')).resolves.toContain('"version": 1')
  })

  it('ignores missing paths, files, malformed JSON and unknown versions', async () => {
    const root = await temporaryRoot()
    const storage = path.join(root, 'output-path-grants.json')
    const regularFile = path.join(root, 'not-a-directory.txt')
    await writeFile(regularFile, 'not a directory', 'utf8')
    await writeFile(storage, JSON.stringify({ version: 1, roots: [regularFile, path.join(root, 'missing')] }), 'utf8')
    await expect(loadOutputPathGrants(storage)).resolves.toEqual(new Set())

    await writeFile(storage, '{broken', 'utf8')
    await expect(loadOutputPathGrants(storage)).resolves.toEqual(new Set())

    await writeFile(storage, JSON.stringify({ version: 99, roots: [root] }), 'utf8')
    await expect(loadOutputPathGrants(storage)).resolves.toEqual(new Set())
  })

  it('extracts only trusted workflow output and artifact paths', async () => {
    const root = await temporaryRoot()
    const output = path.join(root, 'output')
    const artifact = path.join(output, 'transcripts', 'meeting.md')
    await mkdir(path.dirname(artifact), { recursive: true })
    await writeFile(artifact, 'managed', 'utf8')
    const snapshot = {
      spec: { output: { directory: output } },
      artifacts: [{ path: artifact }, { path: null }, 'invalid'],
    }

    expect(workflowOutputRoot(snapshot)).toBe(path.resolve(output))
    expect(workflowArtifactPaths(snapshot)).toEqual([path.resolve(artifact)])
    expect(workflowOutputRoot({ spec: { output: { directory: null } } })).toBeNull()
    expect(workflowArtifactPaths({ artifacts: null })).toEqual([])
  })
})
