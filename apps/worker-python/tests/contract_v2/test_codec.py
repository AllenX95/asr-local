from __future__ import annotations

import json
from pathlib import Path
import unittest

from app.ipc.v2 import ProtocolError, decode_request, encode_response


ROOT = Path(__file__).resolve().parents[4]
FIXTURES = ROOT / "contracts" / "workflow-v2" / "fixtures"


class ContractV2CodecTests(unittest.TestCase):
    def test_all_json_fixtures_decode(self) -> None:
        fixture_paths = sorted(FIXTURES.glob("*.json"))
        self.assertGreaterEqual(len(fixture_paths), 8)
        for path in fixture_paths:
            with self.subTest(path=path.name):
                decoded = decode_request(path.read_bytes())
                self.assertEqual(decoded["protocol_version"], 2)

    def test_submit_normalizes_hotwords_and_preserves_replacement(self) -> None:
        payload = json.loads((FIXTURES / "workflow-submit.request.json").read_text(encoding="utf-8"))
        payload["params"]["draft"]["transcription"]["prompt_input"]["hotwords"] = ["MOSS", "moss", " ASR Local "]
        decoded = decode_request(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        draft = decoded["params"]["draft"]
        self.assertEqual(draft["transcription"]["prompt_input"]["hotwords"], ["MOSS", "ASR Local"])
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
        payload["operation_id"] = "op_clear"
        payload["params"]["delete_artifacts"] = True
        with self.assertRaises(ProtocolError):
            decode_request(json.dumps(payload).encode("utf-8"))

    def test_response_is_utf8_jsonl(self) -> None:
        raw = encode_response("req_1", ok=True, result={"message": "完成"})
        self.assertTrue(raw.endswith(b"\n"))
        self.assertEqual(decode_request(raw), json.loads(raw.decode("utf-8")))


if __name__ == "__main__":
    unittest.main()
