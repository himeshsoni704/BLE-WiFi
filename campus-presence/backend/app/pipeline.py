"""observations -> evidence fusion -> deterministic rules -> Isolation Forest -> attendance/anomaly rows.

One code path serves live uploads, the simulator, and dataset generation, so features seen in
training are computed exactly as they are at run time.

Isolation Forest and the rules only ever raise a case for human review. They never mark a
student ABSENT.
"""
from __future__ import annotations

import json
import math
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .anomaly import AnomalyScore, extract_features
from .core import Services
from .fusion import (ABSENT, REVIEW_REQUIRED, MarkerObs, PeerObs, StudentInputs, WifiObs,
                     build_evidence)
from .models import (Anomaly, Attendance, Classroom, Enrollment, Location, Observation, SessionRow,
                     Student)
from .rules import RuleContext, RuleHit, evaluate_rules

GRACE_BEFORE_S = 300
GRACE_AFTER_S = 120
HISTORY_S = 3600           # how far back location/marker context is loaded
MIN_ELAPSED_FOR_IF_S = 120


@dataclass
class EvalResult:
    student_key: str
    session_id: int
    evidence: dict
    rules: list[RuleHit]
    features: dict[str, float]
    if_score: AnomalyScore | None = None
    anomaly_id: int | None = None
    final_state: str = ABSENT
    is_simulated: bool = False
    eligible_for_if: bool = False
    if_skip_reason: str | None = None


TWIN_BUCKET_S = 60
TWIN_MIN_BUCKETS = 3


