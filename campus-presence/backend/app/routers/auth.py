from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core import Services
from ..deps import get_db, get_svc
from ..models import AccessPoint, Classroom, Student, User
from ..schemas import LoginIn
from ..security import (Principal, create_jwt, current_principal, rate_limit, verify_password)

router = APIRouter(tags=["auth"])
_DUMMY_HASH = "pbkdf2_sha256$1000$AAAAAAAAAAAAAAAAAAAAAA==$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="


@router.post("/auth/login", dependencies=[Depends(rate_limit("login", 10))])
def login(body: LoginIn, svc: Services = Depends(get_svc), db: Session = Depends(get_db)) -> dict:
    name = body.username.strip()
    user = db.scalar(select(User).where(User.username == name)) or db.scalar(
        select(User).where(User.username == name.upper()))
    ok = verify_password(body.password, user.password_hash if user else _DUMMY_HASH)   # constant-ish time
    if not user or not ok:
        raise HTTPException(401, "invalid credentials")
    token = create_jwt(svc.settings, sub=user.username, role=user.role, student_key=user.student_key)
    return {"access_token": token, "token_type": "bearer", "expires_in": svc.settings.jwt_ttl_s,
            "role": user.role, "username": user.username}


@router.get("/auth/me")
def me(who: Principal = Depends(current_principal)) -> dict:
    return {"username": who.sub, "role": who.role, "student_key": who.student_key}


@router.get("/auth/provision")
def provision(who: Principal = Depends(current_principal), svc: Services = Depends(get_svc),
              db: Session = Depends(get_db)) -> dict:
    """What a student phone needs after login: its own token secret and the public campus catalogue.
    The secret is returned only to its owner. Use HTTPS outside a lab network."""
    if who.role != "student" or not who.student_key:
        raise HTTPException(403, "student accounts only")
    st = db.get(Student, who.student_key)
    classrooms = db.scalars(select(Classroom).where(Classroom.marker_idx.is_not(None)).order_by(Classroom.marker_idx))
    return {
        "student_key": st.student_key, "display_name": st.name,
        "token_secret": st.token_secret, "token_window_s": svc.settings.token_window_s,
        "company_id": 0xFFFF,
        "classrooms": [{"marker_idx": c.marker_idx, "room": c.id, "name": c.name, "marker_id": c.marker_id}
                       for c in classrooms],
        "ap_catalogue": [{"ap_id": a.ap_id, "bssid": a.bssid, "frequency_mhz": a.frequency_mhz}
                         for a in db.scalars(select(AccessPoint).where(AccessPoint.bssid.is_not(None)))],
        "privacy": {"broadcasts_real_identity": False, "token_rotation_s": svc.settings.token_window_s,
                    "uploads": ["rotating tokens seen", "RSSI", "timestamps", "durations", "Wi-Fi RSSI of campus APs"]},
    }
