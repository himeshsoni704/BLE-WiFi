"""SQLite persistence.

Only zone-level facts and scores are stored: which zone a student's phone was
seen in, and the derived owner score / last-motion time. Raw sensor samples
and their feature vectors are never written.
"""
from __future__ import annotations

import json
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

-- Proof-of-Presence extension: classrooms/sessions/evidence fusion/anomalies/RAG.
-- Kept in the same database, separate tables, so none of the above is touched.
CREATE TABLE IF NOT EXISTS classrooms (
    classroom_id TEXT PRIMARY KEY, name TEXT NOT NULL, building TEXT, floor INTEGER,
    x REAL NOT NULL, y REAL NOT NULL, ble_marker_id TEXT, display_name TEXT);
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY, course TEXT NOT NULL, classroom_id TEXT NOT NULL,
    start_ts REAL NOT NULL, end_ts REAL);
CREATE INDEX IF NOT EXISTS sessions_classroom ON sessions (classroom_id, start_ts);
CREATE TABLE IF NOT EXISTS peer_observations (
    ts REAL NOT NULL, observer_student_id TEXT NOT NULL, observed_student_id TEXT,
    observed_token TEXT NOT NULL, rssi INTEGER NOT NULL, duration_s REAL, source TEXT NOT NULL DEFAULT 'live');
CREATE INDEX IF NOT EXISTS peer_obs_ts ON peer_observations (ts);
CREATE INDEX IF NOT EXISTS peer_obs_observed ON peer_observations (observed_student_id, ts);
CREATE TABLE IF NOT EXISTS wifi_fingerprint_samples (
    ts REAL NOT NULL, zone TEXT NOT NULL, bssid TEXT NOT NULL, rssi INTEGER NOT NULL,
    source TEXT NOT NULL DEFAULT 'live');
CREATE INDEX IF NOT EXISTS wifi_fp_zone ON wifi_fingerprint_samples (zone);
CREATE TABLE IF NOT EXISTS evidence_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT, student_id TEXT NOT NULL, session_id TEXT, classroom_id TEXT,
    ts REAL NOT NULL, score REAL NOT NULL, state TEXT NOT NULL, breakdown TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'live');
CREATE INDEX IF NOT EXISTS evidence_student_ts ON evidence_records (student_id, ts);
CREATE TABLE IF NOT EXISTS anomalies (
    id INTEGER PRIMARY KEY AUTOINCREMENT, student_id TEXT NOT NULL, ts REAL NOT NULL,
    type TEXT NOT NULL, severity TEXT NOT NULL, iforest_score REAL, is_anomalous INTEGER NOT NULL,
    reasons TEXT NOT NULL, features TEXT NOT NULL, explanation TEXT, status TEXT NOT NULL DEFAULT 'open');
CREATE INDEX IF NOT EXISTS anomalies_student_ts ON anomalies (student_id, ts);
CREATE INDEX IF NOT EXISTS anomalies_status ON anomalies (status);
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT, anomaly_id INTEGER NOT NULL, decision TEXT NOT NULL,
    comment TEXT, ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS verified_cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT, case_type TEXT NOT NULL, issue TEXT NOT NULL,
    resolution TEXT NOT NULL, features TEXT, anomaly_id INTEGER, ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS face_scans (
    student_id TEXT NOT NULL, ts REAL NOT NULL, match INTEGER NOT NULL, confidence REAL);
CREATE INDEX IF NOT EXISTS face_scans_student ON face_scans (student_id, ts);
CREATE TABLE IF NOT EXISTS rfid_reads (
    student_id TEXT, reader TEXT NOT NULL, ts REAL NOT NULL);
CREATE INDEX IF NOT EXISTS rfid_student ON rfid_reads (student_id, ts);
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


class ClassroomRow(NamedTuple):
    classroom_id: str
    name: str
    building: str | None
    floor: int | None
    x: float
    y: float
    ble_marker_id: str | None
    display_name: str | None


class SessionRow(NamedTuple):
    session_id: str
    course: str
    classroom_id: str
    start_ts: float
    end_ts: float | None


class PeerObservationRow(NamedTuple):
    ts: float
    observer_student_id: str
    observed_student_id: str | None
    observed_token: str
    rssi: int
    duration_s: float | None
    source: str


class WifiFingerprintSample(NamedTuple):
    ts: float
    zone: str
    bssid: str
    rssi: int
    source: str


class EvidenceRow(NamedTuple):
    id: int
    student_id: str
    session_id: str | None
    classroom_id: str | None
    ts: float
    score: float
    state: str
    breakdown: str
    source: str


class AnomalyRow(NamedTuple):
    id: int
    student_id: str
    ts: float
    type: str
    severity: str
    iforest_score: float | None
    is_anomalous: bool
    reasons: str
    features: str
    explanation: str | None
    status: str


class FeedbackRow(NamedTuple):
    id: int
    anomaly_id: int
    decision: str
    comment: str | None
    ts: float


