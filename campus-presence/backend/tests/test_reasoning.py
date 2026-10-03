"""Cited, validated reasoning: the validator, the deterministic explainer, and the Gemini provider (fake client)."""
import asyncio
import json
import types

import pytest

from app.knowledge import KnowledgeBase
from app.llm import (GeminiLLMProvider, MockLLMProvider, evidence_view, explain_with_fallback, grounding_check)
from app.llm.base import case_ref, evidence_refs, validate_reasoning
from app.llm.gemini import ExplanationOut, ReasoningSchema
from app.rules import RULES

SNAP = {
    "classroom_ble": {"detected": True, "rssi_smoothed_dbm": -55.0, "duration_s": 2100, "coverage_fraction": 0.9,
                      "other_rooms_detected": []},
    "wifi": {"available": True, "predicted_zone": "205", "confidence": 0.38, "scans_used": 6, "sources": ["scan"],
             "match_fraction": 0.1},
    "peers": {"observed_distinct": 5, "strongest_rssi_dbm": -60, "consistent_distinct": 3},
    "features": {"token_reuse_count": 3}, "score": 62.5, "state": "LIKELY_PRESENT",
}
RULES_HIT = [
    {"rule": "ble_wifi_contradiction", "severity": "warn", "detail": "BLE marker indicates room 204 but Wi-Fi room 205",
     "data": {"ble_room": "204", "wifi_room": "205", "wifi_confidence": 0.38}},
    {"rule": "token_reuse", "severity": "high", "detail": "Token observed by 3 device(s)", "data": {"token_reuse_count": 3}},
    {"rule": "impossible_movement", "severity": "high", "detail": "204 -> 210 implies 18.2 m/s",
     "data": {"speed_mps": 18.2, "from": "204", "to": "210", "seconds": 5.4}},
]
ATTRIBUTION = {"available": True, "top": [
    {"feature": "estimated_speed", "label": "Implied travel speed", "value": 18.2, "typical": 0.0, "share_pct": 61.0,
     "value_note": None},
    {"feature": "rssi_twin_distance", "label": "Signal similarity to another phone", "value": 30.0, "typical": 3.2,
     "share_pct": 22.5, "value_note": "no other phone had readings comparable to this one"}],
    "would_stop_being_flagged_if_typical": {"features": ["estimated_speed"], "score_after": -0.4}}

KB = KnowledgeBase()


def view(rules=RULES_HIT, attribution=ATTRIBUTION):
    return evidence_view(SNAP, student_label="S-abc123", expected_room="204", session_code="CS301", state="LIKELY_PRESENT",
                         risk_demo=88.0, if_raw=-0.71, if_flag=True, rules=rules, attribution=attribution)


def knowledge(tags=("rule_ble_wifi_contradiction", "rule_token_reuse", "rule_impossible_movement", "wifi_conf_low")):
    return KB.retrieve(list(tags), k=6)


def case(case_id, resolution, sim=0.6):
    return {"case_id": case_id, "title": f"Case {case_id}", "summary": "summary text", "resolution": resolution,
            "faculty_comment": "", "similarity": sim, "origin": "seed_demo"}


def run(coro):
    return asyncio.run(coro)


# ---- the evidence the explainer sees ------------------------------------------------------------------------------

def test_the_evidence_view_carries_the_score_drivers_with_plain_language_notes():
    v = view()
    assert [d["feature"] for d in v["score_drivers"]] == ["estimated_speed", "rssi_twin_distance"]
    assert v["score_drivers"][1]["meaning_of_value"].startswith("no other phone")
    assert v["would_not_be_flagged_if_these_were_typical"] == ["estimated_speed"]
    assert "score_drivers" not in view(attribution=None) and "score_drivers" not in view(attribution={"available": False})


def test_evidence_refs_cover_top_level_fields_and_driver_names():
    refs = evidence_refs(view())
    assert {"expected_room", "wifi_confidence", "score_drivers", "estimated_speed", "score_drivers.estimated_speed"} <= refs
    assert "token_reuse_count" in refs and "nonsense" not in refs


# ---- validator ----------------------------------------------------------------------------------------------------

def check(hyps, checks=(), cases=(), kb=None, texts=("ok",), used_cases=(), used_kb=()):
    return validate_reasoning(texts=list(texts), hypotheses=hyps, checks=list(checks), used_case_ids=list(used_cases),
                              used_kb_ids=list(used_kb), evidence=view(), cases=list(cases),
                              knowledge=kb if kb is not None else knowledge())


