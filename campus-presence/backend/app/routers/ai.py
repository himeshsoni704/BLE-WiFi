"""Explanations (LLM), RAG retrieval and faculty feedback."""
from __future__ import annotations

import csv
import json
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from simulation import campus

from ..core import Services
from ..db import session_scope
from ..deps import get_db, get_svc
from .. import xai
from ..llm import MockLLMProvider, evidence_view, explain_with_fallback
from ..models import Anomaly, Attendance, Feedback, SessionRow, Student, VerifiedCase
from ..rag import tags_from_snapshot
from ..schemas import ExplainIn, FeedbackIn, RagQueryIn
from ..security import Principal, rate_limit, require_roles

router = APIRouter(tags=["ai"])
staff = require_roles("faculty", "admin")


def _zone_distance(a: str, b: str) -> float:
    return campus.zone_distance(a, b)


def prepare(svc: Services, anomaly_id: int) -> dict:
    with session_scope(svc.SessionLocal) as db:
        a = db.get(Anomaly, anomaly_id)
        if a is None:
            raise HTTPException(404, "unknown anomaly")
        st, s = db.get(Student, a.student_key), db.get(SessionRow, a.session_id)
        snap = json.loads(a.evidence_json or "{}")
        rules = json.loads(a.rules_json or "[]")
        label = st.student_id if svc.settings.gemini_send_real_ids else f"S-{st.student_key[:8]}"
        # Why the forest scored this record as unusual. Only meaningful if the forest actually evaluated it.
        if a.if_raw_score is None:
            attribution = xai.unavailable("the Isolation Forest did not evaluate this record: " + str(
                (snap.get("isolation_forest") or {}).get("reason") or "not evaluated"))
        else:
            attribution = xai.attribute(svc.anomaly, json.loads(a.features_json or "{}"))
        view = evidence_view(snap, student_label=label, expected_room=s.classroom_id, session_code=s.code,
                             state=snap.get("state", ""), risk_demo=a.risk_demo, if_raw=a.if_raw_score,
                             if_flag=a.if_flag, rules=rules, attribution=attribution)
        tags = tags_from_snapshot({**snap, "rules": rules}, _zone_distance)
        if attribution.get("available"):
            tags = sorted({*tags, *(f"feat_{d['feature']}" for d in attribution["top"] if d["share_pct"] >= 15)})
        return {"view": view, "tags": tags, "attribution": attribution, "snapshot": snap, "rules": rules, "room": s.classroom_id,
                "session_code": s.code, "if_raw": a.if_raw_score, "if_flag": a.if_flag, "risk": a.risk_demo,
                "status": a.status, "source": "SIMULATED" if a.is_simulated else "LIVE"}


def evidence_panel(p: dict) -> dict:
    v = p["view"]
    conf = v.get("wifi_confidence")
    iso = "NOT EVALUATED" if p["if_raw"] is None else ("ANOMALOUS" if p["if_flag"] else "NORMAL")
    return {
        "ble_classroom_marker": f"Room {v['ble_room']}" if v.get("ble_room") else "not detected in the session room",
        "ble_other_rooms": v.get("ble_other_rooms_detected", []),
        "wifi_prediction": f"Room {v['wifi_room']}" if v.get("wifi_room") else "unavailable",
        "wifi_confidence_pct": None if conf is None else round(conf * 100),
        "token_reuse_devices": v.get("token_reuse_count", 0),
        "movement_speed_mps": v.get("movement_speed_mps"),
        "isolation_forest": iso, "isolation_score_raw": None if p["if_raw"] is None else round(p["if_raw"], 3),
        "isolation_forest_note": (p["snapshot"].get("isolation_forest") or {}).get("reason")
                                 if p["if_raw"] is None else None,
        "demo_risk_score_0_100": p["risk"],
        "risk_note": "demo risk score = display rescale of the raw score; not a probability",
        "rules": [{"rule": r["rule"], "severity": r["severity"], "detail": r["detail"]} for r in p["rules"]],
        "fusion_state": v.get("fusion_state"), "fusion_score": v.get("fusion_score"),
    }


