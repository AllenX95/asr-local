// Xiaomi MiMo's published OpenAI-compatible model configuration:
// https://mimo.mi.com/docs/en-US/tokenplan/integration/openclaw
export const MIMO_V26_CONTEXT_TOKENS = 1_048_576
export const MIMO_V26_MAX_OUTPUT_TOKENS = 131_072
// The runtime estimates prompt tokens without the provider's tokenizer.
export const MIMO_V26_ESTIMATION_RESERVE_TOKENS = 65_536
export const MIMO_V26_DEFAULT_OUTPUT_TOKENS = 16_384

export const DEFAULT_SUMMARY_INPUT_TOKENS = 8_000
export const DEFAULT_SUMMARY_OUTPUT_TOKENS = 2_000

const mimoModels = new Set(['mimo-v2.6-flash', 'mimo-v2.6-pro', 'mimo-v2.6-pro-ultraspeed'])
const mimoHosts = new Set(['api.xiaomimimo.com', 'token-plan-cn.xiaomimimo.com'])

export function isOfficialMimoV26(model: unknown, baseUrl: unknown): boolean {
  if (typeof model !== 'string' || !mimoModels.has(model.trim().toLowerCase()) || typeof baseUrl !== 'string') return false
  try {
    const url = new URL(baseUrl.trim())
    return url.protocol === 'https:' && mimoHosts.has(url.hostname.toLowerCase())
  } catch {
    return false
  }
}

function positiveInteger(value: unknown, fallback: number): number {
  const parsed = Number(value)
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : fallback
}

export function resolveSummaryTokenLimits(
  model: unknown,
  baseUrl: unknown,
  inputValue: unknown,
  outputValue: unknown,
): { max_input_tokens: number; max_output_tokens: number } {
  if (!isOfficialMimoV26(model, baseUrl)) {
    return {
      max_input_tokens: positiveInteger(inputValue, DEFAULT_SUMMARY_INPUT_TOKENS),
      max_output_tokens: positiveInteger(outputValue, DEFAULT_SUMMARY_OUTPUT_TOKENS),
    }
  }
  const maxOutput = Math.min(positiveInteger(outputValue, MIMO_V26_DEFAULT_OUTPUT_TOKENS), MIMO_V26_MAX_OUTPUT_TOKENS)
  const availableInput = MIMO_V26_CONTEXT_TOKENS - maxOutput - MIMO_V26_ESTIMATION_RESERVE_TOKENS
  return {
    max_input_tokens: Math.min(positiveInteger(inputValue, availableInput), availableInput),
    max_output_tokens: maxOutput,
  }
}

export function recommendedMimoTokenLimits(): { max_input_tokens: number; max_output_tokens: number } {
  return {
    max_input_tokens: MIMO_V26_CONTEXT_TOKENS - MIMO_V26_DEFAULT_OUTPUT_TOKENS - MIMO_V26_ESTIMATION_RESERVE_TOKENS,
    max_output_tokens: MIMO_V26_DEFAULT_OUTPUT_TOKENS,
  }
}
