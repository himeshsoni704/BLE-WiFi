"""Ingestion and evaluation: scans and reports in, verdicts out."""
from __future__ import annotations

import logging
import math
import statistics
import time
from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence

import numpy as np

from .config import Settings
from .dwell import Dwell, aggregate
from .features import extract_windows, motion_level, window_length
from .fusion import Evidence, State, Verdict, fuse
from .owner_model import STUDENT_ID_RE, ModelError, ModelRegistry
from .store import ReportRow, ScanRow, Store
from .tokens import TokenResolver, new_secret

log = logging.getLogger(__name__)

MAX_SLOTS = 2000


class UnknownScanner(Exception):
    pass


class DuplicateStudent(Exception):
    pass


@dataclass
class IngestResult:
    accepted: int = 0
    unknown_token: int = 0
    bad_timestamp: int = 0


@dataclass
class ReportData:
    token: str
    ts: float | None = None
    wifi_bssid: str | None = None
    wifi_rssi: int | None = None
    accel: np.ndarray | None = None       # (n, 3) m/s^2
    gyro: np.ndarray | None = None        # (n, 3) rad/s
    fs: float | None = None
    interacting: bool = False


@dataclass
class ReportResult:
    student_id: str
    wifi_zone: str | None
    moving: bool | None
    window_score: float | None            # this report's owner probability
    owner_score: float | None             # smoothed score after this report


