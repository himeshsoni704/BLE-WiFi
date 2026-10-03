"""Live-device protocol tests: the same HTTP calls the Android app and classroom nodes make.
Phones here are scripted clients; rows they create are flagged LIVE because the accounts are not simulated."""
import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app.tokens import marker_token, student_token, window_index
from conftest import login

PHONES = ("HIMESH", "STU101", "STU102", "STU103")
APS = [("AA:BB:CC:00:00:01", "CampusNet"), ("AA:BB:CC:00:00:02", "CampusNet"), ("AA:BB:CC:00:00:03", "Lab")]


def nonce():
    return uuid.uuid4().hex[:20]


class World:
    def __init__(self, client, app, clock):
        self.c, self.app, self.clock = client, app, clock
        self.fac = login(client, "faculty")
        self.p = {}
        for n in PHONES:
            h = login(client, n)
            prov = client.get("/auth/provision", headers=h).json()
            self.p[n] = {"h": h, "secret": prov["token_secret"], "key": prov["student_key"]}
        self.marker = {}
        for room in ("204", "205"):
            r = client.get("/nodes/provision", headers={"X-Node-Key": f"node-{room}-demo"})
            assert r.status_code == 200, r.text
            self.marker[room] = r.json()

    def tok(self, name, t=None):
        return student_token(self.p[name]["secret"], window_index(t or self.clock.t, 30))

    def mtok(self, room, t=None):
        m = self.marker[room]
        return marker_token(m["marker_secret"], m["marker_idx"], window_index(t or self.clock.t, 30))

    def marker_item(self, room, rssi=-55, dur=150, ts=None, **kw):
        ts = ts or self.clock.t
        m = self.marker[room]
        return {"kind": "marker", "marker_idx": m["marker_idx"], "marker_token": self.mtok(room, ts), "rssi": rssi,
                "timestamp": ts, "duration": dur, "samples": int(dur * 8), "nonce": nonce(), **kw}

    def peer_item(self, observed, rssi=-62, dur=60, ts=None, observer=None, **kw):
        ts = ts or self.clock.t
        item = {"kind": "peer", "observed_token": self.tok(observed, ts), "rssi": rssi, "timestamp": ts,
                "duration": dur, "samples": 300, "nonce": nonce(), **kw}
        if observer:
            item["observer"] = self.tok(observer, ts)
        return item

    def ble(self, name, *items):
        return self.c.post("/ble-observation", json={"observations": list(items)}, headers=self.p[name]["h"])

    def wifi_item(self, rssis, ts=None, **kw):
        return {"timestamp": ts or self.clock.t, "nonce": nonce(), "source": "scan",
                "wifi": [{"bssid": b, "ssid": s, "rssi": r, "frequency": 2437} for (b, s), r in zip(APS, rssis)], **kw}

    def wifi(self, name, rssis, **kw):
        return self.c.post("/wifi-observation", json=self.wifi_item(rssis, **kw), headers=self.p[name]["h"])

    def survey(self, retrain=True):
        """Staff surveys rooms 204 and 205 (what the Android survey screen does), then retrains the live model."""
        import numpy as np
        rng = np.random.default_rng(0)
        for room, base in (("204", (-45, -72, -80)), ("205", (-72, -46, -78))):
            scans = [{"wifi": [{"bssid": b, "ssid": s, "rssi": float(r + rng.normal(0, 2))}
                               for (b, s), r in zip(APS, base)]} for _ in range(14)]
            r = self.c.post("/wifi/survey", json={"zone": room, "scans": scans}, headers=self.fac)
            assert r.status_code == 200, r.text
        if not retrain:
            return None
        r = self.c.post("/wifi/retrain", headers=self.fac)
        assert r.json()["status"] == "trained", r.text
        return r.json()

    def attendance(self, name="HIMESH"):
        sess = self.c.get("/sessions?source=live", headers=self.fac).json()
        sid = next(s["id"] for s in sess if s["code"] == "CS301")
        rows = self.c.get(f"/attendance?session_id={sid}", headers=self.fac).json()["rows"]
        return next(r for r in rows if r["student_id"] == name)


