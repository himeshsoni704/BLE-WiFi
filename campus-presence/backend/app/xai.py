"""Explanations of WHY the Isolation Forest scored a record as unusual.

Isolation Forest returns a score and nothing else. This module attributes that score to the 12 input features with
**exact Shapley values**, so a reviewer sees "this was flagged mostly because of X and Y" instead of a bare number.

How it works. Let f(x) = -score_samples(x), so a larger f means more anomalous. A "coalition" S is a subset of the
features: those in S take the record's own values, the rest are set to a typical-normal baseline (the training median
of each feature). v(S) = f(that mixed record). The Shapley value of feature i is its average marginal effect
v(S + i) - v(S) over all coalitions, weighted as in cooperative game theory. With 12 features there are only 4096
coalitions, so it is computed exactly (no sampling), in one batch.

What you can rely on:
  * efficiency: the contributions sum to f(record) - f(baseline) (= baseline_score - score in the output), checked
    in the tests;
  * a feature equal to its baseline value contributes exactly 0;
  * it explains THIS MODEL's score. It does not say a feature caused real misconduct, and it depends on the baseline
    chosen (here the training median). A feature can be "unusual" yet matter little if the forest ignores it.
"""
from __future__ import annotations

from math import factorial

import numpy as np

from .anomaly import FEATURES, AnomalyModel

# label, unit, one-line meaning (also the vocabulary the LLM is allowed to use for features)
FEATURE_INFO: dict[str, tuple[str, str, str]] = {
    "ble_duration": ("Bluetooth time in the room", "s", "how long the classroom marker was heard, scaled to a 50-minute session"),
    "mean_ble_rssi": ("Classroom Bluetooth signal strength", "dBm", "smoothed marker RSSI; closer to 0 is stronger"),
    "wifi_confidence": ("Wi-Fi room confidence", "", "the Wi-Fi model's top-class probability, not calibrated"),
    "nearby_device_count": ("Nearby devices", "", "distinct other phones this phone saw"),
    "token_reuse_count": ("Devices elsewhere seeing this token", "", "other-room devices that observed this student's temporary token"),
    "time_between_locations": ("Time between room changes", "s", "shortest gap between marker detections in different rooms"),
    "estimated_speed": ("Implied travel speed", "m/s", "fastest speed implied between marker detections in different rooms"),
    "number_of_classrooms": ("Classrooms detected", "", "distinct classroom markers heard"),
    "session_switch_count": ("Rooms within 5 minutes", "", "most distinct classroom markers heard in any 5-minute window"),
    "signal_consistency": ("Bluetooth and Wi-Fi agreement", "", "share of Wi-Fi evidence agreeing with the Bluetooth room"),
    "peer_rssi_max": ("Strongest nearby phone signal", "dBm", "closest other phone; very strong means very close"),
    "rssi_twin_distance": ("Signal similarity to another phone", "dB", "how closely another phone's marker readings track this one's; small means moving together"),
}
assert set(FEATURE_INFO) == set(FEATURES)

# Values the feature extractor uses to mean "nothing to measure" (anomaly.extract_features). Reading them as real
# numbers would mislead: rssi_twin_distance 30 is not "30 dB similar", it is "no other phone to compare with".
SENTINELS: dict[str, tuple[float, str]] = {
    "mean_ble_rssi": (-100.0, "the classroom marker was not heard"),
    "peer_rssi_max": (-100.0, "no other phone was heard"),
    "wifi_confidence": (0.0, "there was no Wi-Fi estimate"),
    "time_between_locations": (3600.0, "there was no change of room"),
    "estimated_speed": (0.0, "there was no change of room"),
    "rssi_twin_distance": (30.0, "no other phone had readings comparable to this one"),
}


def value_note(feature: str, value: float) -> str | None:
    s = SENTINELS.get(feature)
    return s[1] if s is not None and abs(value - s[0]) < 1e-9 else None


MAX_FEATURES_EXACT = 14                      # 2**14 coalitions is still a single fast batch


def _weights(n: int) -> np.ndarray:
    return np.array([factorial(k) * factorial(n - k - 1) / factorial(n) for k in range(n)])


def _coalitions(x: np.ndarray, b: np.ndarray) -> np.ndarray:
    n = len(x)
    masks = np.arange(1 << n)
    bits = ((masks[:, None] >> np.arange(n)) & 1).astype(bool)
    return np.where(bits, x[None, :], b[None, :])


