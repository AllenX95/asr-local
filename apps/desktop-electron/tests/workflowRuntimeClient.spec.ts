import { EventEmitter } from 'node:events'
import { PassThrough } from 'node:stream'
import type { ChildProcessWithoutNullStreams } from 'node:child_process'
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('node:child_process', () => ({
  spawn: vi.fn(),
}))

import { spawn } from 'node:child_process'
import { WorkflowRuntimeClient } from '../electron/workflowRuntimeClient.js'

type FakeChild = ChildProcessWithoutNullStreams & {
  writes: string[]
  stdout: PassThrough
  kill: ReturnType<typeof vi.fn>
}

function makeFakeChild(): FakeChild {
  const stdout = new PassThrough()
  const stderr = new PassThrough()
  const writes: string[] = []
  const stdin = {
    write: vi.fn((chunk: string, _encoding: BufferEncoding, callback?: (error?: Error | null) => void) => {
      writes.push(String(chunk))
      callback?.()
      return true
    }),
  }
  const child = Object.assign(new EventEmitter(), {
    stdin,
    stdout,
    stderr,
    pid: 4242,
    exitCode: null,
    signalCode: null,
    killed: false,
    kill: vi.fn(() => true),
    writes,
  }) as unknown as FakeChild
  return child
}

function messages(child: FakeChild): Array<{ request_id?: string; method?: string }> {
  return child.writes
    .join('')
    .split('\n')
    .filter(Boolean)
    .map((line) => JSON.parse(line) as { request_id?: string; method?: string })
}

describe('WorkflowRuntimeClient cold start single-flight', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.mocked(spawn).mockReset()
  })

  it('waits for hello before sending concurrent business requests', async () => {
    const child = makeFakeChild()
    vi.mocked(spawn).mockReturnValue(child)
    const client = new WorkflowRuntimeClient('E:/asr-local')
    let firstState = 'pending'
    let secondState = 'pending'
    const first = client.request('runtime.capabilities', {}).then(
      () => { firstState = 'resolved' },
      () => { firstState = 'rejected' },
    )
    const second = client.request('workflow.list', {}).then(
      () => { secondState = 'resolved' },
      () => { secondState = 'rejected' },
    )

    await Promise.resolve()
    expect(messages(child).map((message) => message.method)).toEqual(['runtime.hello'])

    await vi.advanceTimersByTimeAsync(30_001)
    expect(firstState).toBe('pending')
    expect(secondState).toBe('pending')
    expect(messages(child).map((message) => message.method)).toEqual(['runtime.hello'])

    const hello = messages(child)[0]
    child.stdout.push(`${JSON.stringify({ kind: 'response', request_id: hello.request_id, ok: true, result: { selected_version: 2 } })}\n`)
    await vi.waitFor(() => {
      expect(messages(child).map((message) => message.method)).toEqual([
        'runtime.hello',
        'runtime.capabilities',
        'workflow.list',
      ])
    })

    const business = messages(child).slice(1)
    for (const message of business) {
      child.stdout.push(`${JSON.stringify({ kind: 'response', request_id: message.request_id, ok: true, result: { ok: message.method } })}\n`)
    }
    await Promise.all([first, second])
    expect(firstState).toBe('resolved')
    expect(secondState).toBe('resolved')
  })
})