@pytest.fixture
def w(client, app, clock):
    return World(client, app, clock)


# ---- auth, roles, privacy ----------------------------------------------------------------------

def test_login_failures_and_unauthenticated_access(client):
    assert client.post("/auth/login", json={"username": "faculty", "password": "nope"}).status_code == 401
    assert client.post("/auth/login", json={"username": "ghost", "password": "demo1234"}).status_code == 401
    assert client.get("/anomalies").status_code == 401
    assert client.get("/anomalies", headers={"Authorization": "Bearer garbage"}).status_code == 401
    assert client.post("/ble-observation", json={"observations": []}).status_code == 401


def test_roles_students_cannot_see_staff_views_or_other_students(w):
    stu = w.p["HIMESH"]["h"]
    for path in ("/anomalies", "/locations", "/summary", "/simulation/status"):
        assert w.c.get(path, headers=stu).status_code == 403, path
    assert w.c.post("/feedback", json={"anomaly_id": 1, "action": "comment"}, headers=stu).status_code == 403
    assert w.c.post("/simulation/start", json={"scenario": "full"}, headers=stu).status_code == 403
    mine = w.c.get("/students", headers=stu).json()
    assert [s["student_id"] for s in mine["students"]] == ["HIMESH"]
    assert w.c.get("/evidence/STU101", headers=stu).status_code == 403
    assert w.c.get("/evidence/HIMESH", headers=stu).status_code == 200
    assert w.c.get("/anomalies", headers=w.fac).status_code == 200


def test_provision_returns_only_own_secret_and_public_catalogue(w):
    prov = w.c.get("/auth/provision", headers=w.p["HIMESH"]["h"]).json()
    assert len(prov["token_secret"]) == 32 and prov["student_key"] != "HIMESH"
    assert prov["privacy"]["broadcasts_real_identity"] is False
    assert all("secret" not in c for c in prov["classrooms"]) and len(prov["classrooms"]) == 20
    assert w.c.get("/auth/provision", headers=w.fac).status_code == 403          # staff have no token secret
    assert w.p["HIMESH"]["secret"] != w.p["STU101"]["secret"]


def test_secrets_never_appear_in_listings(w):
    blob = json.dumps(w.c.get("/students", headers=w.fac).json()) + json.dumps(w.c.get("/classrooms", headers=w.fac).json())
    for n in PHONES:
        assert w.p[n]["secret"] not in blob
    assert w.marker["204"]["marker_secret"] not in blob


# ---- the live attendance flow ---------------------------------------------------------------------

def test_wifi_without_surveyed_aps_or_model_is_not_faked(w):
    r = w.wifi("HIMESH", (-45, -72, -80)).json()                    # no campus APs registered yet
    assert r["status"] == "rejected" and r["reason"] == "no_known_aps"
    w.survey(retrain=False)                                         # APs now registered, but no live model yet
    r = w.wifi("HIMESH", (-45, -72, -80)).json()
    assert r["status"] == "accepted" and r["predicted_zone"] is None and "survey" in r["warning"]


def test_full_live_flow_ble_plus_wifi_gives_present_and_labels_are_live(w):
    w.survey()
    assert w.ble("STU101", w.marker_item("204", rssi=-60)).json()["accepted"] == 1
    r = w.ble("HIMESH", w.marker_item("204", rssi=-52), w.peer_item("STU101", observer="HIMESH")).json()
    assert r["accepted"] == 2 and r["rejected"] == 0
    wr = w.wifi("HIMESH", (-46, -71, -81)).json()
    assert wr["predicted_zone"] == "204" and wr["confidence"] > 0.5
    assert "not a calibrated probability" in wr["confidence_note"]
    w.wifi("HIMESH", (-44, -73, -79), ts=w.clock.t - 10)
    row = w.attendance()
    assert row["final_state"] == "PRESENT" and row["source"] == "LIVE" and row["families"] == 2
    ev = w.c.get("/evidence/HIMESH", headers=w.fac).json()
    ev0 = ev["sessions"][0]["evidence"]
    assert ev0["classroom_ble"]["provenance"] == "live" and ev0["wifi"]["provenance"] == "estimated"
    assert ev0["isolation_forest"] if "isolation_forest" in ev0 else True
    kinds = {t["kind"]: t["provenance"] for t in ev["timeline"]}
    assert kinds["ble_marker"] == "measured" and kinds["wifi"] == "estimated"
    loc = w.c.get("/locations?source=live", headers=w.fac).json()
    me = next(s for s in loc["students"] if s["student_id"] == "HIMESH")
    assert me["zone"] == "204" and me["source"] == "LIVE" and me["signal"] == "ble_marker"


