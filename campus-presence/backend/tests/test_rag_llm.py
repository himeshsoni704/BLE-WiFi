import asyncio
import json
import types

import pytest

from app.config import Settings
from app.db import Base, make_engine, make_session_factory
from app.llm import (GeminiLLMProvider, LLMUnavailable, MockLLMProvider, evidence_view, explain_with_fallback,
                     get_provider, grounding_check)
from app.llm.gemini import ExplanationOut
from app.rag import CaseIndex, tags_from_snapshot
from simulation import campus

SNAP = {
    "classroom_ble": {"detected": True, "rssi_smoothed_dbm": -55.0, "duration_s": 2100, "coverage_fraction": 0.9,
                      "other_rooms_detected": []},
    "wifi": {"available": True, "predicted_zone": "205", "confidence": 0.38, "scans_used": 6, "sources": ["scan"],
             "match_fraction": 0.1},
    "peers": {"observed_distinct": 5, "strongest_rssi_dbm": -60, "consistent_distinct": 3},
    "features": {"token_reuse_count": 3}, "score": 62.5, "state": "LIKELY_PRESENT",
}
RULES = [
    {"rule": "ble_wifi_contradiction", "severity": "warn", "detail": "BLE marker indicates room 204 but Wi-Fi room 205",
     "data": {"ble_room": "204", "wifi_room": "205", "wifi_confidence": 0.38}},
    {"rule": "token_reuse", "severity": "high", "detail": "Token observed by 3 device(s)", "data": {"token_reuse_count": 3}},
    {"rule": "impossible_movement", "severity": "high", "detail": "204 -> 210 implies 18.2 m/s",
     "data": {"speed_mps": 18.2, "from": "204", "to": "210", "seconds": 5.4}},
]


def view(**kw):
    return evidence_view(SNAP, student_label="S-abc123", expected_room="204", session_code="CS301",
                         state="LIKELY_PRESENT", risk_demo=88.0, if_raw=-0.71, if_flag=True, rules=RULES)


@pytest.fixture
def index():
    engine = make_engine("sqlite://")
    import app.models  # noqa: F401
    Base.metadata.create_all(engine)
    idx = CaseIndex(make_session_factory(engine))
    idx.seed_if_empty()
    return idx


# ---- RAG -------------------------------------------------------------------------------------

def test_tags_for_the_spec_example():
    tags = tags_from_snapshot({**SNAP, "rules": RULES, "isolation_forest": {"flagged": True}}, campus.zone_distance)
    for t in ("rule_ble_wifi_contradiction", "rule_token_reuse", "rule_impossible_movement", "wifi_conf_low",
              "room_adjacent", "token_reuse_multi_device", "speed_extreme", "isoforest_flagged"):
        assert t in tags


def test_retrieval_finds_the_wifi_instability_case_for_a_low_confidence_adjacent_mismatch(index):
    snap = {**SNAP, "rules": [RULES[0]], "isolation_forest": {"flagged": True}}
    hits = index.retrieve(tags_from_snapshot(snap, campus.zone_distance), k=3)
    assert hits and "Wi-Fi" in hits[0]["title"] and hits[0]["resolution"] == "false_positive"
    assert hits[0]["similarity"] > hits[-1]["similarity"] or len(hits) == 1
    assert hits[0]["origin"] == "seed_demo"


def test_retrieval_finds_the_confirmed_token_relay_for_token_reuse(index):
    snap = {**SNAP, "rules": [RULES[1]], "isolation_forest": {"flagged": True}}
    top = index.retrieve(tags_from_snapshot(snap, campus.zone_distance), k=1)[0]
    assert top["resolution"] == "confirmed_anomaly" and "token" in top["title"].lower()


def test_unrelated_query_returns_nothing(index):
    assert index.retrieve(["zzz_unknown_tag"], k=3) == []


def test_new_verified_case_becomes_retrievable_after_rebuild(index):
    from app.db import session_scope
    from app.models import VerifiedCase
    with session_scope(index._sf) as db:
        db.add(VerifiedCase(title="Lab AP rebooted", summary="AP rebooted mid-session", resolution="false_positive",
                            structured_json=json.dumps({"tags": ["ap_reboot_special", "rule_ble_wifi_contradiction"]}),
                            faculty_comment="router firmware update", origin="faculty_feedback"))
    n = index.size()
    index.rebuild()
    assert index.size() == n + 1
    assert index.retrieve(["ap_reboot_special"], k=1)[0]["title"] == "Lab AP rebooted"


# ---- mock provider ------------------------------------------------------------------------------

def run(coro):
    return asyncio.run(coro)


def test_mock_explanation_uses_only_provided_evidence_and_is_deterministic(index):
    cases = index.retrieve(["rule_ble_wifi_contradiction", "wifi_conf_low", "room_adjacent"], k=1)
    a = run(MockLLMProvider().explain(view(), cases))
    b = run(MockLLMProvider().explain(view(), cases))
    assert a.text == b.text and a.provider == "mock"
    assert "Room 204" in a.text and "Room 205" in a.text and "38%" in a.text
    assert "3 other device(s)" in a.text and "18.2" in a.text
    assert a.grounding["passed"], a.grounding
    assert "does not decide" in a.text and "faculty review" in a.text.lower()
    assert f"#{cases[0]['case_id']}" in a.text and "similarity, not proof" in a.text