GOOD = {"cause": "Wi-Fi estimate was off", "likelihood": "more likely", "because": "low confidence",
        "evidence": ["wifi_confidence"], "sources": ["KB-CAUSE-WIFI"]}


def test_a_well_cited_hypothesis_passes():
    assert check([GOOD])["passed"]
    assert check([{**GOOD, "evidence": ["score_drivers.estimated_speed"], "sources": []}])["passed"]
    assert check([{**GOOD, "evidence": [], "sources": [case_ref(7)]}], cases=[case(7, "false_positive")])["passed"]


@pytest.mark.parametrize("bad, fragment", [
    ({**GOOD, "evidence": ["made_up_field"]}, "does not exist"),
    ({**GOOD, "sources": ["KB-NOT-REAL"]}, "was not provided"),
    ({**GOOD, "sources": ["CASE-99"]}, "was not provided"),
    ({**GOOD, "evidence": [], "sources": []}, "cites no evidence and no source"),
    ({**GOOD, "likelihood": "certain"}, "likelihood must be one of"),
])
def test_each_unsupported_hypothesis_is_caught(bad, fragment):
    r = check([bad])
    assert not r["passed"] and any(fragment in p for p in r["problems"]), r


def test_citing_something_that_was_not_retrieved_is_caught():
    assert not check([GOOD], used_cases=[5], cases=[case(7, "false_positive")])["passed"]
    assert not check([GOOD], used_kb=["KB-NOT-REAL"])["passed"]


@pytest.mark.parametrize("phrase", ["The student was cheating.", "This is fraud", "They are guilty", "a dishonest act", "he lied"])
def test_accusatory_wording_is_rejected_in_any_field(phrase):
    assert not check([GOOD], texts=[phrase])["passed"]
    assert not check([GOOD], checks=[phrase])["passed"]


def test_ordinary_words_that_merely_contain_flagged_letters_are_fine():
    assert check([GOOD], texts=["The signal was relied on; belied by nothing; a fraction of the session."])["passed"]


def test_grounding_accepts_numbers_that_appear_in_a_cited_knowledge_passage():
    kb = [{"id": "KB-RULE-MOVE", "title": "Movement", "text": "fires above 4 m/s over 2 minutes"}]
    assert grounding_check("the limit is 4 m/s", view(), [], kb)["passed"]
    assert not grounding_check("the limit is 7 m/s", view(), [], kb)["passed"]


def test_grounding_allows_a_fraction_shown_as_a_rounded_percent_but_not_an_invented_one():
    ev = {"wifi_confidence": 0.695}
    assert grounding_check("confidence 70%", ev, [])["passed"] and grounding_check("confidence 69%", ev, [])["passed"]
    assert not grounding_check("confidence 71%", ev, [])["passed"]


# ---- the deterministic explainer ------------------------------------------------------------------------------------

def test_mock_reasoning_is_cited_validated_and_deterministic():
    cases = [case(7, "false_positive", 0.7)]
    a = run(MockLLMProvider().explain(view(), cases, knowledge()))
    b = run(MockLLMProvider().explain(view(), cases, knowledge()))
    assert a.to_dict() == b.to_dict()
    assert a.validation["passed"], a.validation
    assert a.grounding["passed"], a.grounding
    assert len(a.hypotheses) >= 2 and a.checks
    ids = {k["id"] for k in knowledge()} | {case_ref(7)}
    for h in a.hypotheses:
        assert h["evidence"] or h["sources"]
        assert set(h["sources"]) <= ids
        assert h["likelihood"] in ("more likely", "possible", "unlikely")
    assert {s["id"] for s in a.sources} == {x for h in a.hypotheses for x in h["sources"]}
    assert all(s["title"] and s["text"] for s in a.sources)
    assert "Implied travel speed" in a.text and "no other phone had readings comparable" in a.text


def test_likelihood_follows_how_similar_past_cases_were_resolved():
    benign = run(MockLLMProvider().explain(view(), [case(1, "false_positive", 0.9)], knowledge()))
    concern = run(MockLLMProvider().explain(view(), [case(2, "confirmed_anomaly", 0.9)], knowledge()))
    assert benign.hypotheses[0]["likelihood"] == "more likely" and concern.hypotheses[0]["likelihood"] == "more likely"
    assert benign.hypotheses[0]["cause"] != concern.hypotheses[0]["cause"]            # opposite lean, different first cause
    none = run(MockLLMProvider().explain(view(), [], knowledge()))
    assert {h["likelihood"] for h in none.hypotheses} == {"possible"}                   # no precedent: no ranking claimed


