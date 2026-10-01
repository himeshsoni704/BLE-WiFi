"""Layer 1 of anomaly detection (brief section 15): deterministic rules that
catch obvious, explainable suspicious patterns before the Isolation Forest
(layer 2, anomaly_iforest.py) ever runs. Pure functions over simple
dataclasses -- no DB coupling, so they're testable in isolation and reusable
from both the live engine and the simulator's training-data generator.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class RuleConfig:
    max_speed_mps: float = 2.5          # brisk indoor walking pace; see brief section 15 Rule B's own example
    reuse_window_s: float = 20.0        # brief's own "same 20-second interval" for Rule A
    reuse_min_distance_m: float = 15.0  # two observers this far apart can't both hear one real BLE radio
    session_switch_window_s: float = 300.0
    session_switch_min_rooms: int = 3


@dataclass(frozen=True)
class ZoneSighting:
    ts: float
    classroom_id: str
    source: str   # "ble_marker" | "peer_ble" | "wifi"


@dataclass(frozen=True)
class ObserverSighting:
    """One device's (scanner, classroom marker, or a peer phone) report of
    seeing a given student's token, with that device's own known position."""
    observer_id: str
    ts: float
    x: float
    y: float


@dataclass(frozen=True)
class RuleFlag:
    rule: str
    severity: str   # "low" | "medium" | "high"
    reason: str
    details: dict


def check_ble_wifi_contradiction(ble_zone: str | None, wifi_zone: str | None) -> RuleFlag | None:
    """Rule C: the two independent localisation signals disagree about which
    classroom the student is in."""
    if ble_zone is None or wifi_zone is None or ble_zone == wifi_zone:
        return None
    return RuleFlag(
        "ble_wifi_contradiction", "medium",
        f"BLE classroom marker indicates {ble_zone}, but the Wi-Fi fingerprint indicates {wifi_zone}",
        {"ble_zone": ble_zone, "wifi_zone": wifi_zone},
    )


def check_impossible_movement(
    sightings: list[ZoneSighting], classroom_positions: dict[str, tuple[float, float]],
    cfg: RuleConfig = RuleConfig(),
) -> list[RuleFlag]:
    """Rule B: consecutive DISTINCT-zone sightings imply a travel speed
    beyond what's physically plausible indoors (brief's own example: 250 m
    in 60 s ~= 4.2 m/s, flagged against a 2.5 m/s default)."""
    ordered = sorted(sightings, key=lambda s: s.ts)
    flags: list[RuleFlag] = []
    prev: ZoneSighting | None = None
    for s in ordered:
        if prev is not None and s.classroom_id != prev.classroom_id:
            dt = max(1e-6, s.ts - prev.ts)
            p0, p1 = classroom_positions.get(prev.classroom_id), classroom_positions.get(s.classroom_id)
            if p0 is not None and p1 is not None:
                distance = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
                speed = distance / dt
                if speed > cfg.max_speed_mps:
                    flags.append(RuleFlag(
                        "impossible_movement", "high",
                        f"moved from {prev.classroom_id} to {s.classroom_id} ({distance:.0f} m) in "
                        f"{dt:.0f}s -- {speed:.1f} m/s, above the {cfg.max_speed_mps:.1f} m/s limit",
                        {"from": prev.classroom_id, "to": s.classroom_id, "distance_m": distance,
                         "dt_s": dt, "speed_mps": speed},
                    ))
        prev = s
    return flags


def check_rapid_session_switching(sightings: list[ZoneSighting],
                                  cfg: RuleConfig = RuleConfig()) -> RuleFlag | None:
    """Rule D: 3+ distinct classrooms visited within a short sliding window --
    broader than Rule B (which looks at one speed jump at a time), this
    catches a student bouncing between several rooms in a pattern no real
    class schedule would produce."""
    ordered = sorted(sightings, key=lambda s: s.ts)
    for i, s in enumerate(ordered):
        window = [w for w in ordered[i:] if w.ts - s.ts <= cfg.session_switch_window_s]
        rooms = {w.classroom_id for w in window}
        if len(rooms) >= cfg.session_switch_min_rooms:
            return RuleFlag(
                "rapid_session_switching", "medium",
                f"visited {len(rooms)} different classrooms ({', '.join(sorted(rooms))}) within "
                f"{cfg.session_switch_window_s:.0f}s",
                {"rooms": sorted(rooms), "window_s": cfg.session_switch_window_s},
            )
    return None


def check_token_reuse(sightings: list[ObserverSighting],
                      cfg: RuleConfig = RuleConfig()) -> RuleFlag | None:
    """Rule A/E: the SAME resolved token is reported by two or more observer
    devices, within `reuse_window_s` of each other, whose own positions are
    farther apart than one phone's BLE radio could plausibly span -- i.e.
    the identity is physically in two places at once. A legitimate crowd of
    peers all hearing the same nearby broadcast is NOT this (they'd be
    close together); this only fires on spatially incompatible sightings,
    which is what the brief's "proxy attendance" / cloned-token scenario
    actually looks like. Severity scales with how many distinct observers
    disagree (brief's Rule E, "repeated token reuse")."""
    ordered = sorted(sightings, key=lambda s: s.ts)
    conflicting: set[str] = set()
    for i, a in enumerate(ordered):
        for b in ordered[i + 1:]:
            if b.ts - a.ts > cfg.reuse_window_s:
                break
            distance = math.hypot(b.x - a.x, b.y - a.y)
            if distance > cfg.reuse_min_distance_m:
                conflicting.update({a.observer_id, b.observer_id})
    if not conflicting:
        return None
    severity = "high" if len(conflicting) >= 3 else "medium"
    return RuleFlag(
        "token_reuse", severity,
        f"the same token was observed by {len(conflicting)} devices ({', '.join(sorted(conflicting))}) "
        f"within {cfg.reuse_window_s:.0f}s from positions too far apart for one phone",
        {"observers": sorted(conflicting), "window_s": cfg.reuse_window_s},
    )


def run_all_rules(
    ble_zone: str | None, wifi_zone: str | None,
    zone_sightings: list[ZoneSighting], observer_sightings: list[ObserverSighting],
    classroom_positions: dict[str, tuple[float, float]], cfg: RuleConfig = RuleConfig(),
) -> list[RuleFlag]:
    """Runs every deterministic rule and returns every flag raised; the
    caller (engine.py / the simulator) decides how flags feed the
    Isolation Forest features and the final anomaly record."""
    flags: list[RuleFlag] = []
    c = check_ble_wifi_contradiction(ble_zone, wifi_zone)
    if c:
        flags.append(c)
    flags.extend(check_impossible_movement(zone_sightings, classroom_positions, cfg))
    d = check_rapid_session_switching(zone_sightings, cfg)
    if d:
        flags.append(d)
    a = check_token_reuse(observer_sightings, cfg)
    if a:
        flags.append(a)
    return flags
