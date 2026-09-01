import { existsSync, realpathSync } from 'node:fs'
import path from 'node:path'

export function canonicalizeAccessPath(target: string): string {
  let existingAncestor = path.resolve(target)
  const missingSegments: string[] = []
  while (!existsSync(existingAncestor)) {
    const parent = path.dirname(existingAncestor)
    if (parent === existingAncestor) break
    missingSegments.unshift(path.basename(existingAncestor))
    existingAncestor = parent
  }
  const canonicalAncestor = existsSync(existingAncestor)
    ? realpathSync.native(existingAncestor)
    : existingAncestor
  return path.resolve(canonicalAncestor, ...missingSegments)
}

export function isPathWithinRoots(target: string, roots: Iterable<string>): boolean {
  const canonicalTarget = canonicalizeAccessPath(target)
  return [...roots].some((root) => {
    const relative = path.relative(canonicalizeAccessPath(root), canonicalTarget)
    return relative === '' || (!path.isAbsolute(relative) && relative !== '..' && !relative.startsWith(`..${path.sep}`))
  })
}
