"""Rotating ephemeral BLE tokens, marker tokens, and replay protection helpers.

Wire format (BLE manufacturer-specific data, company id 0xFFFF = "reserved for testing"):

    student presence : 0x01 | token[8]
    classroom marker : 0x02 | marker_idx[2, big-endian] | token[8]

    token(student) = HMAC-SHA256(student_secret, "cp-student/v1|"          + int64_be(window))[:8]
    token(marker)  = HMAC-SHA256(marker_secret,  "cp-marker/v1|" + idx_u16 + int64_be(window))[:8]
    window         = floor(unix_seconds / 30)

A token is meaningless to anyone who does not hold the secret, changes every 30 s, and is
accepted for +/-1 window, so a captured token stops working within about a minute.
Observations also carry a client nonce which the server stores uniquely, so an exact
upload cannot be replayed. A *relay* of a live token within its window is NOT prevented by
tokens alone; that is what the contradiction/reuse rules are for (see docs/LIMITATIONS.md).
"""
from __future__ import annotations

import hashlib
import hmac
import threading
from typing import Callable

TOKEN_BYTES = 8
TOKEN_HEX_LEN = TOKEN_BYTES * 2
COMPANY_ID = 0xFFFF
TYPE_STUDENT = 0x01
TYPE_MARKER = 0x02
_STUDENT_PREFIX = b"cp-student/v1|"
_MARKER_PREFIX = b"cp-marker/v1|"


def window_index(ts: float, window_s: int) -> int:
    return int(ts // window_s)


def student_token(secret_hex: str, window: int) -> str:
    mac = hmac.new(bytes.fromhex(secret_hex), _STUDENT_PREFIX + window.to_bytes(8, "big", signed=True),
                   hashlib.sha256)
    return mac.digest()[:TOKEN_BYTES].hex()


def marker_token(secret_hex: str, marker_idx: int, window: int) -> str:
    msg = _MARKER_PREFIX + marker_idx.to_bytes(2, "big") + window.to_bytes(8, "big", signed=True)
    return hmac.new(bytes.fromhex(secret_hex), msg, hashlib.sha256).digest()[:TOKEN_BYTES].hex()


def encode_student_payload(token_hex: str) -> bytes:
    return bytes([TYPE_STUDENT]) + bytes.fromhex(token_hex)


def encode_marker_payload(marker_idx: int, token_hex: str) -> bytes:
    return bytes([TYPE_MARKER]) + marker_idx.to_bytes(2, "big") + bytes.fromhex(token_hex)


def decode_payload(data: bytes) -> dict | None:
    """Parse manufacturer data (after the 2-byte company id). None if not ours."""
    if len(data) == 1 + TOKEN_BYTES and data[0] == TYPE_STUDENT:
        return {"type": "student", "token": data[1:].hex()}
    if len(data) == 3 + TOKEN_BYTES and data[0] == TYPE_MARKER:
        return {"type": "marker", "marker_idx": int.from_bytes(data[1:3], "big"), "token": data[3:].hex()}
    return None


def verify_marker_token(secret_hex: str, marker_idx: int, token: str, ts: float,
                        window_s: int, skew_windows: int) -> bool:
    centre = window_index(ts, window_s)
    return any(hmac.compare_digest(marker_token(secret_hex, marker_idx, w), token)
               for w in range(centre - skew_windows, centre + skew_windows + 1))


class StudentTokenResolver:
    """token (seen at ts) -> student_key, using per-window lookup tables built lazily."""

    def __init__(self, load_secrets: Callable[[], dict[str, str]], window_s: int = 30,
                 skew_windows: int = 1, max_cached: int = 16):
        self._load, self._window_s, self._skew, self._max = load_secrets, window_s, skew_windows, max_cached
        self._secrets: dict[str, str] | None = None
        self._tables: dict[int, dict[str, str]] = {}
        self._lock = threading.Lock()

    def invalidate(self) -> None:
        with self._lock:
            self._secrets = None
            self._tables.clear()

    def resolve(self, token: str, ts: float) -> str | None:
        c = window_index(ts, self._window_s)
        for w in range(c - self._skew, c + self._skew + 1):
            hit = self._table(w).get(token)
            if hit:
                return hit
        return None

    def _table(self, window: int) -> dict[str, str]:
        with self._lock:
            t = self._tables.get(window)
            if t is None:
                if self._secrets is None:
                    self._secrets = self._load()
                t = {student_token(sec, window): key for key, sec in self._secrets.items()}
                if len(self._tables) >= self._max:
                    self._tables.pop(min(self._tables))
                self._tables[window] = t
            return t


def timestamp_ok(ts: float, now: float, tolerance_s: float) -> bool:
    return abs(ts - now) <= tolerance_s
