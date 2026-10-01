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

    def to_dict(self) -> dict:
        return asdict(self)


class LLMProvider(ABC):
    name = "base"

    @abstractmethod
    async def explain(self, evidence: dict, retrieved_cases: list[dict]) -> Explanation:
        """`evidence` is the reduced structured view from evidence_view(); never raw observations."""


def evidence_view(anomaly_snapshot: dict, *, student_label: str, expected_room: str, session_code: str,
                  state: str, risk_demo: float | None, if_raw: float | None, if_flag: bool,
                  rules: list[dict]) -> dict:
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


def grounding_check(text: str, evidence: dict, cases: list[dict]) -> dict:
    """Every number in the text must appear in the evidence or retrieved cases (allowing the usual
    rewrites: 0.38 <-> 38%, rounding). This catches invented scores/rooms; it cannot catch
    invented *claims*. That is why the prompt is also constrained and the UI shows the evidence."""
    allowed: set[float] = set()
    _collect_numbers(evidence, allowed)
    for c in cases:
        allowed.add(float(c.get("case_id", -1)))
        _collect_numbers([c.get("title"), c.get("summary"), c.get("faculty_comment")], allowed)
    pct = {a * 100 for a in list(allowed) if 0 <= abs(a) <= 1}
    bad = []
    for m in _NUM.findall(text):
        x = float(m)
        ok = any(abs(x - a) <= max(0.006, 0.006 * abs(a)) for a in allowed | pct)
        if not ok:
            bad.append(m)
    return {"checked": True, "passed": not bad, "unverified_terms": sorted(set(bad))}


def evidence_json(evidence: dict, cases: list[dict]) -> str:
    slim = [{"case_id": c["case_id"], "title": c["title"], "summary": c["summary"],
             "resolution": c["resolution"], "faculty_comment": c.get("faculty_comment"),
             "similarity": c.get("similarity"), "origin": c.get("origin")} for c in cases]
    return json.dumps({"evidence": evidence, "similar_verified_cases": slim}, indent=2, default=str)
