import { mkdtemp, mkdir, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { describe, expect, it, vi } from 'vitest'

vi.mock('electron', () => ({
  safeStorage: {
    isEncryptionAvailable: () => true,
    encryptString: (value: string) => Buffer.from(value),
    decryptString: (value: Buffer) => value.toString('utf8'),
  },
}))

import { HostServices } from '../electron/hostServices.js'

describe('HostServices trusted workflow draft', () => {
  it('accepts the normalized catalog identity of a migrated legacy summary profile', async () => {
    const root = await mkdtemp(path.join(tmpdir(), 'asr-local-profile-repro-'))
    const configDir = path.join(root, 'config')
    await mkdir(configDir)
    await writeFile(path.join(configDir, 'summary_profiles.toml'), `[[profiles]]\nname = "DS-V4-Flash"\nbase_url = "https://api.deepseek.com"\nmodel = "deepseek-v4-flash"\n`)
    await writeFile(path.join(configDir, 'summary_templates.toml'), `[[templates]]\nname = "通用模板"\nprompt = "总结"\n`)
    const host = new HostServices(root, configDir, path.join(root, 'outputs'))
    const catalogs = await host.catalogs()
    const profile = catalogs.summary_profiles[0]
    const template = catalogs.summary_templates[0]

    await expect(host.trustedWorkflowDraft({
      summary: {
        profile_id: profile.id,
        profile_version: profile.version,
        template: { id: template.id, version: template.version },
      },
    })).resolves.toMatchObject({
      summary: { profile_id: profile.id, profile_version: profile.version },
    })
  })

  it('resolves a migrated legacy credential by the normalized catalog identity', async () => {
    const root = await mkdtemp(path.join(tmpdir(), 'asr-local-credential-repro-'))
    const configDir = path.join(root, 'config')
    await mkdir(configDir)
    await writeFile(path.join(configDir, 'summary_profiles.toml'), `[[profiles]]\nname = "Legacy"\nbase_url = "https://example.test/v1"\nmodel = "model"\nencrypted_api_key = "safe-storage:v1:c2VjcmV0"\n`)
    const host = new HostServices(root, configDir, path.join(root, 'outputs'))
    const profile = (await host.loadProfiles('summary')).profiles[0]

    await expect(host.secretForProfile('summary', profile.id, profile.version)).resolves.toBe('secret')
  })

  it('classifies the new transcript and summary folders and skips workflow staging files', async () => {
    const root = await mkdtemp(path.join(tmpdir(), 'asr-local-history-layout-'))
    const outputs = path.join(root, 'outputs')
    await mkdir(path.join(outputs, 'transcripts'), { recursive: true })
    await mkdir(path.join(outputs, 'summary'), { recursive: true })
    await mkdir(path.join(outputs, '.staging', 'wf_ignored'), { recursive: true })
    await writeFile(path.join(outputs, 'transcripts', 'meeting--wf_1.md'), '# transcript')
    await writeFile(path.join(outputs, 'summary', 'meeting--wf_1.md'), '# summary')
    await writeFile(path.join(outputs, '.staging', 'wf_ignored', 'temporary.md'), '# temporary')

    const host = new HostServices(root, path.join(root, 'config'), outputs)
    const history = await host.history(100)

    expect(history.filter((item) => item.kind === 'transcript')).toHaveLength(1)
    expect(history.filter((item) => item.kind === 'summary')).toHaveLength(1)
    expect(history.some((item) => item.title === 'temporary.md')).toBe(false)
  })

  it('keeps old output history visible after the packaged output root changes', async () => {
    const root = await mkdtemp(path.join(tmpdir(), 'asr-local-history-legacy-'))
    const outputs = path.join(root, 'current')
    const legacyOutputs = path.join(root, 'legacy')
    await mkdir(path.join(outputs, 'transcripts'), { recursive: true })
    await mkdir(path.join(legacyOutputs, 'summary'), { recursive: true })
    await mkdir(path.join(legacyOutputs, '.jobs', 'wf_ignored'), { recursive: true })
    await writeFile(path.join(outputs, 'transcripts', 'new--wf_1.md'), '# new')
    await writeFile(path.join(legacyOutputs, 'summary', 'old--wf_2.md'), '# old')
    await writeFile(path.join(legacyOutputs, '.jobs', 'wf_ignored', 'job.md'), '# ignored')

    const host = new HostServices(root, path.join(root, 'config'), outputs, undefined, undefined, legacyOutputs)
    const history = await host.history(100)

    expect(history.some((item) => item.title === 'new--wf_1.md')).toBe(true)
    expect(history.some((item) => item.title === 'old--wf_2.md')).toBe(true)
    expect(history.some((item) => item.title === 'job.md')).toBe(false)
  })
})
