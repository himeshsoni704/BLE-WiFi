"""Wi-Fi fingerprint localisation: RSSI vector over a fixed AP list -> zone + confidence.

This is room-level classification, not centimetre positioning. `confidence` is the
classifier's top class probability; it is NOT a calibrated probability.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence

import joblib
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.neighbors import KNeighborsClassifier

MISSING_RSSI = -100.0


@dataclass
class WifiPrediction:
    zone: str
    confidence: float
    top: list[tuple[str, float]]
    aps_used: int


@dataclass
class WifiLocalizer:
    model: object
    ap_ids: list[str]
    classes: list[str]
    kind: str = "rf"
    metrics: dict = field(default_factory=dict)
    trained_on: str = "synthetic"

    def vectorise(self, fingerprints: Sequence[Mapping[str, float]]) -> np.ndarray:
        idx = {a: i for i, a in enumerate(self.ap_ids)}
        X = np.full((len(fingerprints), len(self.ap_ids)), MISSING_RSSI, dtype=float)
        for r, fp in enumerate(fingerprints):
            for ap, rssi in fp.items():
                j = idx.get(ap)
                if j is not None:
                    X[r, j] = float(rssi)
        return X

    def predict_many(self, fingerprints: Sequence[Mapping[str, float]], top_k: int = 3) -> list[WifiPrediction]:
        if not fingerprints:
            return []
        X = self.vectorise(fingerprints)
        proba = self.model.predict_proba(X)
        classes = [str(c) for c in self.model.classes_]
        out = []
        for row, fp in zip(proba, fingerprints):
            order = np.argsort(row)[::-1][:top_k]
            top = [(classes[i], float(row[i])) for i in order]
            used = sum(1 for ap in fp if ap in self.ap_ids)
            out.append(WifiPrediction(top[0][0], top[0][1], top, used))
        return out

    def predict(self, fingerprint: Mapping[str, float]) -> WifiPrediction:
        return self.predict_many([fingerprint])[0]

    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({"model": self.model, "ap_ids": self.ap_ids, "classes": self.classes,
                     "kind": self.kind, "metrics": self.metrics, "trained_on": self.trained_on}, path, compress=3)

    @classmethod
    def load(cls, path: str | Path) -> "WifiLocalizer":
        d = joblib.load(path)
        return cls(d["model"], d["ap_ids"], d["classes"], d.get("kind", "rf"),
                   d.get("metrics", {}), d.get("trained_on", "unknown"))


def build_classifier(kind: str, seed: int = 0):
    if kind == "knn":
        return KNeighborsClassifier(n_neighbors=7, weights="distance")
    if kind == "rf":
        return RandomForestClassifier(n_estimators=100, min_samples_leaf=4, n_jobs=-1, random_state=seed)
    raise ValueError(f"unknown model kind {kind!r}")


def train_localizer(X: np.ndarray, y: Sequence[str], ap_ids: list[str], kind: str = "rf",
                    seed: int = 0, trained_on: str = "synthetic") -> WifiLocalizer:
    clf = build_classifier(kind, seed)
    clf.fit(X, np.asarray(y))
    return WifiLocalizer(clf, list(ap_ids), [str(c) for c in clf.classes_], kind, {}, trained_on)
