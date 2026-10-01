"""Lightweight local RAG over verified cases (no external service, no neural embeddings).

Each verified case and each new anomaly is turned into a bag of deterministic *tags*
(e.g. `rule_ble_wifi_contradiction`, `wifi_conf_low`, `room_adjacent`) plus its narrative; retrieval is
TF-IDF cosine similarity. This is lexical + structured matching, not semantic search, and it is
easy to swap for sentence-transformers/FAISS later.

RAG is NOT training: adding a case changes what gets retrieved for the LLM prompt. It never
changes the Isolation Forest or the rules. (Retraining the forest is a separate, explicit step:
`python ml/retrain_anomaly_model.py`.)
"""
from __future__ import annotations

import json
import threading
import time
from typing import Callable

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .db import session_scope
from .models import VerifiedCase

SEED_CASES = [
    {
        "title": "Wi-Fi predicted the adjacent room because one AP was weak",
        "summary": ("BLE marker said the student was in the session room. Wi-Fi predicted the neighbouring room "
                    "with low confidence because one access point briefly had an unusually weak signal."),
        "tags": ["rule_ble_wifi_contradiction", "wifi_conf_low", "room_adjacent", "ap_instability", "isoforest_flagged"],
        "resolution": "false_positive",
        "comment": "Faculty confirmed the student was physically present in the room. Wi-Fi localization was unstable.",
    },
    {
        "title": "Classroom marker offline mid-session",
        "summary": ("The classroom BLE marker stopped advertising (battery). Students showed Wi-Fi and peer "
                    "evidence for the right room but no marker detections after the outage."),
        "tags": ["marker_missing", "short_presence", "peers_consistent", "isoforest_flagged"],
        "resolution": "false_positive",
        "comment": "Marker battery had died. Students were present; attendance corrected.",
    },
    {
        "title": "Same temporary token seen in three rooms",
        "summary": ("A student's rotating token was observed by several phones in a different room while the "
                    "student's own marker evidence was in the session room."),
        "tags": ["rule_token_reuse", "rule_abnormal_token_reuse", "token_reuse_multi_device", "isoforest_flagged"],
        "resolution": "confirmed_anomaly",
        "comment": "Confirmed relay of the token. Escalated per policy.",
    },
    {
        "title": "Impossible movement caused by a phone clock error",
        "summary": ("Two location estimates a few seconds apart in distant rooms implied an impossible speed. "
                    "The student's phone clock was minutes off."),
        "tags": ["rule_impossible_movement", "speed_extreme", "clock_skew_suspected"],
        "resolution": "false_positive",
        "comment": "Phone clock was wrong. No misconduct.",
    },
    {
        "title": "Several phones carried together",
        "summary": ("Four devices showed near-identical BLE signal patterns and very strong mutual proximity "
                    "for the whole session, with no movement between rooms."),
        "tags": ["device_cluster", "peer_rssi_very_strong", "isoforest_flagged"],
        "resolution": "confirmed_anomaly",
        "comment": "One person was carrying several students' phones.",
    },
    {
        "title": "Student left early",
        "summary": "BLE marker detected for only a short time at the start of the session, then no evidence.",
        "tags": ["short_presence", "isoforest_flagged"],
        "resolution": "false_positive",
        "comment": "Student left early with permission; short presence is expected.",
    },
    {
        "title": "Stale cached Wi-Fi scan",
        "summary": ("Android returned cached Wi-Fi scan results because of scan throttling, so the Wi-Fi zone lagged "
                    "behind the student's real position and disagreed with BLE."),
        "tags": ["rule_ble_wifi_contradiction", "wifi_conf_low", "wifi_cached_scan", "isoforest_flagged"],
        "resolution": "false_positive",
        "comment": "Throttled/cached Wi-Fi scan. Student was present.",
    },
    {
        "title": "Walked past the next classroom's door",
        "summary": "Markers of two neighbouring classrooms were seen within a few minutes as the student walked down the corridor.",
        "tags": ["rule_rapid_session_switching", "room_adjacent", "short_presence"],
        "resolution": "false_positive",
        "comment": "Corridor pass-by; attended the correct class.",
    },
]


