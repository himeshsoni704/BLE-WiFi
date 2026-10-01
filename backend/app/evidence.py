"""Weighted evidence-fusion scoring (Proof-of-Presence hackathon brief,
section 9): BLE classroom marker, Wi-Fi zone match, peer BLE consistency,
sustained presence, and optional face/RFID signals combine into an
explainable 0-100 score and one of four attendance states.

This is a SEPARATE, additional model from fusion.py's handoff-risk verdict
(BLE zone == Wi-Fi zone, owner/gait score, stationarity): fusion.py answers
"is this probably the enrolled student's own phone, not left behind or
handed off"; this module answers the brief's literal question, "how much
evidence do we have that student X was physically in classroom Y during
session Z", as a point total a professor can audit component-by-component.
Both run side by side; this module does not override fusion.py, and
fusion.py's verdict is not consulted here on purpose -- the two are
intentionally independent cross-checks, not a pipeline.

This score is a prototype evidence-fusion mechanism, not a scientifically
validated probability of attendance (explicitly required by the brief).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class AttendanceState(str, Enum):
    PRESENT = "present"
    LIKELY_PRESENT = "likely_present"
    REVIEW_REQUIRED = "review_required"
    ABSENT = "absent"


@dataclass(frozen=True)
class EvidenceWeights:
    """Configurable (the brief: "make the weights configurable"). Defaults
    mirror the brief's own worked example (35/30/20/10/5) -- not claimed to
    be empirically validated, see the module docstring."""
    ble_marker: float = 35.0
    wifi_zone_match: float = 30.0
    peer_consistency: float = 20.0
    sustained_presence: float = 10.0
    face_match: float = 5.0
    rfid_detected: float = 5.0

    # State thresholds, as a fraction of this student's own max-possible score
    # (so enrolling without a face/RFID system doesn't make PRESENT unreachable).
    present_threshold: float = 0.80
    likely_present_threshold: float = 0.55
    review_threshold: float = 0.30   # below this: not even worth flagging for review

    def max_possible(self, has_face: bool, has_rfid: bool) -> float:
        total = self.ble_marker + self.wifi_zone_match + self.peer_consistency + self.sustained_presence
        if has_face:
            total += self.face_match
        if has_rfid:
            total += self.rfid_detected
        return total


@dataclass(frozen=True)
class BleMarkerEvidence:
    detected: bool = False
    rssi: int | None = None
    duration_s: float = 0.0


@dataclass(frozen=True)
class PeerBleEvidence:
    nearby_devices: int = 0
    consistent_observations: int = 0   # distinct peers whose BLE reports corroborate the claimed classroom


@dataclass(frozen=True)
class WifiEvidence:
    predicted_zone: str | None = None
    confidence: float = 0.0


@dataclass(frozen=True)
class FaceEvidence:
    available: bool = False
    match: bool | None = None
    confidence: float | None = None


@dataclass(frozen=True)
class RfidEvidence:
    available: bool = False
    detected: bool | None = None


@dataclass(frozen=True)
class AttendanceEvidence:
    """Mirrors the brief's evidence JSON object (section 9), field for field."""
    student_id: str
    session_id: str
    classroom_id: str
    ble_marker: BleMarkerEvidence = field(default_factory=BleMarkerEvidence)
    peer_ble: PeerBleEvidence = field(default_factory=PeerBleEvidence)
    wifi: WifiEvidence = field(default_factory=WifiEvidence)
    face: FaceEvidence = field(default_factory=FaceEvidence)
    rfid: RfidEvidence = field(default_factory=RfidEvidence)
    ts: float = 0.0

    def to_dict(self) -> dict:
        return {
            "student_id": self.student_id, "session_id": self.session_id, "classroom": self.classroom_id,
            "ble_marker": {"detected": self.ble_marker.detected, "rssi": self.ble_marker.rssi,
                          "duration_seconds": self.ble_marker.duration_s},
            "peer_ble": {"nearby_devices": self.peer_ble.nearby_devices,
                        "consistent_observations": self.peer_ble.consistent_observations},
            "wifi": {"predicted_zone": self.wifi.predicted_zone, "confidence": self.wifi.confidence},
            "face": {"available": self.face.available, "match": self.face.match},
            "rfid": {"available": self.rfid.available, "detected": self.rfid.detected},
            "timestamp": self.ts,
        }


@dataclass(frozen=True)
class EvidenceComponent:
    name: str
    points: float
    max_points: float
    reason: str


