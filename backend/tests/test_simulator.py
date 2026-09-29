"""The simulator end to end, in-process: every role must land where it should."""
from fastapi.testclient import TestClient

from app.config import Settings
from app.fusion import FusionConfig
from app.main import create_app
from simulator.simulate import SimConfig, SimError, compare, run

import pytest


def make_client(tmp_path, **fusion):
    settings = Settings(
        db_path=":memory:", model_dir=str(tmp_path / "models"), max_clock_skew_s=0,
        fusion=FusionConfig(stationary_limit_s=fusion.pop("stationary_limit_s", 600)),
    )
    return TestClient(create_app(settings))


def test_every_role_lands_in_its_expected_state(tmp_path):
    cfg = SimConfig(residents=30, minutes=20, floors=2, enrolled=10, handoff_pairs=2, left=2,
                    bt_off=2, absent=2, walkby=2, model_dir=str(tmp_path / "models"))
    result = run(make_client(tmp_path), cfg, log=lambda *_: None)
    assert result.mismatches == []
    s = result.rollcall["summary"]
    assert s["suspect"] == 2 * 2 + 2 and s["uncertain"] >= 2 and s["not_detected"] == 2


def test_compare_reports_a_wrong_state(tmp_path):
    cfg = SimConfig(residents=12, minutes=15, floors=1, enrolled=6, handoff_pairs=1, left=1,
                    bt_off=1, absent=1, walkby=1, model_dir=str(tmp_path / "models"))
    result = run(make_client(tmp_path), cfg, log=lambda *_: None)
    victim = next(p for p in result.residents if p.role == "normal")
    result.rollcall["students"]["verified"] = [
        x for x in result.rollcall["students"]["verified"] if x["student_id"] != victim.sid
    ]
    result.rollcall["students"]["not_detected"].append({"student_id": victim.sid})
    assert compare(result) == [f"{victim.sid} (normal) expected verified, got not_detected"]


def test_left_behind_needs_the_server_limit_to_be_low(tmp_path):
    cfg = SimConfig(residents=12, minutes=15, floors=1, enrolled=6, handoff_pairs=1, left=1,
                    bt_off=1, absent=1, walkby=1, model_dir=str(tmp_path / "models"))
    result = run(make_client(tmp_path, stationary_limit_s=2 * 3600), cfg, log=lambda *_: None)
    assert any("(left) expected suspect, got verified" in m for m in result.mismatches)


def test_fails_clearly_when_server_enforces_clock_skew(tmp_path):
    settings = Settings(db_path=":memory:", model_dir=str(tmp_path / "models"), max_clock_skew_s=120)
    cfg = SimConfig(residents=8, minutes=5, floors=1, enrolled=4, handoff_pairs=1, left=1,
                    bt_off=1, absent=1, walkby=1, model_dir=str(tmp_path / "models"))
    with pytest.raises(SimError, match="BLEWIFI_MAX_SKEW_S=0"):
        run(TestClient(create_app(settings)), cfg, log=lambda *_: None)


def test_rerun_against_same_database_is_refused(tmp_path):
    client = make_client(tmp_path)
    cfg = SimConfig(residents=8, minutes=2, floors=1, enrolled=4, handoff_pairs=1, left=1,
                    bt_off=1, absent=1, walkby=1, model_dir=str(tmp_path / "models"))
    run(client, cfg, log=lambda *_: None)
    with pytest.raises(SimError, match="already exists"):
        run(client, cfg, log=lambda *_: None)


def test_fails_when_server_and_simulator_use_different_model_dirs(tmp_path):
    cfg = SimConfig(residents=8, minutes=2, floors=1, enrolled=4, handoff_pairs=1, left=1,
                    bt_off=1, absent=1, walkby=1, model_dir=str(tmp_path / "somewhere-else"))
    with pytest.raises(SimError, match="BLEWIFI_MODEL_DIR"):
        run(make_client(tmp_path), cfg, log=lambda *_: None)
