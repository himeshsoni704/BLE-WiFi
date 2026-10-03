"""Attribution of Isolation Forest scores (app/xai.py): the maths, the guarantees, and the unhappy paths."""
import json
from math import isclose

import numpy as np
import pytest

from app import xai
from app.anomaly import FEATURES, AnomalyModel, to_matrix, train_isolation_forest, weak_features

N = len(FEATURES)
TYPICAL = {"ble_duration": 2150.0, "mean_ble_rssi": -58.5, "wifi_confidence": 0.8, "nearby_device_count": 15.0,
           "token_reuse_count": 0.0, "time_between_locations": 3600.0, "estimated_speed": 0.0,
           "number_of_classrooms": 1.0, "session_switch_count": 1.0, "signal_consistency": 0.85,
           "peer_rssi_max": -52.0, "rssi_twin_distance": 3.2}


def normal_rows(n=600, seed=0):
    """Mostly-normal behaviour with a little spread. A few percent of rows have a room change or token reuse, so those
    features vary a little (a forest cannot split on a feature that never varies, which test_a_constant_feature... shows)."""
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        r = {f: v + rng.normal(0, 0.05 * abs(v) + 0.3) for f, v in TYPICAL.items()}
        r["token_reuse_count"] = 2.0 if rng.random() < 0.01 else 0.0                    # ~1%: weakly represented
        moved = rng.random() < 0.04
        r["time_between_locations"] = float(rng.uniform(20, 60)) if moved else 3600.0
        r["estimated_speed"] = float(rng.uniform(3, 6)) if moved else 0.0
        r["session_switch_count"] = 2.0 if moved else 1.0
        r["number_of_classrooms"] = 1.0                                                # never varies
        rows.append(r)
    return rows


@pytest.fixture(scope="module")
def model():
    return train_isolation_forest(normal_rows(), contamination=0.05, seed=0)


# ---- the Shapley maths on functions with known answers ---------------------------------------------------------

def phi_for(fn, x, b):
    """Shapley values of an arbitrary function fn(vector)->float over N features, via xai's own coalition machinery."""
    coalitions = xai._coalitions(np.asarray(x, float), np.asarray(b, float))
    return xai.shapley_values(np.array([fn(c) for c in coalitions]), len(x))


def test_additive_function_gives_each_feature_exactly_its_own_term():
    x, b = np.arange(1, N + 1, dtype=float), np.zeros(N)
    phi = phi_for(lambda v: float(v @ np.arange(1, N + 1)), x, b)       # f = sum_i i * v_i
    assert np.allclose(phi, x * np.arange(1, N + 1))


def test_interaction_is_split_equally_between_the_two_features():
    x, b = np.zeros(N), np.zeros(N)
    x[2], x[5] = 3.0, 4.0
    phi = phi_for(lambda v: float(v[2] * v[5]), x, b)                   # f = v2 * v5: worth 12, only together
    assert isclose(phi[2], 6.0) and isclose(phi[5], 6.0) and np.allclose(np.delete(phi, [2, 5]), 0.0)


def test_efficiency_holds_for_an_arbitrary_nonlinear_function():
    rng = np.random.default_rng(3)
    x, b = rng.normal(size=N), rng.normal(size=N)
    fn = lambda v: float(np.sin(v[0]) * v[1] + np.maximum(v[2], 0) * v[3] ** 2 - np.abs(v[4] - v[5]))
    phi = phi_for(fn, x, b)
    assert isclose(phi.sum(), fn(x) - fn(b), abs_tol=1e-9)


# ---- attribution on a real Isolation Forest ----------------------------------------------------------------------

def test_contributions_sum_to_the_score_difference(model):
    rec = dict(TYPICAL, estimated_speed=9.0, session_switch_count=3.0, time_between_locations=12.0)
    a = xai.attribute(model, rec)
    assert a["available"]
    # contributions are in 'more unusual' units; they sum to baseline_score - score (sklearn score_samples units)
    assert isclose(a["sum_of_contributions"], a["baseline_score"] - a["score"], abs_tol=2e-4)
    assert isclose(sum(f["contribution"] for f in a["features"]), a["sum_of_contributions"], abs_tol=2e-4)


