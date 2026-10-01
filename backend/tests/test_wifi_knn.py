import numpy as np
import pytest

from app.wifi_knn import UNSEEN_RSSI, WifiFingerprintModel

# Four APs, two well-separated zones: in zone A, AP1 is loud and AP4 is faint;
# in zone B it's reversed. A handful of noisy samples each.
RNG = np.random.default_rng(0)


def _samples(n, zone_rssi, noise=2.0):
    out = []
    for _ in range(n):
        out.append({ap: float(rssi + RNG.normal(0, noise)) for ap, rssi in zone_rssi.items()})
    return out


ZONE_A = {"AP1": -42, "AP2": -65, "AP3": -58, "AP4": -81}
ZONE_B = {"AP1": -78, "AP2": -46, "AP3": -60, "AP4": -70}


def _fit_model():
    fps = _samples(30, ZONE_A) + _samples(30, ZONE_B)
    zones = ["ROOM_204"] * 30 + ["ROOM_205"] * 30
    return WifiFingerprintModel(n_neighbors=5).fit(fps, zones)


def test_predicts_the_correct_zone_for_a_clean_fingerprint():
    model = _fit_model()
    pred = model.predict(ZONE_A)
    assert pred.zone == "ROOM_204"
    assert pred.confidence > 0.5


def test_predicts_the_other_zone():
    model = _fit_model()
    pred = model.predict(ZONE_B)
    assert pred.zone == "ROOM_205"


def test_unseen_ap_is_treated_as_floor_not_missing():
    model = _fit_model()
    partial = {"AP1": -42, "AP2": -65}   # AP3/AP4 not heard this scan
    pred = model.predict(partial)
    assert pred.zone == "ROOM_204"


def test_empty_fingerprint_returns_no_prediction():
    model = _fit_model()
    pred = model.predict({})
    assert pred.zone is None
    assert pred.confidence == 0.0


def test_untrained_model_returns_no_prediction():
    model = WifiFingerprintModel()
    assert model.predict(ZONE_A).zone is None


def test_save_and_load_round_trip(tmp_path):
    model = _fit_model()
    path = tmp_path / "wifi_localization.joblib"
    model.save(path)
    loaded = WifiFingerprintModel.load(path)
    assert loaded.bssid_order == model.bssid_order
    assert loaded.predict(ZONE_A).zone == model.predict(ZONE_A).zone


def test_vectorize_fills_unseen_aps_with_floor():
    model = _fit_model()
    vec = model._vectorize({"AP1": -42})
    idx = model.bssid_order.index("AP1")
    assert vec[idx] == -42
    others = [v for i, v in enumerate(vec) if i != idx]
    assert all(v == UNSEEN_RSSI for v in others)


def test_fit_rejects_mismatched_lengths():
    with pytest.raises(ValueError):
        WifiFingerprintModel().fit([{"AP1": -40}], ["a", "b"])


def test_fit_rejects_empty():
    with pytest.raises(ValueError):
        WifiFingerprintModel().fit([], [])
