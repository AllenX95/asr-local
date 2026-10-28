import { mkdtemp, readFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { createHash } from 'node:crypto'
import { describe, expect, it, vi } from 'vitest'

vi.mock('electron', () => ({ safeStorage: {} }))

import { readReferenceDocumentSnapshot } from '../electron/hostServices.js'
import { logReferenceHotwordFailure, withReferenceHotwordFailureLogging } from '../electron/referenceHotwordDiagnostics.js'
import { extractReferenceHotwords, ReferenceHotwordError, type ReferenceHotwordDiagnostics } from '../electron/referenceHotwords.js'
import { createSessionLogger } from '../electron/sessionLogger.js'

describe('reference hotword failure diagnostics', () => {
  it('persists a provider failure and safe request metadata without file content, paths, credentials or provider bodies', async () => {
    const directory = await mkdtemp(path.join(tmpdir(), 'asr-hotword-diagnostics-'))
    const logger = createSessionLogger(directory)
    const privatePath = path.join(directory, 'private-company-notes.md')
    const privateText = 'private-document-content'
    const privateKey = 'private-api-credential'
    const providerBody = 'private-provider-response'
    const fetchImpl = vi.fn(async () => new Response(providerBody, { status: 429 })) as unknown as typeof fetch
    let failure: unknown
    try {
      await withReferenceHotwordFailureLogging(logger, {
        stage: 'provider', startedAt: Date.now(), requestId: 'db85cc6a-f414-4577-96a6-1e01e7424452',
        referencePath: privatePath, referenceSizeBytes: 24, profileVersion: 2,
      }, () => extractReferenceHotwords({
        baseUrl: 'https://example.invalid/v1',
        apiKey: privateKey,
        document: { name: 'private-company-notes.md', content: privateText, size_bytes: 24, sha256: 'a'.repeat(64) },
        fetchImpl,
      }))
    } catch (error) { failure = error }
    expect(failure).toBeInstanceOf(ReferenceHotwordError)

    const log = await readFile(logger.paths.main, 'utf8')
    expect(log).toContain('Reference hotword extraction failed')
    expect(log).toContain('"error_code":"REFERENCE_HOTWORD_RATE_LIMITED"')
    expect(log).toContain('"stage":"provider"')
    expect(log).toContain('"http_status":429')
    expect(log).toContain('"chunk_index":1')
    expect(log).toContain('"request_id":"db85cc6a-f414-4577-96a6-1e01e7424452"')
    expect(log).toContain(createHash('sha256').update(privatePath).digest('hex').slice(0, 16))
    for (const secret of [privatePath, 'private-company-notes.md', privateText, privateKey, providerBody]) {
      expect(log).not.toContain(secret)
    }
  })

  it('records a missing selected file and its safe OS error code without the filename', async () => {
    const directory = await mkdtemp(path.join(tmpdir(), 'asr-hotword-diagnostics-file-'))
    const logger = createSessionLogger(directory)
    const missingPath = path.join(directory, 'sensitive-missing-file.md')
    let failure: unknown
    try { await readReferenceDocumentSnapshot(missingPath, { allowTextFiles: true }) } catch (error) { failure = error }
    logReferenceHotwordFailure(logger, { stage: 'document', startedAt: Date.now(), referencePath: missingPath }, failure)

    const log = await readFile(logger.paths.main, 'utf8')
    expect(log).toContain('"error_code":"REFERENCE_DOCUMENT_UNREADABLE"')
    expect(log).toContain('"document_issue":"not_found"')
    expect(log).toContain('"system_code":"ENOENT"')
    expect(log).not.toContain(missingPath)
    expect(log).not.toContain('sensitive-missing-file.md')
  })

  it('ignores user cancellations and never lets logging failures replace the extraction failure', async () => {
    const logger = { error: vi.fn(() => { throw new Error('log write failed') }) }
    const context = { stage: 'provider' as const, startedAt: Date.now() }
    const cancelled = new ReferenceHotwordError('cancelled')
    await expect(withReferenceHotwordFailureLogging(logger, context, async () => { throw cancelled })).rejects.toBe(cancelled)
    expect(logger.error).not.toHaveBeenCalled()
    const failure = new ReferenceHotwordError('timeout')
    await expect(withReferenceHotwordFailureLogging(logger, context, async () => { throw failure })).rejects.toBe(failure)
    expect(logger.error).toHaveBeenCalledTimes(1)
  })

  it.each([
    ['profile', 'profile_not_found', 'REFERENCE_HOTWORD_PROFILE_NOT_FOUND'],
    ['credential', 'credential_unavailable', 'REFERENCE_HOTWORD_CREDENTIAL_UNAVAILABLE'],
    ['validation', 'busy', 'REFERENCE_HOTWORD_BUSY'],
  ] as const)('records %s failures with their stage and error code', (stage, code, expectedCode) => {
    const logger = { error: vi.fn() }
    logReferenceHotwordFailure(logger, { stage, startedAt: Date.now() }, new ReferenceHotwordError(code))
    expect(logger.error).toHaveBeenCalledWith('Reference hotword extraction failed', expect.objectContaining({ stage, error_code: expectedCode }))
  })

  it('never persists arbitrary exception messages or unapproved diagnostic fields', () => {
    const logger = { error: vi.fn() }
    const context = { stage: 'provider' as const, startedAt: Date.now(), requestId: 'not-a-valid-id-secret' }
    logReferenceHotwordFailure(logger, context, new Error('private-provider-body private-api-key'))
    const untrusted = { network_code: 'private-host', response_issue: 'private-response', http_status: 999 } as unknown as ReferenceHotwordDiagnostics
    logReferenceHotwordFailure(logger, context, new ReferenceHotwordError('network_error', untrusted))
    const recorded = JSON.stringify(logger.error.mock.calls)
    for (const secret of ['private-provider-body', 'private-api-key', 'private-host', 'private-response', 'not-a-valid-id-secret', '999']) {
      expect(recorded).not.toContain(secret)
    }
    expect(recorded).toContain('UNEXPECTED_ERROR')
    expect(recorded).toContain('REFERENCE_HOTWORD_NETWORK_ERROR')
  })
})
