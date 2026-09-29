"""Rotating anti-clone BLE tokens.

A student's phone advertises HMAC-SHA256(secret, time window) truncated to
8 bytes (fits a BLE manufacturer-data payload). The window rotates every
`window_s` seconds, so a sniffed token stops working within a minute or two
and cannot be replayed from another place later. The server holds each
student's secret and recognises tokens by precomputing them per window.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
from typing import Callable

TOKEN_BYTES = 8
TOKEN_HEX_LEN = TOKEN_BYTES * 2
_PREFIX = b"ble-wifi/v1|"


def new_secret() -> str:
    return secrets.token_hex(16)


def window_index(ts: float, window_s: int) -> int:
    return int(ts // window_s)


def token_for(secret_hex: str, window: int) -> str:
    mac = hmac.new(bytes.fromhex(secret_hex), _PREFIX + window.to_bytes(8, "big", signed=True), hashlib.sha256)
    return mac.digest()[:TOKEN_BYTES].hex()


class TokenResolver:
    """Maps a token seen at time `ts` back to a student id, or None."""

    def __init__(
        self,
        load_secrets: Callable[[], dict[str, str]],
        window_s: int = 30,
        skew_windows: int = 1,
        max_cached_windows: int = 16,
    ):
        self._load_secrets = load_secrets
        self._window_s = window_s
        self._skew = skew_windows
        self._max_cached = max_cached_windows
        self._secrets: dict[str, str] | None = None
        self._tables: dict[int, dict[str, str]] = {}
        self._lock = threading.Lock()

    def invalidate(self) -> None:
        """Call after students are added so their tokens become resolvable."""
        with self._lock:
            self._secrets = None
            self._tables.clear()

    def resolve(self, token: str, ts: float) -> str | None:
        centre = window_index(ts, self._window_s)
        for w in range(centre - self._skew, centre + self._skew + 1):
            student = self._table(w).get(token)
            if student is not None:
                return student
        return None

    def _table(self, window: int) -> dict[str, str]:
        with self._lock:
            table = self._tables.get(window)
            if table is None:
                if self._secrets is None:
                    self._secrets = self._load_secrets()
                table = {token_for(sec, window): sid for sid, sec in self._secrets.items()}
                if len(self._tables) >= self._max_cached:
                    self._tables.pop(min(self._tables))
                self._tables[window] = table
            return table
