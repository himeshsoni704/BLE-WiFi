"""RSSI smoothing and proximity-duration helpers.

RSSI is a noisy, orientation- and body-dependent number. It is smoothed here and used only
as a coarse proximity indicator; it is never converted into a distance claim.
"""
from __future__ import annotations

import statistics
from typing import Iterable, Sequence


def ema(values: Sequence[float], alpha: float = 0.3) -> float | None:
    """Exponential moving average, oldest first."""
    out: float | None = None
    for v in values:
        out = v if out is None else alpha * v + (1 - alpha) * out
    return out


def smooth_rssi(values: Sequence[float]) -> float | None:
    """Median of the EMA-filtered series' trailing window: robust to single outliers."""
    if not values:
        return None
    return float(statistics.median(values))


def interval_union_seconds(intervals: Iterable[tuple[float, float]]) -> float:
    """Total covered time of possibly-overlapping [start, end] intervals."""
    spans = sorted((s, e) for s, e in intervals if e > s)
    total, cur_s, cur_e = 0.0, None, None
    for s, e in spans:
        if cur_e is None or s > cur_e:
            if cur_e is not None:
                total += cur_e - cur_s
            cur_s, cur_e = s, e
        else:
            cur_e = max(cur_e, e)
    if cur_e is not None:
        total += cur_e - cur_s
    return total


def proximity_label(rssi: float | None) -> str:
    """Coarse band, an ESTIMATE only. Bands are heuristics, not calibrated distances."""
    if rssi is None:
        return "unknown"
    if rssi >= -55:
        return "immediate"
    if rssi >= -75:
        return "near"
    return "far"
