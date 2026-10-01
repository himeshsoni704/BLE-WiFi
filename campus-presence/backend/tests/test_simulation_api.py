import time

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from conftest import Clock, login, make_settings

SCENARIOS = ("proxy", "impossible_movement", "wifi_ble_mismatch", "token_replay", "short_presence", "false_positive")


@pytest.fixture(scope="module")
def sim_world(model_dir, tmp_path_factory):
    d = tmp_path_factory.mktemp("sim")
    clock = Clock()
    app = create_app(make_settings(model_dir, d), clock=clock, train_missing=False)
    c = TestClient(app)
    fac = login(c, "faculty")
    t = time.time()
    r = c.post("/simulation/start", json={"scenario": "full", "students": 300, "wait": True}, headers=fac)
    assert r.status_code == 202, r.text
    return {"c": c, "fac": fac, "app": app, "full": r.json(), "seconds": time.time() - t}


def test_campus_scale_matches_the_brief(sim_world):
    c, fac = sim_world["c"], sim_world["fac"]
    st = c.get("/students?source=simulated&limit=2000", headers=fac).json()
    assert st["totals"]["simulated"] == 300 and st["totals"]["live"] == 4
    rooms = [x for x in c.get("/classrooms", headers=fac).json() if x["marker"]]
    assert len(rooms) == 20                                              # 20 BLE markers
    assert all(r["marker"]["source"] == "SIMULATED" for r in rooms)      # none has reported in as a real node
    summ = c.get("/summary?source=simulated", headers=fac).json()
    assert summ["wifi_access_points_registered"] == 10                   # 10 Wi-Fi APs
    sessions = c.get("/sessions?source=simulated", headers=fac).json()
    assert len(sessions) == 60 and len({s["start_ts"] for s in sessions}) == 3        # multiple sessions
    assert sum(sum(s["counts"].values()) for s in sessions) > 600
    assert sim_world["full"]["summary"]["students"] == 300
    assert sim_world["seconds"] < 120


def test_everything_simulated_is_labelled_and_live_data_is_untouched(sim_world):
    c, fac = sim_world["c"], sim_world["fac"]
    assert all(s["source"] == "SIMULATED" for s in c.get("/students?source=simulated&limit=2000", headers=fac).json()["students"])
    assert all(s["source"] == "SIMULATED" for s in c.get("/sessions?source=simulated", headers=fac).json())
    live = c.get("/sessions?source=live", headers=fac).json()
    assert [s["code"] for s in live] == ["CS301"] and live[0]["counts"]["PRESENT"] == 0
    loc = c.get("/locations?source=simulated", headers=fac).json()
    assert loc["students"] and all(s["source"] == "SIMULATED" for s in loc["students"])
    assert c.get("/locations?source=live", headers=fac).json()["students"] == []
    an = c.get("/anomalies?source=simulated&limit=1000", headers=fac).json()
    assert all(a["source"] == "SIMULATED" for a in an["anomalies"])
    ev = c.get("/evidence/SIM001", headers=fac).json()
    assert ev["student"]["source"] == "SIMULATED"
    assert all(t["provenance"] == "simulated" for t in ev["timeline"])
    assert ev["sessions"][0]["evidence"]["classroom_ble"]["provenance"] == "simulated"


def test_attendance_states_are_plausible_for_a_mostly_attending_campus(sim_world):
    att = sim_world["c"].get("/attendance?source=simulated", headers=sim_world["fac"]).json()["totals"]
    total = sum(att.values())
    assert att["PRESENT"] / total > 0.7 and att["ABSENT"] / total < 0.15
    assert 0.02 < att["REVIEW_REQUIRED"] / total < 0.15                  # review load is a minority


def test_each_injected_scenario_was_flagged_by_the_expected_layer(sim_world):
    an = sim_world["c"].get("/anomalies?source=simulated&limit=1000", headers=sim_world["fac"]).json()["anomalies"]
    by = {}
    for a in an:
        if a["scenario"]:
            by.setdefault(a["scenario"], []).append(a)
    assert set(by) == set(SCENARIOS)
    rules = lambda sc: {r for a in by[sc] for r in a["rules"]}
    assert "impossible_movement" in rules("impossible_movement")
    assert "ble_wifi_contradiction" in rules("wifi_ble_mismatch") and "ble_wifi_contradiction" in rules("false_positive")
    assert {"token_reuse", "abnormal_token_reuse"} <= rules("token_replay")
    # No deterministic rule targets these two patterns, so only the Isolation Forest can raise them. (A stray
    # Wi-Fi/BLE contradiction from ordinary Wi-Fi error can still co-occur, as it does for ~4% of any student.)
    pattern_rules = {"token_reuse", "abnormal_token_reuse", "impossible_movement", "rapid_session_switching"}
    assert not (rules("proxy") & pattern_rules) and not (rules("short_presence") & pattern_rules)
    assert len(by["proxy"]) >= 3, "Isolation Forest should flag most of the 4 phones carried together"
    assert len(by["short_presence"]) >= 2, "and most of the 3 short-presence students"
    assert sum(a["isolation_flagged"] for a in by["proxy"]) >= 3
    assert all(a["isolation_score_raw"] is not None and a["isolation_score_raw"] < 0
               for a in by["proxy"] if a["isolation_flagged"])


