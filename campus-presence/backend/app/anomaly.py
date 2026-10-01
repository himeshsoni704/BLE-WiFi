"""Isolation Forest over per-(student, session) behaviour features.

Isolation Forest finds *unusual combinations* of features relative to what it saw in
training. It does not decide attendance and it does not know why something is unusual.
Hits go to human review. The model here is trained on SYNTHETIC data (ml/generate_dataset.py);
on a real campus it must be retrained on real normal behaviour.

Raw scores are sklearn `score_samples` values (more negative = more anomalous). The 0-100
`risk_demo` is a min-max rescale of that raw score against the training distribution for the
dashboard only: it is NOT a probability.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import joblib
import numpy as np
from sklearn.ensemble import IsolationForest

from .config import Thresholds
from .rules import RuleContext, max_rooms_in_window

FEATURES = [
    "ble_duration", "mean_ble_rssi", "wifi_confidence", "nearby_device_count", "token_reuse_count",
    "time_between_locations", "estimated_speed", "number_of_classrooms", "session_switch_count",
    "signal_consistency", "peer_rssi_max", "rssi_twin_distance",
]
# ble_duration is normalised to a 50-minute reference session so short live-demo sessions are
# comparable to the synthetic training sessions: coverage_fraction * REF_SESSION_S.
REF_SESSION_S = 3000.0
MODEL_VERSION = 1


def extract_features(ev: dict, ctx: RuleContext, t: Thresholds) -> dict[str, float]:
    ble, wifi, peers = ev["classroom_ble"], ev["wifi"], ev["peers"]
    pts = list(ctx.locations)
    gaps, speeds = [], []
    for (t0, z0, _, _), (t1, z1, _, _) in zip(pts, pts[1:]):
        if z0 != z1:
            dt = max(1.0, t1 - t0)
            gaps.append(dt)
            speeds.append(ctx.zone_distance(z0, z1) / dt)
    n_rooms, _ = max_rooms_in_window(ctx.marker_timeline, t.session_switch_window_s)
    if ble["detected"] and wifi["available"]:
        consistency = wifi["match_fraction"]
    elif ble["detected"] and not wifi["available"]:
        consistency = 0.5            # neutral: no second signal to compare with
    else:
        consistency = 0.0
    return {
        "ble_duration": ble["coverage_fraction"] * REF_SESSION_S,
        "mean_ble_rssi": ble["rssi_smoothed_dbm"] if ble["rssi_smoothed_dbm"] is not None else -100.0,
        "wifi_confidence": wifi["confidence"] or 0.0,
        "nearby_device_count": float(peers["observed_distinct"]),
        "token_reuse_count": float(len({k for k, _ in ctx.remote_observers})),
        "time_between_locations": float(min(gaps)) if gaps else 3600.0,
        "estimated_speed": float(min(50.0, max(speeds))) if speeds else 0.0,
        "number_of_classrooms": float(len({r for _, r in ctx.marker_timeline}) or (1 if ble["detected"] else 0)),
        "session_switch_count": float(n_rooms),
        "signal_consistency": float(consistency),
        "peer_rssi_max": peers["strongest_rssi_dbm"] if peers["strongest_rssi_dbm"] is not None else -100.0,
        "rssi_twin_distance": float(min(30.0, ctx.twin_distance_db)),
    }


def to_matrix(rows: Sequence[dict[str, float]]) -> np.ndarray:
    return np.array([[float(r[f]) for f in FEATURES] for r in rows], dtype=float)


@dataclass
class AnomalyScore:
    raw: float            # sklearn score_samples; lower = more anomalous
    decision: float       # score_samples - offset_; < 0 => flagged
    flagged: bool
    risk_demo: float      # 0-100 rescale for display only


@dataclass
class AnomalyModel:
    model: IsolationForest
    stats: dict
    version: int = MODEL_VERSION
    meta: dict | None = None

    def score(self, rows: Sequence[dict[str, float]]) -> list[AnomalyScore]:
        if not rows:
            return []
        X = to_matrix(rows)
        raw = self.model.score_samples(X)
        dec = self.model.decision_function(X)
        lo, hi = self.stats["p01"], self.stats["median"]
        out = []
        for r, d in zip(raw, dec):
            risk = 100.0 * float(np.clip((hi - r) / max(1e-9, hi - lo), 0.0, 1.0))
            out.append(AnomalyScore(float(r), float(d), bool(d < 0), round(risk, 1)))
        return out

    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({"model": self.model, "stats": self.stats, "features": FEATURES,
                     "version": self.version, "meta": self.meta or {}}, path)

    @classmethod
    def load(cls, path: str | Path) -> "AnomalyModel":
        d = joblib.load(path)
        if list(d["features"]) != FEATURES:
            raise ValueError("isolation_forest.joblib was trained with a different feature list; retrain it")
        return cls(d["model"], d["stats"], d.get("version", 1), d.get("meta"))


def train_isolation_forest(rows: Sequence[dict[str, float]], contamination: float = 0.02,
                           seed: int = 0, n_estimators: int = 200, meta: dict | None = None) -> AnomalyModel:
    X = to_matrix(rows)
    model = IsolationForest(n_estimators=n_estimators, contamination=contamination, random_state=seed)
    model.fit(X)
    s = model.score_samples(X)
    stats = {"median": float(np.median(s)), "p01": float(np.percentile(s, 1)), "min": float(s.min()),
             "offset": float(model.offset_), "n_train": int(len(X)), "contamination": contamination}
    return AnomalyModel(model, stats, MODEL_VERSION, meta)