@dataclass(frozen=True)
class EvidenceResult:
    score: float   # 0-100, normalised against THIS student's available max_possible
    raw_points: float
    max_possible: float
    state: AttendanceState
    components: tuple[EvidenceComponent, ...]

    def to_dict(self) -> dict:
        return {
            "score": round(self.score, 1),
            "raw_points": round(self.raw_points, 1),
            "max_possible": round(self.max_possible, 1),
            "state": self.state.value,
            "components": [
                {"name": c.name, "points": round(c.points, 1), "max_points": round(c.max_points, 1),
                 "reason": c.reason}
                for c in self.components
            ],
        }


MIN_SUSTAINED_S = 300.0   # 5 minutes earns full "sustained presence" credit; ramps linearly from 0


def _label(zone_id: str) -> str:
    return zone_id.replace("ROOM_", "Room ").replace("_", " ")


def score_attendance(ev: AttendanceEvidence, weights: EvidenceWeights = EvidenceWeights()) -> EvidenceResult:
    """Pure function: deterministic weighted-point evidence fusion (brief
    section 9/48). A prototype scoring mechanism, not a validated probability."""
    components: list[EvidenceComponent] = []

    if ev.ble_marker.detected:
        reason = f"BLE marker detected in {_label(ev.classroom_id)}"
        if ev.ble_marker.rssi is not None:
            reason += f" (RSSI {ev.ble_marker.rssi} dBm)"
        components.append(EvidenceComponent("ble_marker", weights.ble_marker, weights.ble_marker, reason))
    else:
        components.append(EvidenceComponent("ble_marker", 0.0, weights.ble_marker, "no BLE classroom marker seen"))

    if ev.wifi.predicted_zone is not None:
        conf = max(0.0, min(1.0, ev.wifi.confidence))
        if ev.wifi.predicted_zone == ev.classroom_id:
            pts = weights.wifi_zone_match * conf
            reason = f"Wi-Fi fingerprint predicts {_label(ev.classroom_id)} ({conf:.0%} confidence)"
        else:
            pts = 0.0
            reason = (f"Wi-Fi fingerprint predicts {_label(ev.wifi.predicted_zone)}, not the "
                      f"expected {_label(ev.classroom_id)}")
        components.append(EvidenceComponent("wifi_zone_match", pts, weights.wifi_zone_match, reason))
    else:
        components.append(EvidenceComponent("wifi_zone_match", 0.0, weights.wifi_zone_match,
                                            "no Wi-Fi fingerprint available"))

    if ev.peer_ble.nearby_devices > 0:
        ratio = min(1.0, ev.peer_ble.consistent_observations / max(1, ev.peer_ble.nearby_devices))
        pts = weights.peer_consistency * ratio
        reason = (f"{ev.peer_ble.consistent_observations}/{ev.peer_ble.nearby_devices} nearby peer "
                  f"devices corroborate this classroom")
        components.append(EvidenceComponent("peer_consistency", pts, weights.peer_consistency, reason))
    else:
        components.append(EvidenceComponent("peer_consistency", 0.0, weights.peer_consistency,
                                            "no nearby peer devices observed"))

    sustained_ratio = max(0.0, min(1.0, ev.ble_marker.duration_s / MIN_SUSTAINED_S))
    components.append(EvidenceComponent(
        "sustained_presence", weights.sustained_presence * sustained_ratio, weights.sustained_presence,
        f"present {ev.ble_marker.duration_s:.0f}s of the {MIN_SUSTAINED_S:.0f}s needed for full credit",
    ))

    has_face = ev.face.available
    if has_face:
        pts = weights.face_match * (ev.face.confidence or 0.0) if ev.face.match else 0.0
        reason = (f"face-scan system: match ({(ev.face.confidence or 0.0):.0%} confidence)"
                  if ev.face.match else "face-scan system: no match")
        components.append(EvidenceComponent("face_match", pts, weights.face_match, reason))

    has_rfid = ev.rfid.available
    if has_rfid:
        pts = weights.rfid_detected if ev.rfid.detected else 0.0
        reason = f"RFID/NFC: {'detected' if ev.rfid.detected else 'not detected'}"
        components.append(EvidenceComponent("rfid_detected", pts, weights.rfid_detected, reason))

    raw_points = sum(c.points for c in components)
    max_possible = weights.max_possible(has_face, has_rfid)
    score = 100.0 * raw_points / max_possible if max_possible > 0 else 0.0

    if score >= weights.present_threshold * 100:
        state = AttendanceState.PRESENT
    elif score >= weights.likely_present_threshold * 100:
        state = AttendanceState.LIKELY_PRESENT
    elif score >= weights.review_threshold * 100:
        state = AttendanceState.REVIEW_REQUIRED
    else:
        state = AttendanceState.ABSENT

    return EvidenceResult(score=score, raw_points=raw_points, max_possible=max_possible,
                          state=state, components=tuple(components))