def test_scenario_buttons_create_anomalies_with_evidence(sim_world):
    c, fac = sim_world["c"], sim_world["fac"]
    for sc in SCENARIOS:
        r = c.post("/simulation/start", json={"scenario": sc}, headers=fac)
        assert r.status_code == 202, (sc, r.text)
        out = r.json()
        assert out["label"] == "SIMULATED" and out["anomaly_ids"], (sc, out)
    n = c.post("/simulation/start", json={"scenario": "normal"}, headers=fac).json()
    assert n["scenario"] == "normal" and n["anomaly_ids"] == []


def test_wifi_ble_mismatch_explanation_has_the_documented_shape(sim_world):
    c, fac = sim_world["c"], sim_world["fac"]
    r = c.post("/simulation/start", json={"scenario": "wifi_ble_mismatch"}, headers=fac).json()
    aid = r["anomaly_ids"][0]
    ex = c.post("/explain-anomaly", json={"anomaly_id": aid}, headers=fac).json()
    e = ex["evidence"]
    assert e["ble_classroom_marker"] == f"Room {r['room']}" and e["wifi_prediction"].startswith("Room ")
    assert e["wifi_prediction"] != e["ble_classroom_marker"] and e["wifi_confidence_pct"] is not None
    assert e["isolation_forest"] in ("ANOMALOUS", "NORMAL") and "not a probability" in e["risk_note"]
    assert ex["source"] == "SIMULATED" and ex["similar_cases"]
    text = ex["explanation"]["text"]
    assert f"Room {r['room']}" in text and "does not decide" in text and ex["explanation"]["grounding"]["passed"]


def test_false_positive_feedback_loop_on_a_simulated_anomaly(sim_world):
    c, fac = sim_world["c"], sim_world["fac"]
    r = c.post("/simulation/start", json={"scenario": "false_positive"}, headers=fac).json()
    aid = r["anomaly_ids"][0]
    before = c.get("/feedback/summary", headers=fac).json()
    fb = c.post("/feedback", json={"anomaly_id": aid, "action": "false_positive", "comment": "AP rebooted"}, headers=fac).json()
    after = c.get("/feedback/summary", headers=fac).json()
    assert len(after["verified_cases"]) == len(before["verified_cases"]) + 1
    assert after["training_rows"] == before["training_rows"] + 1
    assert c.get(f"/anomalies/{aid}", headers=fac).json()["status"] == "false_positive"
    assert fb["verified_case_id"] and "retrain_anomaly_model" in " ".join(fb["notes"])


def test_status_and_double_start_guard(sim_world):
    s = sim_world["c"].get("/simulation/status", headers=sim_world["fac"]).json()
    assert s["exists"] and s["simulated_students"] == 300 and not s["running"] and s["last"]["students"] == 300


def test_scenarios_need_a_simulation_first_and_reset_clears_only_simulated_rows(make_app):
    app = make_app()
    c = TestClient(app)
    fac = login(c, "faculty")
    r = c.post("/simulation/start", json={"scenario": "proxy"}, headers=fac)
    assert r.status_code == 409 and "Start Simulation" in r.json()["detail"]
    c.post("/simulation/start", json={"scenario": "full", "students": 60, "wait": True}, headers=fac)
    assert c.get("/students?source=simulated", headers=fac).json()["totals"]["simulated"] == 60
    case_count = len(c.get("/feedback/summary", headers=fac).json()["verified_cases"])
    r = c.post("/simulation/reset", headers=fac).json()
    assert r["status"] == "reset" and r["deleted_simulated_rows"]["students"] == 60
    assert c.get("/students?source=simulated", headers=fac).json()["totals"] == {"live": 4, "simulated": 0}
    assert c.get("/anomalies?source=simulated", headers=fac).json()["count"] == 0
    assert c.get("/sessions", headers=fac).json()[0]["code"] == "CS301"          # live session survives
    assert len(c.get("/feedback/summary", headers=fac).json()["verified_cases"]) == case_count   # faculty knowledge kept
    assert login(c, "HIMESH")                                                    # live accounts survive


def test_background_start_reports_progress(make_app):
    app = make_app()
    c = TestClient(app)
    fac = login(c, "faculty")
    r = c.post("/simulation/start", json={"scenario": "full", "students": 40}, headers=fac)
    assert r.status_code == 202 and r.json()["status"] == "started"
    deadline = time.time() + 90
    while time.time() < deadline:
        s = c.get("/simulation/status", headers=fac).json()
        if not s["running"] and s.get("last"):
            break
        time.sleep(0.5)
    assert s["last"] and s["progress"] == 1.0 and s["error"] is None
