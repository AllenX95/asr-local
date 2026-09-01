const { app, safeStorage } = require('electron')
const crypto = require('node:crypto')
const fs = require('node:fs/promises')
const path = require('node:path')

const projectRoot = path.resolve(__dirname, '..', '..', '..')
const TOML = require(path.join(projectRoot, 'apps', 'desktop-electron', 'node_modules', '@iarna', 'toml'))
const asrUserData = path.join(process.env.APPDATA, 'ASR Local')
app.setPath('userData', asrUserData)

function sha256(text) {
  return crypto.createHash('sha256').update(text, 'utf8').digest('hex')
}

function chatCompletionsUrl(baseUrl) {
  const trimmed = String(baseUrl).trim().replace(/\/+$/, '')
  return trimmed.endsWith('/chat/completions') ? trimmed : `${trimmed}/chat/completions`
}

function safeName(value) {
  return String(value).replace(/[<>:"/\\|?*]+/g, '-').replace(/\s+/g, '-').replace(/-+/g, '-')
}

async function readToml(filePath) {
  return TOML.parse(await fs.readFile(filePath, 'utf8'))
}

async function main() {
  const outputDir = path.resolve(process.argv[2])
  const runLabel = process.argv[3] || 'run'
  const profilePath = path.join(asrUserData, 'config', 'summary_profiles.toml')
  const templatePath = path.join(projectRoot, 'config', 'summary_templates.toml')
  const cases = [
    { templateName: '通用模板', transcriptPath: path.join(projectRoot, 'outputs', '260630-韦特嘉访谈-CEO赵总.transcript.md') },
    { templateName: '客户访谈', transcriptPath: path.join(projectRoot, 'outputs', '阿米奥客户访谈-海信.transcript.md') },
    { templateName: '团队访谈问答', transcriptPath: path.join(projectRoot, 'outputs', '260630-韦特嘉访谈-CTO李总.transcript.md') },
  ]

  const [profiles, templates] = await Promise.all([
    readToml(profilePath),
    readToml(templatePath),
  ])
  const profile = profiles.profiles.find((item) => item.name === profiles.last_profile)
  if (!profile) throw new Error(`Summary profile not found: ${profiles.last_profile}`)
  const stored = String(profile.encrypted_api_key || '')
  if (!stored.startsWith('safe-storage:v1:')) throw new Error('Summary credential is not in safeStorage v1 format')
  const apiKey = safeStorage.decryptString(Buffer.from(stored.slice('safe-storage:v1:'.length), 'base64'))
  if (!apiKey) throw new Error('Summary credential decrypted to an empty value')

  const inputTokenBudget = Number(profile.max_input_tokens || 8000)
  const maxOutputTokens = Number(profile.max_output_tokens || 2000)
  await fs.mkdir(outputDir, { recursive: true })
  const manifest = {
    generated_at: new Date().toISOString(),
    run_label: runLabel,
    profile: {
      name: profile.name,
      id: profile.id,
      version: profile.version,
      model: profile.model,
      input_token_budget: inputTokenBudget,
      max_output_tokens: maxOutputTokens,
    },
    strategy: 'single_pass',
    runs: [],
  }

  for (const evalCase of cases) {
    const template = templates.templates.find((item) => item.name === evalCase.templateName)
    if (!template) throw new Error(`Template not found: ${evalCase.templateName}`)
    const transcript = await fs.readFile(evalCase.transcriptPath, 'utf8')
    const transcriptSha256 = sha256(transcript)
    const prompt = `# Summary Instructions\n${template.prompt}\n\n# Transcript Markdown\n${transcript}`
    const startedAt = Date.now()
    const response = await fetch(chatCompletionsUrl(profile.base_url), {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${apiKey}`,
        'Content-Type': 'application/json',
        'X-Idempotency-Key': sha256(`${transcriptSha256}:${sha256(template.prompt)}:${runLabel}`),
      },
      body: JSON.stringify({
        model: profile.model,
        messages: [
          { role: 'system', content: 'Summarize the transcript into Markdown. Follow the template exactly.' },
          { role: 'user', content: prompt },
        ],
        max_tokens: maxOutputTokens,
      }),
      signal: AbortSignal.timeout(600000),
    })
    const body = await response.text()
    if (!response.ok) throw new Error(`Provider returned HTTP ${response.status} for ${template.name}: ${body.slice(0, 500)}`)
    const parsed = JSON.parse(body)
    const content = parsed?.choices?.[0]?.message?.content
    const text = typeof content === 'string'
      ? content.trim()
      : Array.isArray(content)
        ? content.filter((part) => part?.type === 'text').map((part) => part.text || '').join('\n').trim()
        : ''
    if (!text) throw new Error(`Provider returned empty content for ${template.name}`)

    const outputName = `${String(manifest.runs.length + 1).padStart(2, '0')}-${safeName(template.name)}.summary.md`
    await fs.writeFile(path.join(outputDir, outputName), `${text}\n`, 'utf8')
    manifest.runs.push({
      template_name: template.name,
      template_sha256: sha256(template.prompt),
      transcript_path: evalCase.transcriptPath,
      transcript_sha256: transcriptSha256,
      transcript_characters: transcript.length,
      estimated_transcript_tokens: Math.max(1, Math.ceil(transcript.length / 4)),
      output_file: outputName,
      output_characters: text.length,
      latency_ms: Date.now() - startedAt,
      finish_reason: parsed?.choices?.[0]?.finish_reason ?? null,
      usage: parsed?.usage ?? null,
    })
    process.stdout.write(`completed ${template.name}: ${text.length} chars\n`)
  }
  await fs.writeFile(path.join(outputDir, 'manifest.json'), JSON.stringify(manifest, null, 2), 'utf8')
}

app.whenReady().then(main).then(() => app.quit()).catch((error) => {
  process.stderr.write(`${error?.stack || error}\n`)
  app.exit(1)
})
