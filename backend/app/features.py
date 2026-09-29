"""Sensor-window features for the owner-vs-other model.

The phone sends raw accelerometer/gyroscope samples; the server turns them into
a fixed-length feature vector here, scores it, and discards the samples. Keeping
the extraction in one place guarantees training and serving see identical
features.

Units: accelerometer in m/s^2 (gravity included), gyroscope in rad/s.
"""
from __future__ import annotations

import numpy as np

WINDOW_S = 2.0
STEP_S = 1.0
MIN_FS = 10.0
MAX_FS = 200.0

# Std-dev of accelerometer magnitude. A phone lying on a desk sits around
# 0.01, one held in a hand around 0.1, one being walked with above 1.0.
# Calibrate these on the real devices.
MOTION_THRESHOLD = 0.05
WALKING_THRESHOLD = 0.8

_EPS = 1e-9
_CHANNELS = ("amag", "gmag", "ax", "ay", "az", "gx", "gy", "gz")

FEATURE_NAMES: tuple[str, ...] = tuple(
    [f"{c}_{s}" for c in _CHANNELS for s in ("mean", "std")]
    + [f"{c}_{s}" for c in ("amag", "gmag") for s in ("range", "domfreq", "peakratio")]
    + ["amag_skew", "amag_kurt", "amag_zcr", "amag_acf_peak", "amag_acf_lag"]
)
N_FEATURES = len(FEATURE_NAMES)


def magnitude(xyz: np.ndarray) -> np.ndarray:
    return np.linalg.norm(xyz, axis=1)


def motion_level(accel: np.ndarray) -> float:
    """Std-dev of accelerometer magnitude: ~0 for a phone at rest."""
    a = np.asarray(accel, dtype=float)
    if len(a) < 2:
        return 0.0
    return float(np.std(magnitude(a)))


def window_length(fs: float) -> int:
    return int(round(WINDOW_S * fs))


def _dominant_frequency(sig: np.ndarray, fs: float) -> tuple[float, float]:
    """(frequency in Hz, share of band power) of the strongest 0.5-6 Hz peak."""
    x = sig - sig.mean()
    power = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    freqs = np.fft.rfftfreq(len(x), 1.0 / fs)
    band = np.flatnonzero((freqs >= 0.5) & (freqs <= 6.0))
    if band.size == 0 or power[band].sum() < _EPS:
        return 0.0, 0.0
    peak = band[int(np.argmax(power[band]))]
    return float(freqs[peak]), float(power[peak] / power[band].sum())


def _autocorrelation_peak(sig: np.ndarray, fs: float) -> tuple[float, float]:
    """(height, lag in s) of the strongest autocorrelation peak at step scale."""
    x = sig - sig.mean()
    denom = float(np.dot(x, x))
    n = len(x)
    lo, hi = int(0.25 * fs), min(int(1.2 * fs), n - 2)
    if denom < _EPS or hi <= lo:
        return 0.0, 0.0
    acf = np.correlate(x, x, mode="full")[n - 1:] / denom
    k = int(np.argmax(acf[lo:hi + 1]))
    return float(acf[lo + k]), float((lo + k) / fs)


def window_features(accel: np.ndarray, gyro: np.ndarray, fs: float) -> np.ndarray:
    """Feature vector (N_FEATURES,) for one window of (n, 3) accel and gyro."""
    amag, gmag = magnitude(accel), magnitude(gyro)
    channels = {
        "amag": amag, "gmag": gmag,
        "ax": accel[:, 0], "ay": accel[:, 1], "az": accel[:, 2],
        "gx": gyro[:, 0], "gy": gyro[:, 1], "gz": gyro[:, 2],
    }
    feats: list[float] = []
    for name in _CHANNELS:
        feats += [float(channels[name].mean()), float(channels[name].std())]
    spectra = {name: _dominant_frequency(channels[name], fs) for name in ("amag", "gmag")}
    for name in ("amag", "gmag"):
        sig = channels[name]
        feats += [float(sig.max() - sig.min()), *spectra[name]]

    centred = amag - amag.mean()
    std = centred.std()
    if std < _EPS:
        skew = kurt = 0.0
    else:
        z = centred / std
        skew, kurt = float(np.mean(z ** 3)), float(np.mean(z ** 4) - 3.0)
    zcr = float(np.mean(centred[:-1] * centred[1:] < 0))
    feats += [skew, kurt, zcr, *_autocorrelation_peak(amag, fs)]

    out = np.asarray(feats, dtype=float)
    assert out.shape == (N_FEATURES,)
    return out


def extract_windows(
    accel: np.ndarray, gyro: np.ndarray, fs: float
) -> tuple[np.ndarray, np.ndarray]:
    """Slide a WINDOW_S window (STEP_S hop) over a recording.

    Returns (features (k, N_FEATURES), motion level per window (k,)). A recording
    shorter than one window yields k == 0.
    """
    accel = np.asarray(accel, dtype=float)
    gyro = np.asarray(gyro, dtype=float)
    width, hop = window_length(fs), max(1, int(round(STEP_S * fs)))
    starts = range(0, len(accel) - width + 1, hop)
    if len(starts) == 0:
        return np.empty((0, N_FEATURES)), np.empty(0)
    feats = np.stack([window_features(accel[s:s + width], gyro[s:s + width], fs) for s in starts])
    motion = np.array([motion_level(accel[s:s + width]) for s in starts])
    return feats, motion
