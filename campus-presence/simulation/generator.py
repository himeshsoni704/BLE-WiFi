"""Synthetic campus behaviour: 300 students, 20 classrooms, 3 sessions, 6 anomaly scenarios.

EVERYTHING here is SIMULATED and every row written is tagged is_simulated=True. The behaviour
parameters (arrival jitter, drop-out rates, RSSI noise, AP loss...) are this project's own
assumptions, not measurements of any real campus. The simulator writes observation rows
directly (bypassing HTTP/JWT) and then runs the same pipeline that live data goes through.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core import Services
from app.models import (Anomaly, Attendance, Classroom, Enrollment, Location, Observation, SessionRow,
                        Student)
from app.pipeline import evaluate_session
from app.security import new_token_secret, student_key_for
from app.seed import seed_campus
from app.tokens import student_token, window_index

from . import campus

SLOTS = 3
SESSION_MIN = 50
SLOT_SPACING_S = 3600
DEPTS = ["CS", "EE", "ME", "MA", "PH", "CH"]
ANOMALY_SCENARIOS = ("proxy", "impossible_movement", "wifi_ble_mismatch", "token_replay", "short_presence")
ALL_SCENARIOS = ANOMALY_SCENARIOS + ("false_positive", "normal")
Progress = Callable[[float, str], None]


def _noop(_f: float, _m: str) -> None:
    pass


@dataclass
class Batch:
    obs: list[dict] = field(default_factory=list)
    locs: list[dict] = field(default_factory=list)
    wifi_pending: list[tuple[dict, dict, bool]] = field(default_factory=list)   # (obs_row, fingerprint, make_location)
    counter: int = 0


class CampusSim:
    def __init__(self, svc: Services, n_students: int = 300, seed: int = 1, sample_interval_s: int = 300):
        self.svc = svc
        self.n = n_students
        self.seed = seed
        self.interval = sample_interval_s
        self.rng = np.random.default_rng(seed)
        self.sessions: dict[int, SessionRow] = {}
        self.enrolled: dict[int, list[str]] = {}
        self.secret: dict[str, str] = {}
        self.sid: dict[str, str] = {}
        self.labels: dict[tuple[int, str], str] = {}
        self._nonce = 0

    # ---- setup / teardown ---------------------------------------------------------------

    def reset(self, db: Session) -> dict:
        """Delete simulated rows only. Verified cases (faculty knowledge) are deliberately kept."""
        sim_sessions = select(SessionRow.id).where(SessionRow.is_simulated == True)    # noqa: E712
        counts = {}
        for name, stmt in (
            ("anomalies", delete(Anomaly).where(Anomaly.is_simulated == True)),        # noqa: E712
            ("attendance", delete(Attendance).where(Attendance.is_simulated == True)),  # noqa: E712
            ("locations", delete(Location).where(Location.is_simulated == True)),       # noqa: E712
            ("observations", delete(Observation).where(Observation.is_simulated == True)),  # noqa: E712
            ("enrollments", delete(Enrollment).where(Enrollment.session_id.in_(sim_sessions))),
            ("sessions", delete(SessionRow).where(SessionRow.is_simulated == True)),    # noqa: E712
            ("students", delete(Student).where(Student.is_simulated == True)),          # noqa: E712
        ):
            counts[name] = db.execute(stmt).rowcount
        db.flush()
        self.svc.tokens.invalidate()
        self.sessions.clear(); self.enrolled.clear(); self.secret.clear(); self.sid.clear(); self.labels.clear()
        return counts

    def load(self, db: Session) -> bool:
        """Rebuild in-memory maps from the database (after a restart). False if no simulation exists."""
        sess = db.scalars(select(SessionRow).where(SessionRow.is_simulated == True)).all()    # noqa: E712
        if not sess:
            return False
        self.sessions = {s.id: s for s in sess}
        self.enrolled = defaultdict(list)
        for e in db.scalars(select(Enrollment).where(Enrollment.session_id.in_(list(self.sessions)))):
            self.enrolled[e.session_id].append(e.student_key)
        for st in db.scalars(select(Student).where(Student.is_simulated == True)):         # noqa: E712
            self.secret[st.student_key] = st.token_secret
            self.sid[st.student_key] = st.student_id
        return True

    def build(self, db: Session) -> None:
        settings = self.svc.settings
        seed_campus(db, settings)
        now = self.svc.clock()
        t0 = math.floor(now / 3600) * 3600 - 3 * 3600
        rooms = [z.id for z in campus.ROOMS]
        for i in range(self.n):
            student_id = f"SIM{i + 1:03d}"
            key = student_key_for(student_id, settings.pepper)
            secret = new_token_secret()
            db.add(Student(student_key=key, student_id=student_id, name=f"Sim Student {i + 1:03d}",
                           token_secret=secret, is_simulated=True))
            self.secret[key], self.sid[key] = secret, student_id
        db.flush()
        for slot in range(SLOTS):
            start = t0 + slot * SLOT_SPACING_S
            for r, room in enumerate(rooms):
                code = f"{DEPTS[(r + slot) % len(DEPTS)]}{100 + 10 * ((r * 3 + slot * 7) % 40)}"
                s = SessionRow(code=code, title=f"{code} (simulated)", classroom_id=room, start_ts=start,
                               end_ts=start + SESSION_MIN * 60, is_simulated=True)
                db.add(s)
                db.flush()
                self.sessions[s.id] = s
                self.enrolled[s.id] = []
            slot_sessions = [s for s in self.sessions.values() if s.start_ts == start]
            for key in self.secret:
                if self.rng.random() < 0.88:
                    s = slot_sessions[int(self.rng.integers(len(slot_sessions)))]
                    db.add(Enrollment(session_id=s.id, student_key=key))
                    self.enrolled[s.id].append(key)
        db.flush()
        self.svc.tokens.invalidate()

    # ---- row generation -------------------------------------------------------------------

    def _nonce_str(self) -> str:
        self._nonce += 1
        return f"sim-{self.seed}-{self._nonce}-{int(self.rng.integers(1 << 30)):x}"

    def _grid(self, session: SessionRow) -> list[float]:
        n = max(2, int((session.end_ts - session.start_ts) // self.interval))
        return [session.start_ts + (k + 1) * self.interval - 30 + float(self.rng.uniform(-10, 10)) for k in range(n)]

    def _add_marker(self, b: Batch, session: SessionRow, key: str, ts: float, rssi: float, duration: float,
                    room: str | None = None) -> None:
        room = room or session.classroom_id
        z = campus.ZONE_BY_ID[room]
        b.obs.append(dict(kind="ble_marker", session_id=session.id, student_key=key, ts=ts, duration=duration,
                          rssi=round(rssi, 1), samples=int(duration * self.rng.uniform(8, 12)),
                          marker_idx=z.marker_idx, room=room, nonce=self._nonce_str(), is_simulated=True))
        b.locs.append(dict(student_key=key, ts=ts, zone=room, x=z.x, y=z.y, signal="ble_marker",
                           confidence=None, session_id=session.id, is_simulated=True))

    def _add_wifi(self, b: Batch, session: SessionRow | None, key: str, ts: float, fp: dict) -> None:
        cached = self.rng.random() < 0.10
        row = dict(kind="wifi", session_id=session.id if session else None, student_key=key, ts=ts,
                   wifi_json=json.dumps(fp), wifi_zone=None, wifi_conf=None,
                   wifi_source="cached" if cached else "scan",
                   scan_age_s=float(self.rng.uniform(5, 60)) if cached else 0.0,
                   samples=len(fp), nonce=self._nonce_str(), is_simulated=True)
        b.obs.append(row)
        b.wifi_pending.append((row, fp, True))

    def _add_peer(self, b: Batch, session: SessionRow, observer: str, observed: str, ts: float, rssi: float,
                  duration: float) -> None:
        b.obs.append(dict(kind="ble_peer", session_id=session.id, student_key=observer, ts=ts,
                          duration=round(duration, 1), rssi=round(rssi, 1),
                          samples=int(duration * self.rng.uniform(6, 12)),
                          observed_token=student_token(self.secret[observed], window_index(ts, self.svc.settings.token_window_s)),
                          observed_student_key=observed, nonce=self._nonce_str(), is_simulated=True))

    def _student_samples(self, b: Batch, session: SessionRow, key: str, grid: list[float], t_in: float,
                         t_out: float, pos: tuple[float, float], present: dict[int, list[str]], *,
                         wifi_mode: str = "normal", marker_series: dict[int, float] | None = None,
                         exact_ts: bool = False, degraded: dict[str, float] | None = None,
                         peer_rssi_override: dict[str, float] | None = None) -> None:
        rng, room = self.rng, session.classroom_id
        neighbor = self._neighbor(room)
        for gi, g in enumerate(grid):
            if not (t_in <= g <= t_out):
                continue
            ts = g if exact_ts else g + float(rng.uniform(-3, 3))
            x, y = pos[0] + float(rng.normal(0, 0.8)), pos[1] + float(rng.normal(0, 0.6))
            if rng.random() < 0.92 or marker_series is not None:
                r = marker_series[gi] if marker_series is not None else campus.marker_rssi(room, x, y, rng)
                self._add_marker(b, session, key, ts, r, self.interval * float(rng.uniform(0.7, 1.0)))
            if rng.random() < 0.95:
                if wifi_mode == "neighbor":
                    nx, ny = campus.sample_position(neighbor, rng)
                    fp = campus.sample_fingerprint(nx, ny, rng)
                else:
                    fp = campus.sample_fingerprint(x, y, rng, degraded=degraded)
                self._add_wifi(b, session, key, ts + 1, fp)
            others = [k for k in present.get(gi, []) if k != key]
            if others:
                k_peers = min(len(others), 1 + int(rng.poisson(2.2)))
                for peer in rng.choice(others, size=k_peers, replace=False):
                    rssi = (peer_rssi_override or {}).get(peer, campus.peer_rssi(rng))
                    self._add_peer(b, session, key, str(peer), ts + 2, float(np.clip(rssi, -92, -30)),
                                   self.interval * float(rng.uniform(0.4, 0.9)))

    def _neighbor(self, room: str) -> str:
        i = int(room[-2:])
        j = i + 1 if i < 10 else i - 1
        return f"{room[0]}{j:02d}"

    def _flush(self, db: Session, b: Batch) -> None:
        model = self.svc.wifi_sim
        if model is None:
            raise RuntimeError("simulated Wi-Fi model missing: run `python ml/train_wifi_model.py` first")
        preds = model.predict_many([fp for _, fp, _ in b.wifi_pending])
        for (row, fp, mk_loc), pr in zip(b.wifi_pending, preds):
            row["wifi_zone"], row["wifi_conf"] = pr.zone, round(pr.confidence, 3)
            if mk_loc:
                z = campus.ZONE_BY_ID.get(pr.zone)
                if z:
                    b.locs.append(dict(student_key=row["student_key"], ts=row["ts"], zone=pr.zone, x=z.x, y=z.y,
                                       signal="wifi", confidence=round(pr.confidence, 3),
                                       session_id=row["session_id"], is_simulated=True))
        if b.obs:
            db.bulk_insert_mappings(Observation, b.obs)
        if b.locs:
            db.bulk_insert_mappings(Location, b.locs)
        db.flush()
        b.obs.clear(); b.locs.clear(); b.wifi_pending.clear()

    def _clear_student(self, db: Session, session: SessionRow, key: str) -> None:
        lo, hi = session.start_ts - 600, session.end_ts + 300
        db.execute(delete(Observation).where(Observation.student_key == key, Observation.is_simulated == True,   # noqa: E712
                                             Observation.ts >= lo, Observation.ts <= hi))
        db.execute(delete(Observation).where(Observation.observed_student_key == key, Observation.is_simulated == True,  # noqa: E712
                                             Observation.ts >= lo, Observation.ts <= hi))
        db.execute(delete(Location).where(Location.student_key == key, Location.is_simulated == True,           # noqa: E712
                                          Location.ts >= lo, Location.ts <= hi))

    # ---- normal behaviour -----------------------------------------------------------------

    def generate_normal(self, db: Session, session_ids: list[int] | None = None, progress: Progress = _noop) -> dict:
        ids = session_ids or sorted(self.sessions)
        total_obs = 0
        for n, sid in enumerate(ids):
          with self.svc.write_lock:                       # short critical section per session
            s = self.sessions[sid]
            grid = self._grid(s)
            plan: dict[str, tuple[float, float, tuple[float, float]]] = {}
            for key in self.enrolled[sid]:
                if self.rng.random() < 0.10:
                    continue                                    # absent: no evidence at all
                t_in = s.start_ts + float(self.rng.uniform(-90, 360))
                t_out = s.end_ts - float(self.rng.uniform(0, 120))
                plan[key] = (t_in, t_out, campus.sample_position(s.classroom_id, self.rng))
                self.labels[(sid, key)] = "normal"
            present = {gi: [k for k, (a, c, _) in plan.items() if a <= g <= c] for gi, g in enumerate(grid)}
            b = Batch()
            for key, (t_in, t_out, pos) in plan.items():
                deg = None
                if self.rng.random() < 0.05:                    # temporary AP weakness, mild
                    deg = {str(self.rng.choice(campus.AP_IDS)): float(self.rng.uniform(5, 12))}
                self._student_samples(b, s, key, grid, t_in, t_out, pos, present, degraded=deg)
            self._flush(db, b)
            self._between_sessions(db, s, list(plan))
            db.commit()
          progress((n + 1) / len(ids), f"normal behaviour: session {n + 1}/{len(ids)}")
        return {"sessions": len(ids)}

    def _between_sessions(self, db: Session, s: SessionRow, keys: list[str]) -> None:
        """Corridor Wi-Fi sample in the break after the session, for students who attended."""
        b = Batch()
        zone = "COR-1" if s.classroom_id.startswith("1") else "COR-2"
        for key in keys:
            if self.rng.random() < 0.7:
                x, y = campus.sample_position(zone, self.rng)
                self._add_wifi(b, None, key, s.end_ts + float(self.rng.uniform(150, 450)),
                               campus.sample_fingerprint(x, y, self.rng))
        self._flush(db, b)

    # ---- scenarios ------------------------------------------------------------------------

    def _pick_session(self, name_hint: int | None = None, min_students: int = 8) -> SessionRow:
        pool = [s for s in self.sessions.values() if len([k for k in self.enrolled[s.id]
                                                           if self.labels.get((s.id, k)) == "normal"]) >= min_students]
        if not pool:
            raise RuntimeError("no simulated sessions with enough attending students; start the simulation first")
        latest = max(s.start_ts for s in pool)
        pool = [s for s in pool if s.start_ts == latest] or pool       # prefer the most recent slot
        return pool[int(self.rng.integers(len(pool)))]

    def _normal_students(self, s: SessionRow, n: int, exclude: set[str] = frozenset()) -> list[str]:
        cand = [k for k in self.enrolled[s.id] if self.labels.get((s.id, k)) == "normal" and k not in exclude]
        if len(cand) < n:
            raise RuntimeError(f"session {s.code} has only {len(cand)} attending students")
        return [str(k) for k in self.rng.choice(cand, size=n, replace=False)]

    def _present_map(self, db: Session, s: SessionRow, grid: list[float], keys: list[str]) -> dict[int, list[str]]:
        """Who else is in the room at each grid time (for peer sampling): approximated by whole-session presence."""
        return {gi: [k for k in self.enrolled[s.id] if self.labels.get((s.id, k)) in ("normal",) or k in keys]
                for gi in range(len(grid))}

    def scenario(self, db: Session, name: str, session: SessionRow | None = None, evaluate: bool = True) -> dict:
        if name not in ALL_SCENARIOS:
            raise ValueError(f"unknown scenario {name!r}")
        s = session or self._pick_session()
        grid = self._grid(s)
        b = Batch()
        targets: list[str] = []
        detail: dict = {}

        if name == "normal":
            targets = self._normal_students(s, 1)
            self._clear_student(db, s, targets[0])
            present = self._present_map(db, s, grid, targets)
            self._student_samples(b, s, targets[0], grid, s.start_ts, s.end_ts - 60,
                                  campus.sample_position(s.classroom_id, self.rng), present)

        elif name == "short_presence":
            targets = self._normal_students(s, 1)
            key = targets[0]
            self._clear_student(db, s, key)
            present = self._present_map(db, s, grid, targets)
            late = s.start_ts + 0.80 * (s.end_ts - s.start_ts)
            self._student_samples(b, s, key, grid, late, s.end_ts, campus.sample_position(s.classroom_id, self.rng), present)
            detail = {"present_from_fraction": 0.8}

        elif name in ("wifi_ble_mismatch", "false_positive"):
            targets = self._normal_students(s, 1)
            key = targets[0]
            self._clear_student(db, s, key)
            present = self._present_map(db, s, grid, targets)
            pos = campus.sample_position(s.classroom_id, self.rng)
            if name == "wifi_ble_mismatch":
                self._student_samples(b, s, key, grid, s.start_ts, s.end_ts - 60, pos, present, wifi_mode="neighbor")
                detail = {"wifi_pointing_at": self._neighbor(s.classroom_id)}
            else:
                ap, att = self._degrade_for_false_positive(s.classroom_id, pos)
                self._student_samples(b, s, key, grid, s.start_ts, s.end_ts - 60, pos, present, degraded={ap: att})
                detail = {"degraded_ap": ap, "extra_attenuation_db": round(att, 1),
                          "meaning": "benign: the student IS present; one AP was unstable"}

        elif name == "impossible_movement":
            targets = self._normal_students(s, 1)
            key = targets[0]
            self._clear_student(db, s, key)
            present = self._present_map(db, s, grid, targets)
            self._student_samples(b, s, key, grid, s.start_ts, s.end_ts - 60,
                                  campus.sample_position(s.classroom_id, self.rng), present)
            far = max((z.id for z in campus.ROOMS), key=lambda r: campus.zone_distance(r, s.classroom_id))
            t = grid[len(grid) // 2]
            self._add_marker(b, s, key, t + 18, -58.0, 20.0, room=far)
            detail = {"far_room": far, "seconds_apart": 18,
                      "distance_m": round(campus.zone_distance(far, s.classroom_id), 1)}

        elif name == "proxy":
            targets = self._normal_students(s, 4)
            for k in targets:
                self._clear_student(db, s, k)
            present = self._present_map(db, s, grid, targets)
            pos = campus.sample_position(s.classroom_id, self.rng)
            base = {gi: campus.marker_rssi(s.classroom_id, pos[0], pos[1], self.rng) for gi in range(len(grid))}
            for k in targets:
                series = {gi: base[gi] + float(self.rng.normal(0, 0.35)) for gi in base}
                override = {o: float(self.rng.normal(-38, 1.5)) for o in targets if o != k}
                self._student_samples(b, s, k, grid, s.start_ts, s.end_ts - 60, pos, present,
                                      marker_series=series, exact_ts=True, peer_rssi_override=override)
            # the carrier's phones also see each other every sample
            for gi, g in enumerate(grid):
                for k in targets:
                    for o in targets:
                        if o != k:
                            self._add_peer(b, s, k, o, g + 2, float(self.rng.normal(-38, 1.5)), self.interval * 0.9)
            detail = {"phones_carried_together": len(targets)}

        elif name == "token_replay":
            targets = self._normal_students(s, 1)
            key = targets[0]
            self._clear_student(db, s, key)
            present = self._present_map(db, s, grid, targets)
            self._student_samples(b, s, key, grid, s.start_ts, s.end_ts - 60,
                                  campus.sample_position(s.classroom_id, self.rng), present)
            others = [x for x in self.sessions.values() if x.start_ts == s.start_ts and x.classroom_id != s.classroom_id
                      and len([k for k in self.enrolled[x.id] if self.labels.get((x.id, k)) == "normal"]) >= 3]
            far = max(others, key=lambda x: campus.zone_distance(x.classroom_id, s.classroom_id))
            observers = self._normal_students(far, 3)
            for g in grid[2:6]:
                for o in observers:
                    self._add_peer(b, far, o, key, g + 3, float(self.rng.normal(-55, 3)), 20.0)
            detail = {"replayed_in_room": far.classroom_id, "observer_devices": len(observers)}

        self._flush(db, b)
        for k in targets:
            self.labels[(s.id, k)] = name
        out = {"scenario": name, "session_id": s.id, "session_code": s.code, "room": s.classroom_id,
               "target_student_keys": targets, "detail": detail, "anomaly_ids": []}
        if evaluate:
            now = s.end_ts + 60
            evaluate_session(db, self.svc, s, now=now, scenario=name if name != "normal" else None)
            out["anomaly_ids"] = [a.id for a in db.scalars(select(Anomaly).where(
                Anomaly.session_id == s.id, Anomaly.student_key.in_(targets)))]
        return out

    def _degrade_for_false_positive(self, room: str, pos: tuple[float, float]) -> tuple[str, float]:
        """Find an AP and attenuation that makes the Wi-Fi model drift to the neighbour at low confidence."""
        model = self.svc.wifi_sim
        rng = np.random.default_rng(int(self.rng.integers(1 << 30)))
        nearest = sorted(campus.APS, key=lambda a: math.hypot(a.x - pos[0], a.y - pos[1]))[:2]
        best = (nearest[0].ap_id, 20.0)
        for ap in nearest:
            for att in (14, 18, 22, 26, 30):
                hits = low = 0
                for _ in range(12):
                    fp = campus.sample_fingerprint(pos[0], pos[1], rng, degraded={ap.ap_id: att})
                    pr = model.predict(fp)
                    hits += pr.zone != room
                    low += pr.zone != room and pr.confidence < 0.6
                if hits >= 8 and low >= 5:
                    return ap.ap_id, float(att)
                best = (ap.ap_id, float(att))
        return best

    # ---- whole-campus run -------------------------------------------------------------------

    def run_full(self, db: Session, progress: Progress = _noop) -> dict:
        progress(0.0, "resetting previous simulation")
        with self.svc.write_lock:
            self.reset(db)
            db.commit()
        progress(0.03, "creating students, classrooms and sessions")
        with self.svc.write_lock:
            self.build(db)
            db.commit()
        self.generate_normal(db, progress=lambda f, m: progress(0.05 + 0.6 * f, m))
        progress(0.68, "injecting anomaly scenarios")
        injected = []
        for name, count in (("proxy", 1), ("impossible_movement", 2), ("wifi_ble_mismatch", 2),
                            ("token_replay", 2), ("short_presence", 3), ("false_positive", 2)):
            for _ in range(count):
                with self.svc.write_lock:
                    injected.append(self.scenario(db, name, evaluate=False))
                    db.commit()
        progress(0.80, "evaluating every session (fusion, rules, Isolation Forest)")
        for i, s in enumerate(sorted(self.sessions.values(), key=lambda x: (x.start_ts, x.classroom_id))):
            with self.svc.write_lock:
                evaluate_session(db, self.svc, s, now=s.end_ts + 60, publish=False)
                db.commit()
            progress(0.80 + 0.2 * (i + 1) / len(self.sessions), "evaluating sessions")
        # evaluate_session above does not know scenario names; label anomalies of injected targets
        with self.svc.write_lock:
            for inj in injected:
                for a in db.scalars(select(Anomaly).where(Anomaly.session_id == inj["session_id"],
                                                           Anomaly.student_key.in_(inj["target_student_keys"]))):
                    a.scenario = inj["scenario"]
            db.commit()
        return {"students": len(self.secret), "sessions": len(self.sessions),
                "injected": [{k: v for k, v in i.items() if k != "anomaly_ids"} for i in injected]}
