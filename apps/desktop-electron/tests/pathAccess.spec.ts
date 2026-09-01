import { mkdtemp, mkdir, rm, symlink } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import { canonicalizeAccessPath, isPathWithinRoots } from '../electron/pathAccess'

const temporaryRoots: string[] = []

afterEach(async () => {
  await Promise.all(temporaryRoots.splice(0).map((root) => rm(root, { recursive: true, force: true })))
})

async function temporaryRoot(): Promise<string> {
  const root = await mkdtemp(path.join(os.tmpdir(), 'asr-local-path-access-'))
  temporaryRoots.push(root)
  return root
}

describe('canonical path containment', () => {
  it('accepts new descendants and rejects path traversal', async () => {
    const root = await temporaryRoot()
    const allowed = path.join(root, 'allowed')
    await mkdir(allowed)
    expect(isPathWithinRoots(path.join(allowed, 'new', 'artifact.md'), [allowed])).toBe(true)
    expect(isPathWithinRoots(path.join(allowed, '..', 'escaped.md'), [allowed])).toBe(false)
  })

  it('rejects a descendant that escapes through a directory link', async () => {
    const root = await temporaryRoot()
    const allowed = path.join(root, 'allowed')
    const outside = path.join(root, 'outside')
    const linked = path.join(allowed, 'linked')
    await mkdir(allowed)
    await mkdir(outside)
    await symlink(outside, linked, process.platform === 'win32' ? 'junction' : 'dir')

    expect(canonicalizeAccessPath(path.join(linked, 'future.md'))).toBe(path.join(canonicalizeAccessPath(outside), 'future.md'))
    expect(isPathWithinRoots(path.join(linked, 'future.md'), [allowed])).toBe(false)
  })
})
