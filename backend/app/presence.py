"""Orchestration layer tying the Proof-of-Presence pieces together: pulls
BLE/Wi-Fi/peer/face/RFID evidence, scores attendance (evidence.py), runs
the two-layer anomaly pipeline (anomaly_rules.py + anomaly_iforest.py),
and drives the RAG+LLM explanation (rag.py + llm.py). main.py's routes are
thin wrappers around this class so the HTTP layer stays simple and this
logic stays independently testable.
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass

import numpy as np

from .anomaly_iforest import AnomalyDetector, AnomalyFeatures
from .anomaly_rules import ObserverSighting, RuleConfig, ZoneSighting, run_all_rules
from .engine import Engine
from .evidence import (
    AttendanceEvidence, BleMarkerEvidence, EvidenceResult, EvidenceWeights,
    FaceEvidence, PeerBleEvidence, RfidEvidence, WifiEvidence, score_attendance,
)
from .llm import LLMProvider
from .rag import CaseRetriever, RetrievedCase, VerifiedCase, anomaly_query_text
from .store import ClassroomRow, PeerObservationRow, SessionRow, WifiFingerprintSample
from .wifi_knn import WifiFingerprintModel

ZONE_HISTORY_LOOKBACK_S = 3 * 3600.0
ZONE_HISTORY_STEP_S = 5 * 60.0
FACE_RFID_MAX_AGE_S = 15 * 60.0
PEER_WINDOW_S = 120.0


class UnknownAnomaly(Exception):
    pass


@dataclass
class AnomalyResult:
    anomaly_id: int | None
    is_anomalous: bool
    severity: str
    reasons: list[str]
    iforest_score: float | None
    demo_risk_score: float | None


class PresenceOrchestrator:
    def __init__(
        self, engine: Engine, llm: LLMProvider,
        wifi_model: WifiFingerprintModel | None = None,
        anomaly_detector: AnomalyDetector | None = None,
        retriever: CaseRetriever | None = None,
        rule_config: RuleConfig = RuleConfig(),
        evidence_weights: EvidenceWeights = EvidenceWeights(),
    ):
        self.engine = engine
        self.store = engine.store
        self.llm = llm
        self.wifi_model = wifi_model
        self.anomaly_detector = anomaly_detector
        self.retriever = retriever or CaseRetriever()
        self.rule_config = rule_config
        self.evidence_weights = evidence_weights
        self.retriever.index([
            VerifiedCase(r.id, r.case_type, r.issue, r.resolution, json.loads(r.features) if r.features else None)
            for r in self.store.verified_cases()
        ])

    # ---- classrooms / sessions ----------------------------------------------

    def classroom_positions(self) -> dict[str, tuple[float, float]]:
        return {cid: (c.x, c.y) for cid, c in self.store.classrooms().items()}

    # ---- evidence (brief section 9) -----------------------------------------

    def _ble_marker_evidence(self, student_id: str, classroom_id: str, at: float) -> BleMarkerEvidence:
        scanners = self.engine.store.scanners()
        marker = next((sid for sid, s in scanners.items() if s.zone == classroom_id), None)
        if marker is None:
            return BleMarkerEvidence()
        lookback = max(0.0, at - ZONE_HISTORY_LOOKBACK_S)
        rows = [r for r in self.store.scans_between(lookback, at)
               if r.student_id == student_id and r.zone == classroom_id]
        if not rows:
            return BleMarkerEvidence()
        rows.sort(key=lambda r: r.ts)
        recent = [r for r in rows if at - r.ts <= self.engine.settings.scan_window_s]
        if not recent:
            return BleMarkerEvidence()
        # continuous duration: walk back from `at` while consecutive sightings of
        # this SAME zone are no more than 2x the scan window apart.
        gap = self.engine.settings.scan_window_s * 2
        start_ts = rows[-1].ts
        for r in reversed(rows[:-1]):
            if start_ts - r.ts > gap:
                break
            start_ts = r.ts
        rssi = sorted(r.rssi for r in recent)[len(recent) // 2]   # median, matching engine._ble_zone
        return BleMarkerEvidence(detected=True, rssi=rssi, duration_s=max(0.0, at - start_ts))

    def _peer_ble_evidence(self, student_id: str, classroom_id: str, at: float) -> PeerBleEvidence:
        rows = [r for r in self.store.peer_observations_between(at - PEER_WINDOW_S, at)
               if r.observed_student_id == student_id]
        nearby = {r.observer_student_id for r in rows}
        if not nearby:
            return PeerBleEvidence()
        consistent = 0
        for observer in nearby:
            ev = self.engine.verdicts_at(at, [observer]).get(observer)
            if ev is not None and ev.zone == classroom_id:
                consistent += 1
        return PeerBleEvidence(nearby_devices=len(nearby), consistent_observations=consistent)

    def _wifi_evidence(self, fingerprint: dict[str, float] | None) -> WifiEvidence:
        if not fingerprint or self.wifi_model is None or not self.wifi_model.is_trained:
            return WifiEvidence()
        pred = self.wifi_model.predict(fingerprint)
        return WifiEvidence(predicted_zone=pred.zone, confidence=pred.confidence)

    def _face_evidence(self, student_id: str, at: float) -> FaceEvidence:
        found = self.store.latest_face_scan(student_id, at, FACE_RFID_MAX_AGE_S)
        if found is None:
            return FaceEvidence(available=False)
        match, confidence = found
        return FaceEvidence(available=True, match=match, confidence=confidence)

    def _rfid_evidence(self, student_id: str, at: float) -> RfidEvidence:
        if not self.store.rfid_ever_read(student_id):
            return RfidEvidence(available=False)
        return RfidEvidence(available=True, detected=self.store.latest_rfid_read(student_id, at, FACE_RFID_MAX_AGE_S))

    def _gather_evidence(self, student_id: str, session_id: str, classroom_id: str, at: float,
                        wifi_fingerprint: dict[str, float] | None) -> AttendanceEvidence:
        return AttendanceEvidence(
            student_id=student_id, session_id=session_id, classroom_id=classroom_id,
            ble_marker=self._ble_marker_evidence(student_id, classroom_id, at),
            peer_ble=self._peer_ble_evidence(student_id, classroom_id, at),
            wifi=self._wifi_evidence(wifi_fingerprint),
            face=self._face_evidence(student_id, at),
            rfid=self._rfid_evidence(student_id, at),
            ts=at,
        )

    def compute_evidence(
        self, student_id: str, session_id: str, classroom_id: str, at: float | None = None,
        wifi_fingerprint: dict[str, float] | None = None, source: str = "live",
    ) -> EvidenceResult:
        at = at if at is not None else time.time()
        ev = self._gather_evidence(student_id, session_id, classroom_id, at, wifi_fingerprint)
        result = score_attendance(ev, self.evidence_weights)
        self.store.add_evidence(student_id, session_id, classroom_id, at, result.score,
                                result.state.value, result.to_dict(), source)
        return result

    # ---- anomaly pipeline (brief sections 15-17) -----------------------------

    def _zone_timeline(self, student_id: str, at: float) -> list[ZoneSighting]:
        times = []
        t = at
        while t > at - ZONE_HISTORY_LOOKBACK_S:
            times.append(t)
            t -= ZONE_HISTORY_STEP_S
        series = self.engine.evidence_series(times, [student_id])
        out = []
        for t, snapshot in zip(times, series):
            ev = snapshot.get(student_id)
            if ev is not None and ev.ble_zone is not None:
                out.append(ZoneSighting(t, ev.ble_zone, "ble_marker"))
        return out

    def _observer_timeline(self, student_id: str, at: float) -> list[ObserverSighting]:
        positions = self.classroom_positions()
        scanners = self.store.scanners()
        out: list[ObserverSighting] = []
        for r in self.store.scans_between(at - self.rule_config.reuse_window_s, at):
            if r.student_id != student_id:
                continue
            pos = positions.get(r.zone)
            if pos is not None:
                out.append(ObserverSighting(r.scanner_id, r.ts, pos[0], pos[1]))
        for r in self.store.peer_observations_between(at - self.rule_config.reuse_window_s, at):
            if r.observed_student_id != student_id:
                continue
            observer_ev = self.engine.verdicts_at(r.ts, [r.observer_student_id]).get(r.observer_student_id)
            pos = positions.get(observer_ev.zone) if observer_ev and observer_ev.zone else None
            if pos is not None:
                out.append(ObserverSighting(r.observer_student_id, r.ts, pos[0], pos[1]))
        _ = scanners   # scanners dict kept for symmetry/future use; positions already cover zone lookup
        return out

    def _features(self, student_id: str, ble: BleMarkerEvidence, wifi: WifiEvidence,
                  peer: PeerBleEvidence, zone_timeline: list[ZoneSighting],
                  token_reuse_count: int, signal_consistency: float) -> AnomalyFeatures:
        speed, time_between, n_rooms, n_switches = 0.0, ble.duration_s, 1, 0
        ordered = sorted(zone_timeline, key=lambda s: s.ts)
        rooms = {s.classroom_id for s in ordered}
        n_rooms = max(1, len(rooms))
        if len(ordered) >= 2:
            n_switches = sum(1 for a, b in zip(ordered, ordered[1:]) if a.classroom_id != b.classroom_id)
            last_change = next(((a, b) for a, b in zip(ordered[::-1], ordered[-2::-1])
                               if a.classroom_id != b.classroom_id), None)
            if last_change:
                a, b = last_change
                positions = self.classroom_positions()
                pa, pb = positions.get(a.classroom_id), positions.get(b.classroom_id)
                if pa and pb:
                    dt = max(1e-6, abs(a.ts - b.ts))
                    speed = math.hypot(pa[0] - pb[0], pa[1] - pb[1]) / dt
                    time_between = dt
        return AnomalyFeatures(
            ble_duration=ble.duration_s, mean_ble_rssi=float(ble.rssi or -100),
            wifi_confidence=wifi.confidence, nearby_device_count=peer.nearby_devices,
            token_reuse_count=token_reuse_count, time_between_locations=time_between,
            estimated_speed=speed, number_of_classrooms=n_rooms, session_switch_count=n_switches,
            signal_consistency=signal_consistency,
        )

    def run_anomaly_check(self, student_id: str, classroom_id: str, ev: AttendanceEvidence,
                          at: float | None = None) -> AnomalyResult:
        """Takes the SAME AttendanceEvidence just gathered for compute_evidence()
        (see process_presence()) rather than re-deriving the Wi-Fi zone from a
        stored evidence record's human-readable text -- that round trip is both
        fragile (string-matches a sentence meant for display) and redundant
        when the caller already has the real WifiEvidence in hand."""
        at = at if at is not None else time.time()
        ble_zone = classroom_id if ev.ble_marker.detected else None
        wifi_zone = ev.wifi.predicted_zone

        zone_timeline = self._zone_timeline(student_id, at)
        observer_timeline = self._observer_timeline(student_id, at)
        flags = run_all_rules(ble_zone, wifi_zone, zone_timeline, observer_timeline,
                              self.classroom_positions(), self.rule_config)

        token_reuse = sum(1 for f in flags if f.rule == "token_reuse")
        consistency = 1.0 if not any(f.rule == "ble_wifi_contradiction" for f in flags) else 0.0
        features = self._features(student_id, ev.ble_marker, ev.wifi, ev.peer_ble, zone_timeline,
                                  token_reuse, consistency)

        iso_score, iso_anom, demo_risk = None, False, None
        if self.anomaly_detector is not None and self.anomaly_detector.is_trained:
            pred = self.anomaly_detector.predict(features)
            iso_score, iso_anom, demo_risk = pred.raw_score, pred.is_anomalous, pred.demo_risk_score

        is_anomalous = bool(flags) or iso_anom
        if not is_anomalous:
            return AnomalyResult(None, False, "none", [], iso_score, demo_risk)

        severity = "high" if any(f.severity == "high" for f in flags) else (
            "medium" if flags else "low")
        reasons = [f.reason for f in flags]
        anomaly_id = self.store.add_anomaly(
            student_id, at, flags[0].rule if flags else "isolation_forest", severity,
            iso_score, is_anomalous, reasons, features.to_dict(),
        )
        return AnomalyResult(anomaly_id, True, severity, reasons, iso_score, demo_risk)

    def process_presence(
        self, student_id: str, session_id: str, classroom_id: str, at: float | None = None,
        wifi_fingerprint: dict[str, float] | None = None, source: str = "live",
    ) -> tuple[EvidenceResult, AnomalyResult]:
        """The single entry point for a presence update (brief's POST
        /presence): gathers evidence once, scores attendance, and runs the
        anomaly pipeline against that SAME evidence -- the combination the
        demo's "Step 2/Step 4" flow (presence shows PRESENT, then an
        injected anomaly flags immediately) needs in one call."""
        at = at if at is not None else time.time()
        ev = self._gather_evidence(student_id, session_id, classroom_id, at, wifi_fingerprint)
        evidence_result = score_attendance(ev, self.evidence_weights)
        self.store.add_evidence(student_id, session_id, classroom_id, at, evidence_result.score,
                                evidence_result.state.value, evidence_result.to_dict(), source)
        anomaly_result = self.run_anomaly_check(student_id, classroom_id, ev, at)
        return evidence_result, anomaly_result

    # ---- RAG + LLM explanation (brief sections 18-20) ------------------------

    def _anomaly_evidence_dict(self, anomaly) -> dict:
        features = json.loads(anomaly.features)
        reasons = json.loads(anomaly.reasons)
        return {
            "student": anomaly.student_id, "type": anomaly.type, "severity": anomaly.severity,
            "isolation_score": anomaly.iforest_score, "reasons": reasons, **features,
        }

    async def explain_anomaly(self, anomaly_id: int) -> tuple[str, list[RetrievedCase]]:
        anomaly = self.store.anomaly(anomaly_id)
        if anomaly is None:
            raise UnknownAnomaly(anomaly_id)
        evidence = self._anomaly_evidence_dict(anomaly)
        query = anomaly_query_text(evidence, json.loads(anomaly.reasons))
        retrieved = self.retriever.retrieve(query, k=3)
        explanation = await self.llm.explain(evidence, retrieved)
        self.store.set_anomaly_explanation(anomaly_id, explanation)
        return explanation, retrieved

    # ---- feedback (brief section 21) -----------------------------------------

    def submit_feedback(self, anomaly_id: int, decision: str, comment: str | None,
                        at: float | None = None) -> None:
        at = at if at is not None else time.time()
        anomaly = self.store.anomaly(anomaly_id)
        if anomaly is None:
            raise UnknownAnomaly(anomaly_id)
        self.store.add_feedback(anomaly_id, decision, comment, at)
        self.store.set_anomaly_status(anomaly_id, decision)
        if decision in ("false_positive", "confirmed"):
            issue = "; ".join(json.loads(anomaly.reasons)) or anomaly.type
            resolution = comment or (
                "Faculty confirmed this was a false positive." if decision == "false_positive"
                else "Faculty confirmed this anomaly.")
            case_id = self.store.add_verified_case(anomaly.type, issue, resolution,
                                                   json.loads(anomaly.features), anomaly_id, at)
            self.retriever.add_case(VerifiedCase(case_id, anomaly.type, issue, resolution,
                                                 json.loads(anomaly.features)))

    # ---- ingestion helpers used by main.py's routes --------------------------

    def ingest_peer_observation(self, observer_token: str, observed_token: str, rssi: int,
                                duration_s: float | None, ts: float | None = None) -> str | None:
        ts = ts if ts is not None else time.time()
        observer_sid = self.engine.tokens.resolve(observer_token, ts)
        observed_sid = self.engine.tokens.resolve(observed_token, ts)
        if observer_sid is None:
            return None
        self.store.add_peer_observations([PeerObservationRow(
            ts, observer_sid, observed_sid, observed_token, rssi, duration_s, "live")])
        return observed_sid

    def ingest_wifi_sample(self, zone: str, fingerprint: dict[str, float], source: str = "live") -> None:
        self.store.add_wifi_fingerprint_samples([
            WifiFingerprintSample(time.time(), zone, bssid, rssi, source)
            for bssid, rssi in fingerprint.items()
        ])

    # ---- synthetic campus simulation (brief sections 26-29) ------------------

    _SIM_REASONS = {
        "short_presence": lambda ev, d: [f"BLE marker detected for only {d:.0f}s"],
        "device_clustering": lambda ev, d: [
            f"{len(ev.nearby_peer_ids)} distinct student identities associated with what looks like one device"],
    }

    def _record_simulated_event(self, ev, classrooms_by_id: dict) -> tuple[EvidenceResult, AnomalyResult | None]:
        """Builds evidence straight from a simulator AttendanceEvent's own
        fields (never through the live token/scan pipeline -- simulated
        students have no real phone, so there is nothing to ingest) and
        stores it with source="simulated", matching the brief's mandatory
        LIVE/SIMULATED distinction (section 39)."""
        duration = max(0.0, ev.leave_ts - ev.enter_ts)
        rssi = int(np.median(ev.ble_rssi_samples)) if ev.ble_rssi_samples else None
        ble = BleMarkerEvidence(detected=bool(ev.ble_rssi_samples), rssi=rssi, duration_s=duration)
        wifi = self._wifi_evidence(ev.wifi_fingerprints[-1] if ev.wifi_fingerprints else None)
        consistent = 0 if ev.label == "device_clustering" else len(ev.nearby_peer_ids)
        peer = PeerBleEvidence(nearby_devices=len(ev.nearby_peer_ids), consistent_observations=consistent)

        attendance_ev = AttendanceEvidence(ev.student_id, ev.session_id, ev.classroom_id, ble, peer, wifi,
                                           FaceEvidence(), RfidEvidence(), ev.enter_ts)
        result = score_attendance(attendance_ev, self.evidence_weights)
        self.store.add_evidence(ev.student_id, ev.session_id, ev.classroom_id, ev.enter_ts, result.score,
                                result.state.value, result.to_dict(), "simulated")

        if ev.label == "normal":
            return result, None

        from simulator.campus import features_from_event
        features = features_from_event(ev, classrooms_by_id, self.wifi_model)
        if ev.label == "wifi_ble_mismatch":
            reasons = [f"BLE classroom marker indicates {ev.classroom_id}, but the Wi-Fi fingerprint "
                      f"indicates {ev.extra_classroom_id}"]
        elif ev.label == "impossible_movement" and len(ev.extra_zones_visited) >= 2:
            reasons = [f"moved between {ev.extra_zones_visited[0]} and {ev.extra_zones_visited[-1]} "
                      f"faster than physically plausible"]
        elif ev.label in ("token_replay", "proxy_attendance"):
            reasons = ["the same temporary token pattern was observed from multiple devices"]
        else:
            reasons = self._SIM_REASONS.get(ev.label, lambda ev, d: [f"flagged as {ev.label}"])(ev, duration)
        severity = "high" if ev.label in ("impossible_movement", "token_replay", "proxy_attendance") else "medium"

        iso_score, demo_risk = None, None
        if self.anomaly_detector is not None and self.anomaly_detector.is_trained:
            pred = self.anomaly_detector.predict(features)
            iso_score, demo_risk = pred.raw_score, pred.demo_risk_score

        anomaly_id = self.store.add_anomaly(ev.student_id, ev.enter_ts, ev.label, severity, iso_score,
                                            True, reasons, features.to_dict())
        return result, AnomalyResult(anomaly_id, True, severity, reasons, iso_score, demo_risk)

    def seed_simulation(self, n_students: int = 300, n_classrooms: int = 20, n_aps: int = 10,
                        anomaly_rate: float = 0.08, seed: int = 0) -> dict:
        """Brief section 26: seeds classrooms/sessions/students and runs
        every simulated attendance event through the SAME evidence+anomaly
        pipeline live data uses, just without the token/scan round trip."""
        from simulator.campus import generate_campus_dataset
        data = generate_campus_dataset(n_students, n_classrooms, n_aps, anomaly_rate, seed, now=self.engine.clock())
        classrooms_by_id = {c.classroom_id: c for c in data["classrooms"]}

        for c in data["classrooms"]:
            self.store.upsert_classroom(ClassroomRow(
                c.classroom_id, c.name, c.building, c.floor, c.x, c.y,
                f"{c.classroom_id}_BEACON", f"Smart Board -- {c.name}"))
            self.store.upsert_scanner(f"{c.classroom_id}_BEACON", c.classroom_id, None)
        for s in data["sessions"]:
            self.store.upsert_session(SessionRow(s.session_id, s.course, s.classroom_id, s.start_ts, s.end_ts))

        existing = set(self.store.student_ids())
        for st in data["students"]:
            if st.student_id not in existing:
                self.engine.enroll_student(st.student_id, st.name)   # secret discarded: no real phone to provision

        n_anomalies = 0
        for ev in data["events"]:
            _, anomaly = self._record_simulated_event(ev, classrooms_by_id)
            if anomaly is not None:
                n_anomalies += 1

        return {
            "students": len(data["students"]), "classrooms": len(data["classrooms"]),
            "wifi_aps": len(data["aps"]), "ble_markers": len(data["markers"]),
            "sessions": len(data["sessions"]), "events": len(data["events"]),
            "anomalies_injected": n_anomalies,
        }

    def inject_demo_anomaly(self, kind: str, student_id: str | None = None,
                            at: float | None = None) -> AnomalyResult:
        """Brief section 29's Demo Control Panel: one button, one immediate,
        visible reaction. Pass a specific `student_id` to target a LIVE
        student (so the demo responds to an actual phone) or any already-
        seeded simulated one; leave it unset and a random already-enrolled
        student is picked automatically -- that is what makes this a true
        one-click button for judges. A classroom/session already registered
        via seed_simulation or PUT /classrooms is required either way.
        """
        at = at if at is not None else time.time()
        enrolled = self.store.student_ids()
        if student_id is None:
            if not enrolled:
                raise ValueError("no students enrolled -- call POST /students or "
                                 "POST /simulation/start first")
            rng = np.random.default_rng(int(at) ^ hash(kind) & 0xFFFF)
            student_id = enrolled[int(rng.integers(len(enrolled)))]
        elif student_id not in enrolled:
            raise ValueError(f"student_id {student_id!r} is not enrolled -- call POST /students or "
                             "POST /simulation/start first")
        classrooms = self.store.classrooms()
        if not classrooms:
            raise ValueError("no classrooms registered -- call POST /simulation/start or "
                             "PUT /classrooms/{id} first")

        if kind in ("proxy_attendance", "token_replay"):
            return self._inject_token_reuse(student_id, classrooms, at)

        from simulator.campus import SimSession, SimStudent, build_demo_event
        rng = np.random.default_rng(int(at) ^ hash(kind) & 0xFFFF)
        classroom_id = next(iter(classrooms))
        other_id = next((c for c in classrooms if c != classroom_id), classroom_id)
        student = SimStudent(student_id, student_id, "N/A", 1)
        # window must be >= _normal_attendance's max dwell (90 min) or its rng.uniform(30min, min(90min,
        # window)) call can see low > high and raise.
        session = SimSession(f"DEMO_{classroom_id}_{int(at)}", "DEMO", classroom_id, at - 3600, at + 3600)
        ev = build_demo_event(student, session, kind, classrooms, [], rng, other_classroom_id=other_id)
        ev.wifi_fingerprints.clear()   # no real APs registered for a bare demo injection; BLE-only story
        _, anomaly = self._record_simulated_event(ev, classrooms)
        if anomaly is None:
            raise RuntimeError(f"demo injection of kind={kind!r} did not produce an anomaly; this is a bug")
        return anomaly

    def _inject_token_reuse(self, student_id: str, classrooms: dict, at: float) -> AnomalyResult:
        """proxy_attendance / token_replay: posts the student's own REAL,
        currently-valid token (same HMAC derivation a real phone uses)
        through two classroom scanners picked as far apart as possible,
        seconds apart -- exactly what a cloned/relayed token looks like to
        the server. This goes through engine.ingest_scans(), the SAME
        token-resolution path a real phone's scan hits, so the anomaly
        comes from the genuine check_token_reuse rule, not a synthesized
        record (unlike the other demo kinds, which build a one-off
        AttendanceEvent since they don't need real token mechanics)."""
        secrets = self.store.student_secrets()
        if student_id not in secrets:
            raise ValueError(f"no token secret on file for {student_id!r}")
        if len(classrooms) < 2:
            raise ValueError("need at least 2 registered classrooms for a token-reuse demo "
                             "(one isn't enough to show spatially-incompatible sightings)")
        from .tokens import token_for, window_index
        tok = token_for(secrets[student_id], window_index(at, self.engine.settings.token_window_s))

        # pick the two FARTHEST-apart classrooms so the demo reliably clears reuse_min_distance_m
        # regardless of how closely-packed the registered layout happens to be.
        rooms = list(classrooms.values())
        room_a, room_b = max(
            ((a, b) for a in rooms for b in rooms if a.classroom_id != b.classroom_id),
            key=lambda ab: math.hypot(ab[0].x - ab[1].x, ab[0].y - ab[1].y),
        )
        for room, ts in ((room_a, at), (room_b, at + 2)):
            marker = room.ble_marker_id or f"{room.classroom_id}_BEACON"
            if marker not in self.store.scanners():
                self.store.upsert_scanner(marker, room.classroom_id, None)
            self.engine.ingest_scans(marker, [(tok, -50, ts)])

        ev = self._gather_evidence(student_id, f"DEMO_{room_a.classroom_id}", room_a.classroom_id, at + 2, None)
        anomaly = self.run_anomaly_check(student_id, room_a.classroom_id, ev, at + 2)
        if not anomaly.is_anomalous:
            raise RuntimeError("token-reuse demo injection did not trigger an anomaly; this is a bug")
        return anomaly
