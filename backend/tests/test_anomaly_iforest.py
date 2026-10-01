import numpy as np
import pytest

from app.anomaly_iforest import FEATURE_NAMES, AnomalyDetector, AnomalyFeatures

RNG = np.random.default_rng(0)


def _normal(rng=RNG) -> AnomalyFeatures:
    # Close to the brief's own "Normal" worked example (section 16).
    return AnomalyFeatures(
        ble_duration=620 + rng.normal(0, 40), mean_ble_rssi=-55 + rng.normal(0, 4),
        wifi_confidence=min(1.0, max(0.0, 0.92 + rng.normal(0, 0.05))), nearby_device_count=int(rng.integers(2, 8)),
        token_reuse_count=0, time_between_locations=3600 + rng.normal(0, 200), estimated_speed=1.2 + rng.normal(0, 0.3),
        number_of_classrooms=1, session_switch_count=0, signal_consistency=min(1.0, max(0.0, 0.95 + rng.normal(0, 0.03))),
    )


def _anomaly() -> AnomalyFeatures:
    # The brief's own "Anomaly" worked example (section 16).
    return AnomalyFeatures(
        ble_duration=12, mean_ble_rssi=-85, wifi_confidence=0.22, nearby_device_count=18,
        token_reuse_count=4, time_between_locations=20, estimated_speed=35.0,
        number_of_classrooms=4, session_switch_count=5, signal_consistency=0.1,
    )


def _fit_detector(n=200):
    return AnomalyDetector(n_estimators=100, random_state=0).fit([_normal() for _ in range(n)])


def test_feature_names_and_vector_shape_match():
    f = _normal()
    assert len(FEATURE_NAMES) == len(f.to_vector())
    assert set(f.to_dict().keys()) == set(FEATURE_NAMES)


def test_untrained_detector_raises():
    with pytest.raises(RuntimeError):
        AnomalyDetector().predict(_normal())


def test_fit_rejects_empty():
    with pytest.raises(ValueError):
        AnomalyDetector().fit([])


def test_brief_anomaly_example_scores_worse_than_normal():
    det = _fit_detector()
    normal_preds = [det.predict(_normal()) for _ in range(30)]
    anomaly_pred = det.predict(_anomaly())
    mean_normal_raw = sum(p.raw_score for p in normal_preds) / len(normal_preds)
    assert anomaly_pred.raw_score < mean_normal_raw
    assert anomaly_pred.demo_risk_score > sum(p.demo_risk_score for p in normal_preds) / len(normal_preds)


def test_brief_anomaly_example_is_flagged_anomalous():
    det = _fit_detector()
    assert det.predict(_anomaly()).is_anomalous is True


def test_typical_normal_example_is_usually_not_flagged():
    det = _fit_detector()
    preds = [det.predict(_normal()) for _ in range(50)]
    flagged = sum(1 for p in preds if p.is_anomalous)
    assert flagged / len(preds) < 0.25   # IsolationForest's own contamination="auto" caps false positives


def test_demo_risk_score_is_bounded_0_100():
    det = _fit_detector()
    for f in [_normal() for _ in range(10)] + [_anomaly()]:
        score = det.predict(f).demo_risk_score
        assert 0.0 <= score <= 100.0


def test_predict_batch_matches_individual_predict():
    det = _fit_detector()
    feats = [_normal(), _anomaly(), _normal()]
    batch = det.predict_batch(feats)
    singles = [det.predict(f) for f in feats]
    for b, s in zip(batch, singles):
        assert b.raw_score == pytest.approx(s.raw_score)
        assert b.is_anomalous == s.is_anomalous


def test_save_and_load_round_trip(tmp_path):
    det = _fit_detector()
    path = tmp_path / "isolation_forest.joblib"
    det.save(path)
    loaded = AnomalyDetector.load(path)
    f = _anomaly()
    assert loaded.predict(f).raw_score == pytest.approx(det.predict(f).raw_score)
    assert loaded.predict(f).is_anomalous == det.predict(f).is_anomalous


def test_to_dict_shape():
    det = _fit_detector()
    d = det.predict(_anomaly()).to_dict()
    assert set(d.keys()) == {"isolation_forest_raw_score", "is_anomalous", "demo_risk_score", "note"}
    assert "not a calibrated probability" in d["note"]
