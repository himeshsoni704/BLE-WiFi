import pytest

from app.evidence import (
    AttendanceEvidence, AttendanceState, BleMarkerEvidence, EvidenceWeights,
    FaceEvidence, PeerBleEvidence, RfidEvidence, WifiEvidence, score_attendance,
)

W = EvidenceWeights()


def ev(**kw):
    base = dict(student_id="s1", session_id="CS301_2026_10_01", classroom_id="ROOM_204")
    base.update(kw)
    return AttendanceEvidence(**base)


def test_full_agreement_is_present():
    e = ev(
        ble_marker=BleMarkerEvidence(detected=True, rssi=-48, duration_s=700),
        wifi=WifiEvidence(predicted_zone="ROOM_204", confidence=1.0),
        peer_ble=PeerBleEvidence(nearby_devices=4, consistent_observations=4),
    )
    r = score_attendance(e, W)
    assert r.state is AttendanceState.PRESENT
    assert r.score == pytest.approx(100.0, abs=0.5)
    assert r.raw_points == pytest.approx(W.max_possible(False, False), abs=0.5)


def test_no_signals_at_all_is_absent():
    r = score_attendance(ev(), W)
    assert r.state is AttendanceState.ABSENT
    assert r.score == 0.0
    assert all(c.points == 0.0 for c in r.components)


def test_ble_only_is_review_required_not_absent():
    e = ev(ble_marker=BleMarkerEvidence(detected=True, rssi=-50, duration_s=600))
    r = score_attendance(e, W)
    assert r.state is AttendanceState.REVIEW_REQUIRED
    ble = next(c for c in r.components if c.name == "ble_marker")
    assert ble.points == pytest.approx(W.ble_marker)


def test_wifi_contradicts_ble_scores_zero_wifi_points():
    e = ev(
        ble_marker=BleMarkerEvidence(detected=True, rssi=-49, duration_s=600),
        wifi=WifiEvidence(predicted_zone="ROOM_205", confidence=0.8),
    )
    r = score_attendance(e, W)
    wifi = next(c for c in r.components if c.name == "wifi_zone_match")
    assert wifi.points == 0.0
    assert "Room 205" in wifi.reason and "Room 204" in wifi.reason


def test_sustained_presence_ramps_linearly():
    short = score_attendance(ev(ble_marker=BleMarkerEvidence(detected=True, duration_s=30)), W)
    long_ = score_attendance(ev(ble_marker=BleMarkerEvidence(detected=True, duration_s=300)), W)
    s_short = next(c for c in short.components if c.name == "sustained_presence")
    s_long = next(c for c in long_.components if c.name == "sustained_presence")
    assert s_short.points < s_long.points
    assert s_long.points == pytest.approx(W.sustained_presence)


def test_face_and_rfid_only_contribute_when_available():
    base = ev(ble_marker=BleMarkerEvidence(detected=True, duration_s=600))
    r_no_face = score_attendance(base, W)
    assert not any(c.name == "face_match" for c in r_no_face.components)
    assert r_no_face.max_possible == pytest.approx(W.max_possible(False, False))

    with_face = ev(
        ble_marker=BleMarkerEvidence(detected=True, duration_s=600),
        face=FaceEvidence(available=True, match=True, confidence=0.94),
    )
    r_face = score_attendance(with_face, W)
    face = next(c for c in r_face.components if c.name == "face_match")
    assert face.points == pytest.approx(W.face_match * 0.94)
    assert r_face.max_possible == pytest.approx(W.max_possible(True, False))


def test_peer_consistency_partial_credit():
    e = ev(peer_ble=PeerBleEvidence(nearby_devices=4, consistent_observations=2))
    r = score_attendance(e, W)
    peer = next(c for c in r.components if c.name == "peer_consistency")
    assert peer.points == pytest.approx(W.peer_consistency * 0.5)


def test_thresholds_are_configurable():
    strict = EvidenceWeights(present_threshold=0.99)
    e = ev(
        ble_marker=BleMarkerEvidence(detected=True, duration_s=600),
        wifi=WifiEvidence(predicted_zone="ROOM_204", confidence=0.9),
        peer_ble=PeerBleEvidence(nearby_devices=2, consistent_observations=2),
    )
    assert score_attendance(e, W).state is AttendanceState.PRESENT
    assert score_attendance(e, strict).state is not AttendanceState.PRESENT


def test_to_dict_round_trips_shape():
    e = ev(ble_marker=BleMarkerEvidence(detected=True, rssi=-49, duration_s=120))
    d = e.to_dict()
    assert d["classroom"] == "ROOM_204"
    assert d["ble_marker"]["rssi"] == -49
    r = score_attendance(e, W).to_dict()
    assert set(r.keys()) == {"score", "raw_points", "max_possible", "state", "components"}
    assert all({"name", "points", "max_points", "reason"} <= set(c.keys()) for c in r["components"])