def test_a_feature_equal_to_its_baseline_contributes_nothing(model):
    rec = dict(model.stats["baseline"])
    rec["estimated_speed"] = 9.0
    a = xai.attribute(model, rec)
    by = {f["feature"]: f for f in a["features"]}
    assert all(abs(by[f]["contribution"]) < 1e-9 for f in FEATURES if f != "estimated_speed")
    assert a["top"][0]["feature"] == "estimated_speed" and a["top"][0]["share_pct"] == 100.0


def test_a_record_extreme_in_every_varying_feature_is_flagged_and_drivers_are_the_changed_features(model):
    changed = dict(ble_duration=50.0, mean_ble_rssi=-96.0, wifi_confidence=0.0, nearby_device_count=0.0,
                   signal_consistency=0.0, peer_rssi_max=-100.0, rssi_twin_distance=30.0)
    a = xai.attribute(model, dict(model.stats["baseline"], **changed))
    assert a["flagged"] and a["baseline_is_typical"] and a["score"] < a["flag_threshold"] < a["baseline_score"]
    assert a["top"] and {f["feature"] for f in a["top"]} <= set(changed)
    assert all(f["outside_typical_range"] for f in a["features"] if f["feature"] in changed)
    clear = a["would_stop_being_flagged_if_typical"]                     # may be None: many features changed at once
    if clear:
        assert clear["score_after"] >= a["flag_threshold"] and set(clear["features"]) <= set(changed)


def test_a_one_feature_anomaly_names_that_feature_and_fixing_it_clears_the_flag():
    rng = np.random.default_rng(1)
    rows = [dict(TYPICAL, mean_ble_rssi=-58.0 + rng.normal(0, 3)) for _ in range(500)]
    only = train_isolation_forest(rows, contamination=0.05, seed=0)
    a = xai.attribute(only, dict(TYPICAL, mean_ble_rssi=-120.0))
    assert a["flagged"]
    assert [f["feature"] for f in a["top"]] == ["mean_ble_rssi"] and a["top"][0]["share_pct"] == 100.0
    assert a["would_stop_being_flagged_if_typical"]["features"] == ["mean_ble_rssi"]
    assert a["would_stop_being_flagged_if_typical"]["score_after"] >= a["flag_threshold"]


def test_a_constant_feature_in_training_is_invisible_to_the_forest_and_the_attribution_says_so(model):
    """number_of_classrooms never varies in this training data: an extreme live value cannot change the score, and the
    attribution reports exactly that (zero contribution) instead of inventing a reason. This is the blind spot the
    deterministic rules exist to cover."""
    a = xai.attribute(model, dict(model.stats["baseline"], number_of_classrooms=9.0))
    by = {f["feature"]: f for f in a["features"]}
    assert by["number_of_classrooms"]["contribution"] == 0.0 and by["number_of_classrooms"]["outside_typical_range"]
    assert a["top"] == [] and not a["flagged"]


def test_a_typical_record_is_not_flagged_and_has_no_counterfactual(model):
    a = xai.attribute(model, dict(model.stats["baseline"]))
    assert not a["flagged"] and a["would_stop_being_flagged_if_typical"] is None
    assert abs(a["sum_of_contributions"]) < 1e-9 and all(f["share_pct"] == 0.0 for f in a["features"])


def test_shares_are_percentages_of_the_unusualness_and_sorted(model):
    a = xai.attribute(model, dict(TYPICAL, estimated_speed=9.0, mean_ble_rssi=-90.0, session_switch_count=3.0))
    shares = [f["share_pct"] for f in a["features"] if f["direction"] == "more_unusual"]
    assert isclose(sum(shares), 100.0, abs_tol=0.5)
    assert [f["contribution"] for f in a["features"]] == sorted((f["contribution"] for f in a["features"]), reverse=True)


