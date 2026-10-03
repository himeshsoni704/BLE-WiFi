"""Deterministic offline explanation built only from the structured evidence."""
from __future__ import annotations

from .base import (Explanation, LLMProvider, case_ref, evidence_refs, grounding_check, resolve_sources,
                   validate_reasoning)


def _pct(v) -> str:
    return f"{round(float(v) * 100)}%"


# Candidate causes per rule: (kind, cause, knowledge ids, evidence fields, a check faculty can make). `kind` is
# "benign" or "concern"; both are offered when the evidence supports both. Sources/evidence that are not actually
# available for this record are dropped, so every hypothesis cites only what exists.
_BLEWIFI = [
    ("benign", "The Wi-Fi room estimate was wrong or out of date (a weak access point, a cached scan, or an unsurveyed room)",
     ["KB-CAUSE-WIFI", "KB-RULE-BLEWIFI"], ["bluetooth_wifi_mismatch", "wifi_confidence"],
     "Check whether the access points near the session room were working and whether the phone's Wi-Fi scans were cached."),
    ("concern", "The phone was not in the session room for part of the session, as the Wi-Fi readings suggest",
     ["KB-RULE-BLEWIFI"], ["bluetooth_wifi_mismatch"],
     "Compare the Bluetooth and Wi-Fi timelines in the Evidence Explorer."),
]
_TOKEN = [
    ("concern", "The student's temporary token was heard by devices in other rooms, which a forwarded token could cause",
     ["KB-RULE-TOKEN", "KB-LIMIT-RELAY"], ["token_reuse_count", "rules_triggered"],
     "Look at which rooms the other devices were in and when they heard the token."),
]
RULE_CAUSES = {
    "ble_wifi_contradiction": _BLEWIFI,
    "token_reuse": _TOKEN, "abnormal_token_reuse": _TOKEN,
    "impossible_movement": [
        ("benign", "A phone clock error made two detections look closer in time than they were",
         ["KB-CAUSE-CLOCK", "KB-RULE-MOVE"], ["movement_speed_mps", "rules_triggered"],
         "Check the phone's clock settings and the times of the two detections."),
        ("concern", "A second device or person was using the same identity in the other room",
         ["KB-RULE-MOVE"], ["movement_speed_mps", "rules_triggered"],
         "Compare the rooms and times of the two detections."),
    ],
    "rapid_session_switching": [
        ("benign", "The student walked past neighbouring classrooms on the way to the right one",
         ["KB-RULE-SWITCH"], ["rules_triggered", "ble_other_rooms_detected"],
         "Check whether the other classrooms are next to the session room."),
    ],
}

# Candidate causes suggested by what drove the forest's score, for records with no rule hit.
FEATURE_CAUSES = {
    "ble_duration": ("benign", "The student arrived late or left early, or the session had only just begun",
                     ["KB-CAUSE-EARLY"], ["ble_duration_s"], "Ask whether the student arrived late or left early."),
    "mean_ble_rssi": ("benign", "The classroom marker was not heard, for example because it stopped broadcasting",
                      ["KB-CAUSE-MARKER"], ["ble_rssi_dbm"], "Check the classroom marker device and its battery."),
    "signal_consistency": ("benign", "Wi-Fi and Bluetooth disagreed because of an unreliable Wi-Fi estimate",
                           ["KB-CAUSE-WIFI"], ["wifi_confidence", "wifi_room"], "Check the Wi-Fi scans for this session."),
    "wifi_confidence": ("benign", "The Wi-Fi model was unsure of the room", ["KB-CAUSE-WIFI"],
                        ["wifi_confidence"], "Check the Wi-Fi scans for this session."),
    "rssi_twin_distance": ("concern", "Two phones moved together for much of the session, as if carried by one person",
                           ["KB-CAUSE-TWINS"], ["peers_distinct"], "Check whether the closest phone also shows its own evidence."),
    "peer_rssi_max": ("concern", "Another phone stayed very close to this one", ["KB-CAUSE-TWINS", "KB-LIMIT-RSSI"],
                      ["peers_distinct"], "Check whether the closest phone also shows its own evidence."),
    "token_reuse_count": ("concern", "The student's temporary token was heard by devices in other rooms",
                          ["KB-RULE-TOKEN", "KB-IF-003"], ["token_reuse_count"], "Look at which rooms the other devices were in."),
    "estimated_speed": ("benign", "A phone clock error made two detections look closer in time than they were",
                        ["KB-CAUSE-CLOCK"], ["movement_speed_mps"], "Check the phone's clock settings."),
    "session_switch_count": ("benign", "The student walked past neighbouring classrooms", ["KB-RULE-SWITCH"],
                             ["ble_other_rooms_detected"], "Check whether the other classrooms are next to the session room."),
}


def _lean(cases: list[dict]) -> str | None:
    """Which way the most similar verified cases were resolved: 'benign', 'concern', or None if no clear lean."""
    w = {"benign": 0.0, "concern": 0.0}
    for c in cases[:3]:
        w["benign" if c["resolution"] == "false_positive" else "concern"] += float(c.get("similarity") or 0.0)
    if abs(w["benign"] - w["concern"]) < 0.05:
        return None
    return "benign" if w["benign"] > w["concern"] else "concern"


