from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect

from ..security import decode_jwt

router = APIRouter()


@router.websocket("/ws")
async def live(ws: WebSocket, token: str = Query(...)) -> None:
    """Live events for the dashboard (staff only): attendance, anomaly, node, simulation, feedback."""
    svc = ws.app.state.svc
    try:
        who = decode_jwt(svc.settings, token)
    except HTTPException:
        await ws.close(code=4401)
        return
    if not who.is_staff():
        await ws.close(code=4403)
        return
    await svc.hub.connect(ws)
    try:
        await ws.send_json({"type": "hello", "recent": list(svc.hub.recent)[-30:]})
        while True:
            await ws.receive_text()            # clients may send pings; content ignored
    except WebSocketDisconnect:
        pass
    finally:
        svc.hub.disconnect(ws)
