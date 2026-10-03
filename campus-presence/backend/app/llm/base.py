"""LLM abstraction. The LLM explains structured evidence after an anomaly has been raised; it
never decides attendance and never overrides the rules or Isolation Forest."""
from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field


class LLMUnavailable(Exception):
    """Provider not configured / call failed. The caller falls back to the mock provider."""


@dataclass
class Explanation:
    text: str
    key_points: list[str]
    recommended_action: str
    provider: str                    # gemini | mock
    model: str | None = None
    grounding: dict = field(default_factory=lambda: {"checked": False, "passed": True, "unverified_terms": []})
    fallback_reason: str | None = None
    rejected_text: str | None = None
    used_case_ids: list[int] = field(default_factory=list)
    # reasoning: ranked possible causes, each citing evidence fields and sources; checks faculty can make
    hypotheses: list[dict] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)
    used_kb_ids: list[str] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)           # the cited passages/cases, resolved for display
    validation: dict = field(default_factory=lambda: {"checked": False, "passed": True, "problems": []})

    def to_dict(self) -> dict:
        return asdict(self)


class LLMProvider(ABC):
    name = "base"

    @abstractmethod
    async def explain(self, evidence: dict, retrieved_cases: list[dict],
                      knowledge: list[dict] | None = None) -> Explanation:
        """`evidence` is the reduced structured view from evidence_view(); never raw observations. `knowledge` are
        the retrieved knowledge-base passages (knowledge.py) the explanation may cite."""


def evidence_view(anomaly_snapshot: dict, *, student_label: str, expected_room: str, session_code: str,
                  state: str, risk_demo: float | None, if_raw: float | None, if_flag: bool,
                  rules: list[dict], attribution: dict | None = None) -> dict:
    """The only facts the LLM is allowed to see. Pseudonymous: `student_label` is a hashed key unless
    the operator explicitly opts in to real ids (GEMINI_SEND_REAL_IDS=1)."""
    ble, wifi, peers = anomaly_snapshot.get("classroom_ble", {}), anomaly_snapshot.get("wifi", {}), anomaly_snapshot.get("peers", {})
    feats = anomaly_snapshot.get("features", {})
    contradiction = next((r for r in rules if r["rule"] == "ble_wifi_contradiction"), None)
    movement = next((r for r in rules if r["rule"] == "impossible_movement"), None)
    view = {
        "student": student_label,
        "session": session_code,
        "expected_room": expected_room,
        "ble_room": expected_room if ble.get("detected") else None,
        "ble_other_rooms_detected": ble.get("other_rooms_detected") or [],
        "ble_rssi_dbm": ble.get("rssi_smoothed_dbm"),
        "ble_duration_s": ble.get("duration_s"),
        "wifi_room": wifi.get("predicted_zone"),
        "wifi_confidence": wifi.get("confidence"),
        "wifi_scans_used": wifi.get("scans_used"),
        "wifi_sources": wifi.get("sources"),
        "peers_distinct": peers.get("observed_distinct"),
        "token_reuse_count": int(feats.get("token_reuse_count", 0)),
        "movement_speed_mps": (movement or {}).get("data", {}).get("speed_mps"),
        "fusion_state": state,
        "fusion_score": anomaly_snapshot.get("score"),
        "isolation_score": None if if_raw is None else round(if_raw, 3),
        "isolation_flagged": if_flag,
        "demo_risk_score_0_100": risk_demo,
        "rules_triggered": [{"rule": r["rule"], "severity": r["severity"], "detail": r["detail"]} for r in rules],
        "bluetooth_wifi_mismatch": None if contradiction is None else {
            "ble_room": contradiction["data"].get("ble_room"), "wifi_room": contradiction["data"].get("wifi_room")},
    }
    if attribution and attribution.get("available"):
        view["score_drivers"] = [
            {k: v for k, v in {"feature": d["feature"], "label": d["label"], "value": round(d["value"], 2),
                               "typical": round(d["typical"], 2),
                               "share_of_unusualness_pct": d["share_pct"], "meaning_of_value": d.get("value_note")}.items()
             if v is not None} for d in attribution["top"][:3]]
        clear = attribution.get("would_stop_being_flagged_if_typical")
        if clear:
            view["would_not_be_flagged_if_these_were_typical"] = clear["features"]
    return {k: v for k, v in view.items() if v not in (None, [], {})}


_NUM = re.compile(r"-?\d+(?:\.\d+)?")


def _collect_numbers(obj, out: set[float]) -> None:
    if isinstance(obj, bool) or obj is None:
        return
    if isinstance(obj, (int, float)):
        out.add(float(obj))
    elif isinstance(obj, str):
        for m in _NUM.findall(obj):
            out.add(float(m))
    elif isinstance(obj, dict):
        for v in obj.values():
            _collect_numbers(v, out)
    elif isinstance(obj, (list, tuple, set)):
        for v in obj:
            _collect_numbers(v, out)


