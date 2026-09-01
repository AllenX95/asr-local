import { describe, expect, it } from 'vitest'
import { artifactStagingPath, describeUtf8Content } from './artifactRevision'

describe('artifact revision helpers', () => {
  it('places staged edits under the workflow-specific output staging root', () => {
    expect(artifactStagingPath(
      'D:\\outputs\\transcripts\\meeting.transcript.md',
      'wf_001',
      'revision-1',
    )).toBe('D:\\outputs/.staging/wf_001/edit-revision-1.md')
    expect(artifactStagingPath(
      '/data/outputs/summary/meeting.summary.md',
      'wf_002',
      'revision-2',
    )).toBe('/data/outputs/.staging/wf_002/edit-revision-2.md')
  })

  it('describes the exact UTF-8 bytes written to staging', async () => {
    await expect(describeUtf8Content('abc')).resolves.toEqual({
      size_bytes: 3,
      sha256: 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad',
    })
  })
})
