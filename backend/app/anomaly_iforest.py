"""Layer 2 of anomaly detection (brief section 16-17): scikit-learn
Isolation Forest over a fixed feature vector describing one student's
recent behaviour. Trained mostly on NORMAL synthetic attendance -- the
whole point of Isolation Forest here (see the brief's own section 17,
surfaced verbatim on the dashboard) is that it needs the *structure* of
normal behaviour, not a large labelled catalogue of every possible scam.

The raw score is NOT a probability (brief section 16: "do not interpret
the raw score as a probability"). `IsolationDetector.predict()` also
returns a `demo_risk_score` in [0, 100], min-max-normalised against the
score distribution observed on the TRAINING set and clamped at the edges --
a display convenience only, explicitly labelled as such everywhere it's
surfaced (dashboard, docstrings), never fed back into any decision.

Features are z-score standardised (fit on the training set, applied at both
train and predict time) before IsolationForest ever sees them. Worth being
precise about what this does and doesn't fix, since it was added while
chasing a real problem found in this module's own synthetic benchmark:
scikit-learn's IsolationForest splits each node on a uniformly random
threshold WITHIN that feature's current range, which is invariant to a
per-feature affine rescaling -- so standardising alone measurably changed
nothing (verified empirically, not assumed). It's kept anyway because it's
harmless and is the right default if this ever swaps to a distance-based
method. The real fix for that problem was `contamination` (see
AnomalyDetector.__init__): with only 10 of 10 informative features and
several of them (ble_duration, mean_ble_rssi, nearby_device_count) varying
just as much for normal rows as for several anomaly types, a handful of
genuinely sparse, highly discriminative dimensions (token_reuse_count,
estimated_speed, signal_consistency) have to be hit early in a tree's
random splits to show up in the averaged path length -- diluted across
150-500 trees and 10 features, `contamination=0.05` (matching this
simulator's ~5.5% true anomaly rate almost exactly) left most real
anomalies just inside the "normal" side of the cut. Raising contamination
recovers them (measured: 0.05 -> 10% recall, 0.10 -> 50%, 0.15 -> 60%,
precision falling from 11% to ~16% along the way) -- a real precision/
recall trade, not a modelling fix, and deliberately taken given
REVIEW_REQUIRED is a one-click human check, not an automatic verdict: a
missed proxy-attendance case costs far more than an extra item in the
review queue. See train_anomaly_model.py's own printed report for the
current numbers on a fresh synthetic run -- they are not hidden or
rounded away.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

FEATURE_NAMES: tuple[str, ...] = (
    "ble_duration", "mean_ble_rssi", "wifi_confidence", "nearby_device_count",
    "token_reuse_count", "time_between_locations", "estimated_speed",
    "number_of_classrooms", "session_switch_count", "signal_consistency",
)
N_FEATURES = len(FEATURE_NAMES)


@dataclass(frozen=True)
class AnomalyFeatures:
    """One row = one student's behaviour summary over the window being
    evaluated (e.g. a class period). Field order matches FEATURE_NAMES."""
    ble_duration: float               # seconds of BLE classroom-marker presence
    mean_ble_rssi: float              # dBm
    wifi_confidence: float            # 0..1, the KNN model's confidence in its zone prediction
    nearby_device_count: int          # distinct peer devices observed nearby
    token_reuse_count: int            # count of distinct observers in a Rule-A conflict (0 if none)
    time_between_locations: float     # seconds since the previous DIFFERENT zone was seen
    estimated_speed: float            # m/s, from the last zone change (0 if no zone change)
    number_of_classrooms: int         # distinct classrooms visited in the window
    session_switch_count: int         # zone changes within the window
    signal_consistency: float         # 0..1, agreement between BLE and Wi-Fi zone over the window

    def to_vector(self) -> np.ndarray:
        return np.array([
            self.ble_duration, self.mean_ble_rssi, self.wifi_confidence, self.nearby_device_count,
            self.token_reuse_count, self.time_between_locations, self.estimated_speed,
            self.number_of_classrooms, self.session_switch_count, self.signal_consistency,
        ], dtype=float)

    def to_dict(self) -> dict:
        return dict(zip(FEATURE_NAMES, self.to_vector().tolist()))


@dataclass(frozen=True)
class AnomalyPrediction:
    raw_score: float          # IsolationForest.decision_function: higher = more normal, NOT a probability
    is_anomalous: bool        # IsolationForest.predict() == -1
    demo_risk_score: float    # 0..100 display-only normalisation of raw_score, for the UI alone

    def to_dict(self) -> dict:
        return {
            "isolation_forest_raw_score": round(self.raw_score, 4),
            "is_anomalous": self.is_anomalous,
            "demo_risk_score": round(self.demo_risk_score, 1),
            "note": "demo_risk_score is a UI-only normalised display value, not a calibrated probability",
        }


class AnomalyDetector:
    def __init__(self, n_estimators: int = 150, contamination: float | str = 0.12, random_state: int = 0):
        """contamination picks the decision threshold on the training
        score distribution -- NOT sklearn's "auto" (a fixed rule from the
        original Isolation Forest paper, untuned to any particular
        false-positive rate; flagged ~15-30% of held-out normal examples
        in this module's own test suite).

        0.12 is deliberately ABOVE this simulator's ~5.5% true anomaly
        rate, not equal to it: see the module docstring for the measured
        recall/precision trade behind that choice (0.05 -> 10% recall,
        0.10 -> 50%, 0.15 -> 60%, precision falling from ~11% to ~16%). A
        missed proxy-attendance case is worse than an extra item in
        REVIEW_REQUIRED's one-click human queue, so recall is favoured on
        purpose here -- train_anomaly_model.py prints the actual numbers
        on every run rather than asserting this trade is correct."""
        self._n_estimators = n_estimators
        self._contamination = contamination
        self._random_state = random_state
        self._model: IsolationForest | None = None
        self._scaler: StandardScaler | None = None
        self._train_score_lo: float | None = None
        self._train_score_hi: float | None = None

    @property
    def is_trained(self) -> bool:
        return self._model is not None

    def _vectors(self, features: list[AnomalyFeatures]) -> np.ndarray:
        return np.stack([f.to_vector() for f in features])

    def fit(self, features: list[AnomalyFeatures]) -> "AnomalyDetector":
        if not features:
            raise ValueError("need at least one training example")
        X = self._vectors(features)
        self._scaler = StandardScaler().fit(X)
        Xs = self._scaler.transform(X)
        self._model = IsolationForest(
            n_estimators=self._n_estimators, contamination=self._contamination,
            random_state=self._random_state,
        ).fit(Xs)
        scores = self._model.decision_function(Xs)
        self._train_score_lo, self._train_score_hi = float(scores.min()), float(scores.max())
        return self

    def _normalize(self, raw_score: float) -> float:
        lo, hi = self._train_score_lo, self._train_score_hi
        if lo is None or hi is None or hi <= lo:
            return 50.0
        # Higher raw_score = more normal, so risk is the INVERTED, clamped fraction.
        frac = (raw_score - lo) / (hi - lo)
        return float(100.0 * (1.0 - max(0.0, min(1.0, frac))))

    def predict(self, features: AnomalyFeatures) -> AnomalyPrediction:
        return self.predict_batch([features])[0]

    def predict_batch(self, features: list[AnomalyFeatures]) -> list[AnomalyPrediction]:
        if self._model is None or self._scaler is None:
            raise RuntimeError("AnomalyDetector is not trained; call fit() or load() first")
        Xs = self._scaler.transform(self._vectors(features))
        raws = self._model.decision_function(Xs)
        preds = self._model.predict(Xs)
        return [AnomalyPrediction(float(r), bool(p == -1), self._normalize(float(r)))
                for r, p in zip(raws, preds)]

    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({
            "n_estimators": self._n_estimators, "contamination": self._contamination,
            "random_state": self._random_state, "model": self._model, "scaler": self._scaler,
            "train_score_lo": self._train_score_lo, "train_score_hi": self._train_score_hi,
        }, path)

    @classmethod
    def load(cls, path: str | Path) -> "AnomalyDetector":
        data = joblib.load(path)
        det = cls(data["n_estimators"], data["contamination"], data["random_state"])
        det._model = data["model"]
        det._scaler = data["scaler"]
        det._train_score_lo, det._train_score_hi = data["train_score_lo"], data["train_score_hi"]
        return det
