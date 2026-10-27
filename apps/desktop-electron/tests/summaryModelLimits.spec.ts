import { describe, expect, it } from 'vitest'
import {
  isOfficialMimoV26,
  recommendedMimoTokenLimits,
  resolveSummaryTokenLimits,
} from '../electron/summaryModelLimits.js'

describe('MiMo V2.6 summary limits', () => {
  it.each(['mimo-v2.6-flash', 'mimo-v2.6-pro', 'mimo-v2.6-pro-ultraspeed'])(
    'uses the documented context and a reserved output budget for %s', (model) => {
      expect(resolveSummaryTokenLimits(model, 'https://api.xiaomimimo.com/v1', undefined, undefined))
        .toEqual({ max_input_tokens: 966_656, max_output_tokens: 16_384 })
      expect(resolveSummaryTokenLimits(model, 'https://api.xiaomimimo.com/v1', 1_024_000, 131_072))
        .toEqual({ max_input_tokens: 851_968, max_output_tokens: 131_072 })
    },
  )

  it('preserves smaller explicit limits and clamps invalid oversized output', () => {
    const base = 'https://token-plan-cn.xiaomimimo.com/v1'
    expect(resolveSummaryTokenLimits('mimo-v2.6-pro', base, 40_000, 4_000))
      .toEqual({ max_input_tokens: 40_000, max_output_tokens: 4_000 })
    expect(resolveSummaryTokenLimits('mimo-v2.6-pro', base, 1_024_000, 200_000))
      .toEqual({ max_input_tokens: 851_968, max_output_tokens: 131_072 })
    expect(recommendedMimoTokenLimits()).toEqual({ max_input_tokens: 966_656, max_output_tokens: 16_384 })
  })

  it('does not assume official limits for other models or third-party endpoints', () => {
    expect(isOfficialMimoV26('MiMo-V2.6-FLASH', 'https://api.xiaomimimo.com/v1')).toBe(true)
    expect(isOfficialMimoV26('mimo-v2.6-flash', 'https://proxy.example/v1')).toBe(false)
    expect(isOfficialMimoV26('mimo-v2.6-flash', 'http://api.xiaomimimo.com/v1')).toBe(false)
    expect(resolveSummaryTokenLimits('other-model', 'https://api.xiaomimimo.com/v1', undefined, undefined))
      .toEqual({ max_input_tokens: 8_000, max_output_tokens: 2_000 })
    expect(resolveSummaryTokenLimits('mimo-v2.6-flash', 'https://proxy.example/v1', 1_024_000, 131_072))
      .toEqual({ max_input_tokens: 1_024_000, max_output_tokens: 131_072 })
  })
})
