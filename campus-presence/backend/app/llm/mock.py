"""Deterministic offline explanation built only from the structured evidence."""
from __future__ import annotations

from .base import Explanation, LLMProvider, grounding_check


def _pct(v) -> str:
    return f"{round(float(v) * 100)}%"


class MockLLMProvider(LLMProvider):
    name = "mock"

    async def explain(self, evidence: dict, retrieved_cases: list[dict]) -> Explanation:
        return self.explain_sync(evidence, retrieved_cases)

    def explain_sync(self, evidence: dict, cases: list[dict]) -> Explanation:
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
        text += " This explains the system's evidence; it does not decide whether the student was present, and faculty review is recommended."
        points = [r[0].upper() + r[1:] for r in reasons]
        exp = Explanation(text=text, key_points=points,
                          recommended_action="Faculty review of the evidence; no automatic action.",
                          provider="mock", model=None, used_case_ids=used)
        exp.grounding = grounding_check(text, evidence, cases)
        return exp
