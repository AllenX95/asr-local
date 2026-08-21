import { mkdtemp, mkdir, writeFile } from 'node:fs/promises'
import { createHash } from 'node:crypto'
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

import { HostServices, MAX_REFERENCE_DOCUMENT_BYTES, freezeReferenceDocumentRequest, readReferenceDocumentSnapshot } from '../electron/hostServices.js'

describe('HostServices trusted workflow draft', () => {
  it('freezes a granted Markdown file as a UTF-8, size and SHA-256 snapshot', async () => {
    const root = await mkdtemp(path.join(tmpdir(), 'asr-local-reference-snapshot-'))
    const filePath = path.join(root, 'notes.md')
    await writeFile(filePath, '# 速记\n采用 Orion。', 'utf8')
    const snapshot = await readReferenceDocumentSnapshot(filePath)
    expect(snapshot).toMatchObject({ name: 'notes.md', content: '# 速记\n采用 Orion。', size_bytes: Buffer.byteLength('# 速记\n采用 Orion。', 'utf8') })
    expect(snapshot.sha256).toMatch(/^[a-f0-9]{64}$/u)
    expect(snapshot).not.toHaveProperty('path')
  })

  it('keeps a UTF-8 BOM consistent across snapshot content, size and digest', async () => {
    const root = await mkdtemp(path.join(tmpdir(), 'asr-local-reference-bom-'))
    const filePath = path.join(root, 'bom.md')
    const bytes = Buffer.concat([Buffer.from([0xef, 0xbb, 0xbf]), Buffer.from('# notes', 'utf8')])
    await writeFile(filePath, bytes)
    const snapshot = await readReferenceDocumentSnapshot(filePath)
    expect(snapshot.content.charCodeAt(0)).toBe(0xfeff)
    expect(snapshot.size_bytes).toBe(bytes.byteLength)
    expect(snapshot.sha256).toBe(createHash('sha256').update(bytes).digest('hex'))
  })

  it('freezes only an authorized path and replaces renderer metadata while preserving null', async () => {
    const root = await mkdtemp(path.join(tmpdir(), 'asr-local-reference-freeze-'))
    const filePath = path.join(root, 'real.md')
    await writeFile(filePath, '# real content', 'utf8')
    const authorizePath = vi.fn((requestedPath: string) => {
      if (requestedPath !== 'selected/real.md') throw new Error('not authorized')
      return filePath
    })
    const snapshot = await freezeReferenceDocumentRequest({
      path: 'selected/real.md',
      content: 'forged',
      size_bytes: 6,
      sha256: '0'.repeat(64),
    }, authorizePath)
    expect(authorizePath).toHaveBeenCalledWith('selected/real.md')
    expect(snapshot).toMatchObject({ name: 'real.md', content: '# real content' })
    expect(snapshot).not.toMatchObject({ content: 'forged', sha256: '0'.repeat(64) })
    await expect(freezeReferenceDocumentRequest({ path: 'outside.md' }, authorizePath)).rejects.toThrow('not authorized')
    expect(await freezeReferenceDocumentRequest(null, authorizePath)).toBeNull()
  })

  it('rejects unsupported extension, empty content, invalid UTF-8 and oversized files', async () => {
    const root = await mkdtemp(path.join(tmpdir(), 'asr-local-reference-invalid-'))
    const txtPath = path.join(root, 'notes.txt')
    await writeFile(txtPath, 'notes', 'utf8')
    await expect(readReferenceDocumentSnapshot(txtPath)).rejects.toThrow('only .md and .markdown')
    const emptyPath = path.join(root, 'empty.md')
    await writeFile(emptyPath, '   ', 'utf8')
    await expect(readReferenceDocumentSnapshot(emptyPath)).rejects.toThrow('empty')
    const invalidPath = path.join(root, 'invalid.md')
    await writeFile(invalidPath, Buffer.from([0xc3, 0x28]))
    await expect(readReferenceDocumentSnapshot(invalidPath)).rejects.toThrow('valid UTF-8')
    const largePath = path.join(root, 'large.md')
    await writeFile(largePath, Buffer.alloc(MAX_REFERENCE_DOCUMENT_BYTES + 1, 0x61))
    await expect(readReferenceDocumentSnapshot(largePath)).rejects.toThrow('exceeds')
  })

  it('does not accept renderer-supplied reference metadata as a trusted snapshot', async () => {
    const root = await mkdtemp(path.join(tmpdir(), 'asr-local-reference-forged-'))
    const configDir = path.join(root, 'config')
    await mkdir(configDir)
    await writeFile(path.join(configDir, 'summary_profiles.toml'), `[[profiles]]\nname = "Profile"\nbase_url = "https://example.test/v1"\nmodel = "model"\n`)
    await writeFile(path.join(configDir, 'summary_templates.toml'), `[[templates]]\nname = "Template"\nprompt = "总结"\n`)
    const host = new HostServices(root, configDir, path.join(root, 'outputs'))
    const catalogs = await host.catalogs()
    await expect(host.trustedWorkflowDraft({
      summary: {
        profile_id: catalogs.summary_profiles[0].id,
        profile_version: catalogs.summary_profiles[0].version,
        template: { id: catalogs.summary_templates[0].id, version: catalogs.summary_templates[0].version },
        reference_document: { path: 'notes.md', content: 'forged', size_bytes: 6, sha256: '0'.repeat(64) },
      },
    })).rejects.toThrow('frozen by the desktop host')
  })

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

    expect(profile).toMatchObject({ max_input_tokens: 8000, max_output_tokens: 2000 })
    await expect(host.trustedWorkflowDraft({
      summary: {
        profile_id: profile.id,
        profile_version: profile.version,
        template: { id: template.id, version: template.version },
      },
    })).resolves.toMatchObject({
      summary: { profile_id: profile.id, profile_version: profile.version },
    })
    await expect(host.trustedSummaryRecipe({
      profile_id: profile.id,
      profile_version: profile.version,
      template: { id: template.id, version: template.version },
    })).resolves.toMatchObject({
      profile_id: profile.id,
      model: 'deepseek-v4-flash',
      template: { id: template.id, prompt_snapshot: '总结' },
    })
  })

  it('exposes profile token limits and locks them into the trusted workflow snapshot', async () => {
    const root = await mkdtemp(path.join(tmpdir(), 'asr-local-profile-token-limits-'))
    const configDir = path.join(root, 'config')
    await mkdir(configDir)
    await writeFile(path.join(configDir, 'summary_profiles.toml'), `[[profiles]]\nname = "Token Profile"\nbase_url = "https://example.test/v1"\nmodel = "model"\nmax_input_tokens = 12000\nmax_output_tokens = 4096\n`)
    await writeFile(path.join(configDir, 'summary_templates.toml'), `[[templates]]\nname = "Template"\nprompt = "总结"\n`)
    const host = new HostServices(root, configDir, path.join(root, 'outputs'))
    const catalogs = await host.catalogs()
    const profile = catalogs.summary_profiles[0]
    const template = catalogs.summary_templates[0]

    expect(profile).toMatchObject({ max_input_tokens: 12000, max_output_tokens: 4096 })
    await expect(host.trustedWorkflowDraft({
      summary: {
        profile_id: profile.id,
        profile_version: profile.version,
        input_token_budget: 1,
        max_output_tokens: 1,
        template: { id: template.id, version: template.version },
      },
    })).resolves.toMatchObject({
      summary: { input_token_budget: 12000, max_output_tokens: 4096 },
    })
    const saved = await host.saveProfile('summary', {
      name: 'Token Profile',
      base_url: 'https://example.test/v1',
      model: 'model',
      api_key: '',
      max_input_tokens: 16000,
      max_output_tokens: 8192,
    })
    expect(saved.profiles[0]).toMatchObject({ max_input_tokens: 16000, max_output_tokens: 8192 })
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