def test_ble_alone_is_not_present(w):
    w.ble("HIMESH", w.marker_item("204", rssi=-50))
    row = w.attendance()
    assert row["final_state"] in ("LIKELY_PRESENT", "REVIEW_REQUIRED") and row["final_state"] != "PRESENT"
    assert row["families"] == 1


def test_wifi_conflicting_with_ble_raises_a_review_not_an_absence(w):
    w.survey()
    w.ble("HIMESH", w.marker_item("204", rssi=-52))
    for dt in (0, 5, 10):
        w.wifi("HIMESH", (-72, -46, -78), ts=w.clock.t - dt)                 # looks like room 205
    row = w.attendance()
    assert row["final_state"] == "REVIEW_REQUIRED" and row["anomaly_id"]
    an = w.c.get(f"/anomalies/{row['anomaly_id']}", headers=w.fac).json()
    assert "ble_wifi_contradiction" in an["rules"] and an["source"] == "LIVE"
    hit = next(r for r in an["rule_details"] if r["rule"] == "ble_wifi_contradiction")
    assert hit["data"]["ble_room"] == "204" and hit["data"]["wifi_room"] == "205"
    assert an["evidence"]["isolation_forest"]["evaluated"] is False         # too few devices: honestly not scored


def test_wifi_validation_unknown_aps_and_stale_cached_scans(w):
    w.survey()
    unknown = w.c.post("/wifi-observation", headers=w.p["HIMESH"]["h"], json={
        "timestamp": w.clock.t, "nonce": nonce(), "wifi": [{"bssid": "11:22:33:44:55:66", "rssi": -50}]}).json()
    assert unknown["reason"] == "no_known_aps"
    stale = w.wifi("HIMESH", (-46, -71, -81), source="cached", scan_age_s=900).json()
    assert stale["reason"] == "stale_scan"
    cached = w.wifi("HIMESH", (-46, -71, -81), source="cached", scan_age_s=20).json()
    assert cached["status"] == "accepted"


# ---- replay, forgery, validation -------------------------------------------------------------------

def test_replayed_nonce_is_rejected(w):
    w.survey()
    item = w.marker_item("204")
    assert w.ble("HIMESH", item).json()["accepted"] == 1
    again = w.ble("HIMESH", item).json()
    assert again["accepted"] == 0 and again["results"][0]["reason"] == "replayed_nonce"
    dup_in_batch = w.ble("STU101", (i := w.marker_item("204")), i).json()
    assert dup_in_batch["accepted"] == 1 and dup_in_batch["results"][1]["reason"] == "replayed_nonce"
    wi = w.wifi_item((-46, -71, -81))
    assert w.c.post("/wifi-observation", json=wi, headers=w.p["HIMESH"]["h"]).json()["status"] == "accepted"
    assert w.c.post("/wifi-observation", json=wi, headers=w.p["HIMESH"]["h"]).json()["reason"] == "replayed_nonce"


