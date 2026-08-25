from __future__ import annotations

import json
import hashlib
from pathlib import Path
import unittest

from app.ipc.v2 import ProtocolError, decode_request, encode_response


ROOT = Path(__file__).resolve().parents[4]
FIXTURES = ROOT / "contracts" / "workflow-v2" / "fixtures"


class ContractV2CodecTests(unittest.TestCase):
    def _reference_snapshot(self, content: str = "# notes\n会议决定采用 Orion。") -> dict:
        return {
            "name": "meeting-notes.md",
            "content": content,
            "size_bytes": len(content.encode("utf-8")),
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        }

    def test_all_json_fixtures_decode(self) -> None:
        fixture_paths = sorted(FIXTURES.glob("*.json"))
        self.assertGreaterEqual(len(fixture_paths), 8)
        for path in fixture_paths:
            with self.subTest(path=path.name):
                decoded = decode_request(path.read_bytes())
                self.assertEqual(decoded["protocol_version"], 2)

    def test_submit_normalizes_hotwords_and_preserves_replacement(self) -> None:
        payload = json.loads((FIXTURES / "workflow-submit.request.json").read_text(encoding="utf-8"))
        payload["params"]["draft"]["transcription"]["prompt_input"]["hotwords"] = ["Qwen", "qwen", " ASR Local "]
        decoded = decode_request(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        draft = decoded["params"]["draft"]
        self.assertEqual(draft["transcription"]["prompt_input"]["hotwords"], ["Qwen", "ASR Local"])
        self.assertEqual(
            draft["transcription"]["postprocess"]["replacements"],
            [{"wrong": "ASRLocal", "correct": "ASR Local"}],
        )

    def test_audio_channel_strategy_defaults_to_mixdown_and_accepts_split_stereo(self) -> None:
        payload = json.loads((FIXTURES / "workflow-submit.request.json").read_text(encoding="utf-8"))
        decoded = decode_request(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        self.assertEqual(decoded["params"]["draft"]["transcription"]["audio"], {"channel_strategy": "mixdown"})

        payload["params"]["draft"]["transcription"]["audio"] = {"channel_strategy": "split_stereo"}
        decoded = decode_request(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        self.assertEqual(decoded["params"]["draft"]["transcription"]["audio"], {"channel_strategy": "split_stereo"})

    def test_unknown_fields_are_rejected(self) -> None:
        payload = json.loads((FIXTURES / "workflow-submit.request.json").read_text(encoding="utf-8"))
        payload["params"]["draft"]["unexpected"] = True
        with self.assertRaises(ProtocolError) as context:
            decode_request(json.dumps(payload).encode("utf-8"))
        self.assertEqual(context.exception.code, "INVALID_REQUEST")

    def test_operation_id_rules(self) -> None:
        payload = json.loads((FIXTURES / "workflow-submit.request.json").read_text(encoding="utf-8"))
        payload.pop("operation_id")
        with self.assertRaises(ProtocolError):
            decode_request(json.dumps(payload).encode("utf-8"))

        payload = json.loads((FIXTURES / "workflow-submit.request.json").read_text(encoding="utf-8"))
        payload["method"] = "secret.provide"
        with self.assertRaises(ProtocolError):
            decode_request(json.dumps(payload).encode("utf-8"))

    def test_auth_mode_none_rejects_credential_ref(self) -> None:
        payload = json.loads((FIXTURES / "workflow-submit.request.json").read_text(encoding="utf-8"))
        summary = payload["params"]["draft"]["summary"]
        summary["auth_mode"] = "none"
        with self.assertRaises(ProtocolError):
            decode_request(json.dumps(payload).encode("utf-8"))

    def test_reference_document_absent_and_null_remain_compatible(self) -> None:
        payload = json.loads((FIXTURES / "workflow-submit.request.json").read_text(encoding="utf-8"))
        payload["params"]["draft"]["summary"].pop("reference_document", None)
        decoded = decode_request(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        self.assertNotIn("reference_document", decoded["params"]["draft"]["summary"])
        payload["params"]["draft"]["summary"]["reference_document"] = None
        decoded = decode_request(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        self.assertIsNone(decoded["params"]["draft"]["summary"]["reference_document"])

    def test_reference_document_validates_size_and_digest(self) -> None:
        payload = json.loads((FIXTURES / "workflow-submit.request.json").read_text(encoding="utf-8"))
        reference = self._reference_snapshot()
        payload["params"]["draft"]["summary"]["reference_document"] = reference
        decoded = decode_request(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        self.assertEqual(decoded["params"]["draft"]["summary"]["reference_document"], reference)
        for mutation in (
            {**reference, "size_bytes": reference["size_bytes"] + 1},
            {**reference, "sha256": "0" * 64},
            {**reference, "name": "meeting-notes.txt"},
            {**reference, "content": ""},
        ):
            payload["params"]["draft"]["summary"]["reference_document"] = mutation
            with self.subTest(mutation=mutation):
                with self.assertRaises(ProtocolError):
                    decode_request(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def test_persistent_operation_payload_rejects_secret(self) -> None:
        payload = json.loads((FIXTURES / "workflow-control.request.json").read_text(encoding="utf-8"))
        payload["params"]["secret"] = "must-not-be-in-operation"
        with self.assertRaises(ProtocolError):
            decode_request(json.dumps(payload).encode("utf-8"))

    def test_workflow_clear_requires_operation_id_and_only_workflow_id(self) -> None:
        payload = {
            "protocol": "asr-local-workflow",
            "protocol_version": 2,
            "kind": "request",
            "request_id": "req_clear",
            "operation_id": "op_clear",
            "method": "workflow.clear",
            "params": {"workflow_id": "wf_done"},
        }
        self.assertEqual(decode_request(json.dumps(payload).encode("utf-8"))["params"], {"workflow_id": "wf_done"})
        payload.pop("operation_id")
        with self.assertRaises(ProtocolError):
            decode_request(json.dumps(payload).encode("utf-8"))

    def test_resummarize_accepts_a_trusted_summary_recipe(self) -> None:
        submit = json.loads((FIXTURES / "workflow-submit.request.json").read_text(encoding="utf-8"))
        payload = {
            **submit,
            "method": "workflow.resummarize",
            "params": {
                "source_workflow_id": "wf_done",
                "expected_attempt_id": "att_done",
                "expected_sequence": 24,
                "input_artifact_id": "artifact_transcript",
                "summary": submit["params"]["draft"]["summary"],
            },
        }
        decoded = decode_request(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        self.assertEqual(decoded["method"], "workflow.resummarize")
        self.assertEqual(decoded["params"]["summary"]["template"]["id"], "summary-template-uuid")
        payload["operation_id"] = "op_clear"
        payload["params"]["delete_artifacts"] = True
        with self.assertRaises(ProtocolError):
            decode_request(json.dumps(payload).encode("utf-8"))

    def test_summary_policy_snapshot_is_required_for_new_submit_and_resummarize(self) -> None:
        payload = json.loads((FIXTURES / "workflow-submit.request.json").read_text(encoding="utf-8"))
        summary = payload["params"]["draft"]["summary"]
        self.assertEqual(summary["policy_snapshot"], {"id": "asr-primary-reference-advisory", "version": 1})
        decoded = decode_request(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        self.assertEqual(decoded["params"]["draft"]["summary"]["policy_snapshot"], summary["policy_snapshot"])

        summary.pop("policy_snapshot")
        with self.assertRaises(ProtocolError) as submit_context:
            decode_request(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        self.assertEqual(submit_context.exception.code, "INVALID_REQUEST")

        resummarize = json.loads((FIXTURES / "workflow-submit.request.json").read_text(encoding="utf-8"))
        resummarize["method"] = "workflow.resummarize"
        resummarize["params"] = {
            "source_workflow_id": "wf_done",
            "expected_attempt_id": "att_done",
            "expected_sequence": 24,
            "input_artifact_id": "artifact_transcript",
            "summary": resummarize["params"]["draft"]["summary"],
        }
        resummarize["params"]["summary"].pop("policy_snapshot")
        with self.assertRaises(ProtocolError) as resummarize_context:
            decode_request(json.dumps(resummarize, ensure_ascii=False).encode("utf-8"))
        self.assertEqual(resummarize_context.exception.code, "INVALID_REQUEST")

    def test_summary_policy_snapshot_rejects_unknown_id_version_or_keys(self) -> None:
        payload = json.loads((FIXTURES / "workflow-submit.request.json").read_text(encoding="utf-8"))
        for policy in (
            {"id": "unknown-policy", "version": 1},
            {"id": "asr-primary-reference-advisory", "version": 2},
            {"id": "asr-primary-reference-advisory", "version": True},
            {"id": "asr-primary-reference-advisory", "version": 1, "extra": True},
        ):
            payload["params"]["draft"]["summary"]["policy_snapshot"] = policy
            with self.subTest(policy=policy):
                with self.assertRaises(ProtocolError) as context:
                    decode_request(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
                self.assertEqual(context.exception.code, "INVALID_REQUEST")

    def test_response_is_utf8_jsonl(self) -> None:
        raw = encode_response("req_1", ok=True, result={"message": "完成"})
        self.assertTrue(raw.endswith(b"\n"))
        self.assertEqual(decode_request(raw), json.loads(raw.decode("utf-8")))


if __name__ == "__main__":
    unittest.main()
