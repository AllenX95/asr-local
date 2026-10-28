import { describe, expect, it, vi } from 'vitest'
import type { ReferenceHotwordExtractionResult, WorkflowSummaryProfile, WorkflowSummaryTemplate } from '../src/ipc/workerTypes'
import {
  isSameReferenceHotwordRequest,
  mergeReferenceHotwords,
  parseHotwordInput,
  formatReferenceHotwordError,
  runReferenceHotwordBatch,
  type ReferenceHotwordRequestIdentity,
} from '../src/features/workflow/referenceHotwords'
import { buildWorkflowDraft } from '../src/features/workflow/workflowDraft'

function extractionResult(terms: string[]): ReferenceHotwordExtractionResult {
  return {
    terms,
    model: 'mimo-v2.6-flash',
    prompt_version: 'test',
    reference_sha256: 'test',
    reference_name: 'test',
    usage: null,
    rejected_count: 0,
    elapsed_ms: 1,
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

describe('reference hotword workflow state', () => {
  it.each([
    ['REFERENCE_DOCUMENT_INVALID: reference document is empty', '文本文件为空'],
    ['REFERENCE_DOCUMENT_EMPTY: reference document is empty', '文本文件为空'],
    ['REFERENCE_DOCUMENT_INVALID: only .md and .markdown files are supported', '.md、.markdown 和 .txt'],
    ['REFERENCE_DOCUMENT_INVALID: selected reference must be a regular file', '不是普通文件'],
    ['REFERENCE_DOCUMENT_INVALID: reference document must be valid UTF-8', 'UTF-8 文本文件'],
    ['REFERENCE_DOCUMENT_UNREADABLE: cannot read file', '无法读取所选文本文件'],
    ['REFERENCE_DOCUMENT_FORBIDDEN: file was not selected', '重新选择'],
    ['INVALID_REQUEST: reference_document path must be non-empty', '文本文件路径无效'],
  ])('shows a reference-file message for %s', (reason, expectedMessage) => {
    const message = formatReferenceHotwordError(reason)
    expect(message).toContain(expectedMessage)
    expect(message).not.toContain('MiMo Profile')
  })

  it('merges into the current manual text, dedupes NFKC/case-insensitively, and keeps manual terms first', () => {
    const currentManualText = 'CustomerName\nＡＳＲ Local\nKeep this edit'
    const merged = mergeReferenceHotwords(currentManualText, ['ASR Local', 'Qwen3-ASR', 'CustomerName'])

    expect(merged).toEqual({
      text: 'CustomerName\nＡＳＲ Local\nKeep this edit\nQwen3-ASR',
      added_count: 1,
      manual_count: 3,
    })
  })

  it('keeps 201 manual terms and merges every unique extracted term without a count cap', () => {
    const manualTerms = Array.from({ length: 201 }, (_value, index) => 'manual-' + index)
    const manual = manualTerms.join('\n')
    const merged = mergeReferenceHotwords(manual, ['manual-0', 'generated-one', 'generated-two'])

    expect(merged).toEqual({
      text: [...manualTerms, 'generated-one', 'generated-two'].join('\n'),
      added_count: 2,
      manual_count: 201,
    })
    expect(parseHotwordInput(merged.text)).toHaveLength(203)
  })

  it('keeps punctuation inside each one-per-line hotword', () => {
    expect(parseHotwordInput('DeepMind, Inc.\nQwen3-ASR')).toEqual(['DeepMind, Inc.', 'Qwen3-ASR'])
  })

  it('preserves a new 201st manual term and merges all extracted terms when extraction returns', async () => {
    const initialText = Array.from({ length: 200 }, (_value, index) => 'manual-' + index).join('\n')
    const extracting = Promise.resolve(['generated-one', 'generated-two'])
    let currentText = initialText
    currentText += '\nmanual-added-while-extracting'

    const merged = mergeReferenceHotwords(currentText, await extracting)
    expect(merged).toEqual({
      text: currentText + '\ngenerated-one\ngenerated-two',
      added_count: 2,
      manual_count: 201,
    })
    expect(parseHotwordInput(merged.text)).toHaveLength(203)
  })

  it('ignores results for an older request, reference path, or profile snapshot', () => {
    const expected: ReferenceHotwordRequestIdentity = {
      requestId: 'request-1',
      referencePath: 'notes.md',
      profileId: 'mimo-profile',
      profileVersion: 3,
    }
    expect(isSameReferenceHotwordRequest(expected, expected)).toBe(true)
    expect(isSameReferenceHotwordRequest(expected, { ...expected, requestId: 'request-2' })).toBe(false)
    expect(isSameReferenceHotwordRequest(expected, { ...expected, referencePath: 'other.md' })).toBe(false)
    expect(isSameReferenceHotwordRequest(expected, { ...expected, profileVersion: 4 })).toBe(false)
    expect(isSameReferenceHotwordRequest(expected, null)).toBe(false)
  })

  it('extracts selected files one at a time, merges successes and continues after a file fails', async () => {
    const first = deferred<ReferenceHotwordExtractionResult>()
    const second = deferred<ReferenceHotwordExtractionResult>()
    const third = deferred<ReferenceHotwordExtractionResult>()
    const extract = vi.fn()
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise)
      .mockReturnValueOnce(third.promise)
    let nextId = 0
    let text = 'Manual'
    const started: string[] = []
    const batch = runReferenceHotwordBatch({
      paths: ['one.md', 'broken.txt', 'three.md'],
      profileId: 'mimo-profile',
      profileVersion: 3,
      createRequestId: () => `request-${++nextId}`,
      extract,
      shouldContinue: () => true,
      onStart: (request) => { started.push(request.referencePath) },
      onFinish: () => {},
      onSuccess: (result) => { text = mergeReferenceHotwords(text, result.terms).text },
    })

    expect(started).toEqual(['one.md'])
    first.resolve(extractionResult(['Shared', 'First']))
    await Promise.resolve()
    expect(started).toEqual(['one.md', 'broken.txt'])
    text += '\nManual edit during extraction'
    second.reject(new Error('REFERENCE_DOCUMENT_UNREADABLE'))
    await Promise.resolve()
    expect(started).toEqual(['one.md', 'broken.txt', 'three.md'])
    third.resolve(extractionResult(['Shared', 'Third']))

    expect(await batch).toEqual({
      completed: 2,
      failures: [{ path: 'broken.txt', message: expect.stringContaining('无法读取所选文本文件') }],
      cancelled: false,
    })
    expect(text).toBe('Manual\nShared\nFirst\nManual edit during extraction\nThird')
    expect(extract.mock.calls.map(([request]) => request)).toEqual([
      { requestId: 'request-1', referencePath: 'one.md', profileId: 'mimo-profile', profileVersion: 3 },
      { requestId: 'request-2', referencePath: 'broken.txt', profileId: 'mimo-profile', profileVersion: 3 },
      { requestId: 'request-3', referencePath: 'three.md', profileId: 'mimo-profile', profileVersion: 3 },
    ])
  })

  it('stops after cancellation, retains earlier results and ignores a late response', async () => {
    const first = deferred<ReferenceHotwordExtractionResult>()
    const inFlight = deferred<ReferenceHotwordExtractionResult>()
    const extract = vi.fn().mockReturnValueOnce(first.promise).mockReturnValueOnce(inFlight.promise)
    let cancelled = false
    let text = 'Manual'
    let requestId = 0
    const finished: string[] = []
    const batch = runReferenceHotwordBatch({
      paths: ['one.md', 'two.txt', 'three.md'],
      profileId: 'mimo-profile',
      profileVersion: 1,
      createRequestId: () => `request-${++requestId}`,
      extract,
      shouldContinue: () => !cancelled,
      onStart: () => {},
      onFinish: (request) => { finished.push(request.referencePath) },
      onSuccess: (result) => { text = mergeReferenceHotwords(text, result.terms).text },
    })

    first.resolve(extractionResult(['First']))
    await Promise.resolve()
    expect(extract).toHaveBeenCalledTimes(2)
    cancelled = true
    inFlight.resolve(extractionResult(['Late result']))

    expect(await batch).toEqual({ completed: 1, failures: [], cancelled: true })
    expect(text).toBe('Manual\nFirst')
    expect(extract).toHaveBeenCalledTimes(2)
    expect(finished).toEqual(['one.md', 'two.txt'])
  })

  it('passes the merged terms into the ASR prompt_input.hotwords in the final draft', () => {
    const profile: WorkflowSummaryProfile = {
      id: 'summary-profile-1',
      version: 1,
      name: 'Summary',
      base_url: 'https://summary.example/v1',
      model: 'summary-model',
      max_input_tokens: 8000,
      max_output_tokens: 2000,
      auth_mode: 'bearer',
      provider_binding_sha256: 'binding',
    }
    const template: WorkflowSummaryTemplate = {
      id: 'summary-template-1',
      version: 1,
      name: 'General',
      prompt: 'Summarize',
    }
    const merged = mergeReferenceHotwords('ManualTerm', ['ExtractedTerm'])
    const draft = buildWorkflowDraft({
      baseName: 'meeting',
      sourcePath: 'meeting.wav',
      pipelineProfile: 'pyannote_qwen3_asr',
      devicePolicy: 'auto',
      recordingBackground: '',
      hotwordsText: merged.text,
      extraInstruction: '',
      profile,
      template,
      referenceMarkdownPaths: ['notes.md', 'other.md'],
      outputDir: 'outputs',
    })

    expect(draft.transcription.prompt_input.hotwords).toEqual(['ManualTerm', 'ExtractedTerm'])
    expect(draft.summary.reference_documents).toEqual([{ path: 'notes.md' }, { path: 'other.md' }])
  })

  it('passes all 500 hotwords into the ASR draft without truncating them', () => {
    const profile: WorkflowSummaryProfile = {
      id: 'summary-profile-1',
      version: 1,
      name: 'Summary',
      base_url: 'https://summary.example/v1',
      model: 'summary-model',
      max_input_tokens: 8000,
      max_output_tokens: 2000,
      auth_mode: 'bearer',
      provider_binding_sha256: 'binding',
    }
    const template: WorkflowSummaryTemplate = {
      id: 'summary-template-1',
      version: 1,
      name: 'General',
      prompt: 'Summarize',
    }
    const hotwords = Array.from({ length: 500 }, (_value, index) => 'manual-' + index)
    const draft = buildWorkflowDraft({
      baseName: 'meeting',
      sourcePath: 'meeting.wav',
      pipelineProfile: 'pyannote_qwen3_asr',
      devicePolicy: 'auto',
      recordingBackground: '',
      hotwordsText: hotwords.join('\n'),
      extraInstruction: '',
      profile,
      template,
      referenceMarkdownPaths: ['notes.md'],
      outputDir: 'outputs',
    })

    expect(draft.transcription.prompt_input.hotwords).toHaveLength(500)
    expect(draft.transcription.prompt_input.hotwords).toEqual(hotwords)
  })
})
