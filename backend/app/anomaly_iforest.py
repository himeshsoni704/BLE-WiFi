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
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import IsolationForest

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
    def __init__(self, n_estimators: int = 150, contamination: float | str = 0.05, random_state: int = 0):
        """contamination defaults to 0.05 (an expected ~5% anomaly rate),
        not sklearn's "auto": "auto" uses the fixed decision-boundary rule
        from the original Isolation Forest paper, which is NOT tuned to any
        particular false-positive rate on a given training set and empirically
        flagged ~15-30% of held-out normal examples in this module's own test
        suite. An explicit contamination fraction is sklearn's documented way
        to control that, and better matches the brief's own framing (train on
        MOSTLY normal data, a small share of real anomalies expected)."""
        self._n_estimators = n_estimators
        self._contamination = contamination
        self._random_state = random_state
        self._model: IsolationForest | None = None
        self._train_score_lo: float | None = None
        self._train_score_hi: float | None = None

    @property
    def is_trained(self) -> bool:
        return self._model is not None

    def fit(self, features: list[AnomalyFeatures]) -> "AnomalyDetector":
        if not features:
            raise ValueError("need at least one training example")
        X = np.stack([f.to_vector() for f in features])
        self._model = IsolationForest(
            n_estimators=self._n_estimators, contamination=self._contamination,
            random_state=self._random_state,
        ).fit(X)
        scores = self._model.decision_function(X)
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
        if self._model is None:
            raise RuntimeError("AnomalyDetector is not trained; call fit() or load() first")
        x = features.to_vector().reshape(1, -1)
        raw = float(self._model.decision_function(x)[0])
        is_anom = bool(self._model.predict(x)[0] == -1)
        return AnomalyPrediction(raw, is_anom, self._normalize(raw))

    def predict_batch(self, features: list[AnomalyFeatures]) -> list[AnomalyPrediction]:
        if self._model is None:
            raise RuntimeError("AnomalyDetector is not trained; call fit() or load() first")
        X = np.stack([f.to_vector() for f in features])
        raws = self._model.decision_function(X)
        preds = self._model.predict(X)
        return [AnomalyPrediction(float(r), bool(p == -1), self._normalize(float(r)))
                for r, p in zip(raws, preds)]

    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({
            "n_estimators": self._n_estimators, "contamination": self._contamination,
            "random_state": self._random_state, "model": self._model,
            "train_score_lo": self._train_score_lo, "train_score_hi": self._train_score_hi,
        }, path)

    @classmethod
    def load(cls, path: str | Path) -> "AnomalyDetector":
        data = joblib.load(path)
        det = cls(data["n_estimators"], data["contamination"], data["random_state"])
        det._model = data["model"]
        det._train_score_lo, det._train_score_hi = data["train_score_lo"], data["train_score_hi"]
        return det
