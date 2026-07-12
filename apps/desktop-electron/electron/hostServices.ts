import { safeStorage } from 'electron'
import { existsSync } from 'node:fs'
import { copyFile, mkdir, readFile, readdir, rename, stat, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { createHash } from 'node:crypto'
import { spawn } from 'node:child_process'
import * as TOML from '@iarna/toml'

type JsonObject = Record<string, any>
const defaultModel = (modelPath: string, required: boolean, description: string) => ({ path: modelPath, required, description })

async function readToml(filePath: string, fallback: JsonObject): Promise<JsonObject> {
  if (!existsSync(filePath)) return structuredClone(fallback)
  return TOML.parse(await readFile(filePath, 'utf8')) as JsonObject
}

async function writeToml(filePath: string, value: JsonObject): Promise<void> {
  await mkdir(path.dirname(filePath), { recursive: true })
  const temp = `${filePath}.tmp-${process.pid}`
  await writeFile(temp, TOML.stringify(value), 'utf8')
  await rename(temp, filePath)
}

function stableId(prefix: string, name: string): string {
  const slug = name.trim().toLowerCase().replace(/[^a-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '') || 'legacy'
  return `${prefix}${slug}`
}

function providerBindingDigest(profile: JsonObject, authMode: string): string {
  const canonical = JSON.stringify({
    auth_mode: authMode,
    base_url: String(profile.base_url ?? '').trim(),
    model: String(profile.model ?? '').trim(),
    profile_id: String(profile.id ?? ''),
    profile_version: Math.max(Number(profile.version ?? 1), 1),
  })
  return createHash('sha256').update(canonical).digest('hex')
}

function decryptSecret(stored: unknown): string {
  if (typeof stored !== 'string' || !stored) return ''
  if (!stored.startsWith('safe-storage:v1:')) return ''
  try { return safeStorage.decryptString(Buffer.from(stored.slice('safe-storage:v1:'.length), 'base64')) } catch { return '' }
}

function encryptSecret(secret: string): string {
  if (!secret.trim()) return ''
  if (!safeStorage.isEncryptionAvailable()) throw new Error('OS secure storage is unavailable')
  return `safe-storage:v1:${safeStorage.encryptString(secret.trim()).toString('base64')}`
}

function normalizeProfile(raw: JsonObject, prefix: string): JsonObject {
  const name = String(raw.name ?? '').trim()
  return {
    id: String(raw.id ?? '').trim() || stableId(prefix, name),
    version: Math.max(Number(raw.version ?? 1), 1),
    name,
    base_url: String(raw.base_url ?? '').trim(),
    model: String(raw.model ?? '').trim(),
    api_key: '',
    has_api_key: Boolean(raw.encrypted_api_key),
  }
}

function normalizedProfileRecord(raw: JsonObject, prefix: string): JsonObject {
  return { ...raw, ...normalizeProfile(raw, prefix) }
}

export class HostServices {
  private readonly configDir: string
  private readonly outputsRoot: string
  constructor(private readonly projectRoot: string, configDir?: string, outputsRoot?: string, private readonly legacyConfigDir?: string, private readonly pythonExecutable?: string, private readonly legacyOutputsRoot?: string) {
    this.configDir = configDir ?? path.join(projectRoot, 'config')
    this.outputsRoot = outputsRoot ?? path.join(projectRoot, 'outputs')
  }

  async initialize(): Promise<void> {
    await mkdir(this.configDir, { recursive: true })
    await mkdir(this.outputsRoot, { recursive: true })
    const defaultsDir = path.join(this.projectRoot, 'config')
    for (const name of ['models.toml', 'summary_templates.toml']) {
      const target = path.join(this.configDir, name)
      const source = path.join(defaultsDir, name)
      if (!existsSync(target) && existsSync(source) && target !== source) await copyFile(source, target)
    }
    if (this.legacyConfigDir && path.resolve(this.legacyConfigDir) !== path.resolve(this.configDir)) {
      for (const name of ['models.toml', 'summary_templates.toml', 'asr_profiles.toml', 'summary_profiles.toml']) {
        const source = path.join(this.legacyConfigDir, name)
        const target = path.join(this.configDir, name)
        if (!existsSync(target) && existsSync(source)) await copyFile(source, target)
      }
      await this.migrateLegacyModelPaths()
    }
    await this.migrateLegacySecrets('asr_profiles.toml')
    await this.migrateLegacySecrets('summary_profiles.toml')
  }

  private async migrateLegacyModelPaths(): Promise<void> {
    if (!this.legacyConfigDir) return
    const filePath = path.join(this.configDir, 'models.toml')
    if (!existsSync(filePath)) return
    const raw = await readToml(filePath, {})
    const legacyRoot = path.dirname(path.resolve(this.legacyConfigDir))
    let changed = false
    for (const key of ['qwen3_asr_1_7b', 'pyannote_speaker_diarization']) {
      const configured = String(raw[key]?.path ?? '').trim()
      if (!configured || path.isAbsolute(configured)) continue
      const absolute = path.resolve(legacyRoot, configured)
      if (!existsSync(absolute)) continue
      raw[key].path = absolute.replace(/\\/g, '/')
      changed = true
    }
    if (!changed) return
    const backup = `${filePath}.pre-electron.bak`
    if (!existsSync(backup)) await copyFile(filePath, backup)
    await writeToml(filePath, raw)
  }

  private async migrateLegacySecrets(name: string): Promise<void> {
    const filePath = path.join(this.configDir, name)
    if (!existsSync(filePath) || !this.pythonExecutable || process.platform !== 'win32') return
    const raw = await readToml(filePath, { profiles: [] })
    let changed = false
    for (const profile of raw.profiles ?? []) {
      const stored = String(profile.encrypted_api_key ?? '')
      if (!stored || stored.startsWith('safe-storage:v1:')) continue
      try {
        const plaintext = await this.decryptLegacyDpapi(stored)
        profile.encrypted_api_key = encryptSecret(plaintext)
        changed = true
      } catch (error) {
        console.warn(`Credential in ${name} requires manual re-entry:`, error instanceof Error ? error.message : String(error))
      }
    }
    if (!changed) return
    const backup = `${filePath}.pre-electron.bak`
    if (!existsSync(backup)) await copyFile(filePath, backup)
    await writeToml(filePath, raw)
  }

  private decryptLegacyDpapi(ciphertext: string): Promise<string> {
    const workerDir = path.join(this.projectRoot, 'apps', 'worker-python')
    return new Promise((resolve, reject) => {
      const child = spawn(this.pythonExecutable!, ['-X', 'utf8', '-m', 'app.diagnostics.dpapi'], { cwd: workerDir, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] })
      const stdout: Buffer[] = []; const stderr: Buffer[] = []
      child.stdout.on('data', (chunk: Buffer) => stdout.push(chunk)); child.stderr.on('data', (chunk: Buffer) => stderr.push(chunk))
      child.once('error', reject)
      child.once('exit', (code) => {
        if (code !== 0) { reject(new Error(`Legacy credential migration failed: ${Buffer.concat(stderr).toString('utf8').trim()}`)); return }
        try { resolve(Buffer.from(Buffer.concat(stdout).toString('ascii'), 'base64').toString('utf8')) } catch (error) { reject(error) }
      })
      child.stdin.end(ciphertext, 'ascii')
    })
  }

  async loadModelsConfig(): Promise<JsonObject> {
    const configPath = path.join(this.configDir, 'models.toml')
    const defaults = {
      model_root: 'models',
      qwen3_asr_1_7b: defaultModel('models/Qwen/Qwen3-ASR-1.7B', true, 'Manual local path for the downloaded Qwen3-ASR-1.7B model directory.'),
      pyannote_speaker_diarization: defaultModel('models/pyannote/speaker-diarization-community-1', true, 'Manual local path for the downloaded pyannote speaker diarization model directory.'),
    }
    const loaded = await readToml(configPath, defaults)
    const raw = {
      model_root: String(loaded.model_root ?? defaults.model_root),
      qwen3_asr_1_7b: { ...defaults.qwen3_asr_1_7b, ...(loaded.qwen3_asr_1_7b ?? {}) },
      pyannote_speaker_diarization: { ...defaults.pyannote_speaker_diarization, ...(loaded.pyannote_speaker_diarization ?? {}) },
    }
    const resolveModel = (entry: JsonObject) => path.isAbsolute(entry.path) ? entry.path : path.join(this.projectRoot, entry.path)
    const qwenPath = resolveModel(raw.qwen3_asr_1_7b)
    const pyannotePath = resolveModel(raw.pyannote_speaker_diarization)
    return { project_root: this.projectRoot, config_path: configPath, raw, qwen_path: qwenPath, pyannote_path: pyannotePath, qwen_exists: existsSync(qwenPath), pyannote_exists: existsSync(pyannotePath) }
  }

  async saveModelPaths(args: JsonObject): Promise<JsonObject> {
    const current = (await this.loadModelsConfig()).raw
    current.model_root = String(args.modelRoot ?? current.model_root).trim().replace(/\\/g, '/')
    current.qwen3_asr_1_7b.path = String(args.qwenPath).trim().replace(/\\/g, '/')
    current.pyannote_speaker_diarization.path = String(args.pyannotePath).trim().replace(/\\/g, '/')
    await writeToml(path.join(this.configDir, 'models.toml'), current)
    return this.loadModelsConfig()
  }

  async loadProfiles(kind: 'asr' | 'summary'): Promise<JsonObject> {
    const filePath = path.join(this.configDir, kind === 'asr' ? 'asr_profiles.toml' : 'summary_profiles.toml')
    const raw = await readToml(filePath, { profiles: [], last_profile: null })
    const prefix = kind === 'asr' ? 'cloud-asr-profile-' : 'summary-profile-'
    return { profiles: (raw.profiles ?? []).map((item: JsonObject) => normalizeProfile(item, prefix)).sort((a: JsonObject, b: JsonObject) => a.name.localeCompare(b.name)), last_profile: raw.last_profile || null }
  }

  async saveProfile(kind: 'asr' | 'summary', input: JsonObject): Promise<JsonObject> {
    const name = String(input.name ?? '').trim(); if (!name) throw new Error('profile name is empty')
    const filePath = path.join(this.configDir, kind === 'asr' ? 'asr_profiles.toml' : 'summary_profiles.toml')
    const raw = await readToml(filePath, { profiles: [], last_profile: null })
    const profiles = (raw.profiles ?? []) as JsonObject[]
    const index = profiles.findIndex((item) => String(item.name ?? '').toLowerCase() === name.toLowerCase())
    const previous = index >= 0 ? profiles[index] : null
    const prefix = kind === 'asr' ? 'cloud-asr-profile-' : 'summary-profile-'
    const secret = String(input.api_key ?? '').trim()
    const storedSecret = secret ? encryptSecret(secret) : String(previous?.encrypted_api_key ?? '')
    const next = { id: String(input.id ?? previous?.id ?? '').trim() || stableId(prefix, name), version: previous ? Math.max(Number(previous.version ?? 1), 1) + 1 : Math.max(Number(input.version ?? 1), 1), name, base_url: String(input.base_url ?? '').trim(), model: String(input.model ?? '').trim(), encrypted_api_key: storedSecret }
    if (index >= 0) profiles[index] = next; else profiles.push(next)
    profiles.sort((a, b) => String(a.name).localeCompare(String(b.name)))
    await writeToml(filePath, { last_profile: name, profiles })
    return this.loadProfiles(kind)
  }

  async deleteProfile(kind: 'asr' | 'summary', nameInput: string): Promise<JsonObject> {
    const filePath = path.join(this.configDir, kind === 'asr' ? 'asr_profiles.toml' : 'summary_profiles.toml')
    const raw = await readToml(filePath, { profiles: [], last_profile: null })
    const name = nameInput.trim().toLowerCase()
    raw.profiles = (raw.profiles ?? []).filter((item: JsonObject) => String(item.name ?? '').toLowerCase() !== name)
    if (String(raw.last_profile ?? '').toLowerCase() === name) raw.last_profile = raw.profiles[0]?.name ?? null
    await writeToml(filePath, raw)
    return this.loadProfiles(kind)
  }

  async secretForProfile(kind: 'asr' | 'summary', profileId: string, version: number): Promise<string> {
    const filePath = path.join(this.configDir, kind === 'asr' ? 'asr_profiles.toml' : 'summary_profiles.toml')
    const raw = await readToml(filePath, { profiles: [] })
    const prefix = kind === 'asr' ? 'cloud-asr-profile-' : 'summary-profile-'
    const profile = (raw.profiles ?? []).map((item: JsonObject) => normalizedProfileRecord(item, prefix)).find((item: JsonObject) => item.id === profileId && item.version === version)
    if (!profile) throw new Error('CREDENTIAL_REJECTED: profile snapshot not found')
    const secret = decryptSecret(profile.encrypted_api_key)
    if (!secret) throw new Error('CREDENTIAL_REJECTED: profile credential is unavailable or requires migration')
    return secret
  }

  async credentialGrant(requestData: JsonObject): Promise<{ secret: string; expectedBinding: string }> {
    const purpose = String(requestData.purpose ?? '')
    const kind = purpose === 'summary_api' ? 'summary' : purpose === 'cloud_asr' ? 'asr' : null
    if (!kind) throw new Error(`CREDENTIAL_REJECTED: unsupported purpose ${purpose}`)
    const filePath = path.join(this.configDir, kind === 'asr' ? 'asr_profiles.toml' : 'summary_profiles.toml')
    const raw = await readToml(filePath, { profiles: [] })
    const prefix = kind === 'asr' ? 'cloud-asr-profile-' : 'summary-profile-'
    const profile = (raw.profiles ?? []).map((item: JsonObject) => normalizedProfileRecord(item, prefix)).find((item: JsonObject) => item.id === String(requestData.profile_id) && item.version === Number(requestData.profile_version))
    if (!profile) throw new Error('CREDENTIAL_REJECTED: profile snapshot not found')
    const expectedBinding = providerBindingDigest(profile, 'bearer')
    if (requestData.provider_binding_sha256 !== expectedBinding) throw new Error('CREDENTIAL_REJECTED: provider binding does not match the submitted profile snapshot')
    const secret = decryptSecret(profile.encrypted_api_key)
    if (!secret) throw new Error('CREDENTIAL_REJECTED: profile credential is unavailable or requires migration')
    return { secret, expectedBinding }
  }

  async loadTemplates(): Promise<JsonObject[]> {
    const raw = await readToml(path.join(this.configDir, 'summary_templates.toml'), { templates: [] })
    return (raw.templates ?? []).map((item: JsonObject) => ({ id: String(item.id ?? '').trim() || stableId('summary-template-', String(item.name ?? '')), version: Math.max(Number(item.version ?? 1), 1), name: String(item.name ?? ''), prompt: String(item.prompt ?? '') }))
  }

  async saveTemplate(nameInput: string, promptInput: string): Promise<JsonObject[]> {
    const name = nameInput.trim(); if (!name) throw new Error('template name is empty')
    const filePath = path.join(this.configDir, 'summary_templates.toml'); const raw = await readToml(filePath, { templates: [] }); const templates = raw.templates as JsonObject[]
    const index = templates.findIndex((item) => String(item.name).toLowerCase() === name.toLowerCase()); const previous = index >= 0 ? templates[index] : null
    const next = { id: previous?.id || stableId('summary-template-', name), version: previous ? Math.max(Number(previous.version ?? 1), 1) + 1 : 1, name, prompt: promptInput.trim() }
    if (index >= 0) templates[index] = next; else templates.push(next); templates.sort((a, b) => String(a.name).localeCompare(String(b.name)))
    await writeToml(filePath, { templates }); return this.loadTemplates()
  }

  async deleteTemplate(nameInput: string): Promise<JsonObject[]> {
    const filePath = path.join(this.configDir, 'summary_templates.toml'); const raw = await readToml(filePath, { templates: [] }); const name = nameInput.trim().toLowerCase()
    raw.templates = (raw.templates ?? []).filter((item: JsonObject) => String(item.name).toLowerCase() !== name); await writeToml(filePath, raw); return this.loadTemplates()
  }

  async catalogs(): Promise<JsonObject> {
    const profiles = (await this.loadProfiles('summary')).profiles.map((profile: JsonObject) => { const authMode = profile.has_api_key ? 'bearer' : 'none'; return { ...profile, auth_mode: authMode, provider_binding_sha256: providerBindingDigest(profile, authMode) } })
    return { summary_profiles: profiles, summary_templates: await this.loadTemplates() }
  }

  async trustedWorkflowDraft(input: JsonObject): Promise<JsonObject> {
    const draft = structuredClone(input)
    const requested = draft.summary ?? {}
    const raw = await readToml(path.join(this.configDir, 'summary_profiles.toml'), { profiles: [] })
    const profile = (raw.profiles ?? []).map((item: JsonObject) => normalizedProfileRecord(item, 'summary-profile-')).find((item: JsonObject) => item.id === String(requested.profile_id) && item.version === Number(requested.profile_version))
    if (!profile) throw new Error('SUMMARY_PROFILE_NOT_FOUND: trusted profile version is unavailable')
    const authMode = profile.encrypted_api_key ? 'bearer' : 'none'
    const templates = await this.loadTemplates()
    const template = templates.find((item) => item.id === requested.template?.id && item.version === requested.template?.version)
    if (!template) throw new Error('SUMMARY_TEMPLATE_NOT_FOUND: trusted template version is unavailable')
    draft.summary = {
      ...requested,
      profile_id: profile.id,
      profile_version: Math.max(Number(profile.version ?? 1), 1),
      base_url: String(profile.base_url ?? '').trim(),
      auth_mode: authMode,
      model: String(profile.model ?? '').trim(),
      model_source: 'profile_default',
      credential_ref: authMode === 'bearer' ? `summary:${profile.id}` : null,
      provider_binding_sha256: providerBindingDigest(profile, authMode),
      template: { id: template.id, version: template.version, name: template.name, prompt_snapshot: template.prompt },
    }
    return draft
  }

  async history(limitInput: unknown): Promise<JsonObject[]> {
    const limit = limitInput == null ? 100 : Number(limitInput); if (!Number.isInteger(limit) || limit < 0) throw new Error('limit must be a non-negative integer')
    const roots = [this.outputsRoot, this.legacyOutputsRoot].filter((root): root is string => Boolean(root && existsSync(root)))
    if (!roots.length) return []
    const items: JsonObject[] = []; const skipped = new Set(['.jobs', 'logs', 'webview2-data', 'node_modules', 'target'])
    const walk = async (directory: string): Promise<void> => { for (const entry of await readdir(directory, { withFileTypes: true })) { if (entry.isDirectory()) { if (!skipped.has(entry.name) && !entry.name.startsWith('cargo-target-')) await walk(path.join(directory, entry.name)); continue } if (!entry.isFile() || path.extname(entry.name) !== '.md') continue; const filePath = path.join(directory, entry.name); const info = await stat(filePath); const suffix = entry.name.endsWith('.summary.md') ? '.summary.md' : entry.name.endsWith('.draft.md') ? '.draft.md' : entry.name.endsWith('.transcript.md') ? '.transcript.md' : ''; const kind = suffix === '.summary.md' ? 'summary' : suffix === '.draft.md' ? 'draft' : suffix === '.transcript.md' ? 'transcript' : 'markdown'; const companion = kind === 'summary' || kind === 'transcript' ? filePath.slice(0, -3) + '.json' : null; items.push({ id: filePath, kind, title: entry.name, path: filePath, companion_json_path: companion && existsSync(companion) ? companion : null, modified_ms: info.mtimeMs, size_bytes: info.size }) } }
    for (const root of roots) await walk(root)
    const unique = new Map(items.map((item) => [item.path, item]))
    return [...unique.values()].sort((a, b) => b.modified_ms - a.modified_ms).slice(0, limit)
  }
}
