import { describe, expect, it } from 'vitest'
import { chunkProgressLabel } from './progress'

describe('chunkProgressLabel', () => {
  it('formats completed and total ASR chunks', () => {
    expect(chunkProgressLabel({ completed_chunks: 500, total_chunks: 1000 })).toBe('500 / 1000 个分块')
    expect(chunkProgressLabel({ completed_chunks: 0, total_chunks: 1000 })).toBe('0 / 1000 个分块')
  })

  it('clamps a stale completed count to the known total', () => {
    expect(chunkProgressLabel({ completed_chunks: 12, total_chunks: 10 })).toBe('10 / 10 个分块')
  })

  it('hides incomplete or invalid counters', () => {
    expect(chunkProgressLabel({ completed_chunks: 2 })).toBeNull()
    expect(chunkProgressLabel({ completed_chunks: 0, total_chunks: 0 })).toBeNull()
    expect(chunkProgressLabel({ completed_chunks: -1, total_chunks: 10 })).toBeNull()
    expect(chunkProgressLabel({ completed_chunks: 1.5, total_chunks: 10 })).toBeNull()
  })
})
