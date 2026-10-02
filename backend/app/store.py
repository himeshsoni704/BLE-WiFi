"""SQLite persistence.

Only zone-level facts and scores are stored: which zone a student's phone was
seen in, and the derived owner score / last-motion time. Raw sensor samples
and their feature vectors are never written.
"""
from __future__ import annotations

import re
import sqlite3
import threading
from typing import Iterable, NamedTuple

_BSSID_RE = re.compile(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS students (
    student_id TEXT PRIMARY KEY, name TEXT NOT NULL, secret TEXT NOT NULL, created_ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS scanners (
    scanner_id TEXT PRIMARY KEY, zone TEXT NOT NULL, min_rssi INTEGER);
CREATE TABLE IF NOT EXISTS bssids (
    bssid TEXT PRIMARY KEY, zone TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS scans (
    ts REAL NOT NULL, student_id TEXT NOT NULL, scanner_id TEXT NOT NULL,
    zone TEXT NOT NULL, rssi INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS scans_ts ON scans (ts);
CREATE TABLE IF NOT EXISTS reports (
    ts REAL NOT NULL, student_id TEXT NOT NULL, wifi_zone TEXT, wifi_rssi INTEGER,
    owner_ema REAL, owner_ema_ts REAL, last_motion_ts REAL NOT NULL, interaction_ts REAL);
CREATE INDEX IF NOT EXISTS reports_student_ts ON reports (student_id, ts);
CREATE INDEX IF NOT EXISTS reports_ts ON reports (ts);
"""


class ScanRow(NamedTuple):
    ts: float
    student_id: str
    scanner_id: str
    zone: str
    rssi: int


class ReportRow(NamedTuple):
    ts: float
    student_id: str
    wifi_zone: str | None
    wifi_rssi: int | None
    owner_ema: float | None
    owner_ema_ts: float | None
    last_motion_ts: float
    interaction_ts: float | None


class ScannerRow(NamedTuple):
    scanner_id: str
    zone: str
    min_rssi: int | None


def normalize_bssid(bssid: str) -> str:
    norm = bssid.strip().upper().replace("-", ":")
    if not _BSSID_RE.match(norm):
        raise ValueError(f"invalid BSSID: {bssid!r}")
    return norm


class Store:
    def __init__(self, path: str = ":memory:"):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.RLock()
        with self._lock, self._conn:
            if path != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)

    def _write(self, sql: str, params=()) -> sqlite3.Cursor:
        with self._lock, self._conn:
            return self._conn.execute(sql, params)

    def _read(self, sql: str, params=()) -> list[tuple]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    # students
    def add_student(self, student_id: str, name: str, secret: str, ts: float) -> bool:
        try:
            self._write("INSERT INTO students VALUES (?,?,?,?)", (student_id, name, secret, ts))
        except sqlite3.IntegrityError:
            return False
        return True

    def student_secrets(self) -> dict[str, str]:
        return {sid: sec for sid, sec in self._read("SELECT student_id, secret FROM students")}

    def students(self) -> list[tuple[str, str]]:
        return self._read("SELECT student_id, name FROM students ORDER BY student_id")

    def student_ids(self) -> list[str]:
        return [sid for sid, _ in self.students()]

    # zones
    def upsert_scanner(self, scanner_id: str, zone: str, min_rssi: int | None) -> None:
        self._write(
            "INSERT INTO scanners VALUES (?,?,?) ON CONFLICT(scanner_id) "
            "DO UPDATE SET zone=excluded.zone, min_rssi=excluded.min_rssi",
            (scanner_id, zone, min_rssi),
        )

    def scanners(self) -> dict[str, ScannerRow]:
        return {r[0]: ScannerRow(*r) for r in self._read("SELECT scanner_id, zone, min_rssi FROM scanners")}

    def upsert_bssid(self, bssid: str, zone: str) -> None:
        self._write(
            "INSERT INTO bssids VALUES (?,?) ON CONFLICT(bssid) DO UPDATE SET zone=excluded.zone",
            (normalize_bssid(bssid), zone),
        )

    def bssid_zone(self, bssid: str) -> str | None:
        try:
            key = normalize_bssid(bssid)
        except ValueError:
            return None
        rows = self._read("SELECT zone FROM bssids WHERE bssid=?", (key,))
        return rows[0][0] if rows else None

    def zones(self) -> list[str]:
        rows = self._read("SELECT zone FROM scanners UNION SELECT zone FROM bssids ORDER BY zone")
        return [r[0] for r in rows]

    # scans
    def add_scans(self, rows: Iterable[ScanRow]) -> None:
        with self._lock, self._conn:
            self._conn.executemany("INSERT INTO scans VALUES (?,?,?,?,?)", list(rows))

    def scans_between(self, t_lo: float, t_hi: float) -> list[ScanRow]:
        """Scans with t_lo < ts <= t_hi, oldest first."""
        sql = "SELECT ts, student_id, scanner_id, zone, rssi FROM scans WHERE ts > ? AND ts <= ? ORDER BY ts"
        return [ScanRow(*r) for r in self._read(sql, (t_lo, t_hi))]

    # reports
    def add_report(self, row: ReportRow) -> None:
        self._write("INSERT INTO reports VALUES (?,?,?,?,?,?,?,?)", tuple(row))

    def latest_report(self, student_id: str, at_or_before: float) -> ReportRow | None:
        rows = self._read(
            "SELECT ts, student_id, wifi_zone, wifi_rssi, owner_ema, owner_ema_ts, last_motion_ts, "
            "interaction_ts FROM reports WHERE student_id=? AND ts<=? ORDER BY ts DESC LIMIT 1",
            (student_id, at_or_before),
        )
        return ReportRow(*rows[0]) if rows else None

    def reports_between(self, t_lo: float, t_hi: float) -> list[ReportRow]:
        sql = (
            "SELECT ts, student_id, wifi_zone, wifi_rssi, owner_ema, owner_ema_ts, last_motion_ts, "
            "interaction_ts FROM reports WHERE ts > ? AND ts <= ? ORDER BY ts"
        )
        return [ReportRow(*r) for r in self._read(sql, (t_lo, t_hi))]

    def purge_before(self, ts: float) -> int:
        with self._lock, self._conn:
            n = self._conn.execute("DELETE FROM scans WHERE ts < ?", (ts,)).rowcount
            n += self._conn.execute("DELETE FROM reports WHERE ts < ?", (ts,)).rowcount
        return n