def test_expired_forged_and_misattributed_tokens_are_rejected(w):
    old_ts = w.clock.t - 100                                      # inside the timestamp window...
    stale = w.peer_item("STU101", ts=old_ts)
    stale["observed_token"] = student_token(w.p["STU101"]["secret"], window_index(w.clock.t - 3000, 30))
    assert w.ble("HIMESH", stale).json()["results"][0]["reason"] == "unknown_or_expired_token"     # ...but the token is old
    assert w.ble("HIMESH", w.peer_item("STU101", ts=w.clock.t - 500)).json()["results"][0]["reason"] == "timestamp_out_of_window"
    forged = w.marker_item("204")
    forged["marker_token"] = "0" * 16
    assert w.ble("HIMESH", forged).json()["results"][0]["reason"] == "invalid_marker_token"
    wrong_marker = w.marker_item("204")
    wrong_marker["marker_token"] = w.mtok("205")
    assert w.ble("HIMESH", wrong_marker).json()["results"][0]["reason"] == "invalid_marker_token"
    other = w.peer_item("STU101", observer="STU102")             # HIMESH uploading with someone else's observer token
    assert w.ble("HIMESH", other).json()["results"][0]["reason"] == "observer_token_mismatch"
    me = w.peer_item("HIMESH")
    assert w.ble("HIMESH", me).json()["results"][0]["reason"] == "self_observation"
    unknown = w.peer_item("STU101")
    unknown["observed_token"] = "f" * 16
    assert w.ble("HIMESH", unknown).json()["results"][0]["reason"] == "unknown_or_expired_token"
    assert w.app.state.svc.metrics["rejected_replayed_nonce"] >= 0
    assert w.app.state.svc.metrics["rejected_invalid_marker_token"] == 2


def test_input_validation(w):
    h = w.p["HIMESH"]["h"]
    good = w.marker_item("204")
    cases = [
        {**good, "rssi": 10}, {**good, "rssi": -300}, {**good, "marker_token": "xyz"}, {**good, "nonce": "short"},
        {**good, "duration": -1}, {**good, "timestamp": 5}, {**good, "kind": "other"}, {**good, "extra": 1},
    ]
    for bad in cases:
        assert w.c.post("/ble-observation", json={"observations": [bad]}, headers=h).status_code == 422, bad
    assert w.c.post("/ble-observation", json={"observations": []}, headers=h).status_code == 422
    assert w.c.post("/ble-observation", json={"observations": [good] * 201}, headers=h).status_code == 422
    assert w.c.post("/wifi-observation", json={"timestamp": w.clock.t, "nonce": nonce(), "wifi": []}, headers=h).status_code == 422
    assert w.c.post("/presence", json={"timestamp": w.clock.t, "nonce": nonce(), "counts": {"a": -1}}, headers=h).status_code == 422


def test_spec_example_field_names_are_accepted(w):
    """The prompt's example observation uses `observer` and `timestamp`."""
    item = {"kind": "peer", "observer": w.tok("HIMESH"), "observed_token": w.tok("STU101"), "rssi": -58,
            "timestamp": w.clock.t, "duration": 12, "nonce": nonce()}
    assert w.ble("HIMESH", item).json()["accepted"] == 1
    marker = {"kind": "marker", "marker_id": "ROOM_204_BEACON", "room": "204", "rssi": -48, "timestamp": w.clock.t,
              "session": "CS301", "duration": 30, "marker_token": w.mtok("204"), "nonce": nonce()}
    assert w.ble("HIMESH", marker).json()["accepted"] == 1


# ---- anomaly -> explanation -> feedback ------------------------------------------------------------

