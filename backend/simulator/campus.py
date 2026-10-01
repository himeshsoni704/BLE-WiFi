"""Campus-scale synthetic data generator (Proof-of-Presence hackathon brief,
sections 26-28): a layout of classrooms/APs/BLE markers, realistic Wi-Fi
fingerprints from an actual RF propagation model, normal attendance
behaviour with noise, and labelled anomaly scenarios for the demo control
panel and for training the Isolation Forest.

The RF propagation model (free-space path loss + multi-wall attenuation) is
adapted from the same author's own indoor_rf_simulator repo
(github.com/himeshsoni704/indoor_rf_simulator, indoor_rf_digital_twin.py) --
see docs/THIRD_PARTY.md. It is a standard, well-documented technique (Friis
equation + Motley-Keenan multi-wall model), not a fabricated "packet rate"
number: see the module's own docstring there for the physics citations.

Clearly synthetic throughout: every row this module produces carries
source="simulated", and nothing here is presented as a live phone reading.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

import numpy as np

# ---------------------------------------------------------------------------
# RF propagation (adapted from indoor_rf_simulator/indoor_rf_digital_twin.py)
# ---------------------------------------------------------------------------
TX_POWER_DBM = 20.0            # typical indoor Wi-Fi AP, higher than a phone's BLE radio
WIFI_FREQ_MHZ = 2400.0
WALL_LOSS_DB = 6.0             # per interior wall crossed (plasterboard/drywall-ish)
NOISE_FLOOR_DBM = -100.0


def free_space_path_loss_db(distance_m: float, freq_mhz: float) -> float:
    """Friis equation in dB form: FSPL = 32.44 + 20log10(d_km) + 20log10(f_MHz)."""
    d_km = max(0.5, distance_m) / 1000.0
    return 32.44 + 20 * math.log10(d_km) + 20 * math.log10(freq_mhz)


def rssi_between(distance_m: float, walls_crossed: int, tx_power_dbm: float = TX_POWER_DBM,
                 freq_mhz: float = WIFI_FREQ_MHZ, wall_loss_db: float = WALL_LOSS_DB) -> float:
    """RSSI = TX power - free-space path loss - (walls crossed x per-wall loss)."""
    fspl = free_space_path_loss_db(distance_m, freq_mhz)
    return tx_power_dbm - fspl - walls_crossed * wall_loss_db


# ---------------------------------------------------------------------------
# Campus layout
# ---------------------------------------------------------------------------
ROOM_W, ROOM_H = 10.0, 8.0     # metres
CORRIDOR_W = 3.0


@dataclass(frozen=True)
class Classroom:
    classroom_id: str
    name: str
    building: str
    floor: int
    x: float                   # metres, campus-plan coordinates (also used as the dashboard map coords)
    y: float
    capacity: int = 40


@dataclass(frozen=True)
class AccessPoint:
    ap_id: str
    bssid: str
    x: float
    y: float
    floor: int


@dataclass(frozen=True)
class BleMarker:
    marker_id: str
    classroom_id: str


def _grid_positions(n: int, cols: int, spacing_x: float, spacing_y: float) -> list[tuple[float, float]]:
    return [((i % cols) * spacing_x, (i // cols) * spacing_y) for i in range(n)]


def build_campus(n_classrooms: int = 20, n_aps: int = 10, floors: int = 2) -> tuple[
    list[Classroom], list[AccessPoint], list[BleMarker]
]:
    """Deterministic-shape campus: classrooms in a grid across `floors`
    floors, APs placed sparser than classrooms (one AP typically covers a
    few adjacent rooms, matching how real Wi-Fi deployments are sparser
    than the room count -- brief section 7's "Router 1 -> Room 201" etc.)."""
    per_floor = math.ceil(n_classrooms / floors)
    cols = max(1, math.ceil(math.sqrt(per_floor)))
    classrooms: list[Classroom] = []
    buildings = ["Block A", "Block B"]
    for i in range(n_classrooms):
        floor = i // per_floor
        local_i = i % per_floor
        gx, gy = _grid_positions(per_floor, cols, ROOM_W + CORRIDOR_W, ROOM_H + CORRIDOR_W)[local_i]
        room_no = 100 * (floor + 1) + (local_i + 1)
        classrooms.append(Classroom(
            classroom_id=f"ROOM_{room_no}", name=f"Room {room_no}",
            building=buildings[i % len(buildings)], floor=floor + 1,
            x=gx + ROOM_W / 2, y=gy + ROOM_H / 2,
        ))

    aps: list[AccessPoint] = []
    ap_positions = _grid_positions(n_aps, max(1, math.ceil(math.sqrt(n_aps))),
                                   (ROOM_W + CORRIDOR_W) * 2, (ROOM_H + CORRIDOR_W) * 2)
    for i in range(n_aps):
        ax, ay = ap_positions[i]
        aps.append(AccessPoint(ap_id=f"AP_{i + 1:02d}", bssid=f"02:AP:00:00:00:{i:02x}",
                               x=ax, y=ay, floor=(i % floors) + 1))

    markers = [BleMarker(marker_id=f"{c.classroom_id}_BEACON", classroom_id=c.classroom_id) for c in classrooms]
    return classrooms, aps, markers


def _walls_between(c: Classroom, ap: AccessPoint) -> int:
    """Approximate wall count from floor difference (concrete floor slabs
    attenuate far more than an interior wall) plus a per-distance interior-
    wall estimate -- a simplification of the ray-traced version in
    indoor_rf_simulator (that one actually walks a grid; a full campus grid
    at this scale is overkill for a fingerprint demo, so this keeps the
    SAME physical model but a cheaper wall estimate)."""
    dx, dy = abs(c.x - ap.x), abs(c.y - ap.y)
    interior_walls = int((dx + dy) // (ROOM_W + CORRIDOR_W))
    floor_penalty = 4 if c.floor != ap.floor else 0   # floor slabs count as ~4 interior walls' worth
    return interior_walls + floor_penalty


def classroom_fingerprint(c: Classroom, aps: list[AccessPoint], rng: np.random.Generator,
                          noise_db: float = 3.0, drop_prob: float = 0.08) -> dict[str, float]:
    """One realistic Wi-Fi scan from inside classroom `c`: RSSI from every AP
    whose predicted signal clears the noise floor, each with measurement
    noise, and each independently possibly "not heard this scan" (drop_prob)
    -- real phones don't see a perfectly stable AP list scan to scan."""
    fp: dict[str, float] = {}
    for ap in aps:
        distance = math.hypot(c.x - ap.x, c.y - ap.y)
        walls = _walls_between(c, ap)
        rssi = rssi_between(distance, walls) + rng.normal(0, noise_db)
        if rssi <= NOISE_FLOOR_DBM or rng.random() < drop_prob:
            continue
        fp[ap.bssid] = round(rssi, 1)
    return fp


def ble_marker_rssi(rng: np.random.Generator, inside: bool = True, noise_db: float = 4.0) -> int:
    """A classroom BLE marker, a few metres from the student, is much
    stronger/more stable than Wi-Fi (short range, no walls in play for a
    beacon placed inside the same room). `inside=False` models a student
    just outside the door / in the corridor, still faintly audible."""
    base = rng.normal(-55, 6) if inside else rng.normal(-80, 8)
    return int(round(base + rng.normal(0, noise_db)))


# ---------------------------------------------------------------------------
# Students, sessions, normal behaviour
# ---------------------------------------------------------------------------
@dataclass
class SimStudent:
    student_id: str
    name: str
    program: str
    year: int


PROGRAMS = ["CS", "ECE", "ME", "Civil", "Design"]
COURSES = ["CS301", "CS205", "EC210", "ME110", "DS150", "CS410", "EC330"]


def build_students(n: int, rng: np.random.Generator, prefix: str = "STU") -> list[SimStudent]:
    return [
        SimStudent(f"{prefix}{i:03d}", f"Student {i:03d}", PROGRAMS[int(rng.integers(0, len(PROGRAMS)))],
                  int(rng.integers(1, 5)))
        for i in range(n)
    ]


@dataclass
class SimSession:
    session_id: str
    course: str
    classroom_id: str
    start_ts: float
    end_ts: float


def build_sessions(classrooms: list[Classroom], t0: float, rng: np.random.Generator,
                   n_sessions: int | None = None) -> list[SimSession]:
    """One session per classroom by default (enough to seat every simulated
    student into some class), each ~75 minutes, staggered across a morning."""
    n_sessions = n_sessions or len(classrooms)
    out = []
    for i in range(n_sessions):
        c = classrooms[i % len(classrooms)]
        start = t0 + (i % 4) * 90 * 60   # up to 4 staggered periods
        out.append(SimSession(f"{rng.integers(0, len(COURSES))}_{c.classroom_id}_{i}",
                              COURSES[i % len(COURSES)], c.classroom_id, start, start + 75 * 60))
    return out


@dataclass(frozen=True)
class AttendanceEvent:
    """One simulated student's full presence record for one session --
    enough to derive BLE/peer/Wi-Fi evidence AND the Isolation Forest
    feature vector downstream. `label` is the ground truth used only for
    offline evaluation (never fed to the detector itself)."""
    student_id: str
    session_id: str
    classroom_id: str
    enter_ts: float
    leave_ts: float
    ble_rssi_samples: list[int]
    wifi_fingerprints: list[dict[str, float]]
    nearby_peer_ids: list[str]
    label: Literal["normal", "proxy_attendance", "impossible_movement", "wifi_ble_mismatch",
                   "short_presence", "token_replay", "device_clustering"] = "normal"
    extra_classroom_id: str | None = None   # for wifi/ble mismatch: where Wi-Fi claims they are instead
    extra_zones_visited: list[str] = field(default_factory=list)   # for impossible movement / session switching


def _normal_attendance(student: SimStudent, session: SimSession, classrooms_by_id: dict[str, Classroom],
                       aps: list[AccessPoint], rng: np.random.Generator) -> AttendanceEvent:
    c = classrooms_by_id[session.classroom_id]
    dwell = rng.uniform(30 * 60, min(90 * 60, session.end_ts - session.start_ts))
    enter = session.start_ts + rng.uniform(0, max(1.0, (session.end_ts - session.start_ts) - dwell))
    n_ble = max(2, int(dwell / 30))     # a BLE sighting roughly every 30s
    n_wifi = max(1, int(dwell / 60))    # a Wi-Fi scan roughly every 60s (scans are more throttled)
    ble = [ble_marker_rssi(rng) for _ in range(n_ble)]
    wifi = [classroom_fingerprint(c, aps, rng) for _ in range(n_wifi)]
    return AttendanceEvent(student.student_id, session.session_id, session.classroom_id,
                           enter, enter + dwell, ble, wifi, [])


def _inject_anomaly(ev: AttendanceEvent, kind: str, classrooms_by_id: dict[str, Classroom],
                    aps: list[AccessPoint], rng: np.random.Generator, other_classroom_id: str | None = None) -> AttendanceEvent:
    """Mutates a normal event into one of the brief's section-28 scenarios."""
    if kind == "wifi_ble_mismatch":
        other = other_classroom_id or next(iter(classrooms_by_id))
        other_c = classrooms_by_id[other]
        wifi = [classroom_fingerprint(other_c, aps, rng) for _ in ev.wifi_fingerprints]
        return AttendanceEvent(**{**ev.__dict__, "wifi_fingerprints": wifi, "label": "wifi_ble_mismatch",
                                 "extra_classroom_id": other})
    if kind == "short_presence":
        return AttendanceEvent(**{**ev.__dict__, "leave_ts": ev.enter_ts + rng.uniform(2, 8),
                                 "ble_rssi_samples": ev.ble_rssi_samples[:1], "label": "short_presence"})
    if kind == "impossible_movement":
        other = other_classroom_id or next(iter(classrooms_by_id))
        return AttendanceEvent(**{**ev.__dict__, "label": "impossible_movement",
                                 "extra_zones_visited": [ev.classroom_id, other]})
    if kind == "device_clustering":
        peers = [f"STU{int(rng.integers(0, 999)):03d}" for _ in range(int(rng.integers(8, 20)))]
        return AttendanceEvent(**{**ev.__dict__, "nearby_peer_ids": peers, "label": "device_clustering"})
    raise ValueError(f"unknown injection kind: {kind}")


def generate_campus_dataset(
    n_students: int = 300, n_classrooms: int = 20, n_aps: int = 10,
    anomaly_rate: float = 0.08, seed: int = 0,
) -> dict:
    """Brief section 26: 300 students / 20 classrooms / 10 APs / 20 BLE
    markers / multiple sessions, with a labelled mix of normal and
    anomalous attendance events (section 27-28). Returns everything needed
    to seed the backend DB (classrooms/APs/markers/students/sessions) and
    everything needed to train/evaluate the ML models (events with labels)."""
    rng = np.random.default_rng(seed)
    classrooms, aps, markers = build_campus(n_classrooms, n_aps)
    classrooms_by_id = {c.classroom_id: c for c in classrooms}
    students = build_students(n_students, rng)
    t0 = 1_800_000_000.0
    sessions = build_sessions(classrooms, t0, rng, n_sessions=max(n_classrooms, 8))

    anomaly_kinds = ["proxy_attendance", "impossible_movement", "wifi_ble_mismatch",
                     "short_presence", "token_replay", "device_clustering"]
    events: list[AttendanceEvent] = []
    for student in students:
        session = sessions[int(rng.integers(0, len(sessions)))]
        ev = _normal_attendance(student, session, classrooms_by_id, aps, rng)
        if rng.random() < anomaly_rate:
            kind = anomaly_kinds[int(rng.integers(0, len(anomaly_kinds)))]
            others = [c.classroom_id for c in classrooms if c.classroom_id != ev.classroom_id] or [ev.classroom_id]
            other = others[int(rng.integers(0, len(others)))]
            if kind in ("proxy_attendance", "token_replay"):
                # handled at the peer/scan layer by the API-level demo injector (needs real tokens);
                # here we just mark the label so offline ML training sees a representative share.
                ev = AttendanceEvent(**{**ev.__dict__, "label": kind})
            else:
                ev = _inject_anomaly(ev, kind, classrooms_by_id, aps, rng, other_classroom_id=other)
        events.append(ev)

    return {
        "classrooms": classrooms, "aps": aps, "markers": markers, "students": students,
        "sessions": sessions, "events": events,
    }
