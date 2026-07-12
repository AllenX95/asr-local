import { appendFileSync, existsSync, mkdirSync, renameSync, statSync, unlinkSync } from 'node:fs'
import path from 'node:path'

const MAX_LOG_BYTES = 5 * 1024 * 1024

export interface SessionLogger {
  paths: { main: string; worker: string }
  info: (message: string, data?: unknown) => void
  warn: (message: string, data?: unknown) => void
  error: (message: string, data?: unknown) => void
  workerStderr: (text: string) => void
}

function redact(value: string): string {
  return value
    .replace(/(authorization\s*:\s*bearer\s+)[^\s"']+/gi, '$1[REDACTED]')
    .replace(/("?(?:api[_-]?key|secret|token)"?\s*[:=]\s*)"?[^,\s}"']+/gi, '$1[REDACTED]')
}

function rotate(filePath: string): void {
  if (!existsSync(filePath) || statSync(filePath).size < MAX_LOG_BYTES) return
  const previous = `${filePath}.1`
  const older = `${filePath}.2`
  if (existsSync(older)) unlinkSync(older)
  if (existsSync(previous)) renameSync(previous, older)
  renameSync(filePath, previous)
}

export function createSessionLogger(directory: string): SessionLogger {
  mkdirSync(directory, { recursive: true })
  const paths = { main: path.join(directory, 'electron-main.log'), worker: path.join(directory, 'python-worker.log') }
  rotate(paths.main)
  rotate(paths.worker)
  const write = (level: string, message: string, data?: unknown) => {
    const suffix = data === undefined ? '' : ` ${JSON.stringify(data, (_key, item) => /api[_-]?key|secret|token/i.test(_key) ? '[REDACTED]' : item)}`
    appendFileSync(paths.main, `${new Date().toISOString()} ${level} pid=${process.pid} ${redact(message + suffix)}\n`, 'utf8')
  }
  return {
    paths,
    info: (message, data) => write('INFO', message, data),
    warn: (message, data) => write('WARN', message, data),
    error: (message, data) => write('ERROR', message, data),
    workerStderr: (text) => appendFileSync(paths.main, `${new Date().toISOString()} WORKER_STDERR ${redact(text).trimEnd()}\n`, 'utf8'),
  }
}
