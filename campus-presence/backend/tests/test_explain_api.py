"""/explain-anomaly end to end on a simulated campus: attribution + knowledge + cited reasoning + Gemini fallback."""
import json
import types

import pytest
from fastapi.testclient import TestClient

from app.llm import GeminiLLMProvider
from app.llm.gemini import ExplanationOut
from app.main import create_app
from conftest import Clock, login, make_settings


@pytest.fixture(scope="module")
def world(model_dir, tmp_path_factory):
    d = tmp_path_factory.mktemp("explain")
    app = create_app(make_settings(model_dir, d), clock=Clock(), train_missing=False)
    c = TestClient(app)
    fac = login(c, "faculty")
    assert c.post("/simulation/start", json={"scenario": "full", "students": 120, "wait": True}, headers=fac).status_code == 202
    anomalies = c.get("/anomalies?source=simulated&limit=500", headers=fac).json()["anomalies"]
    assert len(anomalies) >= 10
    return {"c": c, "fac": fac, "app": app, "an": anomalies}


def explain(w, anomaly_id, **body):
    r = w["c"].post("/explain-anomaly", json={"anomaly_id": anomaly_id, **body}, headers=w["fac"])
    assert r.status_code == 200, r.text
    return r.json()


def test_every_anomaly_gets_a_valid_cited_explanation(world):
    available = 0
    for a in world["an"][:25]:
        r = explain(world, a["id"])
        e, attr = r["explanation"], r["attribution"]
        assert e["validation"]["passed"], (a["id"], e["validation"])
        assert e["grounding"]["passed"], (a["id"], e["grounding"])
        assert "KB-POLICY" in [k["id"] for k in r["knowledge"]]
        assert e["provider"] == "mock" and e["fallback_reason"] is None
        known = {k["id"] for k in r["knowledge"]} | {f"CASE-{c['case_id']}" for c in r["similar_cases"]}
        for h in e["hypotheses"]:
            assert (h["evidence"] or h["sources"]) and set(h["sources"]) <= known
        assert {s["id"] for s in e["sources"]} <= known
        if attr["available"]:
            available += 1
            assert abs(attr["sum_of_contributions"] - (attr["baseline_score"] - attr["score"])) < 2e-4
            assert attr["flagged"] == (attr["score"] < attr["flag_threshold"])
            assert attr["top"] and attr["top"] == sorted(attr["top"], key=lambda f: -f["contribution"])
            assert all(f["contribution"] > 0 for f in attr["top"])
            assert "Isolation Forest" in e["text"] and attr["top"][0]["label"] in e["text"]
        else:
            assert attr["reason"]
    assert available >= 5, "attribution should be available for most anomalies the forest evaluated"


def test_the_attribution_matches_what_the_stored_score_says(world):
    for a in world["an"]:
        if a["isolation_score_raw"] is None:
            continue
        attr = explain(world, a["id"])["attribution"]
        assert attr["available"]
        # the attribution recomputes the score from the stored features; it must agree with the stored raw score
        assert abs(attr["score"] - a["isolation_score_raw"]) < 2e-3, (a["id"], attr["score"], a["isolation_score_raw"])
        return
    pytest.skip("no anomaly in this run was scored by the forest")


def test_an_anomaly_the_forest_did_not_evaluate_says_so_instead_of_inventing_a_reason(world):
    skipped = [a for a in world["an"] if a["isolation_score_raw"] is None]
    if not skipped:
        pytest.skip("every anomaly in this run was scored by the forest")
    r = explain(world, skipped[0]["id"])
    assert r["attribution"]["available"] is False and "did not evaluate" in r["attribution"]["reason"]
    assert r["explanation"]["validation"]["passed"] and r["explanation"]["hypotheses"] is not None


def test_rag_retrieve_returns_knowledge_passages_too(world):
    r = world["c"].post("/rag/retrieve", json={"anomaly_id": world["an"][0]["id"], "k": 3}, headers=world["fac"]).json()
    assert r["knowledge"] and "KB-POLICY" in [k["id"] for k in r["knowledge"]]
    assert all({"id", "title", "text", "score"} <= set(k) for k in r["knowledge"])


def test_students_cannot_ask_for_explanations(world):
    stu = login(world["c"], "HIMESH")
    assert world["c"].post("/explain-anomaly", json={"anomaly_id": world["an"][0]["id"]}, headers=stu).status_code == 403


# ---- Gemini end to end with a fake client ---------------------------------------------------------------------------

class FakeModels:
    def __init__(self, payload):
        self.payload, self.calls = payload, []

    async def generate_content(self, **kw):
        self.calls.append(kw)
        return types.SimpleNamespace(parsed=ExplanationOut(**self.payload), text=json.dumps(self.payload))


def swap_in_gemini(world, payload):
    models = FakeModels(payload)
    client = types.SimpleNamespace(aio=types.SimpleNamespace(models=models))
    world["app"].state.svc.llm = GeminiLLMProvider("key", "test-model", client=client)
    return models


@pytest.fixture
def restore_llm(world):
    original = world["app"].state.svc.llm
    yield
    world["app"].state.svc.llm = original


def test_gemini_reasoning_flows_through_the_endpoint_when_it_is_cited_and_grounded(world, restore_llm):
    a = next(x for x in world["an"] if x["rules"])
    base = explain(world, a["id"])                      # the deterministic explanation gives us valid text and citations
    e0 = base["explanation"]
    payload = {"explanation": e0["text"], "key_points": e0["key_points"], "recommended_action": e0["recommended_action"],
               "used_case_ids": e0["used_case_ids"], "used_kb_ids": e0["used_kb_ids"], "checks": e0["checks"],
               "hypotheses": e0["hypotheses"]}
    models = swap_in_gemini(world, payload)
    r = explain(world, a["id"])
    assert r["provider"]["used"] == "gemini" and r["provider"]["model"] == "test-model"
    assert r["explanation"]["fallback_reason"] is None
    assert r["explanation"]["hypotheses"] == e0["hypotheses"] and r["explanation"]["sources"]
    sent = models.calls[0]["contents"]
    assert "knowledge_passages" in sent and "KB-POLICY" in sent
    if base["attribution"]["available"]:
        assert "score_drivers" in sent
    assert r["provider"]["configured"] == "gemini"


def test_gemini_citing_something_invented_falls_back_through_the_endpoint(world, restore_llm):
    a = next(x for x in world["an"] if x["rules"])
    e0 = explain(world, a["id"])["explanation"]
    bad = {"explanation": e0["text"], "key_points": [], "recommended_action": "Faculty review", "used_case_ids": [],
           "used_kb_ids": [], "checks": [],
           "hypotheses": [{"cause": "Something", "likelihood": "possible", "because": "because",
                           "evidence": ["student_intent"], "sources": ["KB-DOES-NOT-EXIST"]}]}
    swap_in_gemini(world, bad)
    r = explain(world, a["id"])
    assert r["provider"]["used"] == "mock" and r["provider"]["configured"] == "gemini"
    assert "citation checks" in r["provider"]["fallback_reason"]
    assert r["explanation"]["validation"]["passed"]


def test_explanations_are_stored_with_their_reasoning(world):
    from app.db import session_scope
    from app.models import Anomaly
    a = world["an"][0]
    explain(world, a["id"])
    with session_scope(world["app"].state.svc.SessionLocal) as db:
        stored = json.loads(db.get(Anomaly, a["id"]).explanation_json)
    assert {"hypotheses", "checks", "sources", "validation", "used_kb_ids"} <= set(stored)
