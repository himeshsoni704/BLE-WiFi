import pytest

from app.anomaly_rules import (
    ObserverSighting, RuleConfig, ZoneSighting, check_ble_wifi_contradiction,
    check_impossible_movement, check_rapid_session_switching, check_token_reuse, run_all_rules,
)

POS = {"ROOM_204": (0.0, 0.0), "ROOM_305": (250.0, 0.0), "ROOM_206": (20.0, 0.0), "ROOM_207": (40.0, 0.0)}
CFG = RuleConfig(max_speed_mps=2.5, reuse_window_s=20.0, reuse_min_distance_m=15.0,
                 session_switch_window_s=300.0, session_switch_min_rooms=3)


def test_no_contradiction_when_zones_agree():
    assert check_ble_wifi_contradiction("ROOM_204", "ROOM_204") is None


def test_no_contradiction_when_either_signal_missing():
    assert check_ble_wifi_contradiction("ROOM_204", None) is None
    assert check_ble_wifi_contradiction(None, "ROOM_204") is None


def test_contradiction_flagged_when_zones_disagree():
    flag = check_ble_wifi_contradiction("ROOM_204", "ROOM_205")
    assert flag is not None
    assert flag.rule == "ble_wifi_contradiction"
    assert "ROOM_204" in flag.reason and "ROOM_205" in flag.reason


def test_brief_worked_example_is_impossible_movement():
    # brief section 15: 10:03 -> Room 204, 10:04 -> Room 305, distance 250m, 60s => 4.17 m/s
    sightings = [ZoneSighting(0.0, "ROOM_204", "ble_marker"), ZoneSighting(60.0, "ROOM_305", "ble_marker")]
    flags = check_impossible_movement(sightings, POS, CFG)
    assert len(flags) == 1
    assert flags[0].rule == "impossible_movement"
    assert flags[0].details["speed_mps"] == pytest.approx(250 / 60, rel=1e-3)


def test_realistic_walk_is_not_flagged():
    # Room 204 -> Room 206 is 20m; over 30s that's ~0.67 m/s, well under the limit.
    sightings = [ZoneSighting(0.0, "ROOM_204", "ble_marker"), ZoneSighting(30.0, "ROOM_206", "ble_marker")]
    assert check_impossible_movement(sightings, POS, CFG) == []


def test_staying_in_one_zone_is_never_flagged():
    sightings = [ZoneSighting(t, "ROOM_204", "ble_marker") for t in (0, 5, 10, 15)]
    assert check_impossible_movement(sightings, POS, CFG) == []


def test_unknown_classroom_position_is_skipped_not_crashed():
    sightings = [ZoneSighting(0.0, "ROOM_204", "ble_marker"), ZoneSighting(1.0, "ROOM_UNKNOWN", "ble_marker")]
    assert check_impossible_movement(sightings, POS, CFG) == []


def test_rapid_session_switching_flagged():
    sightings = [
        ZoneSighting(0.0, "ROOM_204", "ble_marker"),
        ZoneSighting(60.0, "ROOM_206", "ble_marker"),
        ZoneSighting(120.0, "ROOM_207", "ble_marker"),
    ]
    flag = check_rapid_session_switching(sightings, CFG)
    assert flag is not None
    assert flag.rule == "rapid_session_switching"
    assert set(flag.details["rooms"]) == {"ROOM_204", "ROOM_206", "ROOM_207"}


def test_two_rooms_not_enough_for_session_switching():
    sightings = [ZoneSighting(0.0, "ROOM_204", "ble_marker"), ZoneSighting(60.0, "ROOM_206", "ble_marker")]
    assert check_rapid_session_switching(sightings, CFG) is None


def test_three_rooms_outside_window_not_flagged():
    sightings = [
        ZoneSighting(0.0, "ROOM_204", "ble_marker"),
        ZoneSighting(400.0, "ROOM_206", "ble_marker"),   # outside the 300s window from t=0
        ZoneSighting(800.0, "ROOM_207", "ble_marker"),   # outside the window from t=400 too
    ]
    assert check_rapid_session_switching(sightings, CFG) is None


def test_token_reuse_flagged_for_distant_simultaneous_observers():
    sightings = [ObserverSighting("scan-204", 0.0, 0.0, 0.0), ObserverSighting("scan-305", 5.0, 250.0, 0.0)]
    flag = check_token_reuse(sightings, CFG)
    assert flag is not None
    assert flag.rule == "token_reuse"
    assert set(flag.details["observers"]) == {"scan-204", "scan-305"}


def test_token_reuse_not_flagged_for_nearby_peers():
    # a crowd of peers near each other all seeing the same broadcast is normal, not reuse.
    sightings = [ObserverSighting(f"peer-{i}", float(i), float(i), 0.0) for i in range(5)]
    assert check_token_reuse(sightings, CFG) is None


def test_token_reuse_not_flagged_outside_time_window():
    sightings = [ObserverSighting("scan-204", 0.0, 0.0, 0.0), ObserverSighting("scan-305", 100.0, 250.0, 0.0)]
    assert check_token_reuse(sightings, CFG) is None


def test_token_reuse_severity_scales_with_observer_count():
    few = [ObserverSighting("a", 0, 0, 0), ObserverSighting("b", 1, 250, 0)]
    many = [ObserverSighting(n, float(i), 250.0 * i, 0.0) for i, n in enumerate(["a", "b", "c", "d"])]
    assert check_token_reuse(few, CFG).severity == "medium"
    assert check_token_reuse(many, CFG).severity == "high"


def test_run_all_rules_aggregates_every_flag():
    zone_sightings = [
        ZoneSighting(0.0, "ROOM_204", "ble_marker"),
        ZoneSighting(60.0, "ROOM_305", "ble_marker"),   # impossible movement
    ]
    observer_sightings = [ObserverSighting("scan-204", 0.0, 0.0, 0.0), ObserverSighting("scan-305", 5.0, 250.0, 0.0)]
    flags = run_all_rules("ROOM_204", "ROOM_305", zone_sightings, observer_sightings, POS, CFG)
    rules = {f.rule for f in flags}
    assert "ble_wifi_contradiction" in rules
    assert "impossible_movement" in rules
    assert "token_reuse" in rules


def test_run_all_rules_returns_empty_for_clean_evidence():
    zone_sightings = [ZoneSighting(0.0, "ROOM_204", "ble_marker")]
    flags = run_all_rules("ROOM_204", "ROOM_204", zone_sightings, [], POS, CFG)
    assert flags == []