def test_placeholder_values_are_described_in_words_not_as_numbers(model):
    rec = dict(model.stats["baseline"], rssi_twin_distance=30.0, mean_ble_rssi=-100.0)
    by = {f["feature"]: f for f in xai.attribute(model, rec)["features"]}
    assert "no other phone" in by["rssi_twin_distance"]["value_note"]
    assert "not heard" in by["mean_ble_rssi"]["value_note"]
    assert by["ble_duration"]["value_note"] is None


def test_every_feature_is_documented():
    assert set(xai.FEATURE_INFO) == set(FEATURES)
    assert all(label and meaning for label, _, meaning in xai.FEATURE_INFO.values())


# ---- unhappy paths ----------------------------------------------------------------------------------------------------

def test_missing_model_reference_or_features_are_reported_not_raised(model):
    assert xai.attribute(None, TYPICAL) == {"available": False, "reason": "no Isolation Forest model is loaded"}
    old = AnomalyModel(model.model, {k: v for k, v in model.stats.items() if k not in ("baseline", "ranges")})
    assert not old.has_reference
    a = xai.attribute(old, TYPICAL)
    assert not a["available"] and "predates attribution" in a["reason"] and "train_anomaly_model.py" in a["reason"]
    bad = xai.attribute(model, {f: 1.0 for f in FEATURES[:-1]})
    assert not bad["available"] and "incomplete" in bad["reason"]
    assert not xai.attribute(model, None)["available"]


def test_attribution_is_fast_enough_to_run_per_request(model):
    import time
    t0 = time.time()
    xai.attribute(model, dict(TYPICAL, estimated_speed=9.0))
    assert time.time() - t0 < 3.0


def test_result_is_json_serialisable(model):
    json.dumps(xai.attribute(model, dict(TYPICAL, estimated_speed=9.0)))


# ---- model reference + weak features ---------------------------------------------------------------------------------

def test_training_stores_the_reference_and_it_survives_save_and_load(model, tmp_path):
    assert model.has_reference
    assert set(model.stats["baseline"]) == set(FEATURES) == set(model.stats["ranges"])
    lo, hi = model.stats["ranges"]["mean_ble_rssi"]
    assert lo < model.stats["baseline"]["mean_ble_rssi"] < hi
    model.save(tmp_path / "m.joblib")
    loaded = AnomalyModel.load(tmp_path / "m.joblib")
    assert loaded.has_reference and loaded.stats["baseline"] == model.stats["baseline"]


def test_weak_features_names_features_that_almost_never_vary():
    X = to_matrix(normal_rows(500))
    weak = {w["feature"]: w for w in weak_features(X)}
    assert "token_reuse_count" in weak and "number_of_classrooms" in weak           # ~1% and 0% of rows differ
    assert "mean_ble_rssi" not in weak and "ble_duration" not in weak and "estimated_speed" not in weak
    assert weak["number_of_classrooms"]["fraction_of_training_rows_that_differ"] == 0.0


def test_reference_is_recovered_from_the_training_csv_for_models_that_predate_attribution(make_app, tmp_path):
    app = make_app()
    svc = app.state.svc
    assert svc.anomaly is not None and svc.anomaly.has_reference            # freshly trained models carry it
    for k in ("baseline", "ranges"):
        svc.anomaly.stats.pop(k)
    assert not svc.anomaly.has_reference
    assert "no attribution reference" in svc._recover_reference()           # no CSV yet: says so, does not raise
    from app import mltrain
    rows = [{"session_id": i, "student_key": f"k{i}", "label": "normal", "is_anomaly": 0, "rule_hits": "", **r}
            for i, r in enumerate(normal_rows(80))]
    rows.append({"session_id": 999, "student_key": "bad", "label": "proxy", "is_anomaly": 1, "rule_hits": "",
                 **{f: 999.0 for f in FEATURES}})                       # anomalies must not shape 'typical'
    mltrain.write_feature_csv(tmp_path / "anomaly_features.csv", rows)   # make_app uses tmp_path as data_dir
    assert "recovered" in svc._recover_reference()
    assert svc.anomaly.has_reference
    assert abs(svc.anomaly.stats["baseline"]["mean_ble_rssi"] - (-58.5)) < 1.5
