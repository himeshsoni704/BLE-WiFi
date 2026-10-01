"""End-to-end HTTP tests for the Proof-of-Presence endpoints, following
the brief's own live-demo flow (section 30) as closely as practical: a
student enters a room, BLE+Wi-Fi evidence accumulates, a peer phone sees
them, an anomaly gets injected and explained, and faculty feedback creates
a verified case RAG can retrieve later."""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.tokens import token_for, window_index

T0 = 1_800_000_000.0


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def world(tmp_path):
    clock = Clock()
    settings = Settings(db_path=":memory:", max_clock_skew_s=0, model_dir=str(tmp_path / "models"))
    app = create_app(settings, clock)
    client = TestClient(app)
    return client, clock


def _enroll(client, student_id="STU102", name="Himesh"):
    r = client.post("/students", json={"student_id": student_id, "name": name})
    assert r.status_code == 201, r.text
    return r.json()["secret"]


def _classroom(client, classroom_id="ROOM_204", x=0.0, y=0.0):
    r = client.put(f"/classrooms/{classroom_id}", json={
        "name": classroom_id.replace("ROOM_", "Room "), "building": "Block A", "floor": 1,
        "x": x, "y": y, "ble_marker_id": f"{classroom_id}_BEACON",
    })
    assert r.status_code == 200, r.text
    return r.json()


