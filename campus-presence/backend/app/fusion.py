"""Evidence fusion: raw observations for one (student, session) -> structured evidence + score.

The score is a transparent, weighted checklist (configurable in config.py). It is a
PROTOTYPE MECHANISM, not a validated probability of presence. BLE alone cannot reach PRESENT:
PRESENT needs at least two independent signal families (BLE-family, Wi-Fi, face, RFID).

Every field in the evidence carries a `provenance` so the UI can label what is measured,
what is a model estimate, and what is simulated.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from .ble import interval_union_seconds, smooth_rssi
from .config import FusionWeights, Thresholds

PRESENT = "PRESENT"
LIKELY_PRESENT = "LIKELY_PRESENT"
REVIEW_REQUIRED = "REVIEW_REQUIRED"
ABSENT = "ABSENT"
STATES = (PRESENT, LIKELY_PRESENT, REVIEW_REQUIRED, ABSENT)

SCORE_DISCLAIMER = ("Prototype heuristic score from configurable weights. "
                    "Not a validated probability of presence.")


@dataclass
class MarkerObs:
    ts: float
    duration: float
    rssi: float
    room: str | None
    samples: int = 0


@dataclass
class WifiObs:
    ts: float
    zone: str
    conf: float
    source: str = "scan"            # scan | cached | manual | synthetic
    scan_age_s: float | None = None


@dataclass
class PeerObs:
    ts: float
    duration: float
    rssi: float
    peer_key: str
    direction: str                  # "observed" (I saw them) | "seen_by" (they saw me)
    peer_room: str | None = None    # peer's own room at that time, if known


@dataclass
class StudentInputs:
    student_key: str
    session_id: int
    session_code: str
    room: str
    start_ts: float
    end_ts: float
    now: float
    markers: list[MarkerObs] = field(default_factory=list)       # marker/node detections in ANY room
    wifis: list[WifiObs] = field(default_factory=list)
    peers: list[PeerObs] = field(default_factory=list)
    node_sightings: list[MarkerObs] = field(default_factory=list)
    face: dict | None = None
    rfid: dict | None = None
    is_simulated: bool = False
    peers_in_room: set[str] = field(default_factory=set)         # peers with own marker evidence in `room`
    observed_counts: dict = field(default_factory=dict)          # measured counts, e.g. adverts


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def build_evidence(inp: StudentInputs, w: FusionWeights, t: Thresholds) -> dict:
    prov_live = "simulated" if inp.is_simulated else "live"
    elapsed = max(1.0, min(inp.now, inp.end_ts) - inp.start_ts)
    session_len = max(1.0, inp.end_ts - inp.start_ts)
    window = (inp.start_ts - 60, inp.end_ts + 60)

    def in_window(ts: float) -> bool:
        return window[0] <= ts <= window[1]

    # ---- classroom BLE ---------------------------------------------------------------
    room_markers = [m for m in inp.markers if m.room == inp.room and in_window(m.ts)]
    room_markers += [m for m in inp.node_sightings if in_window(m.ts)]
    valid_markers = [m for m in room_markers if m.rssi >= t.marker_rssi_min]
    other_rooms = sorted({m.room for m in inp.markers if m.room and m.room != inp.room and in_window(m.ts)})
    sm_rssi = smooth_rssi([m.rssi for m in valid_markers])
    covered = interval_union_seconds((m.ts - m.duration, m.ts) for m in valid_markers)
    ble_pts = 0.0
    if valid_markers and sm_rssi is not None:
        quality = 1.0 if sm_rssi >= t.marker_rssi_good else 0.5 + 0.5 * _clamp(
            (sm_rssi - t.marker_rssi_min) / (t.marker_rssi_good - t.marker_rssi_min))
        ble_pts = w.classroom_ble * quality

    # ---- Wi-Fi ------------------------------------------------------------------------
    usable_wifi = [x for x in inp.wifis if in_window(x.ts) and x.conf >= t.wifi_conf_min]
    wifi_available = bool(usable_wifi)
    wifi_pred_zone, wifi_conf, match_fraction = None, None, 0.0
    if wifi_available:
        votes: dict[str, float] = {}
        for x in usable_wifi:
            votes[x.zone] = votes.get(x.zone, 0.0) + x.conf
        wifi_pred_zone = max(votes, key=votes.get)
        total = sum(votes.values())
        match_fraction = votes.get(inp.room, 0.0) / total if total else 0.0
        zone_confs = [x.conf for x in usable_wifi if x.zone == wifi_pred_zone]
        wifi_conf = sum(zone_confs) / len(zone_confs)
    matching = [x.conf for x in usable_wifi if x.zone == inp.room]
    mean_match_conf = sum(matching) / len(matching) if matching else 0.0
    wifi_pts = w.wifi_match * match_fraction * _clamp(mean_match_conf / 0.5)

    # ---- sustained presence (BLE coverage of elapsed session time) --------------------
    coverage_fraction = _clamp(covered / elapsed)
    sustained_pts = w.sustained_presence * _clamp(coverage_fraction / t.sustained_fraction)

    # ---- peers --------------------------------------------------------------------------
    peers_ok = {}
    for p in inp.peers:
        if in_window(p.ts) and p.rssi >= t.peer_rssi_min:
            peers_ok[p.peer_key] = max(peers_ok.get(p.peer_key, -200.0), p.rssi)
    consistent = sorted(k for k in peers_ok if k in inp.peers_in_room)
    own_signal = bool(valid_markers) or match_fraction >= 0.5
    # peers are BLE too: they only count when the student has an independent own signal
    peer_pts = w.peer_consistency * _clamp(len(consistent) / max(1, t.peer_target)) if own_signal else 0.0

    # ---- optional signals ------------------------------------------------------------
    face_ok = bool(inp.face and inp.face.get("match") and float(inp.face.get("confidence", 0)) >= t.face_conf_min)
    rfid_ok = bool(inp.rfid and inp.rfid.get("detected"))
    face_pts = w.face_match if face_ok else 0.0
    rfid_pts = w.rfid if rfid_ok else 0.0

    score = min(100.0, ble_pts + wifi_pts + peer_pts + sustained_pts + face_pts + rfid_pts)

    families = {
        "ble": bool(valid_markers) or (own_signal and bool(consistent)),
        "wifi": wifi_available and match_fraction >= 0.5,
        "face": face_ok,
        "rfid": rfid_ok,
    }
    n_families = sum(families.values())
    if score >= t.present and n_families >= 2:
        state = PRESENT
    elif score >= t.likely_present:
        state = LIKELY_PRESENT
    elif score >= t.review_required:
        state = REVIEW_REQUIRED
    else:
        state = ABSENT
    capped = score >= t.present and n_families < 2

    has_evidence = bool(valid_markers or usable_wifi or peers_ok or face_ok or rfid_ok or other_rooms)
    return {
        "student_key": inp.student_key,
        "session_id": inp.session_id,
        "session_code": inp.session_code,
        "classroom": inp.room,
        "timestamp": inp.now,
        "source": prov_live,
        "classroom_ble": {
            "detected": bool(valid_markers),
            "rssi_smoothed_dbm": None if sm_rssi is None else round(sm_rssi, 1),
            "duration_s": round(covered, 1),
            "coverage_fraction": round(coverage_fraction, 3),
            "detections": len(valid_markers),
            "other_rooms_detected": other_rooms,
            "points": round(ble_pts, 1),
            "provenance": prov_live,
        },
        "wifi": {
            "available": wifi_available,
            "predicted_zone": wifi_pred_zone,
            "confidence": None if wifi_conf is None else round(wifi_conf, 3),
            "match_fraction": round(match_fraction, 3),
            "scans_used": len(usable_wifi),
            "sources": sorted({x.source for x in usable_wifi}),
            "points": round(wifi_pts, 1),
            "provenance": "estimated" if not inp.is_simulated else "simulated",
            "note": "Zone is an ML estimate from RSSI fingerprints; confidence is not a calibrated probability.",
        },
        "peers": {
            "observed_distinct": len(peers_ok),
            "consistent_distinct": len(consistent),
            "strongest_rssi_dbm": max(peers_ok.values()) if peers_ok else None,
            "points": round(peer_pts, 1),
            "gated_by_own_signal": not own_signal,
            "provenance": prov_live,
        },
        "sustained": {"coverage_fraction": round(coverage_fraction, 3), "points": round(sustained_pts, 1),
                      "required_fraction": t.sustained_fraction},
        "face": {"provided": inp.face is not None, "match": face_ok, "points": face_pts,
                 "confidence": None if not inp.face else inp.face.get("confidence")},
        "rfid": {"provided": inp.rfid is not None, "detected": rfid_ok, "points": rfid_pts},
        "independent_signal_families": {**families, "count": n_families},
        "score": round(score, 1),
        "score_note": SCORE_DISCLAIMER,
        "state": state,
        "state_capped_for_single_family": capped,
        "has_evidence": has_evidence,
        "elapsed_s": round(elapsed, 1),
        "session_length_s": round(session_len, 1),
        "measured_counts": inp.observed_counts,
    }
