import pytest

from app.config import FusionWeights, Thresholds
from app.fusion import (ABSENT, LIKELY_PRESENT, PRESENT, REVIEW_REQUIRED, MarkerObs, PeerObs, StudentInputs,
                        WifiObs, build_evidence)

W, T = FusionWeights(), Thresholds()
START, END = 10_000.0, 13_000.0


def inputs(**kw) -> StudentInputs:
    base = dict(student_key="s1", session_id=1, session_code="CS301", room="204", start_ts=START, end_ts=END,
                now=END + 60)
    base.update(kw)
    return StudentInputs(**base)


def marker(ts=START + 1500, dur=2900, rssi=-55, room="204"):
    return MarkerObs(ts, dur, rssi, room)


def wifi(n=6, zone="204", conf=0.8):
    return [WifiObs(START + 100 + i * 300, zone, conf) for i in range(n)]


def peers(n, rssi=-65):
    return [PeerObs(START + 100 + i * 100, 60, rssi, f"p{i}", "observed") for i in range(n)]


def ev(**kw):
    return build_evidence(inputs(**kw), W, T)


def test_ble_wifi_peers_sustained_is_present_with_score_near_100():
    e = ev(markers=[marker()], wifis=wifi(), peers=peers(3), peers_in_room={"p0", "p1", "p2"})
    assert e["state"] == PRESENT and e["score"] >= 95
    assert e["classroom_ble"]["points"] == 35 and e["wifi"]["points"] == 30 and e["peers"]["points"] == 20
    assert e["independent_signal_families"]["count"] == 2
    assert "not a validated probability" in e["score_note"].lower()


def test_ble_alone_can_never_be_present():
    e = ev(markers=[marker()])
    assert e["state"] != PRESENT and e["score"] <= 45 + 1e-6
    # even with peers (also BLE) it cannot reach PRESENT
    e2 = ev(markers=[marker()], peers=peers(3), peers_in_room={"p0", "p1", "p2"})
    assert e2["state"] == LIKELY_PRESENT and e2["independent_signal_families"]["count"] == 1


def test_high_score_but_single_family_is_capped():
    w = FusionWeights(classroom_ble=70, wifi_match=0, peer_consistency=20, sustained_presence=10)
    e = build_evidence(inputs(markers=[marker()], peers=peers(3), peers_in_room={"p0", "p1", "p2"}), w, T)
    assert e["score"] >= 70 and e["state"] == LIKELY_PRESENT and e["state_capped_for_single_family"]


def test_wifi_only_is_not_present():
    e = ev(wifis=wifi())
    assert e["state"] in (REVIEW_REQUIRED, ABSENT) and not e["classroom_ble"]["detected"]


def test_peers_do_not_count_without_an_own_signal():
    e = ev(peers=peers(3), peers_in_room={"p0", "p1", "p2"})
    assert e["peers"]["points"] == 0 and e["peers"]["gated_by_own_signal"]
    assert e["state"] == ABSENT


def test_peers_not_in_the_room_are_inconsistent():
    e = ev(markers=[marker()], peers=peers(3), peers_in_room=set())
    assert e["peers"]["observed_distinct"] == 3 and e["peers"]["consistent_distinct"] == 0


def test_weak_marker_rssi_gets_partial_credit_and_below_floor_none():
    strong, weak, floor = ev(markers=[marker(rssi=-60)]), ev(markers=[marker(rssi=-82)]), ev(markers=[marker(rssi=-95)])
    assert strong["classroom_ble"]["points"] == 35
    assert 17 < weak["classroom_ble"]["points"] < 35
    assert floor["classroom_ble"]["points"] == 0 and not floor["classroom_ble"]["detected"]


def test_wifi_mismatch_and_low_confidence():
    wrong = ev(markers=[marker()], wifis=wifi(zone="205"))
    assert wrong["wifi"]["predicted_zone"] == "205" and wrong["wifi"]["points"] == 0
    low = ev(markers=[marker()], wifis=wifi(conf=0.1))              # below wifi_conf_min: ignored, not "mismatch"
    assert not low["wifi"]["available"]
    half = ev(markers=[marker()], wifis=wifi(conf=0.25))
    assert 0 < half["wifi"]["points"] < 30


def test_sustained_presence_scales_with_coverage():
    short = ev(markers=[marker(ts=START + 400, dur=300)])
    full = ev(markers=[marker()])
    assert short["sustained"]["points"] < full["sustained"]["points"] == 10


