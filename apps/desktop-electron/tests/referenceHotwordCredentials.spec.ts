import { mkdtemp } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { expect, it, vi } from 'vitest'

vi.mock('electron', () => ({ safeStorage: {
  isEncryptionAvailable: () => true,
  encryptString: (value: string) => Buffer.from(value),
  decryptString: (value: Buffer) => value.toString('utf8'),
} }))
import { HostServices } from '../electron/hostServices.js'

it('uses the exact model/provider credential when legacy profile IDs and versions collide', async () => {
  const root = await mkdtemp(path.join(tmpdir(), 'asr-hotword-credential-'))
  const host = new HostServices(root, root, root)
  await host.saveProfile('summary', { id: 'same-id', version: 5, name: 'A Other', model: 'other', base_url: 'https://other.example/v1', api_key: 'other-test-secret' })
  await host.saveProfile('summary', { id: 'same-id', version: 5, name: 'B MiMo', model: 'mimo-v2.6-flash', base_url: 'https://mimo.example/v1', api_key: 'mimo-test-secret' })
  const binding = { model: 'mimo-v2.6-flash', baseUrl: 'https://mimo.example/v1' }
  await expect(host.secretForProfile('summary', 'same-id', 5, binding)).resolves.toBe('mimo-test-secret')
  await expect(host.secretForProfile('summary', 'same-id', 5, { ...binding, baseUrl: 'https://changed.example/v1' })).rejects.toThrow('CREDENTIAL_REJECTED')
  await host.saveProfile('summary', { id: 'same-id', version: 5, name: 'C Duplicate', model: binding.model, base_url: binding.baseUrl, api_key: 'duplicate-test-secret' })
  await expect(host.secretForProfile('summary', 'same-id', 5, binding)).rejects.toThrow('CREDENTIAL_REJECTED')
})
