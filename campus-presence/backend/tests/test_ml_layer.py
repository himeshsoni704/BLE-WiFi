import numpy as np
import pytest

from app import mltrain
from app.anomaly import FEATURES, AnomalyModel, train_isolation_forest
from app.wifi import WifiLocalizer, train_localizer
from simulation import campus


def synthetic_rows(n=300, seed=0):
    rng = np.random.default_rng(seed)
    return [{"label": "normal", "is_anomaly": 0, "rule_hits": "", "session_id": i, **{
        "ble_duration": rng.normal(2200, 250), "mean_ble_rssi": rng.normal(-58, 3), "wifi_confidence": rng.uniform(.6, .95),
        "nearby_device_count": float(rng.integers(8, 22)), "token_reuse_count": 0.0,
        "time_between_locations": rng.uniform(600, 3600), "estimated_speed": rng.uniform(0, .3),
        "number_of_classrooms": float(rng.integers(1, 3)), "session_switch_count": float(rng.integers(1, 3)),
        "signal_consistency": rng.uniform(.6, 1), "peer_rssi_max": rng.normal(-52, 4), "rssi_twin_distance": rng.uniform(2, 6)}}
        for i in range(n)]


# ---- Wi-Fi ----------------------------------------------------------------------------------

def test_wifi_localizer_learns_and_handles_missing_aps():
    fps, ys = mltrain.generate_wifi_dataset(60, 1)
    probe = train_localizer(np.zeros((1, 10)), ["x"], campus.AP_IDS, "knn")
    model = train_localizer(probe.vectorise(fps), ys, campus.AP_IDS, "rf")
    test_fps, test_ys = mltrain.generate_wifi_dataset(20, 99)
    acc = np.mean([p.zone == y for p, y in zip(model.predict_many(test_fps), test_ys)])
    assert acc > 0.6                                    # synthetic, 22 zones; chance is 4.5%
    p = model.predict({"AP_01": -50.0, "UNKNOWN_AP": -40.0})
    assert p.aps_used == 1 and 0 < p.confidence <= 1 and len(p.top) == 3
    assert model.vectorise([{}])[0].tolist() == [-100.0] * 10        # nothing heard -> all "missing"


def test_wifi_save_load_roundtrip(tmp_path):
    fps, ys = mltrain.generate_wifi_dataset(20, 2)
    probe = train_localizer(np.zeros((1, 10)), ["x"], campus.AP_IDS, "knn")
    m = train_localizer(probe.vectorise(fps), ys, campus.AP_IDS, "knn")
    m.save(tmp_path / "w.joblib")
    m2 = WifiLocalizer.load(tmp_path / "w.joblib")
    assert m2.predict(fps[0]).zone == m.predict(fps[0]).zone and m2.ap_ids == m.ap_ids


def test_wifi_report_has_honest_metrics():
    model, rep = mltrain.train_wifi(80, 30)
    for k in ("accuracy", "macro_precision", "macro_recall", "macro_f1", "confusion_matrix", "labels", "caveat"):
        assert k in rep
    assert rep["data"] == "synthetic" and "not a real campus" in rep["caveat"]
    n = len(rep["labels"])
    assert len(rep["confusion_matrix"]) == n and sum(map(sum, rep["confusion_matrix"])) == rep["n_test"]


# ---- Isolation Forest -----------------------------------------------------------------------

def test_isolation_forest_flags_unusual_combinations_and_reports_raw_scores():
    rows = synthetic_rows()
    contam = [{**rows[0], "token_reuse_count": 3.0, "estimated_speed": 12.0, "label": "x"} for _ in range(8)]
    model = train_isolation_forest(rows + contam, contamination=0.05, seed=0)
    normal = model.score(synthetic_rows(50, 5))
    odd = model.score([{**rows[0], "token_reuse_count": 4.0, "estimated_speed": 15.0, "signal_consistency": 0.0,
                        "wifi_confidence": 0.3, "ble_duration": 300}])
    assert odd[0].flagged and odd[0].raw < np.median([s.raw for s in normal])
    assert odd[0].risk_demo > 80
    assert np.mean([s.flagged for s in normal]) < 0.15
    assert all(s.raw < 0 for s in normal)                          # sklearn score_samples are negative


def test_isolation_forest_save_load_and_feature_guard(tmp_path):
    model = train_isolation_forest(synthetic_rows(), seed=0)
    model.save(tmp_path / "if.joblib")
    loaded = AnomalyModel.load(tmp_path / "if.joblib")
    r = synthetic_rows(3, 9)
    assert [s.raw for s in loaded.score(r)] == pytest.approx([s.raw for s in model.score(r)])
    import joblib
    d = joblib.load(tmp_path / "if.joblib"); d["features"] = FEATURES[:-1]; joblib.dump(d, tmp_path / "old.joblib")
    with pytest.raises(ValueError, match="different feature list"):
        AnomalyModel.load(tmp_path / "old.joblib")


def test_train_anomaly_report_is_honest_and_splits_by_session():
    rows = synthetic_rows(300)
    for i in range(30):
        rows.append({**rows[i], "label": "impossible_movement", "is_anomaly": 1, "session_id": 1000 + i // 2,
                     "estimated_speed": 12.0, "token_reuse_count": 0.0, "time_between_locations": 5.0})
    model, rep = mltrain.train_anomaly(rows, seed=1)
    assert 0 < rep["n_train_unlabeled_contaminants"] <= 10        # mostly-normal training, small contamination
    assert rep["data"] == "synthetic" and "does not decide attendance" in rep["caveat"]
    assert len(rep["threshold_sweep"]) == 4
    cm = rep["confusion_matrix"]["matrix"]
    assert sum(map(sum, cm)) == rep["n_test"]
    assert rep["recall"] > 0.5


def test_feature_list_matches_the_spec():
    for f in ("ble_duration", "mean_ble_rssi", "wifi_confidence", "nearby_device_count", "token_reuse_count",
              "time_between_locations", "estimated_speed", "number_of_classrooms", "session_switch_count",
              "signal_consistency"):
        assert f in FEATURES
