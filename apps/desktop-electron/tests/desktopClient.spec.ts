import { reactive } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '../src/ipc/desktopClient'

describe('desktop profile IPC payloads', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('converts a reactive summary profile to a structured-cloneable payload', async () => {
    const calls: Array<{ command: string; args: Record<string, unknown> | undefined }> = []
    vi.stubGlobal('window', {
      asrLocal: {
        invoke: async (command: string, args?: Record<string, unknown>) => {
          structuredClone(args)
          calls.push({ command, args })
          return { profiles: [], last_profile: null }
        },
      },
    })

    const profile = reactive({
      name: 'Token Profile',
      base_url: 'https://example.test/v1',
      model: 'model',
      api_key: '',
      max_input_tokens: 12000,
      max_output_tokens: 4096,
    })

    await expect(api.saveSummaryProfile(profile)).resolves.toEqual({ profiles: [], last_profile: null })
    expect(calls).toHaveLength(1)
    expect(calls[0].command).toBe('save_summary_profile')
    expect(calls[0].args?.profile).toEqual({ ...profile })
    expect(calls[0].args?.profile).not.toBe(profile)
  })
})
