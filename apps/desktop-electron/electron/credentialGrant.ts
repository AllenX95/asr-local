type CredentialsRequiredEvent = {
  workflow_id: string
  attempt_id: string
  data: Record<string, unknown>
}

export type SecretProvideParams = {
  workflow_id: string
  expected_attempt_id: string
  secret_request_id: string
  profile_id: string
  profile_version: number
  credential_ref: string
  purpose: string
  provider_binding_sha256: string
  secret: string
  lease_scope: 'attempt'
}

export function buildSecretProvideParams(event: CredentialsRequiredEvent, secret: string): SecretProvideParams {
  return {
    workflow_id: event.workflow_id,
    expected_attempt_id: event.attempt_id,
    secret_request_id: String(event.data.secret_request_id),
    profile_id: String(event.data.profile_id),
    profile_version: Number(event.data.profile_version),
    credential_ref: String(event.data.credential_ref),
    purpose: String(event.data.purpose),
    provider_binding_sha256: String(event.data.provider_binding_sha256),
    secret,
    lease_scope: 'attempt',
  }
}
