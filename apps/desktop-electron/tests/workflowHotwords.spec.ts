import { describe, expect, it } from 'vitest'
import type { WorkflowSummaryProfile, WorkflowSummaryTemplate } from '../src/ipc/workerTypes'
import {
  isSameReferenceHotwordRequest,
  mergeReferenceHotwords,
  parseHotwordInput,
  formatReferenceHotwordError,
  type ReferenceHotwordRequestIdentity,
} from '../src/features/workflow/referenceHotwords'
import { buildWorkflowDraft } from '../src/features/workflow/workflowDraft'

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
