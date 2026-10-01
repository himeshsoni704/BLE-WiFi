"""SQLAlchemy tables.

Privacy note: observations, locations, attendance and anomalies refer to students
only through `student_key`, a keyed hash of the real student id (see security.py).
The real id and name live only in `students`, which the API exposes to faculty/admin.
"""
from __future__ import annotations

import time

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def _now() -> float:
    return time.time()


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(16))               # student | faculty | admin
    student_key: Mapped[str | None] = mapped_column(String(32), nullable=True)


class Student(Base):
    __tablename__ = "students"
    student_key: Mapped[str] = mapped_column(String(32), primary_key=True)
    student_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    token_secret: Mapped[str] = mapped_column(String(64))       # HMAC key for rotating BLE tokens
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[float] = mapped_column(Float, default=_now)


class Classroom(Base):
    __tablename__ = "classrooms"
    id: Mapped[str] = mapped_column(String(16), primary_key=True)          # "204"
    name: Mapped[str] = mapped_column(String(64))                          # "Room 204"
    building: Mapped[str] = mapped_column(String(32), default="")
    x: Mapped[float] = mapped_column(Float, default=0)                     # metres, campus plan
    y: Mapped[float] = mapped_column(Float, default=0)
    is_corridor: Mapped[bool] = mapped_column(Boolean, default=False)
    marker_idx: Mapped[int | None] = mapped_column(Integer, unique=True, nullable=True)
    marker_id: Mapped[str | None] = mapped_column(String(64), nullable=True)   # "ROOM_204_BEACON"
    marker_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    node_key_hash: Mapped[str | None] = mapped_column(String(256), nullable=True)
    node_kind: Mapped[str] = mapped_column(String(24), default="simulated")    # android|esp32|smartboard|laptop|simulated
    node_label: Mapped[str | None] = mapped_column(String(80), nullable=True)  # "Smart Board — Room 204"
    last_heartbeat: Mapped[float | None] = mapped_column(Float, nullable=True)
    detected_count: Mapped[int] = mapped_column(Integer, default=0)
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=False)


class AccessPoint(Base):
    __tablename__ = "access_points"
    ap_id: Mapped[str] = mapped_column(String(32), primary_key=True)       # "AP_01"
    bssid: Mapped[str | None] = mapped_column(String(24), unique=True, nullable=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    x: Mapped[float] = mapped_column(Float, default=0)
    y: Mapped[float] = mapped_column(Float, default=0)
    frequency_mhz: Mapped[int] = mapped_column(Integer, default=2437)
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=False)


class SessionRow(Base):
    __tablename__ = "sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), index=True)              # "CS301"
    title: Mapped[str] = mapped_column(String(120), default="")
    classroom_id: Mapped[str] = mapped_column(ForeignKey("classrooms.id"))
    start_ts: Mapped[float] = mapped_column(Float)
    end_ts: Mapped[float] = mapped_column(Float)
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=False)


class Enrollment(Base):
    __tablename__ = "enrollments"
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), primary_key=True)
    student_key: Mapped[str] = mapped_column(ForeignKey("students.student_key"), primary_key=True)


class Observation(Base):
    """One row of evidence produced by a phone (or node). `kind` selects which columns apply.

    ble_peer     observer saw observed_token (another phone) for `duration` seconds
    ble_marker   observer saw a classroom marker (marker_idx) for `duration` seconds
    node_sighting a classroom node saw a student token
    wifi         Wi-Fi fingerprint (wifi_json) with the backend's zone estimate
    face / rfid  optional external-system evidence (payload_json)
    """
    __tablename__ = "observations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    session_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    student_key: Mapped[str] = mapped_column(String(32))                   # the observer
    ts: Mapped[float] = mapped_column(Float)
    duration: Mapped[float] = mapped_column(Float, default=0)
    rssi: Mapped[float | None] = mapped_column(Float, nullable=True)       # smoothed on the phone
    samples: Mapped[int] = mapped_column(Integer, default=0)               # advertisements counted on the phone
    observed_token: Mapped[str | None] = mapped_column(String(32), nullable=True)
    observed_student_key: Mapped[str | None] = mapped_column(String(32), nullable=True)
    marker_idx: Mapped[int | None] = mapped_column(Integer, nullable=True)
    room: Mapped[str | None] = mapped_column(String(16), nullable=True)    # resolved room for markers
    wifi_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    wifi_zone: Mapped[str | None] = mapped_column(String(16), nullable=True)
    wifi_conf: Mapped[float | None] = mapped_column(Float, nullable=True)
    wifi_source: Mapped[str | None] = mapped_column(String(16), nullable=True)   # scan|cached|manual|synthetic
    scan_age_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    nonce: Mapped[str | None] = mapped_column(String(48), unique=True, nullable=True)
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[float] = mapped_column(Float, default=_now)

    __table_args__ = (
        Index("ix_obs_student_ts", "student_key", "ts"),
        Index("ix_obs_kind_ts", "kind", "ts"),
        Index("ix_obs_observed_ts", "observed_student_key", "ts"),
    )


