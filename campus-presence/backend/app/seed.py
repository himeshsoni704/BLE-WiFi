"""Seed the campus layout (classrooms, markers, APs) and demo accounts."""
from __future__ import annotations

import secrets
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from simulation import campus

from .config import Settings
from .models import AccessPoint, Classroom, SessionRow, Student, User, Enrollment
from .security import hash_password, hash_secret_key, new_token_secret, student_key_for

LIVE_SESSION_CODE = "CS301"
LIVE_ROOM = "204"


def demo_node_key(room: str, settings: Settings) -> str:
    """Deterministic DEMO-ONLY node key so the README can quote it. Disable with DEMO_MODE=0."""
    return f"node-{room}-demo"


def seed_campus(db: Session, settings: Settings) -> dict:
    created = {"classrooms": 0, "access_points": 0}
    have = {c.id for c in db.scalars(select(Classroom))}
    for z in campus.ZONES:
        if z.id in have:
            continue
        c = Classroom(id=z.id, name=z.name, building=f"Block {z.block}", x=z.x, y=z.y,
                      is_corridor=z.is_corridor, marker_idx=z.marker_idx, marker_id=z.marker_id,
                      marker_secret=new_token_secret() if z.marker_idx else None,
                      node_kind="simulated", node_label=f"Smart Board — {z.name}" if z.marker_idx else None,
                      is_simulated=True)
        if z.marker_idx and settings.demo_mode:
            c.node_key_hash = hash_secret_key(demo_node_key(z.id, settings))
        db.add(c)
        created["classrooms"] += 1
    have_ap = {a.ap_id for a in db.scalars(select(AccessPoint))}
    for ap in campus.APS:
        if ap.ap_id not in have_ap:
            db.add(AccessPoint(ap_id=ap.ap_id, bssid=None, name=ap.name, x=ap.x, y=ap.y,
                               frequency_mhz=ap.frequency_mhz, is_simulated=True))
            created["access_points"] += 1
    db.flush()
    return created


def ensure_user(db: Session, settings: Settings, username: str, password: str, role: str,
                student_key: str | None = None) -> User:
    u = db.scalar(select(User).where(User.username == username))
    if u is None:
        u = User(username=username, password_hash=hash_password(password, settings.password_iterations),
                 role=role, student_key=student_key)
        db.add(u)
        db.flush()
    return u


def create_student(db: Session, settings: Settings, student_id: str, name: str, password: str,
                   simulated: bool = False) -> Student:
    key = student_key_for(student_id, settings.pepper)
    st = db.get(Student, key)
    if st is None:
        st = Student(student_key=key, student_id=student_id.strip().upper(), name=name,
                     token_secret=new_token_secret(), is_simulated=simulated)
        db.add(st)
    ensure_user(db, settings, student_id.strip().upper(), password, "student", key)
    db.flush()
    return st


def seed_demo_accounts(db: Session, settings: Settings) -> list[str]:
    """DEMO ONLY. Accounts share one documented password; set DEMO_MODE=0 to skip."""
    pw = settings.demo_password
    ensure_user(db, settings, "faculty", pw, "faculty")
    ensure_user(db, settings, "admin", pw, "admin")
    names = ["HIMESH", "STU101", "STU102", "STU103"]
    for sid in names:
        create_student(db, settings, sid, sid.title() if sid == "HIMESH" else sid, pw, simulated=False)
    return names


def ensure_live_session(db: Session, settings: Settings, now: float | None = None,
                        minutes: int = 60) -> SessionRow:
    """A rolling live session in Room 204 so a real phone can show attendance immediately."""
    now = now or time.time()
    s = db.scalar(select(SessionRow).where(SessionRow.code == LIVE_SESSION_CODE,
                                           SessionRow.is_simulated == False,       # noqa: E712
                                           SessionRow.end_ts > now))
    if s is None:
        s = SessionRow(code=LIVE_SESSION_CODE, title="CS301 — live demo session",
                       classroom_id=LIVE_ROOM, start_ts=now - 120, end_ts=now + minutes * 60,
                       is_simulated=False)
        db.add(s)
        db.flush()
    enroll_live_students(db, s)
    return s


def enroll_live_students(db: Session, session: SessionRow) -> int:
    live = db.scalars(select(Student.student_key).where(Student.is_simulated == False))   # noqa: E712
    have = {e.student_key for e in db.scalars(select(Enrollment).where(Enrollment.session_id == session.id))}
    n = 0
    for key in live:
        if key not in have:
            db.add(Enrollment(session_id=session.id, student_key=key))
            n += 1
    db.flush()
    return n
