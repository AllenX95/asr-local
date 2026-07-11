from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import unittest

from app.workflow.secrets import CredentialError, EphemeralSecretBroker
from app.supervisor.server import BrokerSecretProvider


class SecretBrokerTests(unittest.TestCase):
    def test_grant_is_bound_to_workflow_attempt_profile_and_purpose(self) -> None:
        now = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
        broker = EphemeralSecretBroker(clock=lambda: now)
        profile = {"profile_id": "p1", "profile_version": 2, "credential_ref": "credential://p1", "purpose": "summary_api", "auth_mode": "bearer", "provider_binding_sha256": "binding"}
        request = broker.request(workflow_id="wf1", attempt_id="att1", profile=profile, purpose="summary_api")
        with self.assertRaises(CredentialError):
            broker.provide(secret_request_id=request.secret_request_id, workflow_id="wf-other", attempt_id="att1", profile_id="p1", profile_version=2, credential_ref="credential://p1", purpose="summary_api", provider_binding_sha256="binding", secret="secret")
        broker.provide(secret_request_id=request.secret_request_id, workflow_id="wf1", attempt_id="att1", profile_id="p1", profile_version=2, credential_ref="credential://p1", purpose="summary_api", provider_binding_sha256="binding", secret="secret")
        self.assertEqual(broker.consume(request.secret_request_id), "secret")
        with self.assertRaises(CredentialError):
            broker.consume(request.secret_request_id)

    def test_no_auth_provider_never_requests_secret(self) -> None:
        broker = EphemeralSecretBroker(clock=lambda: datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc))
        with self.assertRaises(CredentialError):
            broker.request(workflow_id="wf1", attempt_id="att1", profile={"auth_mode": "none"}, purpose="summary_api")

    def test_stdio_bridge_waits_for_and_consumes_native_grant(self) -> None:
        async def scenario() -> None:
            provider = BrokerSecretProvider()
            requested: list[dict] = []
            granted: list[tuple[str, str]] = []

            async def on_request(data: dict) -> None:
                requested.append(data)

            async def on_granted(workflow_id: str, attempt_id: str) -> None:
                granted.append((workflow_id, attempt_id))

            provider.on_request = on_request
            provider.on_granted = on_granted
            profile = {
                "profile_id": "profile-1",
                "profile_version": 2,
                "credential_ref": "credential://summary/profile-1",
                "provider_binding_sha256": "binding",
                "auth_mode": "bearer",
            }
            task = asyncio.create_task(provider.provide(workflow_id="wf-1", attempt_id="att-1", profile=profile, purpose="summary_api"))
            while not requested:
                await asyncio.sleep(0)
            request = requested[0]
            result = await provider.grant({
                **request,
                "expected_attempt_id": "att-1",
                "secret": "ephemeral",
                "lease_scope": "attempt",
            })
            self.assertTrue(result["accepted"])
            self.assertEqual(await task, "ephemeral")
            self.assertEqual(granted, [("wf-1", "att-1")])

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
