from __future__ import annotations

import json
from pathlib import Path
import unittest

from app.ipc.protocol import decode_message


ROOT = Path(__file__).resolve().parents[4]
FIXTURES = ROOT / "contracts" / "worker-v1" / "fixtures"


class V1FixtureFreezeTests(unittest.TestCase):
    def test_v1_messages_remain_decodable(self) -> None:
        expected = {
            "run_job.request.json": "run_job",
            "health_check_ok.response.json": "health_check_ok",
            "job_event.event.json": "job_event",
            "job_completed.response.json": "job_completed",
            "job_failed.response.json": "job_failed",
            "shutdown_ack.response.json": "shutdown_ack",
        }
        for name, message_type in expected.items():
            with self.subTest(name=name):
                message = decode_message((FIXTURES / name).read_text(encoding="utf-8"))
                self.assertEqual(message["type"], message_type)

    def test_v1_fixtures_are_json_objects(self) -> None:
        for path in FIXTURES.glob("*.json"):
            with self.subTest(path=path.name):
                self.assertIsInstance(json.loads(path.read_text(encoding="utf-8")), dict)


if __name__ == "__main__":
    unittest.main()