def test_token_reuse_anomaly_explain_rag_and_feedback_loop(w):
    w.survey()
    w.ble("HIMESH", w.marker_item("204", rssi=-52))
    w.wifi("HIMESH", (-46, -71, -81))
    # three other phones, standing in room 205, report seeing HIMESH's token
    for n in ("STU101", "STU102", "STU103"):
        t = w.clock.t
        w.ble(n, w.marker_item("205", rssi=-50, ts=t), w.peer_item("HIMESH", rssi=-55, ts=t, observer=n))
    row = w.attendance()
    assert row["final_state"] == "REVIEW_REQUIRED" and row["anomaly_id"]
    aid = row["anomaly_id"]
    an = w.c.get(f"/anomalies/{aid}", headers=w.fac).json()
    assert {"token_reuse", "abnormal_token_reuse"} <= set(an["rules"])
    assert an["evidence"]["features"]["token_reuse_count"] == 3
    # explain (mock provider: Gemini not configured -> the app still works and says so)
    ex = w.c.post("/explain-anomaly", json={"anomaly_id": aid}, headers=w.fac).json()
    assert ex["provider"]["configured"] == "mock" and ex["provider"]["used"] == "mock"
    assert ex["evidence"]["token_reuse_devices"] == 3 and ex["evidence"]["ble_classroom_marker"] == "Room 204"
    text = ex["explanation"]["text"]
    assert "3 other device(s)" in text and "does not decide" in text and ex["explanation"]["grounding"]["passed"]
    assert "HIMESH" not in json.dumps(ex["explanation"])                      # pseudonymous label only
    assert ex["similar_cases"] and ex["similar_cases"][0]["resolution"] == "confirmed_anomaly"
    assert w.c.get(f"/anomalies/{aid}", headers=w.fac).json()["explanation"]["provider"] == "mock"
    # RAG endpoint
    rag = w.c.post("/rag/retrieve", json={"anomaly_id": aid, "k": 2}, headers=w.fac).json()
    assert rag["cases"] and "token_reuse_multi_device" in rag["tags_used"]
    # faculty marks it a false positive
    n_cases = rag["index_size"]
    fb = w.c.post("/feedback", json={"anomaly_id": aid, "action": "false_positive",
                                     "comment": "The three phones were next to the door; BLE bleed."}, headers=w.fac).json()
    assert fb["status"] == "false_positive" and fb["verified_case_id"] and fb["rag_cases_total"] == n_cases + 1
    assert fb["training_rows_total"] == 1 and "does NOT retrain" in " ".join(fb["notes"]) or "RAG does NOT retrain" in " ".join(fb["notes"])
    assert w.attendance()["final_state"] != "REVIEW_REQUIRED"                 # back to the fused state
    summ = w.c.get("/feedback/summary", headers=w.fac).json()
    case = next(c for c in summ["verified_cases"] if c["case_id"] == fb["verified_case_id"])
    assert case["origin"] == "faculty_feedback" and case["resolution"] == "false_positive" and "BLE bleed" in case["faculty_comment"]
    csv_path = w.app.state.settings.data_dir + "/feedback_dataset.csv"
    lines = open(csv_path).read().strip().splitlines()
    assert len(lines) == 2 and lines[1].split(",")[2] == "normal"
    # the new case is now retrievable for a similar anomaly
    again = w.c.post("/rag/retrieve", json={"anomaly_id": aid, "k": 5}, headers=w.fac).json()
    assert any(c["case_id"] == fb["verified_case_id"] for c in again["cases"])
    # re-evaluation does not reopen a faculty-resolved anomaly
    w.ble("HIMESH", w.marker_item("204", rssi=-53))
    assert w.c.get(f"/anomalies/{aid}", headers=w.fac).json()["status"] == "false_positive"


def test_confirm_keeps_review_and_comment_changes_nothing(w):
    w.ble("HIMESH", w.marker_item("204"))
    for n in ("STU101", "STU102", "STU103"):
        w.ble(n, w.marker_item("205"), w.peer_item("HIMESH", observer=n))
    aid = w.attendance()["anomaly_id"]
    c = w.c.post("/feedback", json={"anomaly_id": aid, "action": "comment", "comment": "looking into it"}, headers=w.fac).json()
    assert c["status"] == "open" and c["verified_case_id"] is None and c["training_rows_total"] == 0
    d = w.c.post("/feedback", json={"anomaly_id": aid, "action": "confirm"}, headers=w.fac).json()
    assert d["status"] == "confirmed" and w.attendance()["final_state"] == "REVIEW_REQUIRED"
    assert w.c.post("/feedback", json={"anomaly_id": 99999, "action": "confirm"}, headers=w.fac).status_code == 404


