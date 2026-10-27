import { describe, expect, it, vi } from 'vitest'
import {
  MAX_REFERENCE_HOTWORDS_RESPONSE_BYTES,
  REFERENCE_HOTWORDS_MAX_TOKENS,
  REFERENCE_HOTWORDS_MODEL,
  REFERENCE_HOTWORDS_PROMPT_VERSION,
  ReferenceHotwordError,
  extractReferenceHotwords,
  resolveTrustedReferenceHotwordProfile,
  type ReferenceHotwordDocument,
} from '../electron/referenceHotwords.js'

const document: ReferenceHotwordDocument = {
  name: 'notes.md',
  content: '# 项目速记\n团队采用 Qwen3-ASR 和 ASR Local。',
  size_bytes: 0,
  sha256: 'a'.repeat(64),
}

function providerResponse(content: string, options: { model?: string; finishReason?: string; usage?: unknown } = {}): Response {
  return new Response(JSON.stringify({
    model: options.model ?? REFERENCE_HOTWORDS_MODEL,
    choices: [{ finish_reason: options.finishReason ?? 'stop', message: { content } }],
    usage: options.usage ?? { prompt_tokens: 40, completion_tokens: 20, total_tokens: 60 },
  }), { status: 200, headers: { 'Content-Type': 'application/json' } })
}

function input(fetchImpl: typeof fetch, overrides: Partial<Parameters<typeof extractReferenceHotwords>[0]> = {}) {
  return extractReferenceHotwords({
    baseUrl: 'https://mimo.example/v1',
    apiKey: 'never-return-this-key',
    document,
    fetchImpl,
    ...overrides,
  })
}

