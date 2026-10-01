"""Wi-Fi RSSI fingerprint -> zone classifier (brief section 6).

A fingerprint is whatever subset of access points a phone's Wi-Fi scan
currently sees, each with an RSSI. Different scans see different subsets of
APs (one might be out of range, scanning is throttled, etc.), so fingerprints
are normalised into a FIXED-length vector over every BSSID the model was
trained on: an AP that wasn't heard in a given scan gets `UNSEEN_RSSI` (a
floor well below any real reading), which is itself informative ("this AP
is not visible from here") rather than a missing value to impute.

KNN, as the brief asks for ("do not overcomplicate this"): vector distance
between RSSI fingerprints is a reasonable proxy for physical distance
between the two scan locations, which is exactly the indoor-fingerprinting
principle used by the public repos this brief points at (fingerprint
database + nearest-neighbour / weighted-k-NN lookup -- see docs/THIRD_PARTY.md).
Confidence is sklearn's own predict_proba for KNeighborsClassifier: the
fraction of the k nearest neighbours that voted for the predicted zone, not
a fabricated number.

This does NOT claim centimetre-level location (brief section 6) -- it
predicts one of a fixed set of registered zones (rooms/corridors), with a
confidence that is low whenever the fingerprint sits between two zones.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import joblib
import numpy as np
from sklearn.neighbors import KNeighborsClassifier

UNSEEN_RSSI = -100.0


@dataclass(frozen=True)
class WifiPrediction:
    zone: str | None
    confidence: float

    def to_dict(self) -> dict:
        return {"predicted_zone": self.zone, "confidence": round(self.confidence, 3)}


class WifiFingerprintModel:
    """Wraps a KNeighborsClassifier plus the fixed BSSID ordering it needs
    to turn a sparse {bssid: rssi} fingerprint into a dense feature vector."""

    def __init__(self, n_neighbors: int = 5):
        self.n_neighbors = n_neighbors
        self.bssid_order: list[str] = []
        self._clf: KNeighborsClassifier | None = None

    @property
    def is_trained(self) -> bool:
        return self._clf is not None

    def _vectorize(self, fingerprint: dict[str, float]) -> np.ndarray:
        return np.array([fingerprint.get(b, UNSEEN_RSSI) for b in self.bssid_order], dtype=float)

    def fit(self, fingerprints: list[dict[str, float]], zones: list[str]) -> "WifiFingerprintModel":
        if len(fingerprints) != len(zones):
            raise ValueError("fingerprints and zones must be the same length")
        if not fingerprints:
            raise ValueError("need at least one training fingerprint")
        bssids: set[str] = set()
        for fp in fingerprints:
            bssids.update(fp.keys())
        self.bssid_order = sorted(bssids)
        X = np.stack([self._vectorize(fp) for fp in fingerprints])
        y = np.asarray(zones)
        k = min(self.n_neighbors, len(set(y.tolist())), len(y))
        self._clf = KNeighborsClassifier(n_neighbors=max(1, k), weights="distance")
        self._clf.fit(X, y)
        return self

    def predict(self, fingerprint: dict[str, float]) -> WifiPrediction:
        if self._clf is None or not fingerprint:
            return WifiPrediction(None, 0.0)
        x = self._vectorize(fingerprint).reshape(1, -1)
        zone = str(self._clf.predict(x)[0])
        proba = self._clf.predict_proba(x)[0]
        classes = list(self._clf.classes_)
        confidence = float(proba[classes.index(zone)])
        return WifiPrediction(zone, confidence)

    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({"n_neighbors": self.n_neighbors, "bssid_order": self.bssid_order, "clf": self._clf}, path)

    @classmethod
    def load(cls, path: str | Path) -> "WifiFingerprintModel":
        data = joblib.load(path)
        model = cls(n_neighbors=data["n_neighbors"])
        model.bssid_order = data["bssid_order"]
        model._clf = data["clf"]
        return model