def shapley_values(f_of_coalitions: np.ndarray, n: int) -> np.ndarray:
    """Exact Shapley values from f evaluated on every coalition (index = bitmask of included features)."""
    masks = np.arange(1 << n)
    pop = np.array([bin(m).count("1") for m in masks])
    w = _weights(n)
    phi = np.zeros(n)
    for i in range(n):
        without = masks[((masks >> i) & 1) == 0]
        phi[i] = float(np.sum(w[pop[without]] * (f_of_coalitions[without | (1 << i)] - f_of_coalitions[without])))
    return phi


def unavailable(reason: str) -> dict:
    return {"available": False, "reason": reason}


def attribute(model: AnomalyModel | None, features: dict[str, float] | None, top_k: int = 5) -> dict:
    """Attribution of one record's anomaly score. Always returns a dict; `available` says whether it worked."""
    if model is None:
        return unavailable("no Isolation Forest model is loaded")
    if not model.has_reference:
        return unavailable("this model file predates attribution: run `python ml/generate_dataset.py` then "
                           "`python ml/train_anomaly_model.py` (or delete ml/models/isolation_forest.joblib and restart)")
    if not features or any(f not in features for f in FEATURES):
        return unavailable("the stored feature values for this record are incomplete")
    n = len(FEATURES)
    if n > MAX_FEATURES_EXACT:
        return unavailable(f"{n} features is too many for exact attribution")
    x = np.array([float(features[f]) for f in FEATURES])
    b = np.array([float(model.stats["baseline"][f]) for f in FEATURES])
    f_all = -model.model.score_samples(_coalitions(x, b))
    phi = shapley_values(f_all, n)
    full, empty = float(f_all[-1]), float(f_all[0])
    threshold = float(model.model.offset_)                       # flagged when score_samples < offset_
    pos_total = float(phi[phi > 0].sum())
    rows = []
    for i, name in enumerate(FEATURES):
        label, unit, meaning = FEATURE_INFO[name]
        lo, hi = model.stats["ranges"][name]
        rows.append({"feature": name, "label": label, "unit": unit, "meaning": meaning,
                     "value": round(float(x[i]), 4), "typical": round(float(b[i]), 4),
                     "value_note": value_note(name, float(x[i])), "typical_note": value_note(name, float(b[i])),
                     "typical_range": [round(lo, 4), round(hi, 4)],
                     "outside_typical_range": bool(x[i] < lo or x[i] > hi),
                     "contribution": round(float(phi[i]), 5),
                     "share_pct": round(100.0 * float(phi[i]) / pos_total, 1) if phi[i] > 0 and pos_total > 0 else 0.0,
                     "direction": "more_unusual" if phi[i] > 1e-9 else ("more_typical" if phi[i] < -1e-9 else "neutral")})
    rows.sort(key=lambda r: r["contribution"], reverse=True)
    driving = [r for r in rows if r["direction"] == "more_unusual"]

    # counterfactual: return the strongest drivers to their typical values one at a time until it would not be flagged
    order = [FEATURES.index(r["feature"]) for r in driving]
    cf_x, back, would_clear = x.copy(), [], None
    for i in order[:4]:
        cf_x[i] = b[i]
        back.append(FEATURES[i])
        score = float(model.model.score_samples(cf_x[None, :])[0])
        if score >= threshold:
            would_clear = {"features": list(back), "score_after": round(score, 4)}
            break
    return {
        "available": True,
        "method": ("exact Shapley values over the 12 features; baseline = training median of each feature; "
                   "positive contribution = pushes the score toward 'unusual'"),
        # scikit-learn score_samples units: more negative = more unusual; flagged when score < flag_threshold.
        # Contributions are in the opposite direction (positive = more unusual) and sum to baseline_score - score.
        "score": round(-full, 4), "baseline_score": round(-empty, 4), "flag_threshold": round(threshold, 4),
        "flagged": bool(-full < threshold),
        "baseline_is_typical": bool(-empty >= threshold),
        "sum_of_contributions": round(float(phi.sum()), 5),
        "features": rows,
        "top": driving[:top_k],
        "would_stop_being_flagged_if_typical": would_clear,
        "caveat": ("Explains this model's score for this record. It is not evidence of intent, and 'typical' means the "
                   "synthetic training median, not a rule."),
    }
