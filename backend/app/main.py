"""FastAPI app. Run with:  uvicorn app.main:create_app --factory"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import time
from pathlib import Path
from typing import Annotated, Callable

import numpy as np
from fastapi import Depends, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool

from .anomaly_iforest import AnomalyDetector
from .config import Settings
from .engine import DuplicateStudent, Engine, ReportData, UnknownScanner
from .fusion import State
from .llm import get_llm_provider
from .owner_model import ModelRegistry
from .presence import PresenceOrchestrator, UnknownAnomaly
from .rag import anomaly_query_text
from .schemas import (
    BleObservationIn, BssidIn, ClassroomIn, DemoInjectIn, ExplainAnomalyIn, FaceScanIn,
    FeedbackIn, PeerObservationIn, PresenceIn, RagRetrieveIn, ReportIn, RfidIn, ScanBatchIn,
    ScanIn, ScannerIn, SessionIn, SimulationStartIn, StudentIn, WifiObservationIn,
)
from .store import ClassroomRow, SessionRow, Store, normalize_bssid
from .wifi_knn import WifiFingerprintModel

log = logging.getLogger(__name__)


def _load_optional(model_dir: str, filename: str, loader):
    path = Path(model_dir) / filename
    if not path.exists():
        return None
    try:
        return loader(path)
    except Exception as exc:   # a corrupt/incompatible model file must never prevent the app from starting
        log.warning("could not load %s (%s: %s); continuing without it", path, type(exc).__name__, exc)
        return None


def create_app(settings: Settings | None = None, clock: Callable[[], float] = time.time) -> FastAPI:
    settings = settings or Settings.from_env()
    engine = Engine(Store(settings.db_path), ModelRegistry(settings.model_dir), settings, clock)
    engine.purge_old_data()
    app = FastAPI(title="Proof-of-Presence: BLE + Wi-Fi campus attendance")
    app.state.engine = engine

    wifi_model = _load_optional(settings.model_dir, "wifi_localization.joblib", WifiFingerprintModel.load)
    anomaly_detector = _load_optional(settings.model_dir, "isolation_forest.joblib", AnomalyDetector.load)
    orch = PresenceOrchestrator(engine, get_llm_provider(), wifi_model, anomaly_detector)
    app.state.orchestrator = orch

    def key_ok(supplied: str | None) -> bool:
        return settings.api_key is None or (
            supplied is not None and hmac.compare_digest(supplied.encode(), settings.api_key.encode())
        )

    def require_key(x_api_key: Annotated[str | None, Header()] = None) -> None:
        if not key_ok(x_api_key):
            raise HTTPException(401, "missing or invalid X-API-Key")

    auth = [Depends(require_key)]

    @app.get("/health")
    def health() -> dict:
        return {"ok": True}

    # ---- setup -------------------------------------------------------------

    @app.post("/students", status_code=201, dependencies=auth)
    def create_student(body: StudentIn) -> dict:
        """Returns the token secret once; provision it into the student's app."""
        try:
            return {"student_id": body.student_id, "secret": engine.enroll_student(body.student_id, body.name)}
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        except DuplicateStudent:
            raise HTTPException(409, "student already exists")

    @app.get("/students", dependencies=auth)
    def list_students() -> list[dict]:
        return [{"student_id": sid, "name": name, "has_model": engine.has_model(sid)}
                for sid, name in engine.store.students()]

    @app.put("/scanners/{scanner_id}", dependencies=auth)
    def put_scanner(scanner_id: str, body: ScannerIn) -> dict:
        engine.store.upsert_scanner(scanner_id, body.zone, body.min_rssi)
        return {"scanner_id": scanner_id, "zone": body.zone, "min_rssi": body.min_rssi}

    @app.put("/bssids/{bssid}", dependencies=auth)
    def put_bssid(bssid: str, body: BssidIn) -> dict:
        try:
            key = normalize_bssid(bssid)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        engine.store.upsert_bssid(key, body.zone)
        return {"bssid": key, "zone": body.zone}

    # ---- ingestion ---------------------------------------------------------

    @app.post("/scan", dependencies=auth)
    def post_scan(body: ScanIn) -> dict:
        return post_scans(ScanBatchIn(scanner_id=body.scanner_id, scans=[body]))

    @app.post("/scan/batch", dependencies=auth)
    def post_scans(body: ScanBatchIn) -> dict:
        try:
            result = engine.ingest_scans(body.scanner_id, ((s.token, s.rssi, s.ts) for s in body.scans))
        except UnknownScanner:
            raise HTTPException(404, f"unknown scanner {body.scanner_id!r}; register it with PUT /scanners/{{id}}")
        return {"accepted": result.accepted, "unknown_token": result.unknown_token,
                "bad_timestamp": result.bad_timestamp}

    @app.post("/device-report", dependencies=auth)
    def post_report(body: ReportIn) -> dict:
        data = ReportData(
            token=body.token,
            ts=body.ts,
            wifi_bssid=body.wifi.bssid if body.wifi else None,
            wifi_rssi=body.wifi.rssi if body.wifi else None,
            interacting=body.interacting,
        )
        if body.window:
            data.accel = np.asarray(body.window.accel, dtype=float)
            data.gyro = np.asarray(body.window.gyro, dtype=float)
            data.fs = body.window.fs
        result = engine.ingest_report(data)
        if result is None:
            raise HTTPException(401, "unrecognised token or implausible timestamp")
        return {
            "student_id": result.student_id,
            "wifi_zone": result.wifi_zone,
            "moving": result.moving,
            "window_score": result.window_score,
            "owner_score": result.owner_score,
        }

    # ---- views -------------------------------------------------------------

    @app.get("/status/{student_id}", dependencies=auth)
    def status(student_id: str, at: float | None = None) -> dict:
        if student_id not in engine.store.student_ids():
            raise HTTPException(404, "unknown student")
        return {"student_id": student_id, **engine.verdicts_at(at, [student_id])[student_id].to_dict()}

    @app.get("/zones", dependencies=auth)
    def zones(at: float | None = None) -> dict:
        return engine.zone_snapshot(at)

    def _period(start: float | None, end: float | None, default_len: float) -> tuple[float, float]:
        end = clock() if end is None else end
        return (end - default_len if start is None else start), end

    def _dwell(start, end, zone, default_len) -> tuple[float, float, dict]:
        start, end = _period(start, end, default_len)
        try:
            return start, end, engine.dwell(start, end, zone or None)
        except ValueError as exc:
            raise HTTPException(422, str(exc))

    @app.get("/rollcall", dependencies=auth)
    def rollcall(
        zone: Annotated[list[str] | None, Query()] = None,
        start: float | None = None,
        end: float | None = None,
    ) -> dict:
        """One overall state per registered student over the window (default: last 30 min)."""
        start, end, result = _dwell(start, end, zone, 30 * 60)
        names = dict(engine.store.students())
        by_state: dict[str, list[dict]] = {st.value: [] for st in State}
        for sid, d in sorted(result.items()):
            by_state[d.state.value].append({"student_id": sid, "name": names[sid], **d.to_dict()})
        return {
            "start": start, "end": end, "zones": zone or "all",
            "total": len(result),
            "summary": {k: len(v) for k, v in by_state.items()},
            "students": by_state,
        }

    @app.get("/attendance", dependencies=auth)
    def attendance(
        zone: Annotated[list[str], Query(min_length=1)],
        start: float,
        end: float,
    ) -> dict:
        """Per-student presence for a class period; `counted` needs ~70% dwell."""
        _, _, result = _dwell(start, end, zone, 0)
        names = dict(engine.store.students())
        rows = [{"student_id": sid, "name": names[sid], **d.to_dict()} for sid, d in sorted(result.items())]
        return {
            "start": start, "end": end, "zones": zone,
            "counted": sum(r["counted"] for r in rows),
            "flagged": sum(r["flagged"] for r in rows),
            "total": len(rows),
            "students": rows,
        }

    # ---- Proof-of-Presence extension: classrooms / sessions ----------------

    @app.put("/classrooms/{classroom_id}", dependencies=auth)
    def put_classroom(classroom_id: str, body: ClassroomIn) -> dict:
        engine.store.upsert_classroom(ClassroomRow(
            classroom_id, body.name, body.building, body.floor, body.x, body.y,
            body.ble_marker_id, body.display_name))
        if body.ble_marker_id:
            engine.store.upsert_scanner(body.ble_marker_id, classroom_id, None)
        return {"classroom_id": classroom_id, **body.model_dump()}

    @app.get("/classrooms", dependencies=auth)
    def list_classrooms() -> list[dict]:
        return [{"classroom_id": cid, "name": c.name, "building": c.building, "floor": c.floor,
                "x": c.x, "y": c.y, "ble_marker_id": c.ble_marker_id, "display_name": c.display_name}
                for cid, c in engine.store.classrooms().items()]

    @app.put("/sessions/{session_id}", dependencies=auth)
    def put_session(session_id: str, body: SessionIn) -> dict:
        if body.classroom_id not in engine.store.classrooms():
            raise HTTPException(404, f"unknown classroom {body.classroom_id!r}; register it with "
                                     f"PUT /classrooms/{{id}} first")
        engine.store.upsert_session(SessionRow(session_id, body.course, body.classroom_id,
                                               body.start_ts, body.end_ts))
        return {"session_id": session_id, **body.model_dump()}

    @app.get("/sessions", dependencies=auth)
    def list_sessions(active_at: float | None = None) -> list[dict]:
        return [{"session_id": s.session_id, "course": s.course, "classroom_id": s.classroom_id,
                "start_ts": s.start_ts, "end_ts": s.end_ts} for s in engine.store.sessions(active_at)]

    # ---- Proof-of-Presence extension: evidence ingestion ---------------------

    @app.post("/ble-observation", dependencies=auth)
    def post_ble_observation(body: BleObservationIn) -> dict:
        """Brief section 5: a classroom BLE marker sighting. Delegates to
        the same token-resolution/scan-ingestion path as /scan -- the
        marker_id must already be registered (PUT /classrooms with a
        ble_marker_id, or PUT /scanners/{id} directly)."""
        try:
            result = engine.ingest_scans(body.marker_id, [(body.token, body.rssi, body.ts)])
        except UnknownScanner:
            raise HTTPException(404, f"unknown marker {body.marker_id!r}; register its classroom with "
                                     f"PUT /classrooms/{{id}} (ble_marker_id) first")
        if result.unknown_token:
            raise HTTPException(401, "unrecognised token")
        return {"accepted": True}

    @app.post("/peer-observation", dependencies=auth)
    def post_peer_observation(body: PeerObservationIn) -> dict:
        """Brief section 4: student-to-student BLE proximity."""
        ts = body.ts if body.ts is not None else clock()
        if engine.tokens.resolve(body.observer_token, ts) is None:
            raise HTTPException(401, "unrecognised observer token")
        observed_sid = orch.ingest_peer_observation(body.observer_token, body.observed_token,
                                                     body.rssi, body.duration, ts)
        return {"accepted": True, "observed_student_known": observed_sid is not None}

    @app.post("/wifi-observation", dependencies=auth)
    def post_wifi_observation(body: WifiObservationIn) -> dict:
        ts = body.ts if body.ts is not None else clock()
        sid = engine.tokens.resolve(body.token, ts)
        if sid is None:
            raise HTTPException(401, "unrecognised token")
        if orch.wifi_model is not None and orch.wifi_model.is_trained:
            prediction = orch.wifi_model.predict(body.fingerprint)
        else:
            prediction = None
        if body.classroom_id:
            orch.ingest_wifi_sample(body.classroom_id, body.fingerprint, source="live")
        return {
            "student_id": sid,
            "predicted_zone": prediction.zone if prediction else None,
            "confidence": prediction.confidence if prediction else None,
            "model_available": prediction is not None,
        }

    @app.post("/presence", dependencies=auth)
    def post_presence(body: PresenceIn) -> dict:
        """Brief's main presence-update endpoint: gathers BLE/Wi-Fi/peer/
        face/RFID evidence, scores attendance, and runs the anomaly
        pipeline against that SAME evidence, in one call."""
        ts = body.ts if body.ts is not None else clock()
        sid = engine.tokens.resolve(body.token, ts)
        if sid is None:
            raise HTTPException(401, "unrecognised token")
        if body.classroom_id not in engine.store.classrooms():
            raise HTTPException(404, f"unknown classroom {body.classroom_id!r}")
        evidence, anomaly = orch.process_presence(sid, body.session_id, body.classroom_id, ts,
                                                   body.wifi_fingerprint, source="live")
        return {"student_id": sid, "evidence": evidence.to_dict(),
               "anomaly": {"is_anomalous": anomaly.is_anomalous, "anomaly_id": anomaly.anomaly_id,
                          "severity": anomaly.severity, "reasons": anomaly.reasons}}

    @app.post("/face-scan", dependencies=auth)
    def post_face_scan(body: FaceScanIn) -> dict:
        """Brief section 10: optional interop with an existing face-scan
        attendance system. student_id here is the university's own id, not
        a BLE token -- this endpoint trusts the caller's authentication
        (the face-scan system itself), not a student-held secret."""
        if body.student_id not in engine.store.student_ids():
            raise HTTPException(404, "unknown student")
        engine.store.add_face_scan(body.student_id, body.ts if body.ts is not None else clock(),
                                   body.match, body.confidence)
        return {"accepted": True}

    @app.post("/rfid", dependencies=auth)
    def post_rfid(body: RfidIn) -> dict:
        """Brief section 11: optional RFID/NFC reader interop."""
        ts = body.ts if body.ts is not None else clock()
        sid = engine.tokens.resolve(body.token, ts) if body.token else None
        engine.store.add_rfid_read(sid, body.reader, ts)
        return {"accepted": True, "student_id": sid}

    # ---- Proof-of-Presence extension: views -----------------------------------

    @app.get("/evidence/{student_id}", dependencies=auth)
    def get_evidence(student_id: str, at: float | None = None, history_s: float | None = None) -> dict:
        if student_id not in engine.store.student_ids():
            raise HTTPException(404, "unknown student")
        at = at if at is not None else clock()
        latest = engine.store.latest_evidence(student_id, at)
        out = {"student_id": student_id, "latest": None, "history": []}
        if latest is not None:
            out["latest"] = {"ts": latest.ts, "session_id": latest.session_id,
                             "classroom_id": latest.classroom_id, "source": latest.source,
                             **json.loads(latest.breakdown)}
        if history_s:
            out["history"] = [
                {"ts": r.ts, "score": r.score, "state": r.state, "source": r.source}
                for r in engine.store.evidence_history(student_id, at - history_s, at)
            ]
        return out

    def _locations_snapshot(at: float | None = None) -> dict:
        at = at if at is not None else clock()
        classrooms = engine.store.classrooms()
        names = dict(engine.store.students())
        rows = []
        live_n = sim_n = 0
        for sid in engine.store.student_ids():
            row = engine.store.latest_evidence(sid, at)
            if row is None or row.classroom_id not in classrooms:
                continue
            c = classrooms[row.classroom_id]
            rows.append({
                "student_id": sid, "name": names.get(sid, sid), "classroom_id": row.classroom_id,
                "classroom_name": c.name, "x": c.x, "y": c.y, "state": row.state,
                "confidence": row.score, "source": row.source, "last_update": row.ts,
            })
            if row.source == "live":
                live_n += 1
            else:
                sim_n += 1
        return {"ts": at, "live_devices": live_n, "simulated_devices": sim_n, "students": rows}

    @app.get("/locations", dependencies=auth)
    def get_locations(at: float | None = None) -> dict:
        """Live campus map feed (brief section 22): current zone/x,y/
        confidence per student, LIVE vs SIMULATED clearly tagged
        (brief section 39 -- mandatory, never blurred)."""
        return _locations_snapshot(at)

    @app.get("/anomalies", dependencies=auth)
    def get_anomalies(status: str | None = None, student_id: str | None = None, limit: int = 200) -> list[dict]:
        return [{
            "id": a.id, "student_id": a.student_id, "ts": a.ts, "type": a.type, "severity": a.severity,
            "iforest_score": a.iforest_score, "is_anomalous": a.is_anomalous,
            "reasons": json.loads(a.reasons), "features": json.loads(a.features),
            "explanation": a.explanation, "status": a.status,
        } for a in engine.store.anomalies(status, student_id, limit)]

    @app.post("/feedback", dependencies=auth)
    def post_feedback(body: FeedbackIn) -> dict:
        try:
            orch.submit_feedback(body.anomaly_id, body.decision, body.comment, clock())
        except UnknownAnomaly:
            raise HTTPException(404, "unknown anomaly_id")
        return {"accepted": True}

    @app.post("/explain-anomaly", dependencies=auth)
    async def post_explain_anomaly(body: ExplainAnomalyIn) -> dict:
        try:
            explanation, retrieved = await orch.explain_anomaly(body.anomaly_id)
        except UnknownAnomaly:
            raise HTTPException(404, "unknown anomaly_id")
        return {"anomaly_id": body.anomaly_id, "explanation": explanation,
               "similar_verified_cases": [rc.to_dict() for rc in retrieved]}

    @app.post("/rag/retrieve", dependencies=auth)
    def post_rag_retrieve(body: RagRetrieveIn) -> dict:
        if body.anomaly_id is not None:
            anomaly = engine.store.anomaly(body.anomaly_id)
            if anomaly is None:
                raise HTTPException(404, "unknown anomaly_id")
            evidence = {"student": anomaly.student_id, "type": anomaly.type, **json.loads(anomaly.features)}
            query = anomaly_query_text(evidence, json.loads(anomaly.reasons))
        elif body.query:
            query = body.query
        else:
            raise HTTPException(422, "provide either anomaly_id or query")
        return {"query": query, "results": [rc.to_dict() for rc in orch.retriever.retrieve(query, body.k)]}

    # ---- Proof-of-Presence extension: synthetic campus simulation -----------

    @app.post("/simulation/start", dependencies=auth)
    def post_simulation_start(body: SimulationStartIn) -> dict:
        """Brief section 26: seeds a campus-scale synthetic population.
        Safe to call more than once with the same seed (idempotent re-
        enrollment); a different seed/size adds more simulated data
        alongside whatever is already there."""
        return orch.seed_simulation(body.students, body.classrooms, body.aps, body.anomaly_rate, body.seed)

    @app.post("/simulation/inject", dependencies=auth)
    def post_simulation_inject(body: DemoInjectIn) -> dict:
        """Brief section 29's Demo Control Panel buttons."""
        try:
            result = orch.inject_demo_anomaly(body.kind, body.student_id, clock())
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        return {"anomaly_id": result.anomaly_id, "is_anomalous": result.is_anomalous,
               "severity": result.severity, "reasons": result.reasons,
               "isolation_forest_score": result.iforest_score, "demo_risk_score": result.demo_risk_score}

    @app.post("/simulation/reset", dependencies=auth)
    def post_simulation_reset() -> dict:
        """Wipes ALL data (students, classrooms, sessions, evidence,
        anomalies, ...) and starts a fresh in-memory database -- a full
        reset for between demo runs, not a partial rollback."""
        nonlocal engine, orch
        new_store = Store(settings.db_path if settings.db_path == ":memory:" else ":memory:")
        engine = Engine(new_store, ModelRegistry(settings.model_dir), settings, clock)
        orch = PresenceOrchestrator(engine, get_llm_provider(), wifi_model, anomaly_detector)
        app.state.engine = engine
        app.state.orchestrator = orch
        return {"reset": True}

    @app.websocket("/ws")
    async def live(ws: WebSocket, key: str | None = None) -> None:
        """Pushes the zone snapshot every ws_interval_s seconds."""
        if not key_ok(key):
            await ws.close(code=4401)
            return
        await ws.accept()
        try:
            while True:
                await ws.send_json(await run_in_threadpool(engine.zone_snapshot))
                await asyncio.sleep(settings.ws_interval_s)
        except WebSocketDisconnect:
            pass

    @app.websocket("/ws/locations")
    async def live_locations(ws: WebSocket, key: str | None = None) -> None:
        """Brief section 22/13: the Live Location dashboard's feed --
        pushes _locations_snapshot() (the same payload GET /locations
        returns) every ws_interval_s seconds."""
        if not key_ok(key):
            await ws.close(code=4401)
            return
        await ws.accept()
        try:
            while True:
                await ws.send_json(await run_in_threadpool(_locations_snapshot))
                await asyncio.sleep(settings.ws_interval_s)
        except WebSocketDisconnect:
            pass

    return app
