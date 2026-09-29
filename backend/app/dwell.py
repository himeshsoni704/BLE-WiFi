"""Dwell-time logic: presence over a period, not one ping.

Class attendance counts a student only if they were present for about 70% of
the period, so someone walking past the door for a few seconds is not counted.
The same aggregation gives one overall state per student for a roll-call window.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Collection, Sequence

from .fusion import State, Verdict


@dataclass(frozen=True)
class DwellConfig:
    slot_s: float = 60.0
    min_presence: float = 0.7        # fraction of slots needed to count as attending
    suspect_fraction: float = 0.3    # share of present slots that flags the student


@dataclass(frozen=True)
class Dwell:
    slots: int
    present: int
    verified: int
    suspect: int
    uncertain: int
    presence_ratio: float
    state: State                     # overall state for the period
    counted: bool                    # presence_ratio >= min_presence
    flagged: bool                    # too many suspect slots, needs review

    def to_dict(self) -> dict:
        return {
            "slots": self.slots,
            "present": self.present,
            "verified": self.verified,
            "suspect": self.suspect,
            "uncertain": self.uncertain,
            "presence_ratio": round(self.presence_ratio, 3),
            "state": self.state.value,
            "counted": self.counted,
            "flagged": self.flagged,
        }


def aggregate(
    verdicts: Sequence[Verdict],
    cfg: DwellConfig = DwellConfig(),
    zones: Collection[str] | None = None,
) -> Dwell:
    """Collapse one verdict per slot into a Dwell.

    A slot is "present" if the verdict is anything but NOT_DETECTED and (when
    `zones` is given) its zone is one of them. Overall state: SUSPECT if enough
    of the present slots were suspect, else VERIFIED if any slot was verified,
    else UNCERTAIN if present at all, else NOT_DETECTED.
    """
    def in_scope(v: Verdict) -> bool:
        return v.state is not State.NOT_DETECTED and (zones is None or v.zone in zones)

    scoped = [v for v in verdicts if in_scope(v)]
    counts = {s: sum(1 for v in scoped if v.state is s) for s in State}
    present = len(scoped)
    slots = len(verdicts)
    ratio = present / slots if slots else 0.0
    flagged = present > 0 and counts[State.SUSPECT] / present >= cfg.suspect_fraction

    if present == 0:
        state = State.NOT_DETECTED
    elif flagged:
        state = State.SUSPECT
    elif counts[State.VERIFIED] > 0:
        state = State.VERIFIED
    else:
        state = State.UNCERTAIN
    return Dwell(
        slots=slots,
        present=present,
        verified=counts[State.VERIFIED],
        suspect=counts[State.SUSPECT],
        uncertain=counts[State.UNCERTAIN],
        presence_ratio=ratio,
        state=state,
        counted=ratio >= cfg.min_presence,
        flagged=flagged,
    )
