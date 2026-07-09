from __future__ import annotations

import json
from typing import Any


def encode_message(message_type: str, payload: dict[str, Any] | None = None, **extra: Any) -> str:
    message = {"type": message_type, "payload": payload or {}}
    message.update(extra)
    return json.dumps(message, ensure_ascii=False)


def decode_message(line: str) -> dict[str, Any]:
    return json.loads(line)
