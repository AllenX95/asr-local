from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import secrets
from typing import Any


class CredentialError(RuntimeError):
    code = "CREDENTIAL_REJECTED"


@dataclass(frozen=True, slots=True)
class SecretRequest:
    secret_request_id: str
    workflow_id: str
    attempt_id: str
    profile_id: str
    profile_version: int
    credential_ref: str
    purpose: str
    provider_binding_sha256: str
    expires_at: str

    def as_event_data(self) -> dict[str, Any]:
        return {
            "secret_request_id": self.secret_request_id,
            "profile_id": self.profile_id,
            "profile_version": self.profile_version,
            "credential_ref": self.credential_ref,
            "purpose": self.purpose,
            "provider_binding_sha256": self.provider_binding_sha256,
            "expires_at": self.expires_at,
        }


class EphemeralSecretBroker:
    """In-memory broker; desktop owns the actual credential store and grant input."""

    def __init__(self, *, clock=None, ttl_seconds: int = 60) -> None:
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.ttl_seconds = ttl_seconds
        self._requests: dict[str, SecretRequest] = {}
        self._grants: dict[str, str] = {}

    def request(self, *, workflow_id: str, attempt_id: str, profile: dict[str, Any], purpose: str) -> SecretRequest:
        if purpose not in {"summary_api", "cloud_asr"}:
            raise CredentialError("unsupported secret purpose")
        if profile.get("auth_mode") == "none":
            raise CredentialError("auth_mode=none must not request a secret")
        now = self.clock()
        expires_at = now.timestamp() + self.ttl_seconds
        request = SecretRequest(
            secret_request_id=f"secret_req_{secrets.token_urlsafe(12)}",
            workflow_id=workflow_id,
            attempt_id=attempt_id,
            profile_id=str(profile["profile_id"]),
            profile_version=int(profile["profile_version"]),
            credential_ref=str(profile["credential_ref"]),
            purpose=purpose,
            provider_binding_sha256=str(profile["provider_binding_sha256"]),
            expires_at=datetime.fromtimestamp(expires_at, timezone.utc).isoformat().replace("+00:00", "Z"),
        )
        self._requests[request.secret_request_id] = request
        return request

    def provide(
        self,
        *,
        secret_request_id: str,
        workflow_id: str,
        attempt_id: str,
        profile_id: str,
        profile_version: int,
        credential_ref: str,
        purpose: str,
        provider_binding_sha256: str,
        secret: str,
    ) -> str:
        request = self._requests.get(secret_request_id)
        if request is None or _expired(request.expires_at, self.clock()):
            raise CredentialError("secret request is missing or expired")
        expected = (
            request.workflow_id,
            request.attempt_id,
            request.profile_id,
            request.profile_version,
            request.credential_ref,
            request.purpose,
            request.provider_binding_sha256,
        )
        actual = (workflow_id, attempt_id, profile_id, profile_version, credential_ref, purpose, provider_binding_sha256)
        if expected != actual or not secret:
            raise CredentialError("secret request identity does not match")
        self._grants[secret_request_id] = secret
        return secret

    def consume(self, secret_request_id: str) -> str:
        request = self._requests.get(secret_request_id)
        if request is None or _expired(request.expires_at, self.clock()):
            self.revoke(secret_request_id)
            raise CredentialError("secret grant is missing or expired")
        secret = self._grants.pop(secret_request_id, None)
        self._requests.pop(secret_request_id, None)
        if secret is None:
            raise CredentialError("secret grant has not been provided")
        return secret

    def revoke(self, secret_request_id: str) -> None:
        self._grants.pop(secret_request_id, None)
        self._requests.pop(secret_request_id, None)


def _expired(expires_at: str, now: datetime) -> bool:
    return now.timestamp() >= datetime.fromisoformat(expires_at.replace("Z", "+00:00")).timestamp()