def build_reasoning(evidence: dict, cases: list[dict], knowledge: list[dict] | None) -> tuple[list[dict], list[str]]:
    kb_ids = {k["id"] for k in knowledge or []}
    refs = evidence_refs(evidence)
    lean = _lean(cases)
    case_srcs = [case_ref(c["case_id"]) for c in cases[:1]]
    cands = []
    for r in evidence.get("rules_triggered", []):
        cands += RULE_CAUSES.get(r["rule"], [])
    drivers = evidence.get("score_drivers", [])
    if not cands:
        for d in drivers:
            if d["feature"] in FEATURE_CAUSES:
                cands.append(FEATURE_CAUSES[d["feature"]])
    seen, hyps, checks = set(), [], []
    for kind, cause, kb, fields, check in cands:
        if cause in seen:
            continue
        seen.add(cause)
        ev = [f for f in fields if f.lower() in refs]
        ev += [f"score_drivers.{d['feature']}" for d in drivers if f"score_drivers.{d['feature']}".lower() in refs][:1]
        src = [k for k in kb if k in kb_ids] + case_srcs
        if not ev and not src:
            continue                                   # never offer a cause with nothing behind it
        likelihood = "possible" if lean is None else ("more likely" if kind == lean else "possible")
        because = ("The evidence is consistent with this cause" + (
            f", and the most similar past case was resolved as {cases[0]['resolution'].replace('_', ' ')}"
            if cases and lean == kind else "") + ".")
        hyps.append({"cause": cause, "likelihood": likelihood, "because": because, "evidence": ev, "sources": src,
                     "_kind": kind})
        if check not in checks:
            checks.append(check)
    hyps.sort(key=lambda h: 0 if h["likelihood"] == "more likely" else 1)
    for h in hyps:
        h.pop("_kind")
    return hyps[:4], checks[:3]


def driver_sentence(evidence: dict) -> str:
    parts = []
    for d in evidence.get("score_drivers", []):
        shown = d.get("meaning_of_value") or f"{d['value']}"
        parts.append(f"{d['label']} ({shown}; typical {d['typical']}; {d['share_of_unusualness_pct']}% of the unusualness)")
    return ("The features that pushed the Isolation Forest's score the most were: " + "; ".join(parts) + ".") if parts else ""


class MockLLMProvider(LLMProvider):
    name = "mock"

    async def explain(self, evidence: dict, retrieved_cases: list[dict],
                      knowledge: list[dict] | None = None) -> Explanation:
        return self.explain_sync(evidence, retrieved_cases, knowledge)

    def explain_sync(self, evidence: dict, cases: list[dict], knowledge: list[dict] | None = None) -> Explanation:
        reasons: list[str] = []
        rules = {r["rule"]: r for r in evidence.get("rules_triggered", [])}
        mm = evidence.get("bluetooth_wifi_mismatch")
        if mm:
            conf = evidence.get("wifi_confidence")
            reasons.append(f"the BLE classroom marker indicated Room {mm['ble_room']} while the Wi-Fi fingerprint "
                           f"indicated Room {mm['wifi_room']}" + (f" (Wi-Fi confidence {_pct(conf)})" if conf is not None else ""))
        n = evidence.get("token_reuse_count", 0)
        if "token_reuse" in rules or "abnormal_token_reuse" in rules:
            reasons.append(f"the same temporary token was observed by {n} other device(s) outside the expected room")
        if "impossible_movement" in rules:
            sp = evidence.get("movement_speed_mps")
            reasons.append("the estimated movement between locations implies an implausible speed"
                           + (f" ({sp} m/s)" if sp is not None else ""))
        if "rapid_session_switching" in rules:
            reasons.append("markers from several classrooms were detected within a few minutes")
        if not reasons and evidence.get("isolation_flagged"):
            bits = []
            if evidence.get("ble_duration_s") is not None:
                bits.append(f"BLE presence of {evidence['ble_duration_s']} s")
            if evidence.get("wifi_confidence") is not None:
                bits.append(f"Wi-Fi confidence {_pct(evidence['wifi_confidence'])}")
            reasons.append("the combination of signals" + (f" ({', '.join(bits)})" if bits else "")
                           + " is unusual compared with the training data")
        if not reasons:
            reasons.append("it did not match the expected pattern of evidence for this session")

        text = "This record was flagged because " + "; and ".join(reasons) + "."
        iso = evidence.get("isolation_score")
        if iso is not None and evidence.get("isolation_flagged"):
            text += (f" Isolation Forest scored it {iso} (more negative is more unusual), which marks an unusual "
                     "combination of signals, not a decision about attendance.")
        used: list[int] = []
        if cases:
            c = cases[0]
            used.append(c["case_id"])
            text += (f" It resembles verified case #{c['case_id']} ({c['title']}), which was resolved as "
                     f"{c['resolution'].replace('_', ' ')}; this is a similarity, not proof of the same cause.")
        drivers = driver_sentence(evidence)
        if drivers:
            text += " " + drivers
        text += " This explains the system's evidence; it does not decide whether the student was present, and faculty review is recommended."
        points = [r[0].upper() + r[1:] for r in reasons]
        hyps, checks = build_reasoning(evidence, cases, knowledge)
        cited = [x for h in hyps for x in h["sources"]]
        exp = Explanation(text=text, key_points=points,
                          recommended_action="Faculty review of the evidence; no automatic action.",
                          provider="mock", model=None, used_case_ids=used, hypotheses=hyps, checks=checks,
                          used_kb_ids=[x for x in dict.fromkeys(cited) if not x.startswith("CASE-")],
                          sources=resolve_sources(cited, cases, knowledge))
        everything = [text, *points, *(f"{h['cause']} {h['because']}" for h in hyps), *checks]
        exp.grounding = grounding_check(" ".join(everything), evidence, cases, knowledge)
        exp.validation = validate_reasoning(texts=everything, hypotheses=hyps, checks=checks, used_case_ids=used,
                                            used_kb_ids=exp.used_kb_ids, evidence=evidence, cases=cases, knowledge=knowledge)
        return exp
