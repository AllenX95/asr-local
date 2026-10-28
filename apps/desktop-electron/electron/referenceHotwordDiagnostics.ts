import { createHash } from 'node:crypto'
import type { SessionLogger } from './sessionLogger.js'
import {
  REFERENCE_HOTWORD_ERROR_CODES,
  REFERENCE_HOTWORD_NETWORK_CODES,
  REFERENCE_HOTWORD_RESPONSE_ISSUES,
  REFERENCE_HOTWORDS_MODEL,
  ReferenceHotwordError,
} from './referenceHotwords.js'

export type ReferenceHotwordFailureStage = 'validation' | 'document' | 'profile' | 'credential' | 'provider'

export interface ReferenceHotwordFailureContext {
  stage: ReferenceHotwordFailureStage
  startedAt: number
  requestId?: string
  referencePath?: unknown
  referenceSizeBytes?: number
  profileVersion?: unknown
}

function documentError(error: unknown): { code: string; detail?: string } | null {
  if (!(error instanceof Error)) return null
  const match = /^REFERENCE_DOCUMENT_(INVALID|UNREADABLE|FORBIDDEN|EMPTY)(?::|$)/u.exec(error.message)
  if (!match) return null
  const message = error.message
  let detail: string | undefined
  if (message.startsWith('REFERENCE_DOCUMENT_INVALID: reference document is empty')) detail = 'empty'
  else if (message.startsWith('REFERENCE_DOCUMENT_INVALID: only .md')) detail = 'unsupported_extension'
  else if (message.startsWith('REFERENCE_DOCUMENT_INVALID: selected reference must be a regular file')) detail = 'not_regular_file'
  else if (message.startsWith('REFERENCE_DOCUMENT_INVALID: reference document must be valid UTF-8')) detail = 'invalid_utf8'
  else if (message.startsWith('REFERENCE_DOCUMENT_UNREADABLE: file does not exist')) detail = 'not_found'
  else if (message.startsWith('REFERENCE_DOCUMENT_UNREADABLE: cannot read file')) detail = 'read_failed'
  return { code: `REFERENCE_DOCUMENT_${match[1]}`, detail }
}

function safePositiveInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && Number(value) > 0
}

function safeFileSystemCode(error: unknown): string | undefined {
  const allowed = ['ENOENT', 'EACCES', 'EPERM', 'EIO', 'EBUSY', 'ENOTDIR', 'EISDIR']
  const cause = error instanceof Error ? error.cause : undefined
  for (const candidate of [error, cause]) {
    if (!candidate || typeof candidate !== 'object') continue
    const code = (candidate as { code?: unknown }).code
    if (typeof code === 'string' && allowed.includes(code)) return code
  }
  return undefined
}

/** Log only approved metadata. Error messages, paths, prompts, responses and credentials may contain private data. */
export function logReferenceHotwordFailure(
  logger: Pick<SessionLogger, 'error'>,
  context: ReferenceHotwordFailureContext,
  error: unknown,
): void {
  if (error instanceof ReferenceHotwordError && error.code === 'cancelled') return
  try {
    const document = documentError(error)
    const knownHotwordError = error instanceof ReferenceHotwordError
      && (REFERENCE_HOTWORD_ERROR_CODES as readonly string[]).includes(error.code)
    const code = knownHotwordError
      ? `REFERENCE_HOTWORD_${error.code.toUpperCase()}`
      : document?.code ?? (context.stage === 'validation' || (error instanceof Error
        && (error.message.startsWith('INVALID_REQUEST:') || /^(referencePath|profileId) must be a string$/u.test(error.message)))
        ? 'INVALID_REQUEST' : 'UNEXPECTED_ERROR')
    const data: Record<string, string | number> = {
      stage: context.stage,
      error_code: code,
      model: REFERENCE_HOTWORDS_MODEL,
      elapsed_ms: Math.max(0, Date.now() - context.startedAt),
    }
    if (context.requestId && /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/iu.test(context.requestId)) {
      data.request_id = context.requestId
    }
    if (typeof context.referencePath === 'string' && context.referencePath) {
      data.source_path_hash = createHash('sha256').update(context.referencePath, 'utf8').digest('hex').slice(0, 16)
    }
    if (safePositiveInteger(context.referenceSizeBytes)) data.source_size_bytes = context.referenceSizeBytes
    if (safePositiveInteger(context.profileVersion)) data.profile_version = context.profileVersion
    if (document?.detail) data.document_issue = document.detail
    if (context.stage === 'document') {
      const systemCode = safeFileSystemCode(error)
      if (systemCode) data.system_code = systemCode
    }
    if (knownHotwordError) {
      const diagnostics = error.diagnostics
      if (safePositiveInteger(diagnostics.chunk_index)) data.chunk_index = diagnostics.chunk_index
      if (safePositiveInteger(diagnostics.http_status) && diagnostics.http_status >= 100 && diagnostics.http_status <= 599) data.http_status = diagnostics.http_status
      if (safePositiveInteger(diagnostics.timeout_ms)) data.timeout_ms = diagnostics.timeout_ms
      if (diagnostics.network_code && (REFERENCE_HOTWORD_NETWORK_CODES as readonly string[]).includes(diagnostics.network_code)) {
        data.network_code = diagnostics.network_code
      }
      if (diagnostics.response_issue && (REFERENCE_HOTWORD_RESPONSE_ISSUES as readonly string[]).includes(diagnostics.response_issue)) {
        data.response_issue = diagnostics.response_issue
      }
    }
    logger.error('Reference hotword extraction failed', data)
  } catch {
    // A logging failure must never replace the original extraction error.
  }
}

export async function withReferenceHotwordFailureLogging<T>(
  logger: Pick<SessionLogger, 'error'>,
  context: ReferenceHotwordFailureContext,
  operation: () => Promise<T>,
): Promise<T> {
  try {
    return await operation()
  } catch (error) {
    logReferenceHotwordFailure(logger, context, error)
    throw error
  }
}