def test_without_knowledge_passages_only_cases_can_be_cited_and_it_still_validates():
    e = run(MockLLMProvider().explain(view(), [case(7, "false_positive")], None))
    assert e.validation["passed"]
    assert all(s.startswith("CASE-") for h in e.hypotheses for s in h["sources"])
    assert e.used_kb_ids == []


def test_thin_evidence_yields_no_invented_causes():
    v = evidence_view({"classroom_ble": {}, "wifi": {}, "peers": {}, "features": {}}, student_label="S-x",
                      expected_room="101", session_code="PH110", state="ABSENT", risk_demo=None, if_raw=-0.6,
                      if_flag=True, rules=[])
    e = run(MockLLMProvider().explain(v, [], knowledge()))
    assert e.hypotheses == [] and e.checks == [] and e.validation["passed"] and e.grounding["passed"]


@pytest.mark.parametrize("rule", [r.__name__ for r in RULES])
def test_every_rule_the_system_can_raise_gets_at_least_one_cited_hypothesis(rule):
    hit = {"rule": rule, "severity": "warn", "detail": "d", "data": {"ble_room": "204", "wifi_room": "205"}}
    v = evidence_view(SNAP, student_label="S", expected_room="204", session_code="C", state="X", risk_demo=1.0,
                      if_raw=-0.6, if_flag=True, rules=[hit])
    e = run(MockLLMProvider().explain(v, [], knowledge((f"rule_{rule}",))))
    assert e.hypotheses, rule
    assert e.validation["passed"] and e.grounding["passed"]


def test_forest_only_flags_get_hypotheses_from_what_drove_the_score():
    attr = {"available": True, "top": [{"feature": "ble_duration", "label": "Bluetooth time in the room", "value": 480.0,
                                        "typical": 2150.0, "share_pct": 70.0, "value_note": None}]}
    v = evidence_view(SNAP, student_label="S", expected_room="204", session_code="C", state="X", risk_demo=1.0,
                      if_raw=-0.6, if_flag=True, rules=[], attribution=attr)
    e = run(MockLLMProvider().explain(v, [], knowledge(("feat_ble_duration", "short_presence"))))
    assert "arrived late or left early" in e.hypotheses[0]["cause"]
    assert "KB-CAUSE-EARLY" in e.hypotheses[0]["sources"] and e.validation["passed"]


# ---- Gemini (fake client) ---------------------------------------------------------------------------------------------

class FakeModels:
    def __init__(self, response):
        self.response, self.calls = response, []

    async def generate_content(self, **kw):
        self.calls.append(kw)
        return self.response


def client_for(payload):
    resp = types.SimpleNamespace(parsed=ExplanationOut(**payload), text=json.dumps(payload))
    return types.SimpleNamespace(aio=types.SimpleNamespace(models=FakeModels(resp)))


def payload(**over):
    p = {"explanation": "BLE places the student in Room 204 while Wi-Fi says Room 205 at 38%. Faculty review is recommended.",
         "key_points": ["BLE and Wi-Fi disagree"], "recommended_action": "Faculty review",
         "used_case_ids": [7], "used_kb_ids": ["KB-CAUSE-WIFI"], "checks": ["Check the access points near Room 204"],
         "hypotheses": [{"cause": "The Wi-Fi estimate was unreliable", "likelihood": "more likely",
                         "because": "Wi-Fi confidence was only 38%", "evidence": ["wifi_confidence", "bluetooth_wifi_mismatch"],
                         "sources": ["KB-CAUSE-WIFI", "CASE-7"]},
                        {"cause": "The phone left the room for part of the session", "likelihood": "possible",
                         "because": "Wi-Fi pointed to another room", "evidence": ["wifi_room"], "sources": ["KB-RULE-BLEWIFI"]}]}
    p.update(over)
    return p


CASES = [case(7, "false_positive", 0.7)]


def gemini(p):
    c = client_for(p)
    return GeminiLLMProvider("key", "some-model", client=c), c


def test_gemini_reasoning_passes_through_when_cited_and_grounded():
    prov, _ = gemini(payload())
    e = run(explain_with_fallback(prov, view(), CASES, knowledge()))
    assert e.provider == "gemini" and e.fallback_reason is None
    assert e.validation["passed"] and e.grounding["passed"]
    assert [h["likelihood"] for h in e.hypotheses] == ["more likely", "possible"]
    assert e.used_kb_ids == ["KB-CAUSE-WIFI"] and e.checks
    assert {s["id"] for s in e.sources} == {"KB-CAUSE-WIFI", "CASE-7", "KB-RULE-BLEWIFI"}
    assert next(s for s in e.sources if s["id"] == "CASE-7")["kind"] == "similar_case"


