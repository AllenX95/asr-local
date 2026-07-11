from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from .errors import ProtocolError


def normalize_json(value: Any) -> Any:
    """Normalize JSON values before canonicalization.

    Contract v2 accepts JSON data only. Python's non-finite floats are rejected
    because RFC 8785/JCS has no representation for NaN or Infinity.
    """

    if isinstance(value, float) and not math.isfinite(value):
        raise ProtocolError("INVALID_REQUEST", "Non-finite numbers are not valid JSON.", [], {})
    if isinstance(value, dict):
        return {str(key): normalize_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize_json(item) for item in value]
    if value is None or isinstance(value, (str, int, bool, float)):
        return value
    raise ProtocolError("INVALID_REQUEST", "Protocol values must be JSON-compatible.", [], {})


def canonical_json(value: Any) -> str:
    """Return the stable JSON representation used by the v2 digest seam.

    v2 payloads intentionally use strings, integers, booleans, null, arrays
    and objects. The compact UTF-8 encoding with lexicographically sorted keys
    matches JCS for this supported subset and is shared with the Rust fixture
    tests. Unicode is not normalized because JCS does not normalize strings.
    """

    normalized = normalize_json(value)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_operation_digest(method: str, normalized_params: dict[str, Any]) -> str:
    if not isinstance(method, str) or not method:
        raise ProtocolError("INVALID_REQUEST", "method is required for operation digest.", [], {})
    if not isinstance(normalized_params, dict):
        raise ProtocolError("INVALID_REQUEST", "params must be an object.", [], {})
    payload = {"method": method, "params": normalized_params}
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