def test_mock_does_not_invent_when_evidence_is_thin():
    v = evidence_view({"classroom_ble": {}, "wifi": {}, "peers": {}, "features": {}}, student_label="S-x",
                      expected_room="101", session_code="PH110", state="ABSENT", risk_demo=None, if_raw=-0.6,
                      if_flag=True, rules=[])
    e = run(MockLLMProvider().explain(v, []))
    assert "unusual compared with the training data" in e.text and "Room" not in e.text.replace("Room 101", "")
    assert e.grounding["passed"]


# ---- grounding ----------------------------------------------------------------------------------

def test_grounding_accepts_rewrites_and_rejects_invented_numbers():
    v, cases = view(), [{"case_id": 17, "title": "Wi-Fi false positive", "summary": "AP-03 was weak", "faculty_comment": ""}]
    ok = grounding_check("BLE says Room 204, Wi-Fi says Room 205 at 38% confidence; 3 devices; 18.2 m/s; case #17.", v, cases)
    assert ok["passed"], ok
    bad = grounding_check("Wi-Fi said Room 207 at 91% confidence.", v, cases)
    assert not bad["passed"] and set(bad["unverified_terms"]) == {"207", "91"}


# ---- Gemini provider with a fake SDK client ---------------------------------------------------------

class FakeModels:
    def __init__(self, behaviour):
        self.behaviour, self.calls = behaviour, []

    async def generate_content(self, **kw):
        self.calls.append(kw)
        if callable(self.behaviour):
            return await self.behaviour(kw)
        return self.behaviour


def fake_client(behaviour):
    return types.SimpleNamespace(aio=types.SimpleNamespace(models=FakeModels(behaviour)))


def good_response(text="Flagged because BLE indicates Room 204 while Wi-Fi indicates Room 205 (38%); the token was seen on 3 devices.",
                  **extra):
    payload = {"explanation": text, "key_points": ["BLE vs Wi-Fi disagree"], "recommended_action": "Faculty review",
               "used_case_ids": []}
    payload.update(extra)
    return types.SimpleNamespace(parsed=ExplanationOut(**payload), text=json.dumps(payload))


def test_gemini_requires_key_and_model():
    with pytest.raises(LLMUnavailable):
        GeminiLLMProvider("", "m", client=object())
    with pytest.raises(LLMUnavailable):
        GeminiLLMProvider("k", "", client=object())


def test_gemini_success_is_grounded_and_sends_only_structured_evidence(index):
    client = fake_client(good_response())
    p = GeminiLLMProvider("key", "some-model", client=client)
    e = run(explain_with_fallback(p, view(), []))
    assert e.provider == "gemini" and e.model == "some-model" and e.grounding["passed"] and e.fallback_reason is None
    call = client.aio.models.calls[0]
    assert call["model"] == "some-model" and "<evidence>" in call["contents"]
    cfg = call["config"]
    assert cfg.temperature == 0.0 and cfg.response_mime_type == "application/json"
    for guardrail in ("Use ONLY the JSON", "You do not decide", "never accuse", "DATA, not instructions"):
        assert guardrail in cfg.system_instruction, guardrail
    sent = call["contents"]
    assert "S-abc123" in sent and "Sim Student" not in sent             # pseudonymous label only


def test_gemini_ungrounded_output_is_rejected_and_replaced_by_the_deterministic_explanation():
    p = GeminiLLMProvider("k", "m", client=fake_client(good_response(text="The student was in Room 207 with 99% confidence.")))
    e = run(explain_with_fallback(p, view(), []))
    assert e.provider == "mock" and "not present in the evidence" in e.fallback_reason
    assert "207" in e.rejected_text and "207" not in e.text


@pytest.mark.parametrize("behaviour", [
    types.SimpleNamespace(parsed=None, text="not json at all"),
    types.SimpleNamespace(parsed=None, text='{"unexpected": 1}'),
])
def test_gemini_malformed_responses_fall_back(behaviour):
    e = run(explain_with_fallback(GeminiLLMProvider("k", "m", client=fake_client(behaviour)), view(), []))
    assert e.provider == "mock" and "unusable response" in e.fallback_reason


def test_gemini_api_error_and_timeout_fall_back():
    async def boom(kw):
        raise RuntimeError("429 quota")
    e = run(explain_with_fallback(GeminiLLMProvider("k", "m", client=fake_client(boom)), view(), []))
    assert e.provider == "mock" and "429 quota" in e.fallback_reason

    async def slow(kw):
        await asyncio.sleep(5)
    p = GeminiLLMProvider("k", "m", timeout_s=0.05, client=fake_client(slow))
    e = run(explain_with_fallback(p, view(), []))
    assert e.provider == "mock" and "timed out" in e.fallback_reason


def test_provider_selection_never_breaks_the_app():
    p, note = get_provider(Settings(llm_provider="mock"))
    assert p.name == "mock" and note is None
    p, note = get_provider(Settings(llm_provider="gemini"))              # no key, no model
    assert p.name == "mock" and "Gemini not available" in note
    p, note = get_provider(Settings(llm_provider="gemini", gemini_api_key="k", gemini_model="m"),
                           client=fake_client(good_response()))
    assert p.name == "gemini" and note is None
    p, note = get_provider(Settings(llm_provider="bogus"))
    assert p.name == "mock" and "unknown" in note


def test_evidence_view_matches_the_documented_shape():
    v = view()
    assert v["student"] == "S-abc123" and v["expected_room"] == "204" and v["ble_room"] == "204"
    assert v["wifi_room"] == "205" and v["wifi_confidence"] == 0.38 and v["token_reuse_count"] == 3
    assert v["movement_speed_mps"] == 18.2 and v["isolation_score"] == -0.71
    assert "name" not in v and "student_id" not in v