@router.post("/explain-anomaly", dependencies=[Depends(rate_limit("explain", 30))])
async def explain_anomaly(body: ExplainIn, request: Request, who: Principal = Depends(staff)) -> dict:
    svc: Services = request.app.state.svc
    p = await run_in_threadpool(prepare, svc, body.anomaly_id)
    cases = svc.rag.retrieve(p["tags"], k=3)
    knowledge = svc.kb.retrieve(p["tags"], k=4) if svc.kb else []
    primary = svc.llm
    if body.provider == "mock":
        primary = MockLLMProvider()
    elif body.provider == "gemini" and svc.llm.name != "gemini":
        raise HTTPException(409, getattr(svc, "llm_note", None) or "Gemini is not configured "
                            "(set LLM_PROVIDER=gemini, GEMINI_API_KEY and GEMINI_MODEL)")
    exp = await explain_with_fallback(primary, p["view"], cases, knowledge)
    payload = exp.to_dict()

    def store() -> None:
        with session_scope(svc.SessionLocal) as db:
            a = db.get(Anomaly, body.anomaly_id)
            a.explanation_json = json.dumps({**payload, "similar_case_ids": [c["case_id"] for c in cases]})
            a.explanation_provider = payload["provider"]
            a.explanation_ts = time.time()
    await run_in_threadpool(store)
    return {"anomaly_id": body.anomaly_id, "source": p["source"], "evidence": evidence_panel(p),
            "similar_cases": cases, "knowledge": knowledge, "attribution": p["attribution"], "explanation": payload,
            "provider": {"configured": svc.llm.name, "used": payload["provider"], "model": payload.get("model"),
                         "note": getattr(svc, "llm_note", None), "fallback_reason": payload.get("fallback_reason")},
            "disclaimer": ("The LLM explains the system's structured evidence. It does not decide attendance, "
                           "does not override the rules or Isolation Forest, and sees only the evidence shown here.")}


@router.post("/rag/retrieve")
def rag_retrieve(body: RagQueryIn, request: Request, who: Principal = Depends(staff)) -> dict:
    svc: Services = request.app.state.svc
    if body.anomaly_id is None and not body.query:
        raise HTTPException(422, "provide anomaly_id or query")
    tags: list[str] = []
    text = body.query or ""
    if body.anomaly_id is not None:
        p = prepare(svc, body.anomaly_id)
        tags = p["tags"]
    return {"tags_used": tags, "index_size": svc.rag.size(), "method": "TF-IDF cosine over tags + narrative (lexical, not semantic)",
            "cases": svc.rag.retrieve(tags, text, k=body.k),
            "knowledge": svc.kb.retrieve(tags, text, k=body.k) if svc.kb else []}


# ---- feedback ---------------------------------------------------------------------------------------

def _facts_summary(p: dict, resolution: str) -> str:
    v = p["view"]
    bits = [f"Session {v['session']} in Room {v['expected_room']}"]
    if v.get("bluetooth_wifi_mismatch"):
        m = v["bluetooth_wifi_mismatch"]
        bits.append(f"BLE marker Room {m['ble_room']} vs Wi-Fi Room {m['wifi_room']}"
                    + (f" (confidence {round(v['wifi_confidence'] * 100)}%)" if v.get("wifi_confidence") is not None else ""))
    if v.get("token_reuse_count"):
        bits.append(f"token seen on {v['token_reuse_count']} other device(s)")
    if v.get("movement_speed_mps"):
        bits.append(f"implied speed {v['movement_speed_mps']} m/s")
    if v.get("isolation_flagged"):
        bits.append("Isolation Forest flagged an unusual combination")
    return "; ".join(bits) + f". Faculty resolution: {resolution.replace('_', ' ')}."


def write_feedback_dataset(svc: Services, db: Session) -> int:
    path = Path(svc.settings.data_dir) / "feedback_dataset.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    from ..anomaly import FEATURES
    rows = db.execute(select(Feedback, Anomaly).join(Anomaly, Anomaly.id == Feedback.anomaly_id)
                      .where(Feedback.label.is_not(None)).order_by(Feedback.id)).all()
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["feedback_id", "anomaly_id", "label", "scenario", "source", *FEATURES])
        for f, a in rows:
            feats = json.loads(f.features_json or "{}")
            w.writerow([f.id, a.id, f.label, a.scenario or "", "simulated" if a.is_simulated else "live",
                        *[feats.get(k, "") for k in FEATURES]])
    return len(rows)


