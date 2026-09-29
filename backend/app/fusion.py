"""Rule-based fusion of BLE zone, Wi-Fi zone, owner score and stationarity.

The output is a *risk assessment*, not proof of identity. A determined cheater
who carries both phones and imitates the owner's gait still passes; what this
catches is casual handoffs and phones left behind.

States:
  VERIFIED      BLE zone == Wi-Fi zone, owner score high (or unavailable),
                phone not stationary
  SUSPECT       BLE zone == Wi-Fi zone, but owner score low or phone stationary
  UNCERTAIN     one signal only, or the two signals disagree
  NOT_DETECTED  neither signal
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class State(str, Enum):
    VERIFIED = "verified"
    SUSPECT = "suspect"
    UNCERTAIN = "uncertain"
    NOT_DETECTED = "not_detected"


@dataclass(frozen=True)
class FusionConfig:
    owner_threshold: float = 0.5
    # Zero motion for this long while "present" means the phone is on a desk,
    # not with a person. A class period is far shorter than this on purpose:
    # phones sit on desks during lessons.
    stationary_limit_s: float = 2 * 3600.0
    # If True, a student with no owner score (not enrolled, or no recent
    # walking) can be at most UNCERTAIN instead of VERIFIED.
    require_owner_score: bool = False

    def __post_init__(self) -> None:
        if not 0.0 <= self.owner_threshold <= 1.0:
            raise ValueError("owner_threshold must be in [0, 1]")
        if self.stationary_limit_s <= 0:
            raise ValueError("stationary_limit_s must be positive")


@dataclass(frozen=True)
class Evidence:
    ble_zone: str | None = None
    wifi_zone: str | None = None
    owner_score: float | None = None      # P(owner is carrying), 0..1
    stationary_s: float | None = None     # seconds since the phone last moved
    interaction_recent: bool = False      # screen in use: someone is holding it


@dataclass(frozen=True)
class Verdict:
    state: State
    zone: str | None
    risk: float | None                    # 0..1, None when there is nothing to judge
    owner_score: float | None
    reasons: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "state": self.state.value,
            "zone": self.zone,
            "risk": self.risk,
            "owner_score": None if self.owner_score is None else round(self.owner_score, 3),
            "reasons": list(self.reasons),
        }


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def format_duration(seconds: float) -> str:
    minutes = int(seconds // 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m" if hours else f"{minutes}m"


def _left_behind(ev: Evidence, cfg: FusionConfig) -> bool:
    return (
        ev.stationary_s is not None
        and ev.stationary_s >= cfg.stationary_limit_s
        and not ev.interaction_recent
    )


def _owner_low(ev: Evidence, cfg: FusionConfig) -> bool:
    return ev.owner_score is not None and ev.owner_score < cfg.owner_threshold


def risk_score(ev: Evidence, cfg: FusionConfig = FusionConfig()) -> float | None:
    """Noisy-OR of the handoff indicators that are available, in 0..1.

    Owner mismatch contributes (1 - owner_score). Stationarity ramps from 0 at
    half the limit to 1 at the limit. Returns None if neither is available.
    """
    parts: list[float] = []
    if ev.owner_score is not None:
        parts.append(1.0 - _clamp(ev.owner_score))
    if ev.stationary_s is not None and not ev.interaction_recent:
        half = cfg.stationary_limit_s / 2
        parts.append(_clamp((ev.stationary_s - half) / half))
    if not parts:
        return None
    keep = 1.0
    for p in parts:
        keep *= 1.0 - p
    return round(1.0 - keep, 3)


def fuse(ev: Evidence, cfg: FusionConfig = FusionConfig()) -> Verdict:
    if ev.ble_zone is None and ev.wifi_zone is None:
        return Verdict(State.NOT_DETECTED, None, None, None, ("no BLE or Wi-Fi zone evidence",))

    risk = risk_score(ev, cfg)
    left_behind, owner_low = _left_behind(ev, cfg), _owner_low(ev, cfg)
    concerns: list[str] = []
    if left_behind:
        concerns.append(
            f"phone stationary for {format_duration(ev.stationary_s)}; likely left behind"
        )
    if owner_low:
        concerns.append(f"motion pattern does not match owner (score {ev.owner_score:.2f})")

    if ev.ble_zone is None or ev.wifi_zone is None:
        if ev.ble_zone is not None:
            why = "BLE only; Wi-Fi zone not confirmed"
        else:
            why = "Wi-Fi only; BLE not seen (Bluetooth off?)"
        zone = ev.ble_zone or ev.wifi_zone
        return Verdict(State.UNCERTAIN, zone, risk, ev.owner_score, (why, *concerns))

    if ev.ble_zone != ev.wifi_zone:
        why = f"BLE zone {ev.ble_zone} != Wi-Fi zone {ev.wifi_zone}"
        return Verdict(State.UNCERTAIN, ev.ble_zone, risk, ev.owner_score, (why, *concerns))

    if concerns:
        return Verdict(State.SUSPECT, ev.ble_zone, risk, ev.owner_score, tuple(concerns))
    if ev.owner_score is None:
        reason = "BLE and Wi-Fi agree; no owner score (not enrolled or no recent walking)"
        state = State.UNCERTAIN if cfg.require_owner_score else State.VERIFIED
        return Verdict(state, ev.ble_zone, risk, None, (reason,))
    return Verdict(
        State.VERIFIED, ev.ble_zone, risk, ev.owner_score,
        ("BLE and Wi-Fi agree; owner score high; phone moving",),
    )