def test_full_demo_flow_presence_to_feedback(world):
    client, clock = world
    secret = _enroll(client)
    _classroom(client, "ROOM_204", 0.0, 0.0)
    _classroom(client, "ROOM_205", 500.0, 0.0)

    r = client.put("/sessions/CS301_1", json={
        "course": "CS301", "classroom_id": "ROOM_204", "start_ts": clock.t - 600, "end_ts": clock.t + 3000,
    })
    assert r.status_code == 200, r.text

    tok = token_for(secret, window_index(clock.t, 30))

    # Step 2: student's phone detects the classroom BLE marker.
    r = client.post("/ble-observation", json={
        "marker_id": "ROOM_204_BEACON", "token": tok, "rssi": -48, "ts": clock.t, "session": "CS301",
    })
    assert r.status_code == 200, r.text

    # presence update: scores attendance from what's been observed so far.
    r = client.post("/presence", json={
        "token": tok, "session_id": "CS301_1", "classroom_id": "ROOM_204", "ts": clock.t,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["student_id"] == "STU102"
    assert body["evidence"]["score"] > 0
    assert body["anomaly"]["is_anomalous"] is False

    # Step 3: a second phone (peer) sees this student's token nearby.
    secret_b = _enroll(client, "STU103", "Priya")
    tok_b = token_for(secret_b, window_index(clock.t, 30))
    r = client.post("/peer-observation", json={
        "observer_token": tok_b, "observed_token": tok, "rssi": -58, "duration": 12, "ts": clock.t,
    })
    assert r.status_code == 200, r.text
    assert r.json()["observed_student_known"] is True

    # GET /evidence/{id} and /locations both reflect the live update.
    r = client.get("/evidence/STU102")
    assert r.status_code == 200
    assert r.json()["latest"]["state"] in ("present", "likely_present", "review_required")

    r = client.get("/locations")
    assert r.status_code == 200
    locs = r.json()
    assert locs["live_devices"] >= 1
    assert any(s["student_id"] == "STU102" and s["source"] == "live" for s in locs["students"])

    # Step 4: inject a suspicious token-reuse event for this same student.
    r = client.post("/simulation/inject", json={"kind": "proxy_attendance", "student_id": "STU102"})
    assert r.status_code == 200, r.text
    inject = r.json()
    assert inject["is_anomalous"] is True
    anomaly_id = inject["anomaly_id"]

    r = client.get("/anomalies")
    assert r.status_code == 200
    assert any(a["id"] == anomaly_id for a in r.json())

    # Step 5: "Explain with AI" -- mock provider, grounded in the stored evidence.
    r = client.post("/explain-anomaly", json={"anomaly_id": anomaly_id})
    assert r.status_code == 200, r.text
    explanation = r.json()["explanation"]
    assert len(explanation) > 0
    assert "cheated" not in explanation.lower()

    # Step 6: faculty marks it a false positive with a comment.
    r = client.post("/feedback", json={
        "anomaly_id": anomaly_id, "decision": "false_positive", "comment": "Wi-Fi AP was temporarily unstable",
    })
    assert r.status_code == 200, r.text

    # Step 7: that feedback is now a verified case RAG can retrieve.
    r = client.post("/rag/retrieve", json={"query": "temporary token observed multiple devices", "k": 1})
    assert r.status_code == 200, r.text
    results = r.json()["results"]
    assert results and "unstable" in results[0]["resolution"]


def test_presence_rejects_unknown_token(world):
    client, clock = world
    _classroom(client, "ROOM_204")
    r = client.post("/presence", json={
        "token": "0" * 16, "session_id": "s1", "classroom_id": "ROOM_204", "ts": clock.t,
    })
    assert r.status_code == 401


def test_presence_rejects_unknown_classroom(world):
    client, clock = world
    secret = _enroll(client)
    tok = token_for(secret, window_index(clock.t, 30))
    r = client.post("/presence", json={
        "token": tok, "session_id": "s1", "classroom_id": "ROOM_DOES_NOT_EXIST", "ts": clock.t,
    })
    assert r.status_code == 404


def test_ble_observation_unknown_marker_returns_404(world):
    client, clock = world
    secret = _enroll(client)
    tok = token_for(secret, window_index(clock.t, 30))
    r = client.post("/ble-observation", json={"marker_id": "NO_SUCH_MARKER", "token": tok, "rssi": -50})
    assert r.status_code == 404


def test_wifi_observation_returns_prediction_once_model_available(world):
    client, clock = world
    secret = _enroll(client)
    tok = token_for(secret, window_index(clock.t, 30))
    r = client.post("/wifi-observation", json={"token": tok, "fingerprint": {"AP1": -42}})
    assert r.status_code == 200
    body = r.json()
    assert body["model_available"] is False   # no trained model loaded in this test's model_dir
    assert body["predicted_zone"] is None


def test_feedback_unknown_anomaly_returns_404(world):
    client, clock = world
    r = client.post("/feedback", json={"anomaly_id": 999, "decision": "false_positive"})
    assert r.status_code == 404


def test_explain_unknown_anomaly_returns_404(world):
    client, clock = world
    r = client.post("/explain-anomaly", json={"anomaly_id": 999})
    assert r.status_code == 404


def test_simulation_start_and_locations_show_simulated_badge(world):
    client, clock = world
    r = client.post("/simulation/start", json={"students": 30, "classrooms": 6, "aps": 3, "seed": 2})
    assert r.status_code == 200, r.text
    summary = r.json()
    assert summary["students"] == 30
    assert summary["classrooms"] == 6

    r = client.get("/locations")
    assert r.status_code == 200
    locs = r.json()
    assert locs["simulated_devices"] > 0
    assert locs["live_devices"] == 0
    assert all(s["source"] == "simulated" for s in locs["students"])


def test_simulation_inject_unknown_student_returns_422(world):
    client, clock = world
    r = client.post("/simulation/inject", json={"kind": "short_presence", "student_id": "NOBODY"})
    assert r.status_code == 422


def test_simulation_reset_clears_everything(world):
    client, clock = world
    client.post("/simulation/start", json={"students": 10, "classrooms": 3, "aps": 2, "seed": 1})
    assert len(client.get("/students").json()) == 10

    r = client.post("/simulation/reset")
    assert r.status_code == 200

    assert client.get("/students").json() == []
    assert client.get("/classrooms").json() == []
    assert client.get("/locations").json()["students"] == []


def test_classrooms_and_sessions_round_trip(world):
    client, clock = world
    _classroom(client, "ROOM_204")
    r = client.get("/classrooms")
    assert r.status_code == 200
    assert any(c["classroom_id"] == "ROOM_204" for c in r.json())

    r = client.put("/sessions/CS301_1", json={
        "course": "CS301", "classroom_id": "ROOM_204", "start_ts": clock.t, "end_ts": clock.t + 3600,
    })
    assert r.status_code == 200
    r = client.get("/sessions")
    assert any(s["session_id"] == "CS301_1" for s in r.json())


def test_put_session_unknown_classroom_returns_404(world):
    client, clock = world
    r = client.put("/sessions/X", json={
        "course": "CS301", "classroom_id": "ROOM_NOPE", "start_ts": clock.t, "end_ts": clock.t + 3600,
    })
    assert r.status_code == 404


def test_face_scan_and_rfid_endpoints(world):
    client, clock = world
    secret = _enroll(client)
    r = client.post("/face-scan", json={"student_id": "STU102", "match": True, "confidence": 0.94})
    assert r.status_code == 200

    tok = token_for(secret, window_index(clock.t, 30))
    r = client.post("/rfid", json={"reader": "ROOM204_READER", "token": tok})
    assert r.status_code == 200
    assert r.json()["student_id"] == "STU102"