@router.post("/feedback")
def feedback(body: FeedbackIn, request: Request, who: Principal = Depends(staff),
             svc: Services = Depends(get_svc), db: Session = Depends(get_db)) -> dict:
    a = db.get(Anomaly, body.anomaly_id)
    if a is None:
        raise HTTPException(404, "unknown anomaly")
    label = {"confirm": "anomaly", "false_positive": "normal"}.get(body.action)
    case_id = None
    with svc.write_lock:
        db.add(Feedback(anomaly_id=a.id, username=who.sub, action=body.action, comment=body.comment,
                        label=label, features_json=a.features_json))
        if body.action in ("confirm", "false_positive"):
            a.status = "confirmed" if body.action == "confirm" else "false_positive"
            resolution = "confirmed_anomaly" if body.action == "confirm" else "false_positive"
            snap = json.loads(a.evidence_json or "{}")
            rules = json.loads(a.rules_json or "[]")
            s = db.get(SessionRow, a.session_id)
            st = db.get(Student, a.student_key)
            view = evidence_view(snap, student_label=f"S-{st.student_key[:8]}", expected_room=s.classroom_id,
                                 session_code=s.code, state=snap.get("state", ""), risk_demo=a.risk_demo,
                                 if_raw=a.if_raw_score, if_flag=a.if_flag, rules=rules)
            tags = tags_from_snapshot({**snap, "rules": rules}, _zone_distance)
            pk = {"view": view}
            title = ("Confirmed: " if body.action == "confirm" else "False positive: ") + (
                ", ".join(r["rule"].replace("_", " ") for r in rules) or "Isolation Forest flag") + f" (Room {s.classroom_id})"
            case = db.scalar(select(VerifiedCase).where(VerifiedCase.anomaly_id == a.id))
            if case is None:
                case = VerifiedCase(title=title[:160], summary=_facts_summary(pk, resolution),
                                    structured_json=json.dumps({"tags": tags, "evidence_view": view}),
                                    resolution=resolution, faculty_comment=body.comment, origin="faculty_feedback",
                                    anomaly_id=a.id)
                db.add(case)
            else:
                case.resolution, case.faculty_comment, case.title = resolution, body.comment or case.faculty_comment, title[:160]
                case.summary = _facts_summary(pk, resolution)
            db.flush()
            case_id = case.id
            att = db.scalar(select(Attendance).where(Attendance.session_id == a.session_id,
                                                      Attendance.student_key == a.student_key))
            if att:
                att.final_state = att.fused_state if body.action == "false_positive" else "REVIEW_REQUIRED"
        db.flush()
        n_train = write_feedback_dataset(svc, db)
    svc.rag.rebuild()
    svc.hub.publish({"type": "feedback", "anomaly_id": a.id, "action": body.action, "verified_case_id": case_id})
    return {"anomaly_id": a.id, "status": a.status, "verified_case_id": case_id, "rag_cases_total": svc.rag.size(),
            "training_rows_total": n_train,
            "attendance_final_state": None if not case_id else (att.final_state if att else None),
            "notes": ["Stored as feedback and, for confirm/false-positive, as a verified case in the RAG index "
                      "(it will be retrieved for similar anomalies).",
                      "Added to the retraining dataset (ml/data/feedback_dataset.csv). RAG does NOT retrain the "
                      "Isolation Forest; run `python ml/retrain_anomaly_model.py` to retrain it explicitly."]}


@router.get("/feedback/summary")
def feedback_summary(who: Principal = Depends(staff), db: Session = Depends(get_db), svc: Services = Depends(get_svc)) -> dict:
    fb = db.execute(select(Feedback, Anomaly).join(Anomaly, Anomaly.id == Feedback.anomaly_id).order_by(Feedback.id.desc()).limit(100)).all()
    cases = db.scalars(select(VerifiedCase).order_by(VerifiedCase.id.desc())).all()
    return {"feedback": [{"id": f.id, "anomaly_id": f.anomaly_id, "user": f.username, "action": f.action,
                          "comment": f.comment, "label": f.label, "ts": f.created_at,
                          "source": "SIMULATED" if a.is_simulated else "LIVE"} for f, a in fb],
            "verified_cases": [{"case_id": c.id, "title": c.title, "summary": c.summary, "resolution": c.resolution,
                                "faculty_comment": c.faculty_comment, "origin": c.origin, "anomaly_id": c.anomaly_id,
                                "ts": c.created_at} for c in cases],
            "training_rows": db.scalar(select(func.count()).select_from(Feedback).where(Feedback.label.is_not(None))),
            "retrain_command": "python ml/retrain_anomaly_model.py",
            "note": "RAG is retrieval only. The Isolation Forest changes only when you run the retrain script."}
