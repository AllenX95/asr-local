import { mkdtemp, mkdir, readFile, writeFile } from 'node:fs/promises'
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
  it('migrates catalog v2 to v4 with id priority, name fallback, custom preservation, backup, and idempotence', async () => {
    const root = await mkdtemp(path.join(tmpdir(), 'asr-local-template-migration-'))
    const defaultsDir = path.join(root, 'config')
    const userConfigDir = path.join(root, 'user-config')
    await mkdir(defaultsDir)
    await mkdir(userConfigDir)
    await writeFile(path.join(defaultsDir, 'summary_templates.toml'), [
      'catalog_version = 4',
      '',
      '[[templates]]',
      'id = "summary-template-general"',
      'version = 4',
      'name = "通用模板"',
      'prompt = "bundled general"',
      '',
      '[[templates]]',
      'id = "summary-template-customer"',
      'version = 4',
      'name = "客户访谈"',
      'prompt = "bundled customer"',
      '',
    ].join('\n'))
    await writeFile(path.join(userConfigDir, 'summary_templates.toml'), [
      'catalog_version = 2',
      'owner = "keep"',
      '',
      '[[templates]]',
      'id = "summary-template-legacy"',
      'version = 2',
      'name = "通用模板"',
      'prompt = "legacy general"',
      '',
      '[[templates]]',
      'id = "summary-template-customer"',
      'version = 1',
      'name = "旧名称"',
      'prompt = "legacy customer"',
      '',
      '[[templates]]',
      'id = "custom-template"',
      'version = 7',
      'name = "自定义模板"',
      'prompt = "custom prompt"',
      '',
    ].join('\n'))

    const host = new HostServices(root, userConfigDir, path.join(root, 'outputs'))
    await host.initialize()

    await expect(host.loadTemplates()).resolves.toEqual([
      { id: 'summary-template-general', version: 4, name: '通用模板', prompt: 'bundled general' },
      { id: 'summary-template-customer', version: 4, name: '客户访谈', prompt: 'bundled customer' },
      { id: 'custom-template', version: 7, name: '自定义模板', prompt: 'custom prompt' },
    ])
    const migratedPath = path.join(userConfigDir, 'summary_templates.toml')
    const migratedText = await readFile(migratedPath, 'utf8')
    expect(migratedText).toContain('catalog_version = 4')
    expect(migratedText).toContain('owner = "keep"')
    expect(migratedText).not.toContain('template_catalog_version')
    const backupText = await readFile(`${migratedPath}.pre-catalog-v4.bak`, 'utf8')
    expect(backupText).toContain('catalog_version = 2')

    await host.initialize()
    await expect(readFile(migratedPath, 'utf8')).resolves.toBe(migratedText)
    await expect(readFile(`${migratedPath}.pre-catalog-v4.bak`, 'utf8')).resolves.toBe(backupText)
  })

  it('does not downgrade a newer user catalog', async () => {
    const root = await mkdtemp(path.join(tmpdir(), 'asr-local-template-no-downgrade-'))
    const defaultsDir = path.join(root, 'config')
    const userConfigDir = path.join(root, 'user-config')
    await mkdir(defaultsDir)
    await mkdir(userConfigDir)
    await writeFile(path.join(defaultsDir, 'summary_templates.toml'), 'catalog_version = 4\n[[templates]]\nid = "builtin"\nversion = 4\nname = "内置"\nprompt = "bundled"\n')
    await writeFile(path.join(userConfigDir, 'summary_templates.toml'), 'catalog_version = 5\n[[templates]]\nid = "builtin"\nversion = 5\nname = "内置"\nprompt = "user newer"\n')

    const host = new HostServices(root, userConfigDir, path.join(root, 'outputs'))
    await host.initialize()

    await expect(host.loadTemplates()).resolves.toEqual([{ id: 'builtin', version: 5, name: '内置', prompt: 'user newer' }])
    await expect(readFile(path.join(userConfigDir, 'summary_templates.toml'), 'utf8')).resolves.toContain('catalog_version = 5')
  })

  it('generates non-colliding ids for pure Chinese template names', async () => {
    const root = await mkdtemp(path.join(tmpdir(), 'asr-local-template-chinese-'))
    const configDir = path.join(root, 'config')
    await mkdir(configDir)
    await writeFile(path.join(configDir, 'summary_templates.toml'), [
      'catalog_version = 4', '',
      '[[templates]]', 'name = "团队访谈"', 'prompt = "一"', '',
      '[[templates]]', 'name = "客户访谈"', 'prompt = "二"', '',
      '[[templates]]', 'name = "通用模板"', 'prompt = "三"', '',
      '[[templates]]', 'name = "首次交流模板"', 'prompt = "四"', '',
    ].join('\n'))

    const templates = await new HostServices(root, configDir, path.join(root, 'outputs')).loadTemplates()
    expect(new Set(templates.map((template) => template.id)).size).toBe(4)
  })

  it('copies legacy config before bundled defaults and then upgrades the copied catalog', async () => {
    const root = await mkdtemp(path.join(tmpdir(), 'asr-local-template-legacy-order-'))
    const defaultsDir = path.join(root, 'config')
    const legacyDir = path.join(root, 'legacy-config')
    const userConfigDir = path.join(root, 'user-config')
    await mkdir(defaultsDir)
    await mkdir(legacyDir)
    await mkdir(userConfigDir)
    await writeFile(path.join(defaultsDir, 'summary_templates.toml'), 'catalog_version = 4\n[[templates]]\nid = "builtin"\nversion = 4\nname = "通用模板"\nprompt = "bundled"\n')
    await writeFile(path.join(legacyDir, 'summary_templates.toml'), 'template_catalog_version = 2\n[[templates]]\nid = "old"\nversion = 2\nname = "通用模板"\nprompt = "legacy"\n')

    const host = new HostServices(root, userConfigDir, path.join(root, 'outputs'), legacyDir)
    await host.initialize()

    await expect(host.loadTemplates()).resolves.toEqual([{ id: 'builtin', version: 4, name: '通用模板', prompt: 'bundled' }])
  })

  it('preserves catalog metadata when saving and deleting templates', async () => {
    const root = await mkdtemp(path.join(tmpdir(), 'asr-local-template-metadata-'))
    const configDir = path.join(root, 'config')
    await mkdir(configDir)
    const filePath = path.join(configDir, 'summary_templates.toml')
    await writeFile(filePath, 'catalog_version = 4\nowner = "keep"\n[[templates]]\nid = "one"\nversion = 4\nname = "旧模板"\nprompt = "旧"\n')
    const host = new HostServices(root, configDir, path.join(root, 'outputs'))

    await host.saveTemplate('新模板', '新 prompt')
    const saved = await readFile(filePath, 'utf8')
    expect(saved).toContain('catalog_version = 4')
    expect(saved).toContain('owner = "keep"')
    expect(saved).not.toContain('template_catalog_version')

    await host.deleteTemplate('旧模板')
    const deleted = await readFile(filePath, 'utf8')
    expect(deleted).toContain('catalog_version = 4')
    expect(deleted).toContain('owner = "keep"')
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
