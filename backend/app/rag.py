"""Small local RAG over verified anomaly cases (brief section 20).

RAG is NOT model training -- this never touches the Isolation Forest or
the deterministic rules. It only retrieves past cases whose ISSUE TEXT
resembles a new anomaly's, so the LLM explanation (app/llm.py) can say
"this resembles a previously verified X" when that's actually true.

Stack: scikit-learn's TfidfVectorizer + cosine similarity, not ChromaDB /
sentence-transformers. This is a deliberate hackathon-scale choice per the
brief's own section-54 guidance ("if a dependency fails, don't redesign --
use the simplest replacement... sklearn cosine similarity"): ChromaDB and
sentence-transformers (which pulls in torch) are heavy, slow-to-install
dependencies for what is, at hackathon scale, a corpus of a few dozen to a
few hundred short case descriptions -- TF-IDF needs nothing extra (sklearn
is already a dependency) and is more than adequate at that scale.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


@dataclass(frozen=True)
class VerifiedCase:
    case_id: int | None
    case_type: str
    issue: str
    resolution: str
    features: dict | None = None


@dataclass(frozen=True)
class RetrievedCase:
    case: VerifiedCase
    similarity: float

    def to_dict(self) -> dict:
        return {
            "case_id": self.case.case_id, "case_type": self.case.case_type,
            "issue": self.case.issue, "resolution": self.case.resolution,
            "similarity": round(self.similarity, 3),
        }


def anomaly_query_text(evidence: dict, reasons: list[str]) -> str:
    """Turns structured anomaly evidence (the brief's own JSON shape, e.g.
    {"ble_room": "204", "wifi_room": "205", "wifi_confidence": 0.38, ...})
    plus the rule-engine's reasons into a short natural-language query, so
    it can be TF-IDF-matched against stored cases' free-text `issue` field."""
    parts = list(reasons)
    ble_room, wifi_room = evidence.get("ble_room"), evidence.get("wifi_room")
    if ble_room and wifi_room and ble_room != wifi_room:
        parts.append(f"BLE and Wi-Fi disagree about the room ({ble_room} vs {wifi_room})")
    if evidence.get("token_reuse_count", 0):
        parts.append("the same token observed from multiple devices")
    if evidence.get("movement_speed", 0) and evidence["movement_speed"] > 2.5:
        parts.append("movement speed implausibly high")
    if evidence.get("wifi_confidence") is not None and evidence["wifi_confidence"] < 0.5:
        parts.append("low Wi-Fi fingerprint confidence")
    return ". ".join(parts) if parts else "unexplained anomaly"


class CaseRetriever:
    """Rebuilds its TF-IDF index on every add_case() call. Simple, and
    cheap at the case counts this demo will ever hold (TF-IDF needs the
    whole corpus to compute IDF weights anyway, so incremental updates
    buy nothing meaningful at this scale)."""

    def __init__(self):
        self._cases: list[VerifiedCase] = []
        self._vectorizer: TfidfVectorizer | None = None
        self._matrix = None

    def index(self, cases: list[VerifiedCase]) -> None:
        self._cases = list(cases)
        self._reindex()

    def add_case(self, case: VerifiedCase) -> None:
        self._cases.append(case)
        self._reindex()

    def _reindex(self) -> None:
        if not self._cases:
            self._vectorizer, self._matrix = None, None
            return
        texts = [f"{c.issue} {c.resolution}" for c in self._cases]
        self._vectorizer = TfidfVectorizer(stop_words="english", min_df=1)
        self._matrix = self._vectorizer.fit_transform(texts)

    def retrieve(self, query_text: str, k: int = 3, min_similarity: float = 0.05) -> list[RetrievedCase]:
        if self._vectorizer is None or self._matrix is None or not query_text.strip():
            return []
        q = self._vectorizer.transform([query_text])
        sims = cosine_similarity(q, self._matrix)[0]
        order = np.argsort(-sims)[:k]
        return [RetrievedCase(self._cases[i], float(sims[i])) for i in order if sims[i] >= min_similarity]