class Location(Base):
    __tablename__ = "locations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_key: Mapped[str] = mapped_column(String(32))
    ts: Mapped[float] = mapped_column(Float)
    zone: Mapped[str] = mapped_column(String(16))
    x: Mapped[float] = mapped_column(Float, default=0)
    y: Mapped[float] = mapped_column(Float, default=0)
    signal: Mapped[str] = mapped_column(String(16))                        # ble_marker | wifi | fused
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    session_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (Index("ix_loc_student_ts", "student_key", "ts"),)


class Attendance(Base):
    __tablename__ = "attendance"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"))
    student_key: Mapped[str] = mapped_column(String(32))
    fused_state: Mapped[str] = mapped_column(String(20))                   # from deterministic fusion
    final_state: Mapped[str] = mapped_column(String(20))                   # REVIEW_REQUIRED if anomaly open
    score: Mapped[float] = mapped_column(Float, default=0)
    evidence_json: Mapped[str] = mapped_column(Text, default="{}")
    anomaly_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[float] = mapped_column(Float, default=_now)
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (UniqueConstraint("session_id", "student_key", name="uq_att_session_student"),)


class Anomaly(Base):
    __tablename__ = "anomalies"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"))
    student_key: Mapped[str] = mapped_column(String(32))
    ts: Mapped[float] = mapped_column(Float)
    rules_json: Mapped[str] = mapped_column(Text, default="[]")            # deterministic rule hits
    if_raw_score: Mapped[float | None] = mapped_column(Float, nullable=True)   # sklearn score_samples
    if_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    risk_demo: Mapped[float | None] = mapped_column(Float, nullable=True)  # 0-100 rescale of the raw score
    features_json: Mapped[str] = mapped_column(Text, default="{}")
    evidence_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(20), default="open")        # open|confirmed|false_positive
    scenario: Mapped[str | None] = mapped_column(String(32), nullable=True)
    explanation_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    explanation_provider: Mapped[str | None] = mapped_column(String(24), nullable=True)
    explanation_ts: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[float] = mapped_column(Float, default=_now)

    __table_args__ = (UniqueConstraint("session_id", "student_key", name="uq_anom_session_student"),)


class Feedback(Base):
    __tablename__ = "feedback"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    anomaly_id: Mapped[int] = mapped_column(ForeignKey("anomalies.id", ondelete="CASCADE"))
    username: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(20))                        # confirm|false_positive|comment
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    label: Mapped[str | None] = mapped_column(String(10), nullable=True)   # anomaly | normal (training label)
    features_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[float] = mapped_column(Float, default=_now)


class VerifiedCase(Base):
    __tablename__ = "verified_cases"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(160))
    summary: Mapped[str] = mapped_column(Text)
    structured_json: Mapped[str] = mapped_column(Text, default="{}")
    resolution: Mapped[str] = mapped_column(String(24))                    # false_positive | confirmed_anomaly
    faculty_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    origin: Mapped[str] = mapped_column(String(24), default="faculty_feedback")   # seed_demo | faculty_feedback
    anomaly_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=_now)


class WifiTraining(Base):
    """Labelled fingerprints collected in survey mode (live) or generated (simulated)."""
    __tablename__ = "wifi_training"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    zone: Mapped[str] = mapped_column(String(16), index=True)
    fingerprint_json: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16), default="live_survey")
    student_key: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=_now)
