import type { ReferenceHotwordExtractionResult } from '../../ipc/workerTypes'

export const REFERENCE_HOTWORDS_MODEL = 'mimo-v2.6-flash'

export function matchingReferenceHotwordProfiles<T extends { model: string }>(profiles: T[]): T[] {
  return profiles.filter((profile) => profile.model === REFERENCE_HOTWORDS_MODEL)
}

export function preferredReferenceHotwordProfileId<T extends { id: string; model: string }>(
  profiles: T[],
  selectedSummaryProfile: T | null,
): string {
  const matching = matchingReferenceHotwordProfiles(profiles)
  if (selectedSummaryProfile?.model === REFERENCE_HOTWORDS_MODEL) return selectedSummaryProfile.id
  return matching[0]?.id ?? ''
}

function splitHotwords(text: string): string[] {
  return text.split(/\r?\n/u).map((item) => item.trim()).filter(Boolean)
}

function dedupeKey(term: string): string {
  return term.normalize('NFKC').toLowerCase()
}

export function parseHotwordInput(text: string): string[] {
  const terms: string[] = []
  const seen = new Set<string>()
  for (const term of splitHotwords(text)) {
    const key = dedupeKey(term)
    if (seen.has(key)) continue
    seen.add(key)
    terms.push(term)
  }
  return terms
}

export interface MergedHotwords {
  text: string
  added_count: number
  manual_count: number
}

export function mergeReferenceHotwords(currentText: string, extractedTerms: string[]): MergedHotwords {
  const manualTerms = parseHotwordInput(currentText)

  const terms: string[] = []
  const seen = new Set<string>()
  let addedCount = 0

  for (const term of manualTerms) {
    const key = dedupeKey(term)
    seen.add(key)
    terms.push(term)
  }

  for (const candidate of extractedTerms) {
    const term = candidate.trim()
    if (!term) continue
    const key = dedupeKey(term)
    if (seen.has(key)) continue
    seen.add(key)
    terms.push(term)
    addedCount += 1
  }

  return {
    text: terms.join('\n'),
    added_count: addedCount,
    manual_count: manualTerms.length,
  }
}

export function formatReferenceHotwordError(reason: unknown): string {
  const message = String(reason)
  const documentMatch = message.match(/REFERENCE_DOCUMENT_([A-Z_]+)/u)
  if (documentMatch) {
    const documentCode = documentMatch[1].toLowerCase()
    if (documentCode === 'empty' || (documentCode === 'invalid' && /reference document is empty/iu.test(message))) {
      return '所选文本文件为空，未发送。请选择非空文件。'
    }
    if (documentCode === 'invalid' && /only \.md/iu.test(message)) {
      return '只支持 .md、.markdown 和 .txt 文件，请重新选择文本文件。'
    }
    if (documentCode === 'invalid' && /regular file/iu.test(message)) return '所选路径不是普通文件，请重新选择文本文件。'
    if (documentCode === 'invalid' && /valid UTF-8/iu.test(message)) return '所选文件不是有效的 UTF-8 文本文件，未发送。'
    if (documentCode === 'invalid') return '所选文本文件无效，未发送；请重新选择。'
    if (documentCode === 'unreadable') return '无法读取所选文本文件；请检查文件是否存在或可访问后重新选择。'
    if (documentCode === 'forbidden') return '文本文件未通过文件选择器授权，请重新选择。'
  }
  if (/INVALID_REQUEST: reference_document/iu.test(message)) return '文本文件路径无效，请重新选择。'

  const match = message.match(/REFERENCE_HOTWORD_([A-Z_]+)/u)
  const code = match?.[1]?.toLowerCase() ?? ''
  const messages: Record<string, string> = {
    reference_invalid: '所选文本文件无效，请重新选择。',
    invalid_endpoint: 'MiMo Profile 的服务地址无效，请到设置中检查。',
    profile_not_found: 'MiMo Profile 已变更，请刷新后重试。',
    profile_ambiguous: '多个 MiMo Profile 共用相同标识，请在设置中重新创建其中一个后重试。',
    profile_model_mismatch: `所选 Profile 的模型不是 ${REFERENCE_HOTWORDS_MODEL}。`,
    credential_missing: 'MiMo Profile 未配置 API Key，请到设置中填写并保存。',
    credential_unavailable: '无法读取 MiMo Profile 的 API Key，请到设置中重新保存。',
    busy: '当前已有热词提取请求，请稍后再试。',
    cancelled: '热词提取已取消。',
    timeout: 'MiMo 请求超过 90 秒，未更改现有热词；请检查网络后重试。',
    network_error: '无法连接 MiMo 服务，请检查网络和服务地址。',
    http_400: 'MiMo 服务拒绝了请求（HTTP 400），请检查服务地址和模型配置。',
    unauthorized: 'MiMo 服务认证失败，请检查 API Key。',
    rate_limited: 'MiMo 服务当前请求过多，请稍后重试。',
    provider_error: 'MiMo 服务暂时不可用，请稍后重试。',
    response_too_large: 'MiMo 返回内容超过限制，现有热词未更改。',
    invalid_response: 'MiMo 返回内容格式不正确，现有热词未更改。',
    model_mismatch: 'MiMo 返回的模型标识与请求不一致，现有热词未更改。',
  }
  return messages[code] ?? '热词提取失败，现有热词未更改；请检查 MiMo Profile 后重试。'
}

export interface ReferenceHotwordRequestIdentity {
  requestId: string
  referencePath: string
  profileId: string
  profileVersion: number
}

export function isSameReferenceHotwordRequest(
  expected: ReferenceHotwordRequestIdentity,
  current: ReferenceHotwordRequestIdentity | null,
): boolean {
  return current !== null
    && expected.requestId === current.requestId
    && expected.referencePath === current.referencePath
    && expected.profileId === current.profileId
    && expected.profileVersion === current.profileVersion
}

export interface ReferenceHotwordBatchFailure {
  path: string
  message: string
}

export interface ReferenceHotwordBatchResult {
  completed: number
  failures: ReferenceHotwordBatchFailure[]
  cancelled: boolean
}

export async function runReferenceHotwordBatch(options: {
  paths: readonly string[]
  profileId: string
  profileVersion: number
  createRequestId: () => string
  extract: (request: ReferenceHotwordRequestIdentity) => Promise<ReferenceHotwordExtractionResult>
  shouldContinue: () => boolean
  onStart: (request: ReferenceHotwordRequestIdentity, index: number, total: number) => void
  onFinish: (request: ReferenceHotwordRequestIdentity) => void
  onSuccess: (result: ReferenceHotwordExtractionResult) => void
}): Promise<ReferenceHotwordBatchResult> {
  const failures: ReferenceHotwordBatchFailure[] = []
  let completed = 0
  for (const [index, referencePath] of options.paths.entries()) {
    if (!options.shouldContinue()) break
    const request: ReferenceHotwordRequestIdentity = {
      requestId: options.createRequestId(),
      referencePath,
      profileId: options.profileId,
      profileVersion: options.profileVersion,
    }
    options.onStart(request, index, options.paths.length)
    try {
      const result = await options.extract(request)
      if (!options.shouldContinue()) break
      options.onSuccess(result)
      completed += 1
    } catch (reason) {
      if (!options.shouldContinue()) break
      failures.push({ path: referencePath, message: formatReferenceHotwordError(reason) })
    } finally {
      options.onFinish(request)
    }
  }
  return { completed, failures, cancelled: !options.shouldContinue() }
}