class Engine:
    def __init__(
        self,
        store: Store,
        models: ModelRegistry,
        settings: Settings,
        clock: Callable[[], float] = time.time,
    ):
        self.store, self.models, self.settings, self.clock = store, models, settings, clock
        self.tokens = TokenResolver(
            store.student_secrets, settings.token_window_s, settings.token_skew_windows
        )

    # ---- admin -------------------------------------------------------------

    def enroll_student(self, student_id: str, name: str) -> str:
        """Register a student and return their token secret (shown once, for the app)."""
        if not STUDENT_ID_RE.match(student_id):
            raise ValueError("student_id must be 1-64 chars of letters, digits, '_', '.', '-'")
        secret = new_secret()
        if not self.store.add_student(student_id, name, secret, self.clock()):
            raise DuplicateStudent(student_id)
        self.tokens.invalidate()
        return secret

    def has_model(self, student_id: str) -> bool:
        return self.models.path(student_id).exists()

    def purge_old_data(self) -> int:
        return self.store.purge_before(self.clock() - self.settings.retention_s)

    # ---- ingestion ---------------------------------------------------------

    def _timestamp_ok(self, ts: float) -> bool:
        limit = self.settings.max_clock_skew_s
        return limit <= 0 or abs(ts - self.clock()) <= limit

    def ingest_scans(
        self, scanner_id: str, items: Iterable[tuple[str, int, float | None]]
    ) -> IngestResult:
        """items: (token, rssi, ts or None for server time)."""
        scanner = self.store.scanners().get(scanner_id)
        if scanner is None:
            raise UnknownScanner(scanner_id)
        result, rows = IngestResult(), []
        for token, rssi, ts in items:
            ts = self.clock() if ts is None else ts
            if not self._timestamp_ok(ts):
                result.bad_timestamp += 1
                continue
            student = self.tokens.resolve(token, ts)
            if student is None:
                result.unknown_token += 1
                continue
            rows.append(ScanRow(ts, student, scanner_id, scanner.zone, rssi))
        self.store.add_scans(rows)
        result.accepted = len(rows)
        return result

    def _owner_window_score(
        self, student_id: str, accel: np.ndarray, gyro: np.ndarray, fs: float
    ) -> float | None:
        if len(accel) < window_length(fs):
            return None
        try:
            model = self.models.get(student_id)
        except ModelError as exc:
            log.warning("ignoring unusable model for %s: %s", student_id, exc)
            return None
        if model is None:
            return None
        feats, motion = extract_windows(accel, gyro, fs)
        walking = motion >= self.settings.walking_threshold
        if not walking.any():
            return None
        return float(model.score(feats[walking]).mean())

    def ingest_report(self, data: ReportData) -> ReportResult | None:
        """Process a phone's report. Returns None if the token is not recognised
        or the timestamp is implausible."""
        s = self.settings
        ts = self.clock() if data.ts is None else data.ts
        if not self._timestamp_ok(ts):
            return None
        student = self.tokens.resolve(data.token, ts)
        if student is None:
            return None

        prev = self.store.latest_report(student, ts)
        continuous = prev is not None and ts - prev.ts <= s.report_gap_reset_s

        moving: bool | None = None
        window_score: float | None = None
        if data.accel is not None and data.gyro is not None and data.fs:
            moving = motion_level(data.accel) >= s.motion_threshold
            window_score = self._owner_window_score(student, data.accel, data.gyro, data.fs)

        if moving:
            last_motion_ts = ts
        elif continuous:
            last_motion_ts = prev.last_motion_ts
        else:
            last_motion_ts = ts               # can't vouch for the gap; restart the timer

        owner_ema, owner_ema_ts = (prev.owner_ema, prev.owner_ema_ts) if prev else (None, None)
        if window_score is not None:
            fresh_prev = owner_ema is not None and ts - owner_ema_ts <= s.owner_max_age_s
            owner_ema = (
                s.owner_ema_alpha * window_score + (1 - s.owner_ema_alpha) * owner_ema
                if fresh_prev else window_score
            )
            owner_ema_ts = ts

        interaction_ts = ts if data.interacting else (prev.interaction_ts if prev else None)
        wifi_zone = self.store.bssid_zone(data.wifi_bssid) if data.wifi_bssid else None
        self.store.add_report(ReportRow(
            ts, student, wifi_zone, data.wifi_rssi, owner_ema, owner_ema_ts,
            last_motion_ts, interaction_ts,
        ))
        return ReportResult(student, wifi_zone, moving, window_score, owner_ema)

    # ---- evaluation --------------------------------------------------------

    def evidence_series(
        self, times: Sequence[float], student_ids: Sequence[str] | None = None
    ) -> list[dict[str, Evidence]]:
        """Evidence for each student at each time, loading data with two queries."""
        s = self.settings
        if not times:
            return []
        ids = list(student_ids) if student_ids is not None else self.store.student_ids()
        wanted = set(ids)
        t_lo = min(times) - max(s.scan_window_s, s.report_fresh_s)
        t_hi = max(times)
        scanners = self.store.scanners()

        scans: dict[str, list[ScanRow]] = defaultdict(list)
        for row in self.store.scans_between(t_lo, t_hi):
            if row.student_id in wanted:
                scans[row.student_id].append(row)
        reports: dict[str, list[ReportRow]] = defaultdict(list)
        for row in self.store.reports_between(t_lo, t_hi):
            if row.student_id in wanted:
                reports[row.student_id].append(row)
        scan_ts = {sid: [r.ts for r in rows] for sid, rows in scans.items()}
        report_ts = {sid: [r.ts for r in rows] for sid, rows in reports.items()}

        out = []
        for t in times:
            per_student = {}
            for sid in ids:
                per_student[sid] = Evidence(
                    ble_zone=self._ble_zone(t, scans.get(sid, ()), scan_ts.get(sid, ()), scanners),
                    **self._report_evidence(t, reports.get(sid, ()), report_ts.get(sid, ())),
                )
            out.append(per_student)
        return out

    def _ble_zone(self, t, rows, row_ts, scanners) -> str | None:
        s = self.settings
        lo, hi = bisect_right(row_ts, t - s.scan_window_s), bisect_right(row_ts, t)
        by_scanner: dict[tuple[str, str], list[int]] = defaultdict(list)
        for r in rows[lo:hi]:
            by_scanner[(r.scanner_id, r.zone)].append(r.rssi)
        best_zone, best_rssi = None, -math.inf
        for (scanner_id, zone), rssis in by_scanner.items():
            if len(rssis) < s.min_scan_samples:
                continue
            median = statistics.median(rssis)
            info = scanners.get(scanner_id)
            floor = info.min_rssi if info and info.min_rssi is not None else s.default_min_rssi
            if median >= floor and median > best_rssi:
                best_zone, best_rssi = zone, median
        return best_zone

    def _report_evidence(self, t, rows, row_ts) -> dict:
        s = self.settings
        i = bisect_right(row_ts, t) - 1
        if i < 0 or t - row_ts[i] > s.report_fresh_s:
            return {}
        r = rows[i]
        owner = r.owner_ema
        if owner is not None and t - r.owner_ema_ts > s.owner_max_age_s:
            owner = None
        return {
            "wifi_zone": r.wifi_zone,
            "owner_score": owner,
            "stationary_s": max(0.0, t - r.last_motion_ts),
            "interaction_recent": (
                r.interaction_ts is not None and t - r.interaction_ts <= s.interaction_window_s
            ),
        }

    def verdicts_at(
        self, t: float | None = None, student_ids: Sequence[str] | None = None
    ) -> dict[str, Verdict]:
        t = self.clock() if t is None else t
        evidence = self.evidence_series([t], student_ids)[0]
        return {sid: fuse(ev, self.settings.fusion) for sid, ev in evidence.items()}

    def dwell(
        self,
        start: float,
        end: float,
        zones: Sequence[str] | None = None,
        student_ids: Sequence[str] | None = None,
    ) -> dict[str, Dwell]:
        cfg = self.settings.dwell
        if end <= start:
            raise ValueError("end must be after start")
        n = math.ceil((end - start) / cfg.slot_s)
        if n > MAX_SLOTS:
            raise ValueError(f"period too long: {n} slots (max {MAX_SLOTS})")
        times = [min(end, start + (k + 1) * cfg.slot_s) for k in range(n)]
        series = self.evidence_series(times, student_ids)
        per_student: dict[str, list[Verdict]] = defaultdict(list)
        for snapshot in series:
            for sid, ev in snapshot.items():
                per_student[sid].append(fuse(ev, self.settings.fusion))
        return {sid: aggregate(vs, cfg, zones) for sid, vs in per_student.items()}

    def zone_snapshot(self, at: float | None = None) -> dict:
        t = self.clock() if at is None else at
        zones: dict[str, list[dict]] = {z: [] for z in self.store.zones()}
        counts: Counter = Counter()
        not_detected: list[str] = []
        for sid, v in self.verdicts_at(t).items():
            counts[v.state.value] += 1
            if v.zone is None:
                not_detected.append(sid)
            else:
                zones.setdefault(v.zone, []).append(
                    {"student_id": sid, "state": v.state.value, "risk": v.risk}
                )
        return {
            "ts": t,
            "counts": {st.value: counts.get(st.value, 0) for st in State},
            "zones": {z: sorted(rows, key=lambda r: r["student_id"]) for z, rows in zones.items()},
            "not_detected": not_detected,
        }
