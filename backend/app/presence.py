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

from .anomaly_iforest import AnomalyDetector, AnomalyFeatures
from .anomaly_rules import ObserverSighting, RuleConfig, ZoneSighting, run_all_rules
from .engine import Engine
from .evidence import (
    AttendanceEvidence, BleMarkerEvidence, EvidenceResult, EvidenceWeights,
    FaceEvidence, PeerBleEvidence, RfidEvidence, WifiEvidence, score_attendance,
)
from .llm import LLMProvider
from .rag import CaseRetriever, RetrievedCase, VerifiedCase, anomaly_query_text
from .store import PeerObservationRow, WifiFingerprintSample
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
