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

  it('performs one bounded automatic restart after an unexpected exit', async () => {
    const firstChild = makeFakeChild()
    const restartedChild = makeFakeChild()
    vi.mocked(spawn).mockReturnValueOnce(firstChild).mockReturnValueOnce(restartedChild)
    const client = new WorkflowRuntimeClient('E:/asr-local')
    const statuses: string[] = []
    client.on('runtime-status', (status: { state: string }) => statuses.push(status.state))

    const request = client.request('runtime.capabilities', {})
    await Promise.resolve()
    const hello = messages(firstChild)[0]
    firstChild.stdout.push(`${JSON.stringify({ kind: 'response', request_id: hello.request_id, ok: true, result: { selected_version: 2 } })}\n`)
    await vi.waitFor(() => expect(messages(firstChild)).toHaveLength(2))
    const capabilityRequest = messages(firstChild)[1]
    firstChild.stdout.push(`${JSON.stringify({ kind: 'response', request_id: capabilityRequest.request_id, ok: true, result: {} })}\n`)
    await request

    firstChild.emit('exit', 3221225477, null)
    await vi.advanceTimersByTimeAsync(1_000)
    expect(spawn).toHaveBeenCalledTimes(2)
    expect(messages(restartedChild).map((message) => message.method)).toEqual(['runtime.hello'])
    expect(statuses).toContain('unavailable')

    const restartedHello = messages(restartedChild)[0]
    restartedChild.stdout.push(`${JSON.stringify({ kind: 'response', request_id: restartedHello.request_id, ok: true, result: { selected_version: 2 } })}\n`)
    await vi.waitFor(() => expect(statuses.at(-1)).toBe('ready'))
  })

  it('does not report unavailable or restart during an intentional shutdown', async () => {
    const child = makeFakeChild()
    vi.mocked(spawn).mockReturnValue(child)
    const client = new WorkflowRuntimeClient('E:/asr-local')
    const statuses: string[] = []
    client.on('runtime-status', (status: { state: string }) => statuses.push(status.state))

    const request = client.request('runtime.capabilities', {})
    await Promise.resolve()
    const hello = messages(child)[0]
    child.stdout.push(`${JSON.stringify({ kind: 'response', request_id: hello.request_id, ok: true, result: { selected_version: 2 } })}\n`)
    await vi.waitFor(() => expect(messages(child)).toHaveLength(2))
    const capabilityRequest = messages(child)[1]
    child.stdout.push(`${JSON.stringify({ kind: 'response', request_id: capabilityRequest.request_id, ok: true, result: {} })}\n`)
    await request

    const shutdown = client.shutdown()
    await vi.waitFor(() => expect(messages(child).at(-1)?.method).toBe('runtime.shutdown'))
    const shutdownRequest = messages(child).at(-1)!
    child.stdout.push(`${JSON.stringify({ kind: 'response', request_id: shutdownRequest.request_id, ok: true, result: {} })}\n`)
    await Promise.resolve()
    await Promise.resolve()
    child.emit('exit', 0, null)
    await shutdown
    await vi.advanceTimersByTimeAsync(2_000)

    expect(spawn).toHaveBeenCalledTimes(1)
    expect(statuses).not.toContain('unavailable')
    expect(statuses.at(-1)).toBe('stopped')
  })

  it('rolls back a failed hello and allows a clean retry', async () => {
    const failedChild = makeFakeChild()
    const retriedChild = makeFakeChild()
    vi.mocked(spawn).mockReturnValueOnce(failedChild).mockReturnValueOnce(retriedChild)
    const client = new WorkflowRuntimeClient('E:/asr-local')
    const statuses: string[] = []
    client.on('runtime-status', (status: { state: string }) => statuses.push(status.state))

    const failedRequest = client.request('runtime.capabilities', {})
    await Promise.resolve()
    const failedHello = messages(failedChild)[0]
    failedChild.stdout.push(JSON.stringify({
      kind: 'response',
      request_id: failedHello.request_id,
      ok: true,
      result: { selected_version: 1 },
    }) + '\n')
    await expect(failedRequest).rejects.toThrow('did not negotiate protocol version 2')
    expect(failedChild.kill).toHaveBeenCalledOnce()
    expect(statuses.at(-1)).toBe('error')

    const retriedRequest = client.request('runtime.capabilities', {})
    await Promise.resolve()
    const retriedHello = messages(retriedChild)[0]
    retriedChild.stdout.push(JSON.stringify({
      kind: 'response',
      request_id: retriedHello.request_id,
      ok: true,
      result: { selected_version: 2 },
    }) + '\n')
    await vi.waitFor(() => expect(messages(retriedChild)).toHaveLength(2))
    const capabilityRequest = messages(retriedChild)[1]
    retriedChild.stdout.push(JSON.stringify({
      kind: 'response',
      request_id: capabilityRequest.request_id,
      ok: true,
      result: { methods: [] },
    }) + '\n')

    await expect(retriedRequest).resolves.toEqual({ methods: [] })
    expect(spawn).toHaveBeenCalledTimes(2)
    expect(statuses.at(-1)).toBe('ready')
  })
})