def test_gemini_is_sent_the_knowledge_the_cases_and_the_score_drivers_and_nothing_identifying():
    prov, c = gemini(payload())
    run(explain_with_fallback(prov, view(), CASES, knowledge()))
    call = c.aio.models.calls[0]
    sent = call["contents"]
    for needle in ("knowledge_passages", "KB-RULE-BLEWIFI", "CASE-7", "score_drivers", "estimated_speed", "S-abc123"):
        assert needle in sent, needle
    assert "Sim Student" not in sent
    cfg = call["config"]
    assert cfg.response_schema is ReasoningSchema and cfg.temperature == 0.0
    for guardrail in ("Use ONLY the JSON", "never accuse", "A hypothesis with no evidence field"):
        assert guardrail in cfg.system_instruction, guardrail


@pytest.mark.parametrize("name, mutate, fragment", [
    ("cites a passage that was never provided",
     lambda p: p["hypotheses"][0].update(sources=["KB-INVENTED"]), "was not provided"),
    ("cites an evidence field that does not exist",
     lambda p: p["hypotheses"][0].update(evidence=["student_intent"]), "does not exist"),
    ("a cause with no support at all",
     lambda p: p["hypotheses"][1].update(evidence=[], sources=[]), "cites no evidence and no source"),
    ("an unknown likelihood word",
     lambda p: p["hypotheses"][0].update(likelihood="certain"), "likelihood must be one of"),
    ("accuses the student",
     lambda p: p.update(explanation="The student was cheating. Room 204, Room 205, 38%."), "accusatory wording"),
    ("uses a relied-on case that was not retrieved",
     lambda p: p.update(used_case_ids=[99]), "was not retrieved"),
])
def test_gemini_output_that_breaks_the_citation_rules_is_replaced_by_the_deterministic_explanation(name, mutate, fragment):
    p = payload()
    mutate(p)
    prov, _ = gemini(p)
    e = run(explain_with_fallback(prov, view(), CASES, knowledge()))
    assert e.provider == "mock", name
    assert "citation checks" in e.fallback_reason and fragment in e.fallback_reason, e.fallback_reason
    assert e.rejected_text                                                   # what Gemini said is kept for the audit trail
    assert e.validation["passed"]                                            # and what the user sees is itself valid


def test_gemini_inventing_a_number_is_still_rejected_by_the_grounding_check():
    p = payload(explanation="Wi-Fi said Room 207 at 91% confidence.")
    prov, _ = gemini(p)
    e = run(explain_with_fallback(prov, view(), CASES, knowledge()))
    assert e.provider == "mock" and "values not present in the evidence" in e.fallback_reason


def test_gemini_text_may_use_a_number_from_a_cited_passage():
    p = payload(explanation="Room 204 versus Room 205 at 38%; the movement rule's limit is 4 m/s, and faculty review is advised.")
    prov, _ = gemini(p)
    e = run(explain_with_fallback(prov, view(), CASES, knowledge()))
    assert e.provider == "gemini", e.fallback_reason


# ---- the schema we hand to the real SDK ---------------------------------------------------------------------------------

def _keys(obj, acc=None):
    acc = set() if acc is None else acc
    if isinstance(obj, dict):
        for k, v in obj.items():
            acc.add(k)
            _keys(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            _keys(v, acc)
    return acc


def test_the_response_schema_has_no_defaults_because_gemini_structured_output_can_reject_them():
    assert "default" not in _keys(ReasoningSchema.model_json_schema())
    props = ReasoningSchema.model_json_schema()["properties"]
    assert {"explanation", "hypotheses", "checks", "used_kb_ids", "used_case_ids"} <= set(props)


def test_the_real_sdk_accepts_the_config_and_converts_the_schema():
    genai = pytest.importorskip("google.genai")
    from google.genai import types as gt
    cfg = gt.GenerateContentConfig(system_instruction="x", temperature=0.0, max_output_tokens=2048,
                                   response_mime_type="application/json", response_schema=ReasoningSchema)
    assert cfg.response_schema is ReasoningSchema
    try:
        from google.genai import _transformers as t
        schema = t.t_schema(genai.Client(api_key="not-a-real-key")._api_client, ReasoningSchema)
    except (ImportError, AttributeError):
        pytest.skip("SDK internals moved; the public config above was still accepted")
    dumped = schema.model_dump(exclude_none=True, mode="json")
    assert "default" not in json.dumps(dumped) and "hypotheses" in dumped["properties"]
