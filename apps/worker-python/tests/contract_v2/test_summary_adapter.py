from __future__ import annotations

import asyncio
import unittest

from app.summary.openai_compatible import OpenAICompatibleSummaryGenerator, SummaryInputTooLargeError


def spec(strategy: str, budget: int = 1000, auth_mode: str = "none") -> dict:
    return {
        "display_name": "summary-test",
        "summary": {
            "profile_id": "summary-profile",
            "profile_version": 1,
            "base_url": "https://example.com/v1",
            "auth_mode": auth_mode,
            "model": "summary-model",
            "credential_ref": None if auth_mode == "none" else "credential://summary/test",
            "template": {"prompt_snapshot": "Return concise Markdown."},
            "context_strategy": strategy,
            "input_token_budget": budget,
            "max_output_tokens": 100,
        },
    }


class SummaryAdapterTests(unittest.TestCase):
    def test_single_pass_does_not_request_secret_for_no_auth_provider(self) -> None:
        calls: list[tuple[str, dict, dict]] = []

        def request(url, payload, headers):
            calls.append((url, payload, headers))
            return "# Summary\nDone"

        async def scenario() -> None:
            adapter = OpenAICompatibleSummaryGenerator(request_fn=request)
            result = await adapter.summarize(spec("single_pass"), {"text": "short transcript"}, "att_001")
            self.assertEqual(result["strategy"], "single_pass")
            self.assertEqual(len(calls), 1)
            self.assertNotIn("Authorization", calls[0][2])

        asyncio.run(scenario())

    def test_single_pass_rejects_over_budget_without_silent_truncation(self) -> None:
        async def scenario() -> None:
            adapter = OpenAICompatibleSummaryGenerator(request_fn=lambda *_: "unused")
            with self.assertRaises(SummaryInputTooLargeError):
                await adapter.summarize(spec("single_pass", budget=1), {"text": "a" * 100}, "att_001")

        asyncio.run(scenario())

    def test_hierarchical_strategy_calls_chunks_and_final_merge(self) -> None:
        calls: list[str] = []

        def request(_url, payload, headers):
            del headers
            calls.append(payload["messages"][1]["content"])
            return f"summary-{len(calls)}"

        async def scenario() -> None:
            adapter = OpenAICompatibleSummaryGenerator(request_fn=request)
            result = await adapter.summarize(spec("hierarchical", budget=5), {"text": "paragraph\n\n" * 20}, "att_001")
            self.assertEqual(result["strategy"], "hierarchical")
            self.assertGreaterEqual(len(calls), 2)
            self.assertEqual(len(result["provider_request_keys"]), len(calls))

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
