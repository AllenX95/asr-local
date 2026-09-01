export function artifactStagingPath(
  artifactPath: string,
  workflowId: string,
  revisionId: string = crypto.randomUUID(),
): string {
  const slash = Math.max(artifactPath.lastIndexOf('\\'), artifactPath.lastIndexOf('/'))
  const directory = slash >= 0 ? artifactPath.slice(0, slash) : '.'
  const outputRoot = directory.replace(/[\\/](?:transcripts|summary)$/u, '')
  return `${outputRoot}/.staging/${workflowId}/edit-${revisionId}.md`
}

export async function describeUtf8Content(content: string): Promise<{
  size_bytes: number
  sha256: string
}> {
  const bytes = new TextEncoder().encode(content)
  const digestBuffer = await crypto.subtle.digest('SHA-256', bytes)
  const sha256 = Array.from(new Uint8Array(digestBuffer))
    .map((byte) => byte.toString(16).padStart(2, '0'))
    .join('')
  return { size_bytes: bytes.byteLength, sha256 }
}
