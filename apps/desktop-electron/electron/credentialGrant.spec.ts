import { describe, expect, it } from 'vitest'
import { buildSecretProvideParams } from './credentialGrant.js'

describe('buildSecretProvideParams', () => {
  it('converts a credentials_required event into strict secret.provide params', () => {
    const params = buildSecretProvideParams({
      workflow_id: 'wf_0fe602679e24',
      attempt_id: 'att_35b0ca8b9c4a',
      data: {
        attempt_id: 'att_35b0ca8b9c4a',
        credential_ref: 'summary:summary-profile-ds-v4-pro',
        expires_at: '2026-07-13T01:48:49.783542Z',
        profile_id: 'summary-profile-ds-v4-pro',
        profile_version: 1,
        provider_binding_sha256: 'binding-digest',
        purpose: 'summary_api',
        secret_request_id: 'secret-request-1',
      },
    }, 'decrypted-secret')

    expect(params).toEqual({
      workflow_id: 'wf_0fe602679e24',
      expected_attempt_id: 'att_35b0ca8b9c4a',
      secret_request_id: 'secret-request-1',
      profile_id: 'summary-profile-ds-v4-pro',
      profile_version: 1,
      credential_ref: 'summary:summary-profile-ds-v4-pro',
      purpose: 'summary_api',
      provider_binding_sha256: 'binding-digest',
      secret: 'decrypted-secret',
      lease_scope: 'attempt',
    })
    expect(params).not.toHaveProperty('attempt_id')
    expect(params).not.toHaveProperty('expires_at')
  })
})
