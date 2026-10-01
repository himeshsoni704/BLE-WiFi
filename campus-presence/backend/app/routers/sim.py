"""Demo Mode: start/reset the synthetic campus and inject scenarios. Everything it writes is SIMULATED."""
from __future__ import annotations

import threading
import time

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from simulation.generator import CampusSim

from ..core import Services
from ..db import session_scope
from ..deps import get_db, get_svc
from ..schemas import SimulationStartIn
from ..security import Principal, require_roles

router = APIRouter(tags=["simulation"])
staff = require_roles("faculty", "admin")


def _full_job(svc: Services, n: int, seed: int) -> dict:
    svc.sim_state.update(running=True, progress=0.0, message="starting", error=None, started=time.time())
    try:
        def prog(f: float, m: str) -> None:
            svc.sim_state.update(progress=round(f, 3), message=m)
            svc.hub.publish({"type": "simulation", "progress": round(f, 3), "message": m})
        with session_scope(svc.SessionLocal) as db:
            sim = CampusSim(svc, n_students=n, seed=seed)
            summary = sim.run_full(db, prog)
        svc.sim = sim
        summary["seconds"] = round(time.time() - svc.sim_state["started"], 1)
        svc.sim_state.update(last=summary, progress=1.0, message="done")
        svc.hub.publish({"type": "simulation", "progress": 1.0, "message": "done", "summary": summary})
        return summary
    except Exception as exc:                          # surfaced via /simulation/status
        svc.sim_state.update(error=f"{type(exc).__name__}: {exc}", message="failed")
        svc.hub.publish({"type": "simulation", "progress": 0, "message": "failed", "error": str(exc)})
        raise
    finally:
        svc.sim_state["running"] = False


def _get_sim(svc: Services, db: Session, n: int, seed: int) -> CampusSim:
    if svc.sim is None:
        sim = CampusSim(svc, n_students=n, seed=seed)
        if not sim.load(db):
            raise HTTPException(409, "no simulation yet: press Start Simulation first")
        svc.sim = sim
        for key in {k for ks in sim.enrolled.values() for k in ks}:      # mark everyone as 'normal' baseline
            pass
    return svc.sim


@router.post("/simulation/start", status_code=202)
def start(body: SimulationStartIn, who: Principal = Depends(staff), svc: Services = Depends(get_svc),
          db: Session = Depends(get_db)) -> dict:
    if svc.wifi_sim is None:
        raise HTTPException(409, "simulated Wi-Fi model missing: run `python ml/train_wifi_model.py`")
    if body.scenario == "full":
        if svc.sim_state.get("running"):
            raise HTTPException(409, "a simulation is already running")
        if body.wait:
            return {"status": "completed", "summary": _full_job(svc, body.students, body.seed)}
        threading.Thread(target=lambda: _safe(svc, body), daemon=True, name="campus-sim").start()
        return {"status": "started", "poll": "GET /simulation/status", "live_labels": "all rows are SIMULATED"}
    if svc.sim_state.get("running"):
        raise HTTPException(409, "wait for the running simulation to finish")
    sim = _get_sim(svc, db, body.students, body.seed)
    if not sim.labels:
        _rebuild_labels(sim, db)
    try:
        with svc.write_lock:
            out = sim.scenario(db, body.scenario)
            db.commit()
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))
    svc.sim_state["last_scenario"] = {k: v for k, v in out.items() if k != "target_student_keys"}
    return {"status": "completed", **out, "label": "SIMULATED"}


def _safe(svc: Services, body: SimulationStartIn) -> None:
    try:
        _full_job(svc, body.students, body.seed)
    except Exception:
        pass


def _rebuild_labels(sim: CampusSim, db: Session) -> None:
    """After a server restart the in-memory 'who attended normally' labels are gone: derive them from attendance rows."""
    from sqlalchemy import select
    from ..models import Attendance
    for sid, key, state in db.execute(select(Attendance.session_id, Attendance.student_key, Attendance.fused_state)
                                      .where(Attendance.is_simulated == True)):    # noqa: E712
        if state != "ABSENT":
            sim.labels[(sid, key)] = "normal"


@router.post("/simulation/reset")
def reset(who: Principal = Depends(staff), svc: Services = Depends(get_svc), db: Session = Depends(get_db)) -> dict:
    if svc.sim_state.get("running"):
        raise HTTPException(409, "a simulation is running")
    with svc.write_lock:
        counts = CampusSim(svc).reset(db)
        db.commit()
    svc.sim = None
    svc.sim_state.update(last=None, progress=0.0, message="reset", error=None)
    svc.hub.publish({"type": "simulation", "progress": 0, "message": "reset"})
    return {"status": "reset", "deleted_simulated_rows": counts,
            "kept": "live data, accounts, and verified cases (faculty feedback) are untouched"}


@router.get("/simulation/status")
def status(who: Principal = Depends(staff), svc: Services = Depends(get_svc), db: Session = Depends(get_db)) -> dict:
    from sqlalchemy import func, select
    from ..models import Student
    n = db.scalar(select(func.count()).select_from(Student).where(Student.is_simulated == True))   # noqa: E712
    return {**svc.sim_state, "simulated_students": n, "exists": bool(n)}