def twin_distances(marker_rows: dict[str, list[Observation]], keys: list[str], room: str,
                   start: float, end: float, rssi_min: float) -> dict[str, float]:
    """For each student: smallest mean |RSSI difference| between their classroom-marker series and any
    other phone's, over 60 s buckets seen by both. Phones carried together track each other within a
    dB or so; independent people differ by several dB. 99 means 'no comparable phone'."""
    series: dict[str, dict[int, float]] = {}
    for k in keys:
        buckets: dict[int, list[float]] = defaultdict(list)
        for o in marker_rows.get(k, []):
            if o.room == room and start - GRACE_BEFORE_S <= o.ts <= end + GRACE_AFTER_S \
                    and o.rssi is not None and o.rssi >= rssi_min:
                buckets[int(o.ts // TWIN_BUCKET_S)].append(o.rssi)
        series[k] = {b: sum(v) / len(v) for b, v in buckets.items()}
    out: dict[str, float] = {}
    for k in keys:
        best = 99.0
        for j in keys:
            if j == k:
                continue
            common = series[k].keys() & series[j].keys()
            if len(common) >= TWIN_MIN_BUCKETS:
                best = min(best, sum(abs(series[k][b] - series[j][b]) for b in common) / len(common))
        out[k] = best
    return out


def zone_xy(db: Session) -> dict[str, tuple[float, float]]:
    return {c.id: (c.x, c.y) for c in db.scalars(select(Classroom))}


def _nearest_room(marks: list[tuple[float, str]], ts: float, within: float = 90.0) -> str | None:
    best, best_dt = None, within + 1
    for t, room in marks:
        dt = abs(t - ts)
        if dt < best_dt:
            best, best_dt = room, dt
    return best


def evaluate_session(db: Session, svc: Services, session: SessionRow, now: float | None = None,
                     student_keys: list[str] | None = None, scenario: str | None = None,
                     publish: bool = True, persist: bool = True, use_model: bool = True) -> list[EvalResult]:
    st = svc.settings
    now = svc.clock() if now is None else now
    keys = [e.student_key for e in db.scalars(select(Enrollment).where(Enrollment.session_id == session.id))]
    if student_keys is not None:
        keys = [k for k in keys if k in set(student_keys)]
    if not keys:
        return []
    key_set = set(keys)
    sim_flag = {k: sim for k, sim in db.execute(select(Student.student_key, Student.is_simulated)
                                                  .where(Student.student_key.in_(keys)))}
    xy = zone_xy(db)

    def distance(a: str, b: str) -> float:
        if a not in xy or b not in xy:
            return 0.0
        return math.hypot(xy[a][0] - xy[b][0], xy[a][1] - xy[b][1])

    lo, hi = session.start_ts - HISTORY_S, session.end_ts + GRACE_AFTER_S
    obs = db.scalars(select(Observation).where(
        Observation.ts >= lo, Observation.ts <= hi,
        or_(Observation.student_key.in_(keys), Observation.observed_student_key.in_(keys)),
    ).order_by(Observation.ts)).all()

    # markers of every phone involved (observers and peers) to know which room they were in
    involved = set(keys)
    for o in obs:
        if o.kind == "ble_peer":
            involved.add(o.student_key)
            if o.observed_student_key:
                involved.add(o.observed_student_key)
    marks: dict[str, list[tuple[float, str]]] = defaultdict(list)
    marker_rows: dict[str, list[Observation]] = defaultdict(list)
    for o in db.scalars(select(Observation).where(
            Observation.kind == "ble_marker", Observation.student_key.in_(involved),
            Observation.ts >= lo, Observation.ts <= hi).order_by(Observation.ts)):
        if o.room and (o.rssi is None or o.rssi >= st.thresholds.marker_rssi_min):
            marks[o.student_key].append((o.ts, o.room))
        marker_rows[o.student_key].append(o)
    peers_in_room = {k for k, m in marks.items()
                     if any(r == session.classroom_id and session.start_ts - GRACE_BEFORE_S <= t <= hi
                            for t, r in m)}

    per: dict[str, StudentInputs] = {}
    for k in keys:
        per[k] = StudentInputs(
            student_key=k, session_id=session.id, session_code=session.code, room=session.classroom_id,
            start_ts=session.start_ts, end_ts=session.end_ts, now=now,
            markers=[MarkerObs(o.ts, o.duration, o.rssi if o.rssi is not None else -100.0, o.room, o.samples)
                     for o in marker_rows.get(k, [])],
            is_simulated=bool(sim_flag.get(k)), peers_in_room=peers_in_room - {k})
    win_lo = session.start_ts - GRACE_BEFORE_S
    remote_candidates: dict[str, list[tuple[str, float, float]]] = defaultdict(list)   # S -> (observer, ts, rssi)
    adverts: Counter = Counter()
    wifi_scans: Counter = Counter()
    for o in obs:
        if o.kind == "wifi" and o.student_key in per and o.wifi_zone:
            per[o.student_key].wifis.append(WifiObs(o.ts, o.wifi_zone, o.wifi_conf or 0.0,
                                                    o.wifi_source or "scan", o.scan_age_s))
            if win_lo <= o.ts <= hi:
                wifi_scans[o.student_key] += 1
        elif o.kind == "ble_peer":
            if o.student_key in per and o.observed_student_key:
                per[o.student_key].peers.append(PeerObs(o.ts, o.duration, o.rssi or -100.0,
                                                        o.observed_student_key, "observed"))
                if win_lo <= o.ts <= hi:
                    adverts[o.student_key] += o.samples
            if o.observed_student_key in per:
                per[o.observed_student_key].peers.append(PeerObs(
                    o.ts, o.duration, o.rssi or -100.0, o.student_key, "seen_by",
                    _nearest_room(marks.get(o.student_key, []), o.ts)))
                # only sightings during THIS session count towards token reuse (history is for movement context)
                if win_lo <= o.ts <= hi and (o.rssi or -100.0) >= st.thresholds.remote_rssi_min:
                    remote_candidates[o.observed_student_key].append((o.student_key, o.ts, o.rssi or -100.0))
        elif o.kind == "node_sighting" and o.observed_student_key in per and o.room == session.classroom_id:
            per[o.observed_student_key].node_sightings.append(
                MarkerObs(o.ts, o.duration, o.rssi if o.rssi is not None else -100.0, o.room, o.samples))
        elif o.kind in ("face", "rfid") and o.student_key in per:
            payload = json.loads(o.payload_json or "{}")
            payload["ts"] = o.ts
            setattr(per[o.student_key], o.kind, payload)
    locs: dict[str, list[tuple[float, str, float, float]]] = defaultdict(list)
    for loc in db.scalars(select(Location).where(
            Location.student_key.in_(keys), Location.ts >= lo, Location.ts <= hi).order_by(Location.ts)):
        # Movement is judged from verified classroom-marker detections only. Wi-Fi zone estimates are
        # probabilistic and flicker between rooms; treating them as positions made normal students "teleport".
        if loc.signal == "ble_marker":
            locs[loc.student_key].append((loc.ts, loc.zone, loc.x, loc.y))

    twins = twin_distances(marker_rows, keys, session.classroom_id, session.start_ts, session.end_ts,
                           st.thresholds.marker_rssi_min)
    results: list[EvalResult] = []
    for k in keys:
        inp = per[k]
        inp.observed_counts = {"ble_adverts_counted_on_phone": int(adverts[k]),
                               "wifi_scans_uploaded": int(wifi_scans[k]),
                               "marker_detections": len(inp.markers),
                               "measured": not inp.is_simulated}
        ev = build_evidence(inp, st.weights, st.thresholds)
        own_marks = marks.get(k, [])
        own_rooms = {r for t, r in own_marks if session.start_ts - GRACE_BEFORE_S <= t <= hi}
        remote: list[tuple[str, str]] = []
        cand = remote_candidates.get(k, [])
        if cand:
            rooms_at = [(obs_key, _nearest_room(marks.get(obs_key, []), ts)) for obs_key, ts, _ in cand]
            rooms_at = [(ok, r) for ok, r in rooms_at if r]
            base = own_rooms
            if not base and rooms_at:
                base = {Counter(r for _, r in rooms_at).most_common(1)[0][0]}
            seen = set()
            for ok, r in rooms_at:
                if r not in base and (ok, r) not in seen:
                    seen.add((ok, r))
                    remote.append((ok, r))
        ctx = RuleContext(own_rooms=own_rooms, remote_observers=remote, locations=locs.get(k, []),
                          marker_timeline=sorted(own_marks), zone_distance=distance,
                          twin_distance_db=twins.get(k, 99.0))
        hits = evaluate_rules(ev, ctx, st.thresholds)
        feats = extract_features(ev, ctx, st.thresholds)
        skip = None
        if not ev["has_evidence"] or not (ev["classroom_ble"]["detected"] or ev["wifi"]["available"]
                                          or ev["peers"]["observed_distinct"] > 0):
            skip = "no BLE/Wi-Fi/peer evidence to score"
        elif ev["elapsed_s"] < MIN_ELAPSED_FOR_IF_S:
            skip = "session started less than 2 minutes ago"
        elif ev["peers"]["observed_distinct"] < st.if_min_peers:
            skip = (f"fewer than {st.if_min_peers} distinct nearby devices: the model was trained on classroom-scale "
                    "density, so its score would be meaningless here")
        results.append(EvalResult(k, session.id, ev, hits, feats, None, None, ev["state"],
                                  inp.is_simulated, skip is None, skip))

    if use_model and svc.anomaly is not None:
        idx = [i for i, r in enumerate(results) if r.eligible_for_if]
        for i, sc in zip(idx, svc.anomaly.score([results[i].features for i in idx])):
            results[i].if_score = sc
    elif use_model:
        for r in results:
            r.if_skip_reason = r.if_skip_reason or "Isolation Forest model file not found (run ml/train_anomaly_model.py)"

    if persist:
        _persist(db, svc, session, results, now, scenario, publish)
    return results


def _persist(db: Session, svc: Services, session: SessionRow, results: list[EvalResult], now: float,
             scenario: str | None, publish: bool) -> None:
    existing_att = {a.student_key: a for a in db.scalars(select(Attendance).where(Attendance.session_id == session.id))}
    existing_an = {a.student_key: a for a in db.scalars(select(Anomaly).where(Anomaly.session_id == session.id))}
    events: list[dict] = []
    for r in results:
        hits = [h for h in r.rules if h.severity in ("warn", "high")]
        flagged = bool(hits) or bool(r.if_score and r.if_score.flagged)
        an = existing_an.get(r.student_key)
        snapshot = {**r.evidence, "rules": [h.to_dict() for h in r.rules],
                    "isolation_forest": {"evaluated": False, "reason": r.if_skip_reason or "not evaluated"} if r.if_score is None else {
                        "evaluated": True,
                        "raw_score": round(r.if_score.raw, 4), "decision_function": round(r.if_score.decision, 4),
                        "flagged": r.if_score.flagged, "risk_demo_0_100": r.if_score.risk_demo,
                        "note": "Raw sklearn score_samples (lower = more unusual). risk_demo_0_100 is a display "
                                "rescale, not a probability. Isolation Forest flags unusual combinations; it "
                                "does not decide attendance."},
                    "features": {k: round(v, 3) for k, v in r.features.items()}}
        if flagged:
            if an is None:
                an = Anomaly(session_id=session.id, student_key=r.student_key, ts=now, scenario=scenario,
                             is_simulated=r.is_simulated, status="open")   # explicit: column defaults apply only at INSERT
                db.add(an)
                new_event = True
            else:
                new_event = an.status == "cleared"
                if an.status == "cleared":
                    an.status = "open"
            if an.status == "open":
                an.ts = now
                an.rules_json = json.dumps([h.to_dict() for h in r.rules])
                an.if_raw_score = None if r.if_score is None else r.if_score.raw
                an.if_flag = bool(r.if_score and r.if_score.flagged)
                an.risk_demo = None if r.if_score is None else r.if_score.risk_demo
                an.features_json = json.dumps({k: round(v, 4) for k, v in r.features.items()})
                an.evidence_json = json.dumps(snapshot)
                if scenario:
                    an.scenario = scenario
            db.flush()
            r.anomaly_id = an.id
            if new_event:
                events.append({"type": "anomaly", "id": an.id, "student_key": r.student_key,
                               "session_id": session.id, "source": "simulated" if r.is_simulated else "live",
                               "rules": [h.rule for h in hits], "if_flag": an.if_flag})
        elif an is not None and an.status == "open":
            an.status = "cleared"            # evidence changed and nothing is flagged any more
            r.anomaly_id = an.id
        elif an is not None:
            r.anomaly_id = an.id

        review = an is not None and an.status in ("open", "confirmed")
        final = REVIEW_REQUIRED if review else r.evidence["state"]
        r.final_state = final
        row = existing_att.get(r.student_key)
        ev_json = json.dumps(snapshot)
        if row is None:
            row = Attendance(session_id=session.id, student_key=r.student_key, fused_state=r.evidence["state"],
                             final_state=final, score=r.evidence["score"], evidence_json=ev_json,
                             anomaly_id=r.anomaly_id, updated_at=now, is_simulated=r.is_simulated)
            db.add(row)
            changed = True
        else:
            changed = (row.final_state, row.fused_state) != (final, r.evidence["state"])
            row.fused_state, row.final_state, row.score = r.evidence["state"], final, r.evidence["score"]
            row.evidence_json, row.anomaly_id, row.updated_at = ev_json, r.anomaly_id, now
        if changed:
            events.append({"type": "attendance", "student_key": r.student_key, "session_id": session.id,
                           "state": final, "score": r.evidence["score"],
                           "source": "simulated" if r.is_simulated else "live"})
    db.flush()
    if publish:
        for e in events[:200]:
            svc.hub.publish(e)


def evaluate_live_student(db: Session, svc: Services, student_key: str, now: float | None = None) -> list[EvalResult]:
    """Re-evaluate every live session this student is enrolled in that is currently running."""
    now = svc.clock() if now is None else now
    sessions = db.scalars(select(SessionRow).join(Enrollment, Enrollment.session_id == SessionRow.id).where(
        Enrollment.student_key == student_key,
        SessionRow.start_ts - GRACE_BEFORE_S <= now, SessionRow.end_ts + GRACE_AFTER_S >= now))
    out: list[EvalResult] = []
    for s in sessions:
        out.extend(evaluate_session(db, svc, s, now=now, student_keys=[student_key]))
    return out
