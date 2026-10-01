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


def test_seed_simulation_populates_db_and_matches_requested_counts(tmp_path):
    clock = Clock()
    store = Store()
    engine = Engine(store, ModelRegistry(tmp_path / "models"), Settings(max_clock_skew_s=0), clock)
    orch = PresenceOrchestrator(engine, MockLLMProvider())

    summary = orch.seed_simulation(n_students=40, n_classrooms=8, n_aps=4, anomaly_rate=0.15, seed=3)

    assert summary["students"] == 40
    assert summary["classrooms"] == 8
    assert len(store.classrooms()) == 8
    assert len(store.student_ids()) == 40
    assert len(store.sessions()) >= 1
    assert summary["anomalies_injected"] > 0
    anomalies = store.anomalies()
    assert len(anomalies) == summary["anomalies_injected"]
    # every anomaly's source attendance evidence was stored as "simulated", not "live"
    sample_sid = anomalies[0].student_id
    ev_row = store.latest_evidence(sample_sid)
    assert ev_row is not None and ev_row.source == "simulated"


def test_seed_simulation_is_idempotent_for_already_enrolled_students(tmp_path):
    clock = Clock()
    store = Store()
    engine = Engine(store, ModelRegistry(tmp_path / "models"), Settings(max_clock_skew_s=0), clock)
    orch = PresenceOrchestrator(engine, MockLLMProvider())
    orch.seed_simulation(n_students=20, n_classrooms=4, n_aps=2, seed=1)
    n_before = len(store.student_ids())
    orch.seed_simulation(n_students=20, n_classrooms=4, n_aps=2, seed=1)   # same seed, same student ids
    assert len(store.student_ids()) == n_before   # no DuplicateStudent crash, no duplicate rows


def test_inject_demo_anomaly_short_presence(tmp_path):
    clock = Clock()
    engine, orch, secret = _setup(tmp_path, clock)
    anomaly = orch.inject_demo_anomaly("short_presence", student_id="STU102", at=clock.t)
    assert anomaly.is_anomalous is True
    assert anomaly.anomaly_id is not None
    stored = engine.store.anomaly(anomaly.anomaly_id)
    assert stored.type == "short_presence"


def test_inject_demo_anomaly_requires_enrolled_student(tmp_path):
    clock = Clock()
    engine, orch, secret = _setup(tmp_path, clock)
    with pytest.raises(ValueError, match="enrolled"):
        orch.inject_demo_anomaly("short_presence", student_id="NOBODY", at=clock.t)


def test_inject_demo_anomaly_picks_a_student_when_none_given(tmp_path):
    """Demo Control Panel buttons (brief section 29) must be genuinely
    one-click: a judge shouldn't have to look up a student_id first."""
    clock = Clock()
    engine, orch, secret = _setup(tmp_path, clock)
    anomaly = orch.inject_demo_anomaly("short_presence", at=clock.t)
    assert anomaly.is_anomalous is True
    stored = engine.store.anomaly(anomaly.anomaly_id)
    assert stored.student_id == "STU102"   # the only enrolled student


def test_inject_demo_anomaly_with_no_student_fails_clearly(tmp_path):
    clock = Clock()
    store = Store()
    engine = Engine(store, ModelRegistry(tmp_path / "models"), Settings(max_clock_skew_s=0), clock)
    orch = PresenceOrchestrator(engine, MockLLMProvider())
    with pytest.raises(ValueError, match="no students enrolled"):
        orch.inject_demo_anomaly("short_presence", at=clock.t)


def test_inject_demo_anomaly_token_reuse_uses_real_token_pipeline(tmp_path):
    clock = Clock()
    engine, orch, secret = _setup(tmp_path, clock)
    anomaly = orch.inject_demo_anomaly("proxy_attendance", student_id="STU102", at=clock.t)
    assert anomaly.is_anomalous is True
    assert any("token" in r.lower() for r in anomaly.reasons)

    # the underlying mechanism really did post two real scans through engine.ingest_scans
    rows = engine.store.scans_between(clock.t - 10, clock.t + 10)
    assert len(rows) == 2
    assert {r.zone for r in rows} == {ROOM_204, ROOM_205}
    assert all(r.student_id == "STU102" for r in rows)


def test_inject_demo_anomaly_token_reuse_needs_two_classrooms(tmp_path):
    clock = Clock()
    store = Store()
    engine = Engine(store, ModelRegistry(tmp_path / "models"), Settings(max_clock_skew_s=0), clock)
    orch = PresenceOrchestrator(engine, MockLLMProvider())
    store.upsert_classroom(ClassroomRow(ROOM_204, "Room 204", "Block A", 1, 0.0, 0.0, "ROOM_204_BEACON", None))
    engine.enroll_student("STU102", "Himesh")
    with pytest.raises(ValueError, match="2 registered classrooms"):
        orch.inject_demo_anomaly("token_replay", student_id="STU102", at=clock.t)
