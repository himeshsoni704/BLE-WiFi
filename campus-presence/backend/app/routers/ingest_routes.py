from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import ingest, wifi_live
from ..core import Services
from ..deps import get_db, get_svc
from ..models import Classroom
from ..schemas import BleBatchIn, NodeHeartbeatIn, PresenceIn, WifiObservationIn, WifiSurveyIn
from ..security import Principal, current_principal, rate_limit, require_roles, verify_secret_key

router = APIRouter(tags=["ingest"])
staff = require_roles("faculty", "admin")


def _guard(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@router.post("/ble-observation", dependencies=[Depends(rate_limit("upload", 240))])
def ble_observation(body: BleBatchIn, who: Principal = Depends(current_principal),
                    svc: Services = Depends(get_svc), db: Session = Depends(get_db)) -> dict:
    with svc.write_lock:
        return _guard(ingest.ingest_ble, db, svc, who, body.observations)


@router.post("/wifi-observation", dependencies=[Depends(rate_limit("upload", 240))])
def wifi_observation(body: WifiObservationIn, who: Principal = Depends(current_principal),
                     svc: Services = Depends(get_svc), db: Session = Depends(get_db)) -> dict:
    with svc.write_lock:
        return _guard(ingest.ingest_wifi, db, svc, who, body)


@router.post("/presence", dependencies=[Depends(rate_limit("upload", 240))])
def presence(body: PresenceIn, who: Principal = Depends(current_principal),
             svc: Services = Depends(get_svc), db: Session = Depends(get_db)) -> dict:
    with svc.write_lock:
        return _guard(ingest.ingest_presence, db, svc, who, body)


# ---- classroom nodes (X-Node-Key) --------------------------------------------------------------

def node_room(request: Request, x_node_key: str | None = Header(default=None), db: Session = Depends(get_db)) -> Classroom:
    if not x_node_key:
        raise HTTPException(401, "missing X-Node-Key")
    for c in db.scalars(select(Classroom).where(Classroom.node_key_hash.is_not(None))):
        if verify_secret_key(x_node_key, c.node_key_hash):
            return c
    raise HTTPException(401, "invalid node key")


@router.get("/nodes/provision", dependencies=[Depends(rate_limit("node", 60))])
def node_provision(room: Classroom = Depends(node_room), svc: Services = Depends(get_svc)) -> dict:
    """A classroom node (ESP32 / Android / Pi / laptop) fetches its marker identity and secret."""
    return {"room": room.id, "name": room.name, "marker_id": room.marker_id, "marker_idx": room.marker_idx,
            "marker_secret": room.marker_secret, "token_window_s": svc.settings.token_window_s,
            "company_id": 0xFFFF, "advertise": {"local_name": f"CP-{room.id}",
                                                  "human_readable": f"CampusPresence Room:{room.id} Session:<active>"}}


@router.post("/nodes/heartbeat", dependencies=[Depends(rate_limit("node", 120))])
def node_heartbeat(body: NodeHeartbeatIn, room: Classroom = Depends(node_room),
                   svc: Services = Depends(get_svc), db: Session = Depends(get_db)) -> dict:
    with svc.write_lock:
        return ingest.node_heartbeat(db, svc, room, body)


# ---- Wi-Fi survey and live model ---------------------------------------------------------------

@router.post("/wifi/survey", dependencies=[Depends(rate_limit("upload", 240))])
def wifi_survey(body: WifiSurveyIn, who: Principal = Depends(staff), svc: Services = Depends(get_svc),
                db: Session = Depends(get_db)) -> dict:
    """Staff-only: label the current spot as `zone` and store its Wi-Fi scans as training data."""
    with svc.write_lock:
        return _guard(ingest.save_survey, db, svc, who, body)


@router.post("/wifi/retrain")
def wifi_retrain(who: Principal = Depends(staff), svc: Services = Depends(get_svc), db: Session = Depends(get_db)) -> dict:
    with svc.write_lock:
        return wifi_live.retrain_live(db, svc)


@router.get("/wifi/reference")
def wifi_reference(who: Principal = Depends(current_principal), db: Session = Depends(get_db)) -> dict:
    return wifi_live.reference_fingerprints(db)
