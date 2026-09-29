"""FastAPI app. Run with:  uvicorn app.main:create_app --factory"""
from __future__ import annotations

import asyncio
import hmac
import time
from typing import Annotated, Callable

import numpy as np
from fastapi import Depends, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool

from .config import Settings
from .engine import DuplicateStudent, Engine, ReportData, UnknownScanner
from .fusion import State
from .owner_model import ModelRegistry
from .schemas import BssidIn, ReportIn, ScanBatchIn, ScanIn, ScannerIn, StudentIn
from .store import Store, normalize_bssid


def create_app(settings: Settings | None = None, clock: Callable[[], float] = time.time) -> FastAPI:
    settings = settings or Settings.from_env()
    engine = Engine(Store(settings.db_path), ModelRegistry(settings.model_dir), settings, clock)
    engine.purge_old_data()
    app = FastAPI(title="BLE + Wi-Fi presence with handoff risk")
    app.state.engine = engine

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

    return app
