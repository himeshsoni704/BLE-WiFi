"""Train/refresh the LIVE Wi-Fi localiser from fingerprints collected in survey mode."""
from __future__ import annotations

import json
import time
from collections import Counter

import numpy as np
from sklearn.model_selection import StratifiedKFold, cross_val_score
from sqlalchemy import select
from sqlalchemy.orm import Session

from .core import Services
from .models import AccessPoint, WifiTraining
from .wifi import WifiLocalizer, build_classifier, train_localizer

MIN_PER_ZONE = 8


def retrain_live(db: Session, svc: Services) -> dict:
    rows = db.execute(select(WifiTraining.zone, WifiTraining.fingerprint_json)
                      .where(WifiTraining.source == "live_survey")).all()
    counts = Counter(z for z, _ in rows)
    ok_zones = {z for z, n in counts.items() if n >= MIN_PER_ZONE}
    if len(ok_zones) < 2:
        return {"status": "not_enough_data", "samples_per_zone": dict(counts),
                "need": f"at least 2 zones with >= {MIN_PER_ZONE} survey scans each"}
    ap_ids = sorted(a.ap_id for a in db.scalars(select(AccessPoint).where(AccessPoint.is_simulated == False)))   # noqa: E712
    fps = [json.loads(f) for z, f in rows if z in ok_zones]
    ys = [z for z, _ in rows if z in ok_zones]
    probe = train_localizer(np.zeros((1, max(1, len(ap_ids)))), ["x"], ap_ids or ["_"], "knn")
    X = probe.vectorise(fps)
    folds = min(5, min(Counter(ys).values()))
    best = None
    scores = {}
    for kind in ("knn", "rf"):
        cv = cross_val_score(build_classifier(kind), X, np.asarray(ys),
                             cv=StratifiedKFold(folds, shuffle=True, random_state=0))
        scores[kind] = float(cv.mean())
        if best is None or scores[kind] > best[0]:
            best = (scores[kind], kind)
    model = train_localizer(X, ys, ap_ids, best[1], trained_on="live_survey")
    model.metrics = {"cv_accuracy": best[0], "folds": folds, "n": len(ys), "zones": sorted(ok_zones)}
    model.save(svc.settings.wifi_live_model_path)
    svc.wifi_live = model
    return {"status": "trained", "model": best[1], "cv_accuracy": round(best[0], 3), "candidates": scores,
            "zones": sorted(ok_zones), "n_scans": len(ys), "aps": ap_ids,
            "caveat": ("cross-validated on scans from the same survey walk, so this is optimistic; a live "
                       "check from a different spot/time is the real test"),
            "trained_at": time.time()}


def reference_fingerprints(db: Session) -> dict:
    """Mean fingerprint per surveyed zone, for the phone's on-device nearest-centroid estimate."""
    rows = db.execute(select(WifiTraining.zone, WifiTraining.fingerprint_json)
                      .where(WifiTraining.source == "live_survey")).all()
    ap_ids = sorted(a.ap_id for a in db.scalars(select(AccessPoint).where(AccessPoint.is_simulated == False)))   # noqa: E712
    acc: dict[str, list[list[float]]] = {}
    for z, f in rows:
        fp = json.loads(f)
        acc.setdefault(z, []).append([fp.get(a, -100.0) for a in ap_ids])
    return {"ap_ids": ap_ids, "centroids": {z: np.mean(v, axis=0).round(1).tolist() for z, v in acc.items()},
            "samples": {z: len(v) for z, v in acc.items()}, "missing_value": -100.0,
            "note": "empty until rooms are surveyed (POST /wifi/survey)" if not acc else "from live survey scans"}