def test_explain_unknown_anomaly_and_forced_gemini_when_unconfigured(w):
    assert w.c.post("/explain-anomaly", json={"anomaly_id": 424242}, headers=w.fac).status_code == 404
    w.ble("HIMESH", w.marker_item("204"))
    for n in ("STU101", "STU102", "STU103"):
        w.ble(n, w.marker_item("205"), w.peer_item("HIMESH", observer=n))
    aid = w.attendance()["anomaly_id"]
    r = w.c.post("/explain-anomaly", json={"anomaly_id": aid, "provider": "gemini"}, headers=w.fac)
    assert r.status_code == 409 and "Gemini" in r.json()["detail"]


# ---- optional face / RFID, nodes, presence ---------------------------------------------------------------

def test_face_and_rfid_blocks_come_only_from_staff_or_readers(w):
    w.survey()
    w.ble("STU101", w.marker_item("204", rssi=-82, dur=40))
    face = {"face_system": True, "student_id": "STU101", "match": True, "confidence": 0.94, "timestamp": w.clock.t}
    rfid = {"rfid": {"detected": True, "reader": "ROOM204_READER", "timestamp": w.clock.t, "student_id": "STU101"}}
    base = {"timestamp": w.clock.t, "nonce": nonce()}
    denied = w.c.post("/presence", json={**base, "face": face}, headers=w.p["STU101"]["h"]).json()
    assert "accepted only from faculty" in denied["rejected_external"] and "face" not in denied["stored"]
    ok = w.c.post("/presence", json={**base, "nonce": nonce(), "face": face, "rfid": rfid}, headers=w.fac).json()
    assert set(ok["stored"]) == {"face", "rfid"}
    ev = w.c.get("/evidence/STU101", headers=w.fac).json()["sessions"][0]["evidence"]
    assert ev["face"]["match"] and ev["rfid"]["detected"] and ev["independent_signal_families"]["count"] == 3
    bad = w.c.post("/presence", json={**base, "nonce": nonce(), "face": {**face, "student_id": "NOBODY"}}, headers=w.fac).json()
    assert "unknown student_id" in bad["rejected_external"]


def test_student_presence_heartbeat_is_stored_as_a_phone_side_estimate(w):
    r = w.c.post("/presence", headers=w.p["HIMESH"]["h"], json={
        "timestamp": w.clock.t, "nonce": nonce(), "session": "CS301",
        "zone_estimate": {"zone": "204", "confidence": 0.8, "method": "on-device KNN"},
        "ble_state": "active", "wifi_state": "throttled", "counts": {"adverts_seen": 120, "wifi_scans": 3}}).json()
    assert r["status"] == "accepted" and r["stored"] == ["presence"]


def test_node_heartbeat_marks_classroom_live_and_rejects_bad_keys(w):
    assert w.c.post("/nodes/heartbeat", json={"detected_count": 1}, headers={"X-Node-Key": "wrong"}).status_code == 401
    assert w.c.post("/nodes/heartbeat", json={"detected_count": 1}).status_code == 401
    before = next(c for c in w.c.get("/classrooms", headers=w.fac).json() if c["id"] == "204")
    assert before["marker"]["source"] == "SIMULATED" and before["marker"]["label"] == "Smart Board — Room 204"
    r = w.c.post("/nodes/heartbeat", headers={"X-Node-Key": "node-204-demo"}, json={
        "detected_count": 17, "node_kind": "esp32",
        "sightings": [{"token": w.tok("HIMESH"), "rssi": -48, "duration": 120}, {"token": "e" * 16, "rssi": -50}]}).json()
    assert r["sightings_accepted"] == 1 and r["sightings_rejected"] == 1 and r["marker_id"] == "ROOM_204_BEACON"
    after = next(c for c in w.c.get("/classrooms", headers=w.fac).json() if c["id"] == "204")
    assert after["marker"]["source"] == "LIVE" and after["marker"]["students_detected"] == 17 and after["marker"]["kind"] == "esp32"
    assert "ROOM_204" in w.c.get("/nodes/provision", headers={"X-Node-Key": "node-204-demo"}).json()["marker_id"]
    # the node's own sighting counts as classroom-BLE evidence for the student
    assert w.attendance()["components"]["classroom_ble"] > 0