def grounding_check(text: str, evidence: dict, cases: list[dict], knowledge: list[dict] | None = None) -> dict:
    """Every number in the text must appear in the evidence or retrieved cases (allowing the usual
    rewrites: 0.38 <-> 38%, rounding). This catches invented scores/rooms; it cannot catch
    invented *claims*. That is why the prompt is also constrained and the UI shows the evidence."""
    allowed: set[float] = set()
    _collect_numbers(evidence, allowed)
    for c in cases:
        allowed.add(float(c.get("case_id", -1)))
        _collect_numbers([c.get("title"), c.get("summary"), c.get("faculty_comment")], allowed)
    for k in knowledge or []:
        _collect_numbers([k.get("title"), k.get("text")], allowed)
    pct = {a * 100 for a in list(allowed) if 0 <= abs(a) <= 1}
    bad = []
    for m in _NUM.findall(text):
        x = float(m)
        # a fraction shown as a whole percent may be off by up to half a point (0.695 -> 70%); anything else must match
        ok = any(abs(x - a) <= max(0.006, 0.006 * abs(a)) for a in allowed) or any(abs(x - a) <= 0.5 + 1e-9 for a in pct)
        if not ok:
            bad.append(m)
    return {"checked": True, "passed": not bad, "unverified_terms": sorted(set(bad))}


def case_ref(case_id) -> str:
    return f"CASE-{case_id}"


def evidence_json(evidence: dict, cases: list[dict], knowledge: list[dict] | None = None) -> str:
    slim = [{"id": case_ref(c["case_id"]), "title": c["title"], "summary": c["summary"],
             "resolution": c["resolution"], "faculty_comment": c.get("faculty_comment"),
             "similarity": c.get("similarity"), "origin": c.get("origin")} for c in cases]
    kb = [{"id": k["id"], "title": k["title"], "text": k["text"]} for k in knowledge or []]
    return json.dumps({"evidence": evidence, "similar_verified_cases": slim, "knowledge_passages": kb},
                      indent=2, default=str)


# ---- validation of a structured explanation ---------------------------------------------------------------

LIKELIHOODS = ("more likely", "possible", "unlikely")
_ACCUSATION = re.compile(r"\b(cheat\w*|guilty|fraud\w*|dishonest\w*|lied|lying|liar)\b", re.I)


def evidence_refs(evidence: dict) -> set[str]:
    """Names an explanation may cite as evidence: any top-level field, a score driver's feature name, or
    score_drivers.<feature>."""
    refs = {k.lower() for k in evidence}
    for d in evidence.get("score_drivers", []):
        refs.add(d["feature"].lower())
        refs.add(f"score_drivers.{d['feature']}".lower())
    return refs


def validate_reasoning(*, texts: list[str], hypotheses: list[dict], checks: list[str], used_case_ids: list[int],
                       used_kb_ids: list[str], evidence: dict, cases: list[dict], knowledge: list[dict] | None) -> dict:
    """Mechanical checks that an explanation stays inside what it was given. It cannot prove a sentence is true; it
    makes sure every cause it offers points at evidence and sources that exist, never accuses anyone, and never cites
    something that was not retrieved."""
    problems: list[str] = []
    refs = evidence_refs(evidence)
    kb_ids = {k["id"] for k in knowledge or []}
    case_ids = {case_ref(c["case_id"]) for c in cases}
    sources_ok = kb_ids | case_ids
    for i, h in enumerate(hypotheses, 1):
        name = f"hypothesis {i}"
        if h.get("likelihood") not in LIKELIHOODS:
            problems.append(f"{name}: likelihood must be one of {', '.join(LIKELIHOODS)}")
        ev, src = h.get("evidence") or [], h.get("sources") or []
        if not ev and not src:
            problems.append(f"{name} cites no evidence and no source")
        for e in ev:
            if str(e).strip().lower() not in refs:
                problems.append(f"{name} cites evidence field {e!r} which does not exist")
        for sref in src:
            if str(sref).strip() not in sources_ok:
                problems.append(f"{name} cites source {sref!r} which was not provided")
    for cid in used_case_ids:
        if case_ref(cid) not in case_ids:
            problems.append(f"used case {cid} was not retrieved")
    for kid in used_kb_ids:
        if kid not in kb_ids:
            problems.append(f"used passage {kid!r} was not retrieved")
    for t in [*texts, *checks]:
        m = _ACCUSATION.search(t or "")
        if m:
            problems.append(f"accusatory wording {m.group(0)!r}: the explainer describes evidence and never judges intent")
    return {"checked": True, "passed": not problems, "problems": sorted(set(problems))}


def resolve_sources(cited: list[str], cases: list[dict], knowledge: list[dict] | None) -> list[dict]:
    """Turn cited ids into the passages/cases themselves, for display next to the explanation."""
    kb = {k["id"]: k for k in knowledge or []}
    cs = {case_ref(c["case_id"]): c for c in cases}
    out = []
    for ref in dict.fromkeys(cited):
        if ref in kb:
            out.append({"id": ref, "kind": "knowledge", "title": kb[ref]["title"], "text": kb[ref]["text"]})
        elif ref in cs:
            c = cs[ref]
            out.append({"id": ref, "kind": "similar_case", "title": c["title"], "text": c["summary"],
                        "resolution": c["resolution"]})
    return out
