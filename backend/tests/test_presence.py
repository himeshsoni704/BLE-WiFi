import asyncio

import pytest

from app.config import Settings
from app.engine import Engine
from app.llm import MockLLMProvider
from app.owner_model import ModelRegistry
from app.presence import PresenceOrchestrator, UnknownAnomaly
from app.store import ClassroomRow, Store
from app.tokens import token_for, window_index
from app.wifi_knn import WifiFingerprintModel

T0 = 1_800_000_000.0
ROOM_204, ROOM_205 = "ROOM_204", "ROOM_205"


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


def _setup(tmp_path, clock):
    store = Store()
    engine = Engine(store, ModelRegistry(tmp_path / "models"), Settings(max_clock_skew_s=0), clock)
    orch = PresenceOrchestrator(engine, MockLLMProvider())
    store.upsert_classroom(ClassroomRow(ROOM_204, "Room 204", "Block A", 1, 0.0, 0.0, "ROOM_204_BEACON", None))
    store.upsert_classroom(ClassroomRow(ROOM_205, "Room 205", "Block A", 1, 300.0, 0.0, "ROOM_205_BEACON", None))
    store.upsert_scanner("ROOM_204_BEACON", ROOM_204, None)
    store.upsert_scanner("ROOM_205_BEACON", ROOM_205, None)
    secret = engine.enroll_student("STU102", "Himesh")
    return engine, orch, secret


def _scan(engine, secret, scanner_id, clock, rssi=-50, n=5, back_s=5):
    for i in range(n):
        ts = clock.t - i * back_s
        tok = token_for(secret, window_index(ts, engine.settings.token_window_s))
        engine.ingest_scans(scanner_id, [(tok, rssi, ts)])


def test_process_presence_detects_ble_marker_and_scores_present(tmp_path):
    clock = Clock()
    engine, orch, secret = _setup(tmp_path, clock)
    _scan(engine, secret, "ROOM_204_BEACON", clock)

    evidence, anomaly = orch.process_presence("STU102", "CS301_1", ROOM_204, clock.t)
    assert evidence.score > 0
    ble = next(c for c in evidence.components if c.name == "ble_marker")
    assert ble.points > 0
    assert anomaly.is_anomalous is False


def test_no_evidence_at_all_scores_absent(tmp_path):
    clock = Clock()
    engine, orch, secret = _setup(tmp_path, clock)
    evidence, anomaly = orch.process_presence("STU102", "CS301_1", ROOM_204, clock.t)
    assert evidence.score == 0.0
    assert anomaly.is_anomalous is False   # no evidence at all is ABSENT, not itself an anomaly


def test_wifi_fingerprint_contradicting_ble_triggers_anomaly(tmp_path):
    clock = Clock()
    engine, orch, secret = _setup(tmp_path, clock)
    _scan(engine, secret, "ROOM_204_BEACON", clock)

    fps = [{"AP1": -40}, {"AP1": -40}, {"AP1": -80}, {"AP1": -80}]
    zones = [ROOM_204, ROOM_204, ROOM_205, ROOM_205]
    orch.wifi_model = WifiFingerprintModel(n_neighbors=1).fit(fps, zones)

    evidence, anomaly = orch.process_presence("STU102", "CS301_1", ROOM_204, clock.t,
                                               wifi_fingerprint={"AP1": -80})   # predicts ROOM_205
    assert anomaly.is_anomalous is True
    assert any("Wi-Fi fingerprint indicates" in r for r in anomaly.reasons)
    wifi_component = next(c for c in evidence.components if c.name == "wifi_zone_match")
    assert wifi_component.points == 0.0   # the mismatch earns zero wifi points too


def test_feedback_creates_a_verified_case_and_rag_can_retrieve_it(tmp_path):
    clock = Clock()
    engine, orch, secret = _setup(tmp_path, clock)
    anomaly_id = engine.store.add_anomaly(
        "STU102", clock.t, "ble_wifi_contradiction", "medium", None, True,
        ["BLE indicates Room 204, Wi-Fi indicates Room 205"], {"wifi_confidence": 0.3},
    )

    orch.submit_feedback(anomaly_id, "false_positive", "Wi-Fi AP was temporarily unstable")

    cases = engine.store.verified_cases()
    assert len(cases) == 1
    assert "unstable" in cases[0].resolution

    retrieved = orch.retriever.retrieve("Wi-Fi AP instability Room 204", k=1)
    assert retrieved
    assert retrieved[0].case.case_id == cases[0].id


def test_explain_anomaly_grounded_in_evidence(tmp_path):
    clock = Clock()
    engine, orch, secret = _setup(tmp_path, clock)
    anomaly_id = engine.store.add_anomaly(
        "STU102", clock.t, "ble_wifi_contradiction", "medium", -0.4, True,
        ["BLE classroom marker indicates ROOM_204, but the Wi-Fi fingerprint indicates ROOM_205"],
        {"ble_room": "ROOM_204", "wifi_room": "ROOM_205", "wifi_confidence": 0.3},
    )
    explanation, retrieved = asyncio.run(orch.explain_anomaly(anomaly_id))
    assert "204" in explanation
    stored = engine.store.anomaly(anomaly_id)
    assert stored.explanation == explanation


def test_explain_anomaly_on_unknown_id_raises(tmp_path):
    clock = Clock()
    engine, orch, secret = _setup(tmp_path, clock)
    with pytest.raises(UnknownAnomaly):
        asyncio.run(orch.explain_anomaly(999))


def test_submit_feedback_on_unknown_anomaly_raises(tmp_path):
    clock = Clock()
    engine, orch, secret = _setup(tmp_path, clock)
    with pytest.raises(UnknownAnomaly):
        orch.submit_feedback(999, "false_positive", None)


def test_ingest_peer_observation_resolves_both_tokens(tmp_path):
    clock = Clock()
    engine, orch, secret_a = _setup(tmp_path, clock)
    secret_b = engine.enroll_student("STU103", "Priya")
    tok_a = token_for(secret_a, window_index(clock.t, engine.settings.token_window_s))
    tok_b = token_for(secret_b, window_index(clock.t, engine.settings.token_window_s))

    observed = orch.ingest_peer_observation(tok_b, tok_a, rssi=-58, duration_s=12.0, ts=clock.t)
    assert observed == "STU102"

    rows = engine.store.peer_observations_between(clock.t - 1, clock.t + 1)
    assert len(rows) == 1
    assert rows[0].observer_student_id == "STU103" and rows[0].observed_student_id == "STU102"


def test_ingest_peer_observation_unknown_observer_token_is_dropped(tmp_path):
    clock = Clock()
    engine, orch, secret_a = _setup(tmp_path, clock)
    tok_a = token_for(secret_a, window_index(clock.t, engine.settings.token_window_s))
    observed = orch.ingest_peer_observation("0" * 16, tok_a, rssi=-58, duration_s=None, ts=clock.t)
    assert observed is None
    assert engine.store.peer_observations_between(clock.t - 1, clock.t + 1) == []