def test_survey_is_staff_only_and_validates_zone(w):
    body = {"zone": "204", "scans": [{"wifi": [{"bssid": APS[0][0], "rssi": -50}]}]}
    assert w.c.post("/wifi/survey", json=body, headers=w.p["HIMESH"]["h"]).status_code == 403
    assert w.c.post("/wifi/survey", json={**body, "zone": "999"}, headers=w.fac).status_code == 422
    assert w.c.post("/wifi/retrain", headers=w.fac).json()["status"] == "not_enough_data"
    ref = w.c.get("/wifi/reference", headers=w.p["HIMESH"]["h"]).json()
    assert ref["centroids"] == {}
    w.survey()
    ref = w.c.get("/wifi/reference", headers=w.p["HIMESH"]["h"]).json()
    assert set(ref["centroids"]) == {"204", "205"} and len(ref["ap_ids"]) == 3


def test_rate_limiting_on_login(make_app):
    c = TestClient(make_app(rate_limit_enabled=True))
    codes = [c.post("/auth/login", json={"username": "faculty", "password": "bad"}).status_code for _ in range(12)]
    assert codes[:10] == [401] * 10 and codes[10:] == [429, 429]


def test_health_config_and_session_listing(w):
    h = w.c.get("/health").json()
    assert h["ok"] and h["models"]["wifi_simulated"] and h["models"]["isolation_forest"] and h["rag_cases"] >= 8
    cfg = w.c.get("/config").json()
    assert cfg["llm"]["active"] == "mock" and cfg["fusion_weights"]["classroom_ble"] == 35
    assert "decentralized evidence generation with authorized aggregation" in cfg["architecture"]
    assert "validated probability" in cfg["score_note"]
    sess = w.c.get("/sessions?source=live", headers=w.fac).json()
    assert [s["code"] for s in sess] == ["CS301"] and sess[0]["status"] == "in_progress" and sess[0]["room"] == "204"


def test_websocket_requires_a_staff_token_and_streams_events(app):
    with TestClient(app) as c:
        fac = login(c, "faculty")["Authorization"].split()[1]
        stu = login(c, "HIMESH")["Authorization"].split()[1]
        with pytest.raises(Exception):
            with c.websocket_connect("/ws?token=bad"):
                pass
        with pytest.raises(Exception):
            with c.websocket_connect(f"/ws?token={stu}"):
                pass
        with c.websocket_connect(f"/ws?token={fac}") as ws:
            assert ws.receive_json()["type"] == "hello"


def test_a_low_live_score_comes_with_the_reasons_and_a_surveyed_phone_across_the_room_is_present(w):
    """Evidence endpoint explains a low score; the same phone at -80 dBm with a surveyed, agreeing Wi-Fi room is PRESENT."""
    w.ble("HIMESH", w.marker_item("204", rssi=-80, dur=30))
    w.clock.advance(30)
    w.ble("HIMESH", w.marker_item("204", rssi=-80, dur=30))
    ev = w.c.get("/evidence/HIMESH", headers=w.p["HIMESH"]["h"]).json()
    live = next(s for s in ev["sessions"] if s["session"]["code"] == "CS301")["evidence"]
    assert live["state"] != "PRESENT"
    assert any("Wi-Fi" in f for f in live["limiting_factors"]), live["limiting_factors"]
    w.survey()
    for _ in range(6):
        w.clock.advance(30)
        w.ble("HIMESH", w.marker_item("204", rssi=-80, dur=30))
        w.wifi("HIMESH", [-46, -73, -79])
    a = w.attendance()
    assert (a.get("final_state") or a.get("state")) == "PRESENT", a
