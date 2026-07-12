import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import { createInterface } from 'node:readline'
import { existsSync } from 'node:fs'
import path from 'node:path'
import { EventEmitter } from 'node:events'

type PendingRequest = { resolve: (value: unknown) => void; reject: (error: Error) => void; timer: NodeJS.Timeout }
type ProtocolMessage = { kind?: string; request_id?: string; ok?: boolean; result?: unknown; error?: { code?: string; message?: string }; payload?: unknown }

export class WorkflowRuntimeClient extends EventEmitter {
  private child: ChildProcessWithoutNullStreams | null = null
  private pending = new Map<string, PendingRequest>()
  private nextRequestId = 0
  private starting: Promise<void> | null = null

  constructor(private readonly projectRoot: string) { super() }

  async request(method: string, params: Record<string, unknown>, operationId?: string): Promise<unknown> {
    await this.ensureStarted()
    return this.send(method, params, operationId)
  }

  async shutdown(): Promise<void> {
    const child = this.child
    if (!child) return
    try {
      await this.send('runtime.shutdown', { mode: 'interrupt', grace_ms: 10_000 }, undefined, 12_000)
    } catch {
      child.kill()
    }
    await new Promise<void>((resolve) => {
      if (child.exitCode !== null) return resolve()
      const timer = setTimeout(() => { child.kill(); resolve() }, 2_000)
      child.once('exit', () => { clearTimeout(timer); resolve() })
    })
    this.child = null
  }

  private async ensureStarted(): Promise<void> {
    if (this.child && this.child.exitCode === null) return
    if (!this.starting) this.starting = this.start().finally(() => { this.starting = null })
    await this.starting
  }

  private async start(): Promise<void> {
    const workerDir = path.join(this.projectRoot, 'apps', 'worker-python')
    const configured = process.env.ASR_LOCAL_PYTHON?.trim()
    const packagedPython = path.join(this.projectRoot, 'runtime', 'python', 'python.exe')
    const localPython = path.join(workerDir, '.venv', 'Scripts', 'python.exe')
    const program = configured || (existsSync(packagedPython) ? packagedPython : existsSync(localPython) ? localPython : process.platform === 'win32' ? 'python' : 'python3')
    if (configured && !existsSync(configured)) throw new Error(`ASR_LOCAL_PYTHON does not exist: ${configured}`)
    const pipelineMode = process.env.ASR_LOCAL_V2_PIPELINE_MODE ?? (process.env.NODE_ENV === 'production' ? 'production' : 'auto')
    const child = spawn(program, ['-X', 'utf8', '-m', 'app.main', '--contract', 'v2', '--pipeline-mode', pipelineMode], {
      cwd: workerDir, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'],
      env: {
        ...process.env,
        PYTHONUTF8: '1',
        PYTHONIOENCODING: 'utf-8',
        ASR_LOCAL_PROJECT_ROOT: this.projectRoot,
        ASR_LOCAL_STATE_DIR: process.env.ASR_LOCAL_STATE_DIR ?? path.join(this.projectRoot, 'outputs', '.workflow'),
        ASR_LOCAL_CONFIG_DIR: process.env.ASR_LOCAL_CONFIG_DIR ?? path.join(this.projectRoot, 'config'),
      },
    })
    this.child = child
    child.stderr.setEncoding('utf8')
    child.stderr.on('data', (chunk: string) => process.stderr.write(`[workflow-v2] ${chunk}`))
    createInterface({ input: child.stdout, crlfDelay: Infinity }).on('line', (line) => this.handleLine(line))
    child.once('exit', (code, signal) => {
      if (this.child === child) this.child = null
      const error = new Error(`Workflow runtime exited (code=${code}, signal=${signal})`)
      for (const pending of this.pending.values()) { clearTimeout(pending.timer); pending.reject(error) }
      this.pending.clear()
      this.emit('unavailable', { code, signal })
    })
    child.once('error', (error) => this.emit('error', error))
    const hello = await this.send('runtime.hello', { supported_versions: [2] }) as { selected_version?: number; capabilities?: { pipeline_mode?: { resolved?: string } } }
    if (hello.selected_version !== 2) { child.kill(); throw new Error('Workflow runtime did not negotiate protocol version 2') }
    if (pipelineMode === 'production' && hello.capabilities?.pipeline_mode?.resolved !== 'production') {
      child.kill()
      throw new Error('Production workflow runtime did not resolve production pipeline mode')
    }
  }

  private send(method: string, params: Record<string, unknown>, operationId?: string, timeoutMs = 30_000): Promise<unknown> {
    const child = this.child
    if (!child || child.exitCode !== null) return Promise.reject(new Error('Workflow runtime is not running'))
    const requestId = `req_electron_${++this.nextRequestId}`
    const message: Record<string, unknown> = { protocol: 'asr-local-workflow', protocol_version: 2, kind: 'request', request_id: requestId, method, params }
    if (operationId) message.operation_id = operationId
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { this.pending.delete(requestId); reject(new Error(`Workflow request timed out: ${method}`)) }, timeoutMs)
      this.pending.set(requestId, { resolve, reject, timer })
      child.stdin.write(`${JSON.stringify(message)}\n`, 'utf8', (error) => {
        if (!error) return
        const pending = this.pending.get(requestId)
        if (!pending) return
        clearTimeout(pending.timer); this.pending.delete(requestId); reject(error)
      })
    })
  }

  private handleLine(line: string): void {
    let message: ProtocolMessage
    try { message = JSON.parse(line) as ProtocolMessage }
    catch { this.emit('protocol-error', new Error(`Non-JSON stdout from workflow runtime: ${line.slice(0, 200)}`)); return }
    if (message.kind === 'event') { this.emit('workflow-event', message.payload); return }
    if (!message.request_id) return
    const pending = this.pending.get(message.request_id)
    if (!pending) return
    clearTimeout(pending.timer); this.pending.delete(message.request_id)
    if (message.ok) pending.resolve(message.result)
    else pending.reject(new Error(`${message.error?.code ?? 'WORKFLOW_ERROR'}: ${message.error?.message ?? 'Unknown workflow error'}`))
  }
}
