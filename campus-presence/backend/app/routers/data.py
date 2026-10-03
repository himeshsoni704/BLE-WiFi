"""Read-only views for the dashboard (staff) and a student's own data."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..core import Services
from ..deps import get_db, get_svc
from ..fusion import STATES
from ..models import (AccessPoint, Anomaly, Attendance, Classroom, Enrollment, Feedback, Location,
                      Observation, SessionRow, Student, VerifiedCase, WifiTraining)
from ..schemas import SessionCreateIn
from ..security import Principal, current_principal, require_roles
from ..seed import enroll_live_students

router = APIRouter(tags=["data"])
staff = require_roles("faculty", "admin")
Source = Literal["all", "live", "simulated"]


def _src(is_sim: bool) -> str:
    return "SIMULATED" if is_sim else "LIVE"


def _sim_filter(col, source: str):
    if source == "live":
        return col == False          # noqa: E712
    if source == "simulated":
        return col == True           # noqa: E712
    return True


def _student_row(st: Student) -> dict:
    return {"student_id": st.student_id, "student_key": st.student_key, "name": st.name,
            "source": _src(st.is_simulated)}


def _find_student(db: Session, ident: str) -> Student | None:
    return db.get(Student, ident) or db.scalar(select(Student).where(Student.student_id == ident.strip().upper()))


@router.get("/students")
def students(source: Source = "all", q: str | None = Query(default=None, max_length=40), limit: int = Query(500, ge=1, le=2000),
             who: Principal = Depends(current_principal), db: Session = Depends(get_db)) -> dict:
    stmt = select(Student).order_by(Student.student_id).limit(limit)
    if who.role == "student":
        stmt = select(Student).where(Student.student_key == who.student_key)
    else:
        if source != "all":
            stmt = stmt.where(_sim_filter(Student.is_simulated, source))
        if q:
            stmt = stmt.where(or_(Student.student_id.ilike(f"%{q}%"), Student.name.ilike(f"%{q}%")))
    rows = db.scalars(stmt).all()
    states: dict[str, Counter] = defaultdict(Counter)
    for key, state in db.execute(select(Attendance.student_key, Attendance.final_state)
                                 .where(Attendance.student_key.in_([r.student_key for r in rows]))):
        states[key][state] += 1
    return {"count": len(rows),
            "totals": {"live": db.scalar(select(func.count()).select_from(Student).where(Student.is_simulated == False)),   # noqa: E712
                       "simulated": db.scalar(select(func.count()).select_from(Student).where(Student.is_simulated == True))},  # noqa: E712
            "students": [{**_student_row(r), "sessions": dict(states[r.student_key])} for r in rows]}


@router.get("/classrooms")
def classrooms(who: Principal = Depends(current_principal), db: Session = Depends(get_db),
               svc: Services = Depends(get_svc)) -> list[dict]:
    now = svc.clock()
    out = []
    for c in db.scalars(select(Classroom).order_by(Classroom.id)):
        live = (not c.is_simulated) and c.last_heartbeat is not None and now - c.last_heartbeat < 120
        out.append({"id": c.id, "name": c.name, "building": c.building, "x": c.x, "y": c.y,
                    "is_corridor": c.is_corridor,
                    "marker": None if not c.marker_idx else {
                        "marker_id": c.marker_id, "marker_idx": c.marker_idx, "label": c.node_label,
                        "kind": c.node_kind, "source": "LIVE" if live else "SIMULATED",
                        "last_heartbeat": c.last_heartbeat, "students_detected": c.detected_count}})
    return out


def _latest_per_student(db: Session, simulated: bool, at: float, window_s: float) -> list[tuple[Location, Student]]:
    rows = db.execute(select(Location, Student).join(Student, Student.student_key == Location.student_key)
                      .where(Location.ts <= at, Location.ts >= at - window_s, Location.is_simulated == simulated)
                      .order_by(Location.ts)).all()
    best: dict[str, tuple[Location, Student]] = {}
    for loc, st in rows:
        cur = best.get(loc.student_key)
        # a marker detection within 2 minutes of the newest row beats a Wi-Fi estimate
        if cur is None or (loc.signal == "ble_marker" and cur[0].signal != "ble_marker") or (
                loc.signal == cur[0].signal and loc.ts >= cur[0].ts) or (
                cur[0].signal == "ble_marker" and loc.signal == "wifi" and loc.ts - cur[0].ts > 120):
            best[loc.student_key] = (loc, st)
    return list(best.values())


@router.get("/locations")
def locations(source: Source = "all", at: float | None = None, window_s: float = Query(900, ge=30, le=7200),
              who: Principal = Depends(staff), db: Session = Depends(get_db), svc: Services = Depends(get_svc)) -> dict:
    """Latest estimated zone per student. `signal` says where it came from: ble_marker is a verified classroom
    marker detection; wifi is an ML estimate. Unless `at` is given, each source (live, simulated) is anchored to
    its own newest location: simulated data is a replayed day with its own timestamps, so a single live phone
    must not push its window past every simulated row. `anchors` reports the instant used per source."""
    sources = [(name, name == "simulated") for name in ("live", "simulated") if source in ("all", name)]
    anchors: dict[str, float] = {}
    picked: list[tuple[Location, Student]] = []
    for name, simulated in sources:
        anchor = at if at is not None else db.scalar(
            select(func.max(Location.ts)).where(Location.is_simulated == simulated))
        if anchor is None:
            continue
        anchors[name] = anchor
        picked += _latest_per_student(db, simulated, anchor, window_s)
    at = at if at is not None else (max(anchors.values()) if anchors else svc.clock())
    students_out = [{"student_id": st.student_id, "student_key": st.student_key, "name": st.name,
                     "zone": loc.zone, "x": loc.x, "y": loc.y, "signal": loc.signal,
                     "estimate": "verified marker detection" if loc.signal == "ble_marker" else "ML estimate",
                     "confidence": loc.confidence, "ts": loc.ts, "source": _src(st.is_simulated)}
                    for loc, st in picked]
    zones = Counter(s["zone"] for s in students_out)
    aps = [{"ap_id": a.ap_id, "name": a.name, "x": a.x, "y": a.y, "bssid": a.bssid,
            "source": "SIMULATED" if a.is_simulated else "LIVE"} for a in db.scalars(select(AccessPoint))]
    return {"at": at, "age_s": round(svc.clock() - at, 1), "window_s": window_s, "anchors": anchors,
            "students": students_out, "per_zone": dict(zones), "access_points": aps}


@router.get("/sessions")
def sessions(source: Source = "all", who: Principal = Depends(current_principal), db: Session = Depends(get_db),
             svc: Services = Depends(get_svc)) -> list[dict]:
    stmt = select(SessionRow).where(_sim_filter(SessionRow.is_simulated, source)).order_by(SessionRow.start_ts.desc(), SessionRow.classroom_id)
    now = svc.clock()
    counts: dict[int, Counter] = defaultdict(Counter)
    for sid, state, n in db.execute(select(Attendance.session_id, Attendance.final_state, func.count())
                                    .group_by(Attendance.session_id, Attendance.final_state)):
        counts[sid][state] = n
    out = []
    for s in db.scalars(stmt):
        out.append({"id": s.id, "code": s.code, "title": s.title, "room": s.classroom_id, "start_ts": s.start_ts,
                    "end_ts": s.end_ts, "source": _src(s.is_simulated),
                    "status": "in_progress" if s.start_ts <= now <= s.end_ts else ("completed" if now > s.end_ts else "scheduled"),
                    "counts": {st: counts[s.id].get(st, 0) for st in STATES}})
    return out


@router.post("/sessions", status_code=201)
def create_session(body: SessionCreateIn, who: Principal = Depends(staff), db: Session = Depends(get_db),
                   svc: Services = Depends(get_svc)) -> dict:
    room = db.get(Classroom, body.classroom_id)
    if room is None or not room.marker_idx:
        raise HTTPException(422, "unknown classroom or classroom without a marker")
    now = svc.clock()
    s = SessionRow(code=body.code, title=body.title or body.code, classroom_id=room.id, start_ts=now - 60,
                   end_ts=now + body.minutes * 60, is_simulated=False)
    db.add(s)
    db.flush()
    n = enroll_live_students(db, s)
    return {"id": s.id, "code": s.code, "room": room.id, "start_ts": s.start_ts, "end_ts": s.end_ts, "enrolled_live_students": n}


@router.get("/attendance")
def attendance(session_id: int | None = None, source: Source = "all", who: Principal = Depends(current_principal),
               db: Session = Depends(get_db), svc: Services = Depends(get_svc)) -> dict:
    sess = sessions(source=source, who=who, db=db, svc=svc)
    if session_id is None and sess:
        live = [s for s in sess if s["source"] == "LIVE"]
        session_id = (live or sess)[0]["id"]
    totals = Counter()
    stmt = select(Attendance.final_state, func.count()).join(SessionRow, SessionRow.id == Attendance.session_id) \
        .where(_sim_filter(SessionRow.is_simulated, source)).group_by(Attendance.final_state)
    if who.role == "student":
        stmt = stmt.where(Attendance.student_key == who.student_key)
    for state, n in db.execute(stmt):
        totals[state] = n
    rows = []
    if session_id is not None:
        q = select(Attendance, Student).join(Student, Student.student_key == Attendance.student_key) \
            .where(Attendance.session_id == session_id).order_by(Student.student_id)
        if who.role == "student":
            q = q.where(Attendance.student_key == who.student_key)
        for a, st in db.execute(q):
            ev = json.loads(a.evidence_json or "{}")
            rows.append({"student_id": st.student_id, "student_key": st.student_key, "name": st.name,
                         "source": _src(st.is_simulated), "final_state": a.final_state, "fused_state": a.fused_state,
                         "score": a.score, "anomaly_id": a.anomaly_id,
                         "components": {k: (ev.get(k) or {}).get("points") for k in
                                        ("classroom_ble", "wifi", "peers", "sustained", "face", "rfid")},
                         "families": (ev.get("independent_signal_families") or {}).get("count"),
                         "updated_at": a.updated_at})
    return {"sessions": sess, "selected_session_id": session_id, "totals": {s: totals.get(s, 0) for s in STATES},
            "score_note": "Scores are a prototype heuristic from configurable weights, not validated probabilities.",
            "rows": rows}


@router.get("/evidence/{student_id}")
def evidence(student_id: str, session_id: int | None = None, who: Principal = Depends(current_principal),
             db: Session = Depends(get_db)) -> dict:
    st = _find_student(db, student_id)
    if st is None:
        raise HTTPException(404, "unknown student")
    if who.role == "student" and who.student_key != st.student_key:
        raise HTTPException(403, "students may only view their own evidence")
    q = select(Attendance, SessionRow).join(SessionRow, SessionRow.id == Attendance.session_id) \
        .where(Attendance.student_key == st.student_key).order_by(SessionRow.start_ts.desc())
    if session_id:
        q = q.where(Attendance.session_id == session_id)
    sessions_out, windows = [], []
    for a, s in db.execute(q):
        an = db.get(Anomaly, a.anomaly_id) if a.anomaly_id else None
        sessions_out.append({"session": {"id": s.id, "code": s.code, "room": s.classroom_id, "start_ts": s.start_ts,
                                         "end_ts": s.end_ts, "source": _src(s.is_simulated)},
                             "final_state": a.final_state, "fused_state": a.fused_state, "score": a.score,
                             "evidence": json.loads(a.evidence_json or "{}"),
                             "anomaly": None if an is None else {"id": an.id, "status": an.status}})
        windows.append((s.start_ts - 300, s.end_ts + 120))
    timeline = []
    if windows:
        lo, hi = min(w[0] for w in windows), max(w[1] for w in windows)
        if session_id is None and len(windows) > 1:
            lo = max(lo, hi - 4 * 3600)
        for o in db.scalars(select(Observation).where(
                or_(Observation.student_key == st.student_key, Observation.observed_student_key == st.student_key),
                Observation.ts >= lo, Observation.ts <= hi).order_by(Observation.ts).limit(1500)):
            timeline.append({"ts": o.ts, "kind": o.kind, "direction": "seen_by_peer" if o.observed_student_key == st.student_key and o.kind == "ble_peer" else "observed",
                             "rssi": o.rssi, "duration": o.duration, "room": o.room, "wifi_zone": o.wifi_zone,
                             "wifi_conf": o.wifi_conf, "wifi_source": o.wifi_source,
                             "peer": (o.observed_student_key if o.student_key == st.student_key else o.student_key) if o.kind == "ble_peer" else None,
                             "provenance": ("estimated" if o.kind == "wifi" else "measured") if not o.is_simulated else "simulated"})
    return {"student": _student_row(st), "sessions": sessions_out, "timeline": timeline[-600:]}


def _anomaly_row(a: Anomaly, st: Student, s: SessionRow) -> dict:
    rules = json.loads(a.rules_json or "[]")
    return {"id": a.id, "student_id": st.student_id, "student_key": st.student_key, "name": st.name,
            "session_id": s.id, "session_code": s.code, "room": s.classroom_id, "ts": a.ts,
            "rules": [r["rule"] for r in rules], "rule_details": rules,
            "isolation_score_raw": a.if_raw_score, "isolation_flagged": a.if_flag, "risk_demo_0_100": a.risk_demo,
            "status": a.status, "scenario": a.scenario, "source": _src(a.is_simulated),
            "has_explanation": a.explanation_json is not None, "explanation_provider": a.explanation_provider}


@router.get("/anomalies")
def anomalies(status: str | None = Query(default=None, pattern="^(open|confirmed|false_positive|cleared)$"),
              source: Source = "all", scenario: str | None = Query(default=None, max_length=32),
              limit: int = Query(200, ge=1, le=1000), who: Principal = Depends(staff), db: Session = Depends(get_db)) -> dict:
    q = select(Anomaly, Student, SessionRow).join(Student, Student.student_key == Anomaly.student_key) \
        .join(SessionRow, SessionRow.id == Anomaly.session_id).where(_sim_filter(Anomaly.is_simulated, source))
    if status:
        q = q.where(Anomaly.status == status)
    if scenario:
        q = q.where(Anomaly.scenario == scenario)
    rows = db.execute(q.order_by(Anomaly.ts.desc(), Anomaly.id.desc()).limit(limit)).all()
    counts = Counter(s for (s,) in db.execute(select(Anomaly.status).where(_sim_filter(Anomaly.is_simulated, source))))
    return {"count": len(rows), "by_status": dict(counts),
            "note": ("Rules are deterministic facts; Isolation Forest scores are raw sklearn values (lower = more "
                     "unusual) and risk_demo_0_100 is a display rescale, not a probability. Neither decides attendance."),
            "anomalies": [_anomaly_row(a, st, s) for a, st, s in rows]}


@router.get("/anomalies/{anomaly_id}")
def anomaly_detail(anomaly_id: int, who: Principal = Depends(staff), db: Session = Depends(get_db)) -> dict:
    a = db.get(Anomaly, anomaly_id)
    if a is None:
        raise HTTPException(404, "unknown anomaly")
    st, s = db.get(Student, a.student_key), db.get(SessionRow, a.session_id)
    fb = [{"id": f.id, "user": f.username, "action": f.action, "comment": f.comment, "label": f.label, "ts": f.created_at}
          for f in db.scalars(select(Feedback).where(Feedback.anomaly_id == a.id).order_by(Feedback.id))]
    return {**_anomaly_row(a, st, s), "evidence": json.loads(a.evidence_json or "{}"),
            "features": json.loads(a.features_json or "{}"),
            "explanation": json.loads(a.explanation_json) if a.explanation_json else None,
            "explanation_ts": a.explanation_ts, "feedback": fb}


@router.get("/summary")
def summary(source: Source = "all", who: Principal = Depends(staff), db: Session = Depends(get_db),
            svc: Services = Depends(get_svc)) -> dict:
    now = svc.clock()
    att = Counter()
    for state, n in db.execute(select(Attendance.final_state, func.count()).join(SessionRow, SessionRow.id == Attendance.session_id)
                               .where(_sim_filter(SessionRow.is_simulated, source)).group_by(Attendance.final_state)):
        att[state] = n
    active_rooms = {r for (r,) in db.execute(select(SessionRow.classroom_id).where(
        SessionRow.start_ts <= now, SessionRow.end_ts >= now, _sim_filter(SessionRow.is_simulated, source)))}
    live_nodes = [c.id for c in db.scalars(select(Classroom).where(Classroom.is_simulated == False))   # noqa: E712
                  if c.last_heartbeat and now - c.last_heartbeat < 120]
    tok = db.scalar(select(func.count(func.distinct(Observation.observed_student_key))).where(
        Observation.kind == "ble_peer", _sim_filter(Observation.is_simulated, source)))
    aps_seen = db.scalar(select(func.count()).select_from(AccessPoint).where(
        _sim_filter(AccessPoint.is_simulated, source)))
    an = Counter(s for (s,) in db.execute(select(Anomaly.status).where(_sim_filter(Anomaly.is_simulated, source))))
    return {
        "attendance": {s: att.get(s, 0) for s in STATES},
        "active_classrooms": sorted(active_rooms), "live_nodes_online": live_nodes,
        "ble_devices_seen": tok, "wifi_access_points_registered": aps_seen,
        "anomalies": dict(an), "verified_cases": db.scalar(select(func.count()).select_from(VerifiedCase)),
        "wifi_survey_samples": db.scalar(select(func.count()).select_from(WifiTraining).where(WifiTraining.source == "live_survey")),
        "students": {"live": db.scalar(select(func.count()).select_from(Student).where(Student.is_simulated == False)),   # noqa: E712
                     "simulated": db.scalar(select(func.count()).select_from(Student).where(Student.is_simulated == True))},  # noqa: E712
        "measured_counters_since_start": {k: v for k, v in svc.metrics.items()},
        "unavailable_metrics": ["BLE/Wi-Fi packet rates are not exposed by Android, so none are shown. Counts above "
                                "are observations the phones actually uploaded."],
    }
