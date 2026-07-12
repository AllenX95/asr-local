from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
import sys


class DataBlob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_byte))]


def unprotect(encoded: str) -> bytes:
    encrypted = base64.b64decode(encoded)
    buffer = ctypes.create_string_buffer(encrypted)
    source = DataBlob(len(encrypted), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    result = DataBlob()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(source), None, None, None, None, 0, ctypes.byref(result)
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        ctypes.windll.kernel32.LocalFree(result.data)


def main() -> int:
    if sys.platform != "win32":
        raise RuntimeError("DPAPI migration is only available on Windows")
    encoded = sys.stdin.read().strip()
    sys.stdout.write(base64.b64encode(unprotect(encoded)).decode("ascii"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
