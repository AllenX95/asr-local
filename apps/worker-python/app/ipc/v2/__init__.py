"""Versioned, inference-independent Workflow Contract v2 codec."""

from .canonical import canonical_operation_digest
from .codec import decode_request, encode_event, encode_response
from .errors import ProtocolError

__all__ = [
    "ProtocolError",
    "canonical_operation_digest",
    "decode_request",
    "encode_event",
    "encode_response",
]