describe('extractReferenceHotwords', () => {
  it('requires the exact fixed-model profile version and returns no credential material', () => {
    const profile = {
      id: 'mimo-profile',
      version: 4,
      name: 'MiMo Flash',
      model: REFERENCE_HOTWORDS_MODEL,
      base_url: 'https://mimo.example/v1',
      has_api_key: true,
      api_key: 'secret-must-not-be-returned',
    }
    expect(resolveTrustedReferenceHotwordProfile([profile], 'mimo-profile', 4)).toEqual({
      id: 'mimo-profile',
      version: 4,
      baseUrl: 'https://mimo.example/v1',
    })
    expect(() => resolveTrustedReferenceHotwordProfile([profile], 'mimo-profile', 3))
      .toThrow('REFERENCE_HOTWORD_PROFILE_NOT_FOUND')
    expect(() => resolveTrustedReferenceHotwordProfile([{ ...profile, model: 'other-model' }], 'mimo-profile', 4))
      .toThrow('REFERENCE_HOTWORD_PROFILE_MODEL_MISMATCH')
    expect(() => resolveTrustedReferenceHotwordProfile([{ ...profile, has_api_key: false }], 'mimo-profile', 4))
      .toThrow('REFERENCE_HOTWORD_CREDENTIAL_MISSING')
  })

  it('disambiguates a legacy ID shared by another model and rejects duplicate MiMo bindings', () => {
    const profile = { id: 'legacy-id', version: 5, name: 'MiMo', model: REFERENCE_HOTWORDS_MODEL, base_url: 'https://mimo.example/v1', has_api_key: true }
    const other = { ...profile, name: 'Other', model: 'other-model' }
    expect(resolveTrustedReferenceHotwordProfile([other, profile], 'legacy-id', 5).baseUrl).toBe(profile.base_url)
    expect(() => resolveTrustedReferenceHotwordProfile([profile, { ...profile, name: 'Second' }], 'legacy-id', 5)).toThrow('REFERENCE_HOTWORD_PROFILE_AMBIGUOUS')
  })

  it('uses the fixed model and prompt, and returns only source-backed terms with metadata', async () => {
    const fetchImpl = vi.fn(async () => providerResponse(JSON.stringify({
      terms: ['Qwen3-ASR', 'ASR Local', 'not in notes', 'ASR Local', ''],
    }))) as unknown as typeof fetch

    const result = await input(fetchImpl)
    const [url, request] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit]
    const payload = JSON.parse(String(request.body)) as Record<string, any>

    expect(url).toBe('https://mimo.example/v1/chat/completions')
    expect(payload.model).toBe('mimo-v2.6-flash')
    expect(payload.max_tokens).toBe(REFERENCE_HOTWORDS_MAX_TOKENS)
    expect(payload.messages[1].content).toContain('<reference_notes>\n' + document.content + '\n</reference_notes>')
    expect(payload.messages[0].content).toContain('不设置固定词数上限')
    expect(request.headers).toMatchObject({ Authorization: 'Bearer never-return-this-key' })
    expect(result).toMatchObject({
      terms: ['Qwen3-ASR', 'ASR Local'],
      model: REFERENCE_HOTWORDS_MODEL,
      prompt_version: REFERENCE_HOTWORDS_PROMPT_VERSION,
      reference_sha256: document.sha256,
      reference_name: document.name,
      usage: { prompt_tokens: 40, completion_tokens: 20, total_tokens: 60 },
      rejected_count: 3,
    })
    expect(result.elapsed_ms).toBeGreaterThanOrEqual(0)
  })

  it('extracts from all parts of long text, including boundary terms, and deduplicates overlaps', async () => {
    const shared = '重复术语'
    const boundary = '跨界😄词条'
    const tail = '末尾术语'
    const content = '甲'.repeat(19_900) + shared + '乙'.repeat(93) + boundary + '丙'.repeat(20_300) + tail
    const chunks: string[] = []
    const fetchImpl = vi.fn(async (_url, request) => {
      const payload = JSON.parse(String(request?.body)) as { messages: { content: string }[] }
      const chunk = payload.messages[1].content.slice('<reference_notes>\n'.length, -'\n</reference_notes>'.length)
      chunks.push(chunk)
      return providerResponse(JSON.stringify({ terms: [shared, boundary, tail].filter((term) => chunk.includes(term)) }))
    }) as unknown as typeof fetch
    const result = await input(fetchImpl, { document: { ...document, content } })

    expect(chunks.length).toBeGreaterThan(2)
    let covered = 0
    for (const chunk of chunks) {
      const start = content.indexOf(chunk, Math.max(0, covered - 200))
      expect(start).toBeGreaterThanOrEqual(0)
      expect(start).toBeLessThanOrEqual(covered)
      covered = start + chunk.length
      expect(Buffer.from(chunk, 'utf8').toString('utf8')).toBe(chunk)
    }
    expect(covered).toBe(content.length)
    expect(chunks[0]).toContain(shared)
    expect(chunks[1]).toContain(boundary)
    expect(result.terms).toEqual([shared, boundary, tail])
    expect(result.rejected_count).toBe(1)
    expect(result.usage).toEqual({ prompt_tokens: 40 * chunks.length, completion_tokens: 20 * chunks.length, total_tokens: 60 * chunks.length })
  })

  it('fails the whole extraction when a later chunk fails', async () => {
    const content = 'Qwen3-ASR' + '字'.repeat(40_000)
    const fetchImpl = vi.fn()
      .mockImplementationOnce(async () => providerResponse('{"terms":["Qwen3-ASR"]}'))
      .mockImplementationOnce(async () => new Response('', { status: 400 })) as unknown as typeof fetch
    await expect(input(fetchImpl, { document: { ...document, content } }))
      .rejects.toMatchObject<Partial<ReferenceHotwordError>>({ code: 'http_400' })
    expect(fetchImpl).toHaveBeenCalledTimes(2)
  })

  it('cancels an in-flight later chunk without returning partial terms', async () => {
    const content = 'Qwen3-ASR' + '字'.repeat(40_000)
    const externalController = new AbortController()
    let notifySecondStarted!: () => void
    const secondStarted = new Promise<void>((resolve) => { notifySecondStarted = resolve })
    let callCount = 0
    const fetchImpl: typeof fetch = ((_url, init) => {
      callCount += 1
      if (callCount === 1) return Promise.resolve(providerResponse('{"terms":["Qwen3-ASR"]}'))
      notifySecondStarted()
      return new Promise((_resolve, reject) => {
        init?.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')), { once: true })
      })
    }) as typeof fetch
    const pending = input(fetchImpl, { document: { ...document, content }, signal: externalController.signal })
    await secondStarted
    externalController.abort()
    await expect(pending).rejects.toMatchObject<Partial<ReferenceHotwordError>>({ code: 'cancelled' })
    expect(callCount).toBe(2)
  })

  it('filters invisible Unicode formatting controls from source-backed terms', async () => {
    const controlledTerm = '\u202eHidden'
    const sourceDocument = { ...document, content: document.content + ' ' + controlledTerm }
    const fetchImpl = vi.fn(async () => providerResponse(JSON.stringify({ terms: [controlledTerm] }))) as unknown as typeof fetch
    const result = await input(fetchImpl, { document: sourceDocument })
    expect(result.terms).toEqual([])
    expect(result.rejected_count).toBe(1)
  })

  it.each([
    ['markdown-wrapped JSON', '```json\n{"terms":[]}\n```', {}],
    ['truncated response', JSON.stringify({ terms: [] }), { finishReason: 'length' }],
    ['extra response keys', JSON.stringify({ terms: [], explanation: 'ignored' }), {}],
  ])('rejects %s', async (_label, content, options) => {
    const fetchImpl = vi.fn(async () => providerResponse(content, options)) as unknown as typeof fetch
    await expect(input(fetchImpl)).rejects.toMatchObject<Partial<ReferenceHotwordError>>({ code: 'invalid_response' })
  })

  it('preserves every valid source term without imposing a 40-term limit', async () => {
    const terms = Array.from({ length: 43 }, (_, i) => `专名${i}`)
    const fetchImpl = vi.fn(async () => providerResponse(JSON.stringify({ terms }))) as unknown as typeof fetch
    const result = await input(fetchImpl, { document: { ...document, content: terms.join('、') } })
    expect(result.terms).toEqual(terms)
    expect(result.rejected_count).toBe(0)
  })

  it('distinguishes a provider model mismatch', async () => {
    const fetchImpl = vi.fn(async () => providerResponse('{"terms":[]}', { model: 'another-model' })) as unknown as typeof fetch
    await expect(input(fetchImpl)).rejects.toMatchObject<Partial<ReferenceHotwordError>>({ code: 'model_mismatch' })
  })

  it('maps HTTP failures without exposing provider bodies or credentials', async () => {
    const providerBody = 'provider-private-detail never-return-this-key'
    const fetchImpl = vi.fn(async () => new Response(providerBody, { status: 400 })) as unknown as typeof fetch
    let caught: unknown
    try {
      await input(fetchImpl)
    } catch (error) {
      caught = error
    }
    expect(caught).toMatchObject<Partial<ReferenceHotwordError>>({ code: 'http_400' })
    expect(String(caught)).not.toContain(providerBody)
    expect(String(caught)).not.toContain('never-return-this-key')
  })

  it('bounds provider responses', async () => {
    const fetchImpl = vi.fn(async () => new Response('x'.repeat(MAX_REFERENCE_HOTWORDS_RESPONSE_BYTES + 1))) as unknown as typeof fetch
    await expect(input(fetchImpl)).rejects.toMatchObject<Partial<ReferenceHotwordError>>({ code: 'response_too_large' })
  })

  it('supports explicit cancellation and a bounded timeout', async () => {
    const waitForAbort: typeof fetch = ((_url, init) => new Promise((_resolve, reject) => {
      init?.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')), { once: true })
    })) as typeof fetch
    const externalController = new AbortController()
    const pending = input(waitForAbort, { signal: externalController.signal })
    externalController.abort()
    await expect(pending).rejects.toMatchObject<Partial<ReferenceHotwordError>>({ code: 'cancelled' })

    await expect(input(waitForAbort, { timeoutMs: 1 }))
      .rejects.toMatchObject<Partial<ReferenceHotwordError>>({ code: 'timeout' })
  })
})