class VerifiedCaseRow(NamedTuple):
    id: int
    case_type: str
    issue: str
    resolution: str
    features: str | None
    anomaly_id: int | None
    ts: float


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

    # ---- classrooms / sessions (Proof-of-Presence extension) ---------------

    def upsert_classroom(self, row: ClassroomRow) -> None:
        self._write(
            "INSERT INTO classrooms VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(classroom_id) DO UPDATE SET "
            "name=excluded.name, building=excluded.building, floor=excluded.floor, x=excluded.x, "
            "y=excluded.y, ble_marker_id=excluded.ble_marker_id, display_name=excluded.display_name",
            tuple(row),
        )

    def classrooms(self) -> dict[str, ClassroomRow]:
        sql = "SELECT classroom_id, name, building, floor, x, y, ble_marker_id, display_name FROM classrooms"
        return {r[0]: ClassroomRow(*r) for r in self._read(sql)}

    def upsert_session(self, row: SessionRow) -> None:
        self._write(
            "INSERT INTO sessions VALUES (?,?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET "
            "course=excluded.course, classroom_id=excluded.classroom_id, start_ts=excluded.start_ts, "
            "end_ts=excluded.end_ts",
            tuple(row),
        )

    def sessions(self, active_at: float | None = None) -> list[SessionRow]:
        sql = "SELECT session_id, course, classroom_id, start_ts, end_ts FROM sessions"
        params: tuple = ()
        if active_at is not None:
            sql += " WHERE start_ts <= ? AND (end_ts IS NULL OR end_ts >= ?)"
            params = (active_at, active_at)
        return [SessionRow(*r) for r in self._read(sql + " ORDER BY start_ts", params)]

    # ---- peer BLE observations ----------------------------------------------

    def add_peer_observations(self, rows: Iterable[PeerObservationRow]) -> None:
        with self._lock, self._conn:
            self._conn.executemany("INSERT INTO peer_observations VALUES (?,?,?,?,?,?,?)", list(rows))

    def peer_observations_between(self, t_lo: float, t_hi: float) -> list[PeerObservationRow]:
        sql = ("SELECT ts, observer_student_id, observed_student_id, observed_token, rssi, duration_s, source "
               "FROM peer_observations WHERE ts > ? AND ts <= ? ORDER BY ts")
        return [PeerObservationRow(*r) for r in self._read(sql, (t_lo, t_hi))]

    # ---- Wi-Fi fingerprint training samples ---------------------------------

    def add_wifi_fingerprint_samples(self, rows: Iterable[WifiFingerprintSample]) -> None:
        with self._lock, self._conn:
            self._conn.executemany("INSERT INTO wifi_fingerprint_samples VALUES (?,?,?,?,?)", list(rows))

    def wifi_fingerprint_samples(self) -> list[WifiFingerprintSample]:
        sql = "SELECT ts, zone, bssid, rssi, source FROM wifi_fingerprint_samples ORDER BY ts"
        return [WifiFingerprintSample(*r) for r in self._read(sql)]

    # ---- evidence records ----------------------------------------------------

    def add_evidence(self, student_id: str, session_id: str | None, classroom_id: str | None,
                      ts: float, score: float, state: str, breakdown: dict, source: str = "live") -> int:
        cur = self._write(
            "INSERT INTO evidence_records (student_id, session_id, classroom_id, ts, score, state, "
            "breakdown, source) VALUES (?,?,?,?,?,?,?,?)",
            (student_id, session_id, classroom_id, ts, score, state, json.dumps(breakdown), source),
        )
        return cur.lastrowid

    def latest_evidence(self, student_id: str, at_or_before: float | None = None) -> EvidenceRow | None:
        sql = ("SELECT id, student_id, session_id, classroom_id, ts, score, state, breakdown, source "
               "FROM evidence_records WHERE student_id=?")
        params: tuple = (student_id,)
        if at_or_before is not None:
            sql += " AND ts<=?"
            params += (at_or_before,)
        rows = self._read(sql + " ORDER BY ts DESC LIMIT 1", params)
        return EvidenceRow(*rows[0]) if rows else None

    def evidence_history(self, student_id: str, t_lo: float, t_hi: float) -> list[EvidenceRow]:
        sql = ("SELECT id, student_id, session_id, classroom_id, ts, score, state, breakdown, source "
               "FROM evidence_records WHERE student_id=? AND ts>? AND ts<=? ORDER BY ts")
        return [EvidenceRow(*r) for r in self._read(sql, (student_id, t_lo, t_hi))]

    # ---- anomalies / feedback / verified cases -------------------------------

    def add_anomaly(self, student_id: str, ts: float, type_: str, severity: str,
                     iforest_score: float | None, is_anomalous: bool, reasons: list[str],
                     features: dict, explanation: str | None = None) -> int:
        cur = self._write(
            "INSERT INTO anomalies (student_id, ts, type, severity, iforest_score, is_anomalous, "
            "reasons, features, explanation, status) VALUES (?,?,?,?,?,?,?,?,?,'open')",
            (student_id, ts, type_, severity, iforest_score, int(is_anomalous),
             json.dumps(reasons), json.dumps(features), explanation),
        )
        return cur.lastrowid

    def set_anomaly_explanation(self, anomaly_id: int, explanation: str) -> None:
        self._write("UPDATE anomalies SET explanation=? WHERE id=?", (explanation, anomaly_id))

    def set_anomaly_status(self, anomaly_id: int, status: str) -> None:
        self._write("UPDATE anomalies SET status=? WHERE id=?", (status, anomaly_id))

    def anomaly(self, anomaly_id: int) -> AnomalyRow | None:
        sql = ("SELECT id, student_id, ts, type, severity, iforest_score, is_anomalous, reasons, "
               "features, explanation, status FROM anomalies WHERE id=?")
        rows = self._read(sql, (anomaly_id,))
        return self._anomaly_row(rows[0]) if rows else None

    def anomalies(self, status: str | None = None, student_id: str | None = None,
                  limit: int = 200) -> list[AnomalyRow]:
        sql = ("SELECT id, student_id, ts, type, severity, iforest_score, is_anomalous, reasons, "
               "features, explanation, status FROM anomalies WHERE 1=1")
        params: tuple = ()
        if status is not None:
            sql += " AND status=?"
            params += (status,)
        if student_id is not None:
            sql += " AND student_id=?"
            params += (student_id,)
        sql += " ORDER BY ts DESC LIMIT ?"
        params += (limit,)
        return [self._anomaly_row(r) for r in self._read(sql, params)]

    @staticmethod
    def _anomaly_row(r: tuple) -> AnomalyRow:
        return AnomalyRow(r[0], r[1], r[2], r[3], r[4], r[5], bool(r[6]), r[7], r[8], r[9], r[10])

    def add_feedback(self, anomaly_id: int, decision: str, comment: str | None, ts: float) -> int:
        cur = self._write(
            "INSERT INTO feedback (anomaly_id, decision, comment, ts) VALUES (?,?,?,?)",
            (anomaly_id, decision, comment, ts),
        )
        return cur.lastrowid

    def feedback_history(self, limit: int = 200) -> list[FeedbackRow]:
        sql = "SELECT id, anomaly_id, decision, comment, ts FROM feedback ORDER BY ts DESC LIMIT ?"
        return [FeedbackRow(*r) for r in self._read(sql, (limit,))]

    def add_verified_case(self, case_type: str, issue: str, resolution: str,
                           features: dict | None, anomaly_id: int | None, ts: float) -> int:
        cur = self._write(
            "INSERT INTO verified_cases (case_type, issue, resolution, features, anomaly_id, ts) "
            "VALUES (?,?,?,?,?,?)",
            (case_type, issue, resolution, json.dumps(features) if features is not None else None,
             anomaly_id, ts),
        )
        return cur.lastrowid

    def verified_cases(self) -> list[VerifiedCaseRow]:
        sql = "SELECT id, case_type, issue, resolution, features, anomaly_id, ts FROM verified_cases ORDER BY ts"
        return [VerifiedCaseRow(*r) for r in self._read(sql)]

    # ---- optional face / RFID signals ---------------------------------------

    def add_face_scan(self, student_id: str, ts: float, match: bool, confidence: float | None) -> None:
        self._write("INSERT INTO face_scans VALUES (?,?,?,?)", (student_id, ts, int(match), confidence))

    def latest_face_scan(self, student_id: str, at_or_before: float, max_age_s: float) -> tuple[bool, float | None] | None:
        rows = self._read(
            "SELECT match, confidence, ts FROM face_scans WHERE student_id=? AND ts<=? ORDER BY ts DESC LIMIT 1",
            (student_id, at_or_before),
        )
        if not rows or at_or_before - rows[0][2] > max_age_s:
            return None
        return bool(rows[0][0]), rows[0][1]

    def add_rfid_read(self, student_id: str | None, reader: str, ts: float) -> None:
        self._write("INSERT INTO rfid_reads VALUES (?,?,?)", (student_id, reader, ts))

    def latest_rfid_read(self, student_id: str, at_or_before: float, max_age_s: float) -> bool:
        rows = self._read(
            "SELECT ts FROM rfid_reads WHERE student_id=? AND ts<=? ORDER BY ts DESC LIMIT 1",
            (student_id, at_or_before),
        )
        return bool(rows) and (at_or_before - rows[0][0] <= max_age_s)

    def purge_extension_before(self, ts: float) -> int:
        """Same retention policy as purge_before(), for the extension tables."""
        with self._lock, self._conn:
            n = 0
            for table, col in (("peer_observations", "ts"), ("wifi_fingerprint_samples", "ts"),
                               ("evidence_records", "ts"), ("face_scans", "ts"), ("rfid_reads", "ts")):
                n += self._conn.execute(f"DELETE FROM {table} WHERE {col} < ?", (ts,)).rowcount
        return n
