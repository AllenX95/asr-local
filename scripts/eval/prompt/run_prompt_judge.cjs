const { app, safeStorage } = require('electron')
const crypto = require('node:crypto')
const fs = require('node:fs/promises')
const path = require('node:path')

const projectRoot = path.resolve(__dirname, '..', '..', '..')
const TOML = require(path.join(projectRoot, 'apps', 'desktop-electron', 'node_modules', '@iarna', 'toml'))
const asrUserData = path.join(process.env.APPDATA, 'ASR Local')
app.setPath('userData', asrUserData)

const sha256 = (text) => crypto.createHash('sha256').update(text, 'utf8').digest('hex')
const chatUrl = (base) => {
  const value = String(base).trim().replace(/\/+$/, '')
  return value.endsWith('/chat/completions') ? value : `${value}/chat/completions`
}
const safeName = (value) => String(value).replace(/[<>:"/\\|?*]+/g, '-').replace(/\s+/g, '-').replace(/-+/g, '-')

function parseJsonResponse(text) {
  const trimmed = text.trim().replace(/^```(?:json)?\s*/i, '').replace(/\s*```$/, '')
  const start = trimmed.indexOf('{')
  const end = trimmed.lastIndexOf('}')
  if (start < 0 || end < start) throw new Error('Judge response did not contain a JSON object')
  return JSON.parse(trimmed.slice(start, end + 1))
}

async function requestJson(profile, apiKey, messages, requestKey) {
  const response = await fetch(chatUrl(profile.base_url), {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${apiKey}`,
      'Content-Type': 'application/json',
      'X-Idempotency-Key': requestKey,
    },
    body: JSON.stringify({ model: profile.model, messages, max_tokens: Number(profile.max_output_tokens || 32000) }),
    signal: AbortSignal.timeout(600000),
  })
  const body = await response.text()
  if (!response.ok) throw new Error(`Judge provider returned HTTP ${response.status}: ${body.slice(0, 500)}`)
  const parsed = JSON.parse(body)
  const content = parsed?.choices?.[0]?.message?.content
  const text = typeof content === 'string'
    ? content.trim()
    : Array.isArray(content)
      ? content.filter((part) => part?.type === 'text').map((part) => part.text || '').join('\n').trim()
      : ''
  if (!text) throw new Error('Judge provider returned empty content')
  return { value: parseJsonResponse(text), usage: parsed?.usage ?? null, finishReason: parsed?.choices?.[0]?.finish_reason ?? null }
}

async function main() {
  const evalDir = path.resolve(process.argv[2])
  const goldSourceDir = process.argv[3] ? path.resolve(process.argv[3]) : null
  const profileDoc = TOML.parse(await fs.readFile(path.join(asrUserData, 'config', 'summary_profiles.toml'), 'utf8'))
  const profile = profileDoc.profiles.find((item) => item.name === profileDoc.last_profile)
  if (!profile) throw new Error(`Summary profile not found: ${profileDoc.last_profile}`)
  const stored = String(profile.encrypted_api_key || '')
  if (!stored.startsWith('safe-storage:v1:')) throw new Error('Summary credential is not in safeStorage v1 format')
  const apiKey = safeStorage.decryptString(Buffer.from(stored.slice('safe-storage:v1:'.length), 'base64'))
  const manifestPath = path.join(evalDir, 'manifest.json')
  const manifest = JSON.parse(await fs.readFile(manifestPath, 'utf8'))
  manifest.judge = { model: profile.model, generated_at: new Date().toISOString(), runs: [] }

  for (const run of manifest.runs) {
    const slug = safeName(run.template_name)
    const goldFile = `${slug}.gold.json`
    if (goldSourceDir) {
      try {
        await fs.access(path.join(goldSourceDir, goldFile))
      } catch {
        process.stdout.write(`skipped ${run.template_name}: no corrected gold\n`)
        continue
      }
    }
    const [transcript, summary] = await Promise.all([
      fs.readFile(run.transcript_path, 'utf8'),
      fs.readFile(path.join(evalDir, run.output_file), 'utf8'),
    ])
    const goldPrompt = `你是严谨的 VC 访谈纪要评测员。请只以给定 transcript 为真值，建立可复核的 gold checklist；不要使用外部知识，也不要纠正 ASR。\n\n要求：\n1. points：覆盖所有对 VC 判断有实质意义且彼此独立的信息点。importance=3 表示关键结论/风险/核心数字，2 表示重要支撑，1 表示背景。category 仅用 fact/judgment/plan/risk/negative/constraint。每项附 transcript 中最接近的时间戳。关键否定、条件和时间范围应拆成独立项。\n2. questions：只收录 transcript 中真实说出口且有实质信息的问题意图，不得把“还可以继续了解什么”或回答中的信息缺口推导成问题/追问。一个连续提问轮次中的复合问题保留为一个 Q，并在 intent 中列清所有子意图，不因纪要可能拆分而改变 gold 数量。answer_elements 只取 transcript 后续实际回答中的最小有效要素。answer_status 定义：所有子意图均有实质回应=answered；仅部分子意图有回应=partial；没有触及核心或明确不清楚=unanswered。followup 只收录 transcript 中在初次回答后真实说出口的确认、澄清、量化或深挖问题；每条写明实际追问意图与时间戳，不得把同一轮初始复合问题的未答子项或评测者建议伪造成 followup。寒暄、无内容确认不收录。\n3. numbers：收录对判断有意义的数字四元组（值、单位、周期/时点、口径/限定），附时间戳。\n4. uncertainties：只收录影响实质含义的疑似错词、明显矛盾、口径不清或需核实点。\n5. 只输出合法 JSON，不要 Markdown，不要解释。\n\nJSON 结构：\n{"points":[{"id":"P001","timestamp":"00:00:00","description":"...","importance":3,"category":"fact"}],"questions":[{"id":"Q001","timestamp":"00:00:00","intent":"...","answer_elements":["..."],"answer_status":"answered","followup":["[00:00:10] 实际追问意图"]}],"numbers":[{"id":"N001","timestamp":"00:00:00","value":"...","unit":"...","period":"...","scope":"..."}],"uncertainties":[{"id":"U001","timestamp":"00:00:00","description":"..."}]}\n\n# Transcript\n${transcript}`
    const goldStarted = Date.now()
    const goldResult = goldSourceDir
      ? {
          value: JSON.parse(await fs.readFile(path.join(goldSourceDir, goldFile), 'utf8')),
          usage: null,
          finishReason: 'reused',
        }
      : await requestJson(profile, apiKey, [
          { role: 'system', content: 'Extract a complete, source-grounded evaluation inventory as strict JSON.' },
          { role: 'user', content: goldPrompt },
        ], sha256(`${run.transcript_sha256}:gold-v2-explicit-questions`))
    await fs.writeFile(path.join(evalDir, goldFile), JSON.stringify(goldResult.value, null, 2), 'utf8')
    process.stdout.write(`gold ${run.template_name}: ${goldResult.value.points?.length || 0} points, ${goldResult.value.questions?.length || 0} questions\n`)

    const scorePrompt = `你是严谨的 VC 访谈纪要评测员。下面给出从 transcript 独立抽取的 gold checklist，以及待评纪要。请逐项判断纪要的语义覆盖与保真度。\n\n规则：\n1. point_coverage.score 只能是 1（完整且不改意）、0.5（部分覆盖但未歪曲）、0（遗漏或歪曲）。\n2. question_coverage 必须按语义匹配，不得按 gold 数组位置、gold Q 编号或纪要 Q 编号机械对齐；纪要可以合法拆分或合并问题，只要语义可追溯。question_retained 表示完整问题意图是否可识别；answer_elements_covered 填被正确保留的 answer_elements 的从 0 开始索引；followup_covered 只填纪要正确保留的真实 followup 索引；answer_status_preserved 判断回答/部分回答/未回答状态是否保真，纪要若显式标错状态则为 false；order_correct 判断相对顺序是否合理。每个 gold id 必须且只能输出一次，不得为纪要额外问题新增 id。\n3. number_coverage.score 只能是 1（值、单位、周期/时点、口径全部保真）、0.5（部分保真且未产生相反含义）、0（遗漏或错误）。\n4. uncertainty_coverage 填是否保留。\n5. unsupported_claims 只列纪要中 transcript/gold 无支撑的实质陈述；重大幻觉涉及数字、客户、融资、因果、身份或投资结论。\n6. repeated_claims_count 是纪要中未增加新证据/限定的重复陈述次数；主题索引中的纯 Q 编号不算重复。empty_boilerplate_characters 估算空章节及“未涉及”样板文字字符数；summary_claims_count 估算纪要中的实质原子陈述总数。\n7. 只输出合法 JSON，不要 Markdown，不要解释。\n\nJSON 结构：\n{"point_coverage":[{"id":"P001","score":1,"note":"..."}],"question_coverage":[{"id":"Q001","question_retained":true,"answer_elements_covered":[0],"followup_covered":[],"answer_status_preserved":true,"order_correct":true,"note":"..."}],"number_coverage":[{"id":"N001","score":1,"note":"..."}],"uncertainty_coverage":[{"id":"U001","retained":true,"note":"..."}],"unsupported_claims":[{"claim":"...","severity":"major|minor","note":"..."}],"summary_claims_count":0,"repeated_claims_count":0,"empty_boilerplate_characters":0}\n\n# Gold checklist\n${JSON.stringify(goldResult.value)}\n\n# Summary\n${summary}`
    const scoreStarted = Date.now()
    const scoreResult = await requestJson(profile, apiKey, [
      { role: 'system', content: 'Score a summary against a fixed gold inventory as strict JSON. Be conservative and evidence-based.' },
      { role: 'user', content: scorePrompt },
    ], sha256(`${run.transcript_sha256}:${run.template_sha256}:${sha256(summary)}:score-v2-semantic`))
    const scoreFile = `${slug}.score.json`
    await fs.writeFile(path.join(evalDir, scoreFile), JSON.stringify(scoreResult.value, null, 2), 'utf8')
    manifest.judge.runs.push({
      template_name: run.template_name,
      gold_file: goldFile,
      score_file: scoreFile,
      gold_latency_ms: Date.now() - goldStarted,
      score_latency_ms: Date.now() - scoreStarted,
      gold_finish_reason: goldResult.finishReason,
      score_finish_reason: scoreResult.finishReason,
      gold_usage: goldResult.usage,
      score_usage: scoreResult.usage,
    })
    process.stdout.write(`scored ${run.template_name}\n`)
  }
  await fs.writeFile(manifestPath, JSON.stringify(manifest, null, 2), 'utf8')
}

app.whenReady().then(main).then(() => app.quit()).catch((error) => {
  process.stderr.write(`${error?.stack || error}\n`)
  app.exit(1)
})
