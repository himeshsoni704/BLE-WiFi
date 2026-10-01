"""Deterministic anomaly rules. Transparent, explainable, no ML.

Rules fire on facts in the evidence; Isolation Forest (anomaly.py) separately looks for
unusual *combinations* of features. Neither decides attendance: hits are routed to human review.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from .config import Thresholds


@dataclass
class RuleHit:
    rule: str
    severity: str                 # info | warn | high
    detail: str
    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"rule": self.rule, "severity": self.severity, "detail": self.detail, "data": self.data}


@dataclass
class RuleContext:
    """Facts derived by the pipeline from the whole database, not just this student."""
    own_rooms: set[str]                                   # rooms where this student's own marker was detected
    remote_observers: list[tuple[str, str]]               # (observer_key, observer_room) seeing this token elsewhere
    locations: Sequence[tuple[float, str, float, float]]  # (ts, zone, x, y) ordered by ts
    marker_timeline: Sequence[tuple[float, str]]          # (ts, room) of own marker detections, ordered
    zone_distance: callable                               # (zone_a, zone_b) -> metres
    twin_distance_db: float = 99.0                        # smallest mean |RSSI difference| to another phone's marker series


def token_reuse(ev: dict, ctx: RuleContext, t: Thresholds) -> list[RuleHit]:
    remote = [(k, r) for k, r in ctx.remote_observers]
    if not remote:
        return []
    devices = len({k for k, _ in remote})
    rooms = sorted({r for _, r in remote})
    hits = [RuleHit("token_reuse", "warn" if devices < t.abnormal_token_reuse else "high",
                    f"Token observed by {devices} device(s) in other room(s) {rooms} "
                    f"while this student's own evidence is in {sorted(ctx.own_rooms) or 'no room'}",
                    {"token_reuse_count": devices, "rooms": rooms})]
    if devices >= t.abnormal_token_reuse:
        hits.append(RuleHit("abnormal_token_reuse", "high",
                            f"Same temporary token seen by {devices} devices outside the student's room",
                            {"token_reuse_count": devices}))
    return hits


def impossible_movement(ev: dict, ctx: RuleContext, t: Thresholds) -> list[RuleHit]:
    worst = None
    pts = list(ctx.locations)
    for (t0, z0, _, _), (t1, z1, _, _) in zip(pts, pts[1:]):
        if z0 == z1:
            continue
        dt = max(1.0, t1 - t0)
        speed = ctx.zone_distance(z0, z1) / dt
        if speed > t.max_speed_mps and (worst is None or speed > worst[0]):
            worst = (speed, z0, z1, dt)
    if worst is None:
        return []
    speed, z0, z1, dt = worst
    return [RuleHit("impossible_movement", "high",
                    f"Estimated movement {z0} -> {z1} implies {speed:.1f} m/s over {dt:.0f} s "
                    f"(limit {t.max_speed_mps:.1f} m/s)",
                    {"speed_mps": round(speed, 2), "from": z0, "to": z1, "seconds": round(dt, 1)})]


def ble_wifi_contradiction(ev: dict, ctx: RuleContext, t: Thresholds) -> list[RuleHit]:
    ble, wifi = ev["classroom_ble"], ev["wifi"]
    if not ble["detected"] or not wifi["available"] or wifi["scans_used"] < 2:
        return []
    mismatch = 1.0 - wifi["match_fraction"]
    if mismatch < t.contradiction_fraction:
        return []
    return [RuleHit("ble_wifi_contradiction", "warn",
                    f"BLE marker indicates room {ev['classroom']} but Wi-Fi fingerprints indicate "
                    f"room {wifi['predicted_zone']} (confidence {wifi['confidence']})",
                    {"ble_room": ev["classroom"], "wifi_room": wifi["predicted_zone"],
                     "wifi_confidence": wifi["confidence"], "mismatch_fraction": round(mismatch, 2)})]


def max_rooms_in_window(timeline: Sequence[tuple[float, str]], window_s: float) -> tuple[int, list[str]]:
    """Largest number of distinct rooms whose markers were seen within any `window_s` span."""
    best: tuple[int, list[str]] = (0, [])
    tl = list(timeline)
    for i, (ts, _) in enumerate(tl):
        rooms = {r for (u, r) in tl[i:] if u - ts <= window_s}
        if len(rooms) > best[0]:
            best = (len(rooms), sorted(rooms))
    return best


def rapid_session_switching(ev: dict, ctx: RuleContext, t: Thresholds) -> list[RuleHit]:
    count, rooms = max_rooms_in_window(ctx.marker_timeline, t.session_switch_window_s)
    if count <= t.session_switch_max:
        return []
    return [RuleHit("rapid_session_switching", "warn",
                    f"Markers from {count} different classrooms {rooms} within "
                    f"{int(t.session_switch_window_s / 60)} minutes",
                    {"classrooms": rooms, "count": count})]


RULES = (token_reuse, impossible_movement, ble_wifi_contradiction, rapid_session_switching)


def evaluate_rules(ev: dict, ctx: RuleContext, t: Thresholds) -> list[RuleHit]:
    hits: list[RuleHit] = []
    for rule in RULES:
        hits.extend(rule(ev, ctx, t))
    return hits
