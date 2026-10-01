"""Synthetic campus: 20 classrooms, 2 corridors, 10 Wi-Fi APs, 20 BLE markers.

Everything here is a *model* used to generate simulated data. Nothing in this file is
measured. Coordinates are metres on a 2-D plan:

    Block A  rooms 101-110  (y = 10)   corridor COR-1 (y = 22)   APs AP_01..AP_05 (y = 10)
    Block B  rooms 201-210  (y = 50)   corridor COR-2 (y = 38)   APs AP_06..AP_10 (y = 50)

Rooms are 10 m wide, so 204 and 205 are neighbours. Block A/B are treated as separate
buildings (extra attenuation) rather than modelling floors.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

ROOM_W, ROOM_D = 10.0, 8.0
ROW_Y = {"A": 10.0, "B": 50.0}
CORRIDOR_Y = {"A": 22.0, "B": 38.0}
MISSING_RSSI = -100.0        # fill value for "AP not heard" (same convention as many fingerprint datasets)
HEARD_FLOOR = -92.0          # below this the AP is not detected
WALL_LOSS_DB = 4.0           # partition-wall attenuation per room crossed
SHADOWING_DB = 3.0           # log-normal shadowing sigma
FADING_DB = 1.5              # fast-fading sigma


@dataclass(frozen=True)
class Zone:
    id: str
    name: str
    block: str
    x: float
    y: float
    is_corridor: bool = False
    marker_idx: int | None = None

    @property
    def marker_id(self) -> str | None:
        return f"ROOM_{self.id}_BEACON" if self.marker_idx else None


@dataclass(frozen=True)
class AP:
    ap_id: str
    name: str
    x: float
    y: float
    frequency_mhz: int


def _build() -> tuple[list[Zone], list[AP]]:
    zones: list[Zone] = []
    idx = 1
    for block, prefix in (("A", 100), ("B", 200)):
        for i in range(10):
            rid = str(prefix + i + 1)
            zones.append(Zone(rid, f"Room {rid}", block, 5 + 10 * i, ROW_Y[block], False, idx))
            idx += 1
    zones.append(Zone("COR-1", "Corridor A", "A", 50.0, CORRIDOR_Y["A"], True))
    zones.append(Zone("COR-2", "Corridor B", "B", 50.0, CORRIDOR_Y["B"], True))
    aps: list[AP] = []
    n = 1
    for block in ("A", "B"):
        for x in (10, 30, 50, 70, 90):
            aps.append(AP(f"AP_{n:02d}", f"Campus AP {n:02d}", float(x), ROW_Y[block],
                          2437 if n % 2 else 5180))
            n += 1
    return zones, aps


ZONES, APS = _build()
ROOMS = [z for z in ZONES if not z.is_corridor]
ZONE_BY_ID = {z.id: z for z in ZONES}
AP_IDS = [a.ap_id for a in APS]
ZONE_IDS = [z.id for z in ZONES]


def zone_distance(a: str, b: str) -> float:
    za, zb = ZONE_BY_ID[a], ZONE_BY_ID[b]
    return math.hypot(za.x - zb.x, za.y - zb.y)


def sample_position(zone_id: str, rng: np.random.Generator) -> tuple[float, float]:
    """A random point inside a room, or along a corridor."""
    z = ZONE_BY_ID[zone_id]
    if z.is_corridor:
        return float(rng.uniform(2, 98)), z.y + float(rng.uniform(-1.2, 1.2))
    return (z.x + float(rng.uniform(-ROOM_W / 2 + 1, ROOM_W / 2 - 1)),
            z.y + float(rng.uniform(-ROOM_D / 2 + 1, ROOM_D / 2 - 1)))


def mean_rssi(ap: AP, x: float, y: float, n: float = 3.0, p0: float = -38.0) -> float:
    """Log-distance path loss with wall and building penalties (deterministic part)."""
    d = max(1.0, math.hypot(ap.x - x, ap.y - y))
    loss = 10.0 * n * math.log10(d)
    walls = abs(round((ap.x - x) / ROOM_W))            # rooms between AP and point along the row
    loss += WALL_LOSS_DB * walls
    if (ap.y < 30) != (y < 30):                        # different block
        loss += 22.0
    return p0 - loss


def sample_fingerprint(
    x: float, y: float, rng: np.random.Generator, *,
    shadowing_db: float = SHADOWING_DB, fading_db: float = FADING_DB,
    ap_loss_prob: float = 0.03, degraded: dict[str, float] | None = None,
) -> dict[str, float]:
    """One simulated Wi-Fi scan: {ap_id: rssi_dbm} for the APs that were 'heard'.

    ap_loss_prob  chance an AP is temporarily missing from this scan
    degraded      {ap_id: extra attenuation dB} to model an unstable AP
    """
    out: dict[str, float] = {}
    for ap in APS:
        if rng.random() < ap_loss_prob:
            continue
        r = mean_rssi(ap, x, y) + rng.normal(0, shadowing_db) + rng.normal(0, fading_db)
        if degraded and ap.ap_id in degraded:
            r -= degraded[ap.ap_id]
        if r >= HEARD_FLOOR:
            out[ap.ap_id] = round(float(r), 1)
    return out


def marker_rssi(zone_id: str, x: float, y: float, rng: np.random.Generator) -> float:
    """Simulated RSSI of the classroom marker seen from (x, y) inside its own room."""
    z = ZONE_BY_ID[zone_id]
    d = max(0.5, math.hypot(z.x - x, z.y - y))
    return float(-50.0 - 20.0 * math.log10(d) + rng.normal(0, 3.5))


def peer_rssi(rng: np.random.Generator, close: bool = False) -> float:
    return float(rng.normal(-45 if close else -68, 5 if close else 7))
