"""Synthetic accelerometer/gyroscope generator for tests and the simulator.

Each `Person` has a stable gait signature (step rate, sway amplitudes, harmonic
mix, how the phone is tilted in their pocket); each recording adds a little
run-to-run variation. This is *synthetic*: it proves the pipeline works end to
end, not how accurate gait recognition will be on real people and phones.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

G = 9.81


def _rotation(rx: float, ry: float) -> np.ndarray:
    cx, sx, cy, sy = np.cos(rx), np.sin(rx), np.cos(ry), np.sin(ry)
    return np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]]) @ np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])


@dataclass(frozen=True)
class Person:
    step_hz: float
    vertical: float
    forward: float
    lateral: float
    harmonic: float
    gyro_amp: tuple[float, float, float]
    tilt: tuple[float, float]
    phases: tuple[float, float, float]

    @classmethod
    def random(cls, seed: int) -> "Person":
        r = np.random.default_rng(seed)
        return cls(
            step_hz=r.uniform(1.5, 2.4),
            vertical=r.uniform(2.0, 4.5),
            forward=r.uniform(0.8, 2.2),
            lateral=r.uniform(0.4, 1.4),
            harmonic=r.uniform(0.1, 0.5),
            gyro_amp=tuple(r.uniform(0.4, 2.0, 3)),
            tilt=tuple(r.uniform(-0.6, 0.6, 2)),
            phases=tuple(r.uniform(0, 2 * np.pi, 3)),
        )


def walk(person: Person, seconds: float, fs: float = 50.0, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """(accel (n, 3), gyro (n, 3)) for a phone carried while walking."""
    rng = np.random.default_rng(seed)
    n = int(seconds * fs)
    t = np.arange(n) / fs
    f = person.step_hz * (1 + rng.normal(0, 0.03))
    scale = 1 + rng.normal(0, 0.06)
    w = 2 * np.pi * f * t
    p1, p2, p3 = person.phases
    vertical = scale * person.vertical * (np.sin(w) + person.harmonic * np.sin(2 * w + p1))
    forward = scale * person.forward * np.sin(w + p2)
    lateral = scale * person.lateral * np.sin(w / 2 + p3)
    body = np.stack([lateral, forward, G + vertical], axis=1)
    gyro = np.stack([
        a * np.sin(w + ph) for a, ph in zip(person.gyro_amp, (p1, p2, p3))
    ], axis=1)
    rot = _rotation(person.tilt[0] + rng.normal(0, 0.05), person.tilt[1] + rng.normal(0, 0.05))
    accel = body @ rot.T + rng.normal(0, 0.15, (n, 3))
    return accel, gyro @ rot.T + rng.normal(0, 0.05, (n, 3))


def _rest(seconds: float, fs: float, noise: float, seed: int) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    n = int(seconds * fs)
    accel = np.tile([0.3, 0.2, G], (n, 1)) + rng.normal(0, noise, (n, 3))
    return accel, rng.normal(0, noise / 10, (n, 3))


def on_desk(seconds: float, fs: float = 50.0, seed: int = 0):
    """Phone lying still: sensor noise only (motion level ~0.01)."""
    return _rest(seconds, fs, 0.01, seed)


def in_hand(seconds: float, fs: float = 50.0, seed: int = 0):
    """Phone held while sitting: small tremor (motion level ~0.1)."""
    return _rest(seconds, fs, 0.1, seed)


def write_csv(path, accel: np.ndarray, gyro: np.ndarray, fs: float = 50.0) -> None:
    t = np.arange(len(accel)) / fs
    np.savetxt(
        path, np.column_stack([t, accel, gyro]), delimiter=",",
        header="t,ax,ay,az,gx,gy,gz", comments="", fmt="%.5f",
    )
