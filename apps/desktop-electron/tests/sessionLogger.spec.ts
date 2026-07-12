import { mkdtemp, readFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { describe, expect, it } from 'vitest'
import { createSessionLogger } from '../electron/sessionLogger.js'

describe('session logger', () => {
  it('persists UTF-8 runtime diagnostics and redacts secrets', async () => {
    const directory = await mkdtemp(path.join(tmpdir(), 'asr-local-logs-'))
    const logger = createSessionLogger(directory)
    logger.info('模型加载', { workflow_id: 'wf_test', api_key: 'secret-value' })
    logger.workerStderr('Authorization: Bearer abc123')
    const content = await readFile(logger.paths.main, 'utf8')
    expect(content).toContain('模型加载')
    expect(content).toContain('wf_test')
    expect(content).not.toContain('secret-value')
    expect(content).not.toContain('abc123')
  })
})
