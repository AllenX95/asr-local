import type { PipelineProfile, WorkflowSummaryProfile, WorkflowSummaryTemplate } from '../../ipc/workerTypes'
import type { WorkflowDraft } from '../../workflows/types'
import { parseHotwordInput } from './referenceHotwords'

export interface WorkflowDraftInput {
  baseName: string
  sourcePath: string
  pipelineProfile: Exclude<PipelineProfile, 'cloud_asr'>
  devicePolicy: 'auto' | 'cpu' | 'cuda'
  recordingBackground: string
  hotwordsText: string
  extraInstruction: string
  profile: WorkflowSummaryProfile
  template: WorkflowSummaryTemplate
  referenceMarkdownPaths: string[]
  outputDir: string
}

export function buildWorkflowDraft(input: WorkflowDraftInput): WorkflowDraft {
  const hotwords = parseHotwordInput(input.hotwordsText)
  const authMode = input.profile.auth_mode
  return {
    draft_version: 2,
    display_name: input.baseName.trim() || 'meeting',
    source: { path: input.sourcePath.trim() },
    transcription: {
      pipeline_profile: input.pipelineProfile,
      pipeline_profile_version: 1,
      device_policy: input.devicePolicy,
      audio: { channel_strategy: 'mixdown' },
      language: { mode: 'auto', value: null },
      prompt_input: {
        recording_background: input.recordingBackground,
        hotwords,
        extra_instruction: input.extraInstruction,
      },
      postprocess: { replacements: [], keep_fillers: true, auto_punctuation: true },
      cloud_profile: null,
    },
    summary: {
      profile_id: input.profile.id,
      profile_version: input.profile.version,
      base_url: input.profile.base_url,
      auth_mode: authMode,
      model: input.profile.model,
      model_source: 'profile_default',
      credential_ref: authMode === 'bearer' ? 'summary:' + input.profile.id : null,
      provider_binding_sha256: input.profile.provider_binding_sha256,
      template: {
        id: input.template.id,
        version: input.template.version,
        name: input.template.name,
        prompt_snapshot: input.template.prompt,
      },
      context_strategy: 'auto',
      input_token_budget: input.profile.max_input_tokens,
      max_output_tokens: input.profile.max_output_tokens,
      reference_documents: input.referenceMarkdownPaths.map((path) => path.trim()).filter(Boolean).map((path) => ({ path })),
    },
    output: {
      directory: input.outputDir.trim() || 'outputs',
      base_name: input.baseName.trim() || 'meeting',
      collision_policy: 'unique_suffix',
    },
  }
}