def tags_from_snapshot(snap: dict, zone_distance: Callable[[str, str], float] | None = None) -> list[str]:
    tags: list[str] = []
    rules = snap.get("rules") or []
    for r in rules:
        tags.append(f"rule_{r['rule']}")
    iso = snap.get("isolation_forest")
    if iso and iso.get("flagged"):
        tags.append("isoforest_flagged")
    wifi, ble, peers = snap.get("wifi") or {}, snap.get("classroom_ble") or {}, snap.get("peers") or {}
    conf = wifi.get("confidence")
    if conf is not None:
        if conf < 0.3:
            tags.append("wifi_conf_very_low")
        if conf < 0.5:
            tags.append("wifi_conf_low")
    if wifi.get("sources") and "cached" in wifi["sources"]:
        tags.append("wifi_cached_scan")
    for r in rules:
        if r["rule"] == "ble_wifi_contradiction":
            d = r.get("data", {})
            a, b = d.get("ble_room"), d.get("wifi_room")
            if a and b and zone_distance is not None:
                try:
                    if zone_distance(a, b) <= 12.5:
                        tags.append("room_adjacent")
                except KeyError:
                    pass
        if r["rule"] in ("token_reuse", "abnormal_token_reuse") and (r.get("data", {}).get("token_reuse_count", 0) >= 3):
            tags.append("token_reuse_multi_device")
        if r["rule"] == "impossible_movement":
            tags.append("speed_extreme" if r.get("data", {}).get("speed_mps", 0) >= 10 else "speed_high")
        if r["rule"] == "rapid_session_switching" and zone_distance is not None:
            tags.append("room_adjacent")
    if ble and ble.get("coverage_fraction") is not None and 0 < ble["coverage_fraction"] < 0.3:
        tags.append("short_presence")
    if ble and not ble.get("detected") and (wifi.get("match_fraction", 0) >= 0.5 or peers.get("consistent_distinct", 0) > 0):
        tags.append("marker_missing")
        if peers.get("consistent_distinct", 0) > 0:
            tags.append("peers_consistent")
    if peers.get("strongest_rssi_dbm") is not None and peers["strongest_rssi_dbm"] >= -40:
        tags.append("peer_rssi_very_strong")
        if peers.get("observed_distinct", 0) >= 3:
            tags.append("device_cluster")
    return sorted(set(tags))


def case_document(tags: list[str], narrative: str = "") -> str:
    return " ".join(tags) + " " + narrative


class CaseIndex:
    def __init__(self, session_factory: sessionmaker[Session]):
        self._sf = session_factory
        self._lock = threading.Lock()
        self._vec: TfidfVectorizer | None = None
        self._matrix = None
        self._cases: list[dict] = []

    def seed_if_empty(self) -> int:
        with session_scope(self._sf) as db:
            if db.scalar(select(VerifiedCase.id).limit(1)) is not None:
                return 0
            for c in SEED_CASES:
                db.add(VerifiedCase(title=c["title"], summary=c["summary"],
                                    structured_json=json.dumps({"tags": c["tags"]}),
                                    resolution=c["resolution"], faculty_comment=c["comment"],
                                    origin="seed_demo", created_at=time.time()))
        self.rebuild()
        return len(SEED_CASES)

    def rebuild(self) -> int:
        with session_scope(self._sf) as db:
            rows = db.scalars(select(VerifiedCase).order_by(VerifiedCase.id)).all()
            cases = []
            for r in rows:
                s = json.loads(r.structured_json or "{}")
                cases.append({"case_id": r.id, "title": r.title, "summary": r.summary, "resolution": r.resolution,
                              "faculty_comment": r.faculty_comment, "origin": r.origin, "tags": s.get("tags", []),
                              "anomaly_id": r.anomaly_id})
        docs = [case_document(c["tags"], f"{c['title']} {c['summary']} {c.get('faculty_comment') or ''}")
                for c in cases]
        with self._lock:
            self._cases = cases
            if docs:
                self._vec = TfidfVectorizer(token_pattern=r"[A-Za-z0-9_]+", lowercase=True, sublinear_tf=True)
                self._matrix = self._vec.fit_transform(docs)
            else:
                self._vec, self._matrix = None, None
        return len(cases)

    def size(self) -> int:
        return len(self._cases)

    def retrieve(self, tags: list[str], text: str = "", k: int = 3, min_similarity: float = 0.08) -> list[dict]:
        with self._lock:
            if self._vec is None or not self._cases:
                return []
            q = self._vec.transform([case_document(tags, text)])
            sims = (self._matrix @ q.T).toarray().ravel()
            order = np.argsort(-sims)[:k]
            return [{**self._cases[i], "similarity": round(float(sims[i]), 3)}
                    for i in order if sims[i] >= min_similarity]