def test_optional_face_and_rfid_add_points_but_core_works_without():
    base = ev(markers=[marker()], wifis=wifi())
    both = ev(markers=[marker()], wifis=wifi(), face={"match": True, "confidence": 0.94}, rfid={"detected": True})
    assert both["score"] == min(100, base["score"] + 10)
    weak_face = ev(markers=[marker()], wifis=wifi(), face={"match": True, "confidence": 0.3})
    assert weak_face["face"]["points"] == 0
    assert base["state"] == PRESENT


def test_face_plus_rfid_are_independent_families():
    e = ev(markers=[marker(rssi=-80, dur=600)], face={"match": True, "confidence": 0.95}, rfid={"detected": True})
    assert e["independent_signal_families"]["count"] == 3


def test_no_evidence_is_absent():
    e = ev()
    assert e["state"] == ABSENT and e["score"] == 0 and not e["has_evidence"]


def test_weights_are_configurable():
    w = FusionWeights(classroom_ble=10, wifi_match=10, peer_consistency=10, sustained_presence=10)
    e = build_evidence(inputs(markers=[marker()], wifis=wifi()), w, T)
    assert e["classroom_ble"]["points"] == 10 and e["score"] == pytest.approx(30)


def test_marker_from_another_room_does_not_count():
    e = ev(markers=[marker(room="205")])
    assert not e["classroom_ble"]["detected"] and e["classroom_ble"]["other_rooms_detected"] == ["205"]


def test_provenance_labels():
    live = ev(markers=[marker()], wifis=wifi())
    sim = ev(markers=[marker()], wifis=wifi(), is_simulated=True)
    assert live["classroom_ble"]["provenance"] == "live" and live["wifi"]["provenance"] == "estimated"
    assert sim["classroom_ble"]["provenance"] == "simulated" and sim["wifi"]["provenance"] == "simulated"


# ---- a phone that is in the room but not at its best must still be able to reach PRESENT --------------------------------

def mixed_wifi(n_room, n_next, conf=0.8):
    return [WifiObs(START + 100 + i * 100, "204" if i < n_room else "205", conf) for i in range(n_room + n_next)]


def test_a_phone_across_the_room_with_a_good_wifi_match_is_present_without_peers():
    """Two-phone demo: no peers. Weak-but-valid Bluetooth (-80 dBm) plus Wi-Fi that agrees must be enough."""
    e = ev(markers=[marker(rssi=-80)], wifis=wifi())
    assert e["classroom_ble"]["points"] == 35 and e["state"] == PRESENT and e["limiting_factors"] == []


def test_a_few_next_room_wifi_scans_do_not_cost_the_whole_state():
    """Single scans are noisy and most errors are the adjacent room. 4 of 5 scans agreeing is a clear majority."""
    e = ev(markers=[marker(rssi=-65)], wifis=mixed_wifi(4, 1))
    assert e["wifi"]["points"] == 30 and e["state"] == PRESENT
    split = ev(markers=[marker(rssi=-65)], wifis=mixed_wifi(3, 2))              # 60%: partial credit, honest about it
    assert 0 < split["wifi"]["points"] < 30
    assert any("60%" in f for f in split["limiting_factors"])


def test_the_adjacent_room_is_still_not_present():
    """A phone next door hears the marker faintly and Wi-Fi says it is in the other room: never PRESENT."""
    e = ev(markers=[marker(rssi=-83)], wifis=wifi(zone="205"))
    assert e["state"] in (REVIEW_REQUIRED, LIKELY_PRESENT) and e["wifi"]["points"] == 0
    assert e["independent_signal_families"]["wifi"] is False
    # and a phone that only hears the neighbouring marker, with no room-204 signal at all, gets nothing for this session
    away = ev(markers=[marker(rssi=-60, room="205")], wifis=wifi(zone="205"))
    assert away["classroom_ble"]["points"] == 0 and away["state"] != PRESENT
    assert any("205" in f for f in away["limiting_factors"])


def test_limiting_factors_say_why_the_score_is_below_present():
    e = ev(markers=[marker(rssi=-84)])
    texts = " | ".join(e["limiting_factors"])
    assert "weak" in texts and "No usable Wi-Fi" in texts and "two independent signal families" in texts
    assert ev(markers=[marker()], wifis=wifi(), peers=peers(3), peers_in_room={"p0", "p1", "p2"})["limiting_factors"] == []
    early = ev(markers=[marker(ts=START + 400, dur=300)], wifis=wifi())
    assert any("sustained" in f for f in early["limiting_factors"])
