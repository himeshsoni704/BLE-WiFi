"""Live ingestion: validates and stores what phones and classroom nodes upload.

Checks, in order: timestamp window, replay (unique nonce), observer identity (JWT vs token),
token validity (rotating HMAC), marker token validity. Rejections are reported per item and
counted in `svc.metrics`; they are never silently dropped.
"""
from __future__ import annotations

import json
import re
import secrets
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .core import Services
from .models import (AccessPoint, Classroom, Enrollment, Location, Observation, SessionRow, Student,
                     WifiTraining)
from .pipeline import GRACE_AFTER_S, GRACE_BEFORE_S, evaluate_live_student
from .security import Principal
from .tokens import timestamp_ok, verify_marker_token

BSSID_RE = re.compile(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$")
MAX_SCAN_AGE_S = 120


def normalize_bssid(b: str | None) -> str | None:
    if not b:
        return None
    n = b.strip().upper().replace("-", ":")
    return n if BSSID_RE.match(n) else None


@dataclass
class ItemResult:
    index: int
    status: str            # accepted | rejected
    reason: str | None = None

    def to_dict(self) -> dict:
        return {"index": self.index, "status": self.status, "reason": self.reason}


def _reject(svc: Services, results: list[ItemResult], i: int, reason: str) -> None:
    svc.metrics[f"rejected_{reason}"] += 1
    results.append(ItemResult(i, "rejected", reason))


def _session_for_room(db: Session, room: str, ts: float) -> int | None:
    s = db.scalar(select(SessionRow.id).where(
        SessionRow.classroom_id == room, SessionRow.start_ts - GRACE_BEFORE_S <= ts,
        SessionRow.end_ts + GRACE_AFTER_S >= ts).order_by(SessionRow.is_simulated))
    return s


def _session_for_student(db: Session, key: str, ts: float) -> int | None:
    return db.scalar(select(SessionRow.id).join(Enrollment, Enrollment.session_id == SessionRow.id).where(
        Enrollment.student_key == key, SessionRow.start_ts - GRACE_BEFORE_S <= ts,
        SessionRow.end_ts + GRACE_AFTER_S >= ts).order_by(SessionRow.is_simulated))


def _nonce_taken(db: Session, nonce: str) -> bool:
    return db.scalar(select(Observation.id).where(Observation.nonce == nonce)) is not None


def ingest_ble(db: Session, svc: Services, who: Principal, items: list, now: float | None = None) -> dict:
    st = svc.settings
    now = svc.clock() if now is None else now
    if who.role != "student" or not who.student_key:
        raise PermissionError("BLE observations are uploaded by student phones")
    student = db.get(Student, who.student_key)
    simulated = bool(student and student.is_simulated)
    classrooms = {c.marker_idx: c for c in db.scalars(select(Classroom).where(Classroom.marker_idx.is_not(None)))}
    by_marker_id = {c.marker_id: c for c in classrooms.values()}
    results: list[ItemResult] = []
    seen_nonces: set[str] = set()
    affected: dict[str, None] = {who.student_key: None}      # observer first, then everyone they reported seeing
    for i, it in enumerate(items):
        if not timestamp_ok(it.timestamp, now, st.ts_tolerance_s):
            _reject(svc, results, i, "timestamp_out_of_window")
            continue
        if it.nonce in seen_nonces or _nonce_taken(db, it.nonce):
            _reject(svc, results, i, "replayed_nonce")
            continue
        if it.observer is not None and svc.tokens.resolve(it.observer, it.timestamp) != who.student_key:
            _reject(svc, results, i, "observer_token_mismatch")
            continue
        seen_nonces.add(it.nonce)
        if it.kind == "peer":
            if not it.observed_token:
                _reject(svc, results, i, "missing_observed_token")
                continue
            peer = svc.tokens.resolve(it.observed_token, it.timestamp)
            if peer is None:
                _reject(svc, results, i, "unknown_or_expired_token")
                continue
            if peer == who.student_key:
                _reject(svc, results, i, "self_observation")
                continue
            db.add(Observation(kind="ble_peer", session_id=_session_for_student(db, who.student_key, it.timestamp),
                               student_key=who.student_key, ts=it.timestamp, duration=it.duration,
                               rssi=it.rssi, samples=it.samples, observed_token=it.observed_token,
                               observed_student_key=peer, nonce=it.nonce, is_simulated=simulated))
            affected[peer] = None                  # their token was seen: recompute their evidence/token-reuse too
        else:
            room = classrooms.get(it.marker_idx) if it.marker_idx else by_marker_id.get(it.marker_id)
            if room is None or not it.marker_token:
                _reject(svc, results, i, "unknown_marker")
                continue
            if not verify_marker_token(room.marker_secret, room.marker_idx, it.marker_token, it.timestamp,
                                       st.token_window_s, st.token_skew_windows):
                _reject(svc, results, i, "invalid_marker_token")
                continue
            db.add(Observation(kind="ble_marker", session_id=_session_for_room(db, room.id, it.timestamp),
                               student_key=who.student_key, ts=it.timestamp, duration=it.duration,
                               rssi=it.rssi, samples=it.samples, marker_idx=room.marker_idx, room=room.id,
                               nonce=it.nonce, is_simulated=simulated))
            db.add(Location(student_key=who.student_key, ts=it.timestamp, zone=room.id, x=room.x, y=room.y,
                            signal="ble_marker", confidence=None, is_simulated=simulated,
                            session_id=_session_for_room(db, room.id, it.timestamp)))
        results.append(ItemResult(i, "accepted"))
    db.flush()
    accepted = sum(1 for r in results if r.status == "accepted")
    svc.metrics["ble_accepted"] += accepted
    state = None
    if accepted:
        for n, key in enumerate(affected):
            ev = evaluate_live_student(db, svc, key, now)
            if n == 0 and ev:
                state = ev[0].final_state
    return {"accepted": accepted, "rejected": len(results) - accepted,
            "results": [r.to_dict() for r in results], "state": state}


def fingerprint_from(db: Session, aps: list, register_unknown: bool = False) -> tuple[dict[str, float], int, int]:
    """Map reported APs to {ap_id: rssi}. Returns (fingerprint, matched, unknown)."""
    by_bssid = {a.bssid: a for a in db.scalars(select(AccessPoint).where(AccessPoint.bssid.is_not(None)))}
    known_ids = {a.ap_id for a in db.scalars(select(AccessPoint))}
    fp: dict[str, float] = {}
    unknown = 0
    for ap in aps:
        ap_id = None
        b = normalize_bssid(ap.bssid)
        if b and b in by_bssid:
            ap_id = by_bssid[b].ap_id
        elif ap.ap_id and ap.ap_id in known_ids:
            ap_id = ap.ap_id
        elif register_unknown and b:
            n = db.scalar(select(AccessPoint.ap_id).where(AccessPoint.ap_id.like("LIVE_%")).order_by(
                AccessPoint.ap_id.desc()).limit(1))
            nxt = int(n.split("_")[1]) + 1 if n else 1
            new = AccessPoint(ap_id=f"LIVE_{nxt:02d}", bssid=b, name=(ap.ssid or "")[:60],
                              frequency_mhz=ap.frequency or 0, is_simulated=False)
            db.add(new)
            db.flush()
            by_bssid[b] = new
            ap_id = new.ap_id
        if ap_id is None:
            unknown += 1
            continue
        fp[ap_id] = max(fp.get(ap_id, -127.0), float(ap.rssi))
    return fp, len(fp), unknown


def ingest_wifi(db: Session, svc: Services, who: Principal, body, now: float | None = None) -> dict:
    st = svc.settings
    now = svc.clock() if now is None else now
    if who.role != "student" or not who.student_key:
        raise PermissionError("Wi-Fi observations are uploaded by student phones")
    if not timestamp_ok(body.timestamp, now, st.ts_tolerance_s):
        svc.metrics["rejected_timestamp_out_of_window"] += 1
        return {"status": "rejected", "reason": "timestamp_out_of_window"}
    if _nonce_taken(db, body.nonce):
        svc.metrics["rejected_replayed_nonce"] += 1
        return {"status": "rejected", "reason": "replayed_nonce"}
    if body.scan_age_s is not None and body.scan_age_s > MAX_SCAN_AGE_S:
        svc.metrics["rejected_stale_scan"] += 1
        return {"status": "rejected", "reason": "stale_scan",
                "detail": f"cached scan is {body.scan_age_s:.0f}s old (limit {MAX_SCAN_AGE_S}s)"}
    student = db.get(Student, who.student_key)
    simulated = bool(student and student.is_simulated)
    fp, matched, unknown = fingerprint_from(db, body.wifi)
    if matched == 0:
        svc.metrics["rejected_no_known_aps"] += 1
        return {"status": "rejected", "reason": "no_known_aps", "unknown_aps": unknown}
    model = svc.wifi_model_for(simulated)
    zone, conf, top, warning = None, None, [], None
    if model is None:
        warning = ("no Wi-Fi localisation model for live data yet: survey rooms with POST /wifi/survey "
                   "then POST /wifi/retrain")
    else:
        pred = model.predict(fp)
        zone, conf, top = pred.zone, pred.confidence, pred.top
    sid = _session_for_student(db, who.student_key, body.timestamp)
    db.add(Observation(kind="wifi", session_id=sid, student_key=who.student_key, ts=body.timestamp,
                       wifi_json=json.dumps(fp), wifi_zone=zone, wifi_conf=conf, wifi_source=body.source,
                       scan_age_s=body.scan_age_s, samples=matched, nonce=body.nonce, is_simulated=simulated))
    if zone:
        room = db.get(Classroom, zone)
        db.add(Location(student_key=who.student_key, ts=body.timestamp, zone=zone,
                        x=room.x if room else 0, y=room.y if room else 0, signal="wifi", confidence=conf,
                        is_simulated=simulated, session_id=sid))
    db.flush()
    svc.metrics["wifi_accepted"] += 1
    evaluated = evaluate_live_student(db, svc, who.student_key, now) if zone else []
    return {"status": "accepted", "aps_matched": matched, "aps_unknown": unknown, "predicted_zone": zone,
            "confidence": None if conf is None else round(conf, 3),
            "top": [(z, round(p, 3)) for z, p in top], "warning": warning,
            "confidence_note": "model confidence, not a calibrated probability",
            "state": evaluated[0].final_state if evaluated else None}


def ingest_presence(db: Session, svc: Services, who: Principal, body, now: float | None = None) -> dict:
    st = svc.settings
    now = svc.clock() if now is None else now
    if not timestamp_ok(body.timestamp, now, st.ts_tolerance_s):
        svc.metrics["rejected_timestamp_out_of_window"] += 1
        return {"status": "rejected", "reason": "timestamp_out_of_window"}
    if _nonce_taken(db, body.nonce):
        svc.metrics["rejected_replayed_nonce"] += 1
        return {"status": "rejected", "reason": "replayed_nonce"}
    out: dict = {"status": "accepted", "stored": []}
    key = who.student_key
    if who.role == "student" and key:
        student = db.get(Student, key)
        db.add(Observation(kind="presence", session_id=_session_for_student(db, key, body.timestamp),
                           student_key=key, ts=body.timestamp, nonce=body.nonce,
                           is_simulated=bool(student and student.is_simulated),
                           payload_json=json.dumps({"zone_estimate": body.zone_estimate.model_dump() if body.zone_estimate else None,
                                                    "ble_state": body.ble_state, "wifi_state": body.wifi_state,
                                                    "counts": body.counts, "session": body.session,
                                                    "provenance": "phone-side estimate"})))
        out["stored"].append("presence")
    external = [("face", body.face), ("rfid", body.rfid)]
    if any(b is not None for _, b in external):
        if not who.is_staff():
            out["rejected_external"] = "face/rfid blocks are accepted only from faculty/admin/reader accounts"
        else:
            if body.face is not None:
                s = _student_by_id(db, svc, body.face.student_id)
                if s is None:
                    out["rejected_external"] = "unknown student_id in face block"
                else:
                    db.add(Observation(kind="face", student_key=s.student_key, ts=body.face.timestamp,
                                       payload_json=json.dumps({"match": body.face.match,
                                                                "confidence": body.face.confidence,
                                                                "system": "external face system"}),
                                       is_simulated=s.is_simulated, nonce="face-" + secrets.token_hex(8)))
                    out["stored"].append("face")
                    key = s.student_key
            if body.rfid is not None:
                blk = body.rfid.rfid if hasattr(body.rfid, "rfid") else body.rfid
                s = _student_by_id(db, svc, blk.student_id) if blk.student_id else None
                if s is None:
                    out["rejected_external"] = "rfid block needs a known student_id"
                else:
                    db.add(Observation(kind="rfid", student_key=s.student_key, ts=blk.timestamp,
                                       payload_json=json.dumps({"detected": blk.detected, "reader": blk.reader}),
                                       is_simulated=s.is_simulated, nonce="rfid-" + secrets.token_hex(8)))
                    out["stored"].append("rfid")
                    key = s.student_key
    db.flush()
    if key:
        ev = evaluate_live_student(db, svc, key, now)
        out["state"] = ev[0].final_state if ev else None
    return out


def _student_by_id(db: Session, svc: Services, student_id: str) -> Student | None:
    return db.scalar(select(Student).where(Student.student_id == student_id.strip().upper()))


def node_heartbeat(db: Session, svc: Services, room: Classroom, body, now: float | None = None) -> dict:
    st = svc.settings
    now = svc.clock() if now is None else now
    room.last_heartbeat = now
    room.detected_count = body.detected_count
    room.node_kind = body.node_kind
    room.is_simulated = False
    accepted = rejected = 0
    for s in body.sightings:
        ts = s.timestamp or now
        if not timestamp_ok(ts, now, st.ts_tolerance_s):
            rejected += 1
            continue
        key = svc.tokens.resolve(s.token, ts)
        if key is None:
            rejected += 1
            continue
        student = db.get(Student, key)
        db.add(Observation(kind="node_sighting", session_id=_session_for_room(db, room.id, ts),
                           student_key=f"node:{room.marker_id}", ts=ts, duration=s.duration, rssi=s.rssi,
                           observed_token=s.token, observed_student_key=key, marker_idx=room.marker_idx,
                           room=room.id, is_simulated=bool(student and student.is_simulated),
                           nonce="node-" + secrets.token_hex(10)))
        accepted += 1
    db.flush()
    for key in {svc.tokens.resolve(s.token, s.timestamp or now) for s in body.sightings} - {None}:
        evaluate_live_student(db, svc, key, now)
    svc.hub.publish({"type": "node", "room": room.id, "detected": body.detected_count, "kind": body.node_kind})
    return {"room": room.id, "marker_id": room.marker_id, "sightings_accepted": accepted,
            "sightings_rejected": rejected}


def save_survey(db: Session, svc: Services, who: Principal, body) -> dict:
    room = db.get(Classroom, body.zone)
    if room is None:
        raise ValueError(f"unknown zone {body.zone!r}")
    stored = skipped = 0
    for scan in body.scans:
        fp, matched, _ = fingerprint_from(db, scan.wifi, register_unknown=body.register_unknown_aps)
        if matched == 0:
            skipped += 1
            continue
        db.add(WifiTraining(zone=body.zone, fingerprint_json=json.dumps(fp), source="live_survey",
                            student_key=who.student_key))
        stored += 1
    db.flush()
    counts: dict[str, int] = {}
    for z, n in db.execute(select(WifiTraining.zone, func.count())
                           .where(WifiTraining.source == "live_survey").group_by(WifiTraining.zone)):
        counts[z] = n
    return {"stored": stored, "skipped": skipped, "samples_per_zone": counts}
