"""FastAPI app: `uvicorn app.main:create_app --factory`"""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from .bootstrap import bootstrap
from .config import ROOT, Settings
from .core import Services
from .routers import ai, auth, data, ingest_routes, sim, ws

log = logging.getLogger("campus")


def create_app(settings: Settings | None = None, svc: Services | None = None, clock=time.time,
               train_missing: bool = True, quick: bool = False) -> FastAPI:
    settings = settings or Settings.load()
    svc = svc or bootstrap(settings, clock, train_missing=train_missing, quick=quick)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        svc.hub.loop = asyncio.get_running_loop()
        yield

    app = FastAPI(title="Proof-of-Presence", version="0.1.0", lifespan=lifespan,
                  description=("Decentralized evidence generation with authorized aggregation. "
                               "Scores are prototype heuristics, not validated probabilities."))
    app.state.svc = svc
    app.state.settings = settings
    app.state.limiter = svc.limiter
    app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins), allow_credentials=False,
                       allow_methods=["*"], allow_headers=["*"])
    for r in (auth.router, data.router, ingest_routes.router, ai.router, sim.router, ws.router):
        app.include_router(r)

    @app.get("/health", tags=["meta"])
    def health() -> dict:
        with svc.SessionLocal() as db:
            db.execute(text("select 1"))
        return {"ok": True, "time": svc.clock(), "models": {
                    "wifi_simulated": svc.wifi_sim is not None, "wifi_live": svc.wifi_live is not None,
                    "isolation_forest": svc.anomaly is not None},
                "rag_cases": svc.rag.size() if svc.rag else 0,
                "counters": dict(svc.metrics)}

    @app.get("/config", tags=["meta"])
    def public_config() -> dict:
        return {"llm": {"configured": settings.llm_provider, "active": svc.llm.name if svc.llm else None,
                        "note": svc.llm_note, "model": settings.gemini_model if svc.llm and svc.llm.name == "gemini" else None},
                "token_window_s": settings.token_window_s, "demo_mode": settings.demo_mode,
                "fusion_weights": settings.weights.__dict__,
                "fusion_thresholds": {k: v for k, v in settings.thresholds.__dict__.items()
                                      if k in ("present", "likely_present", "review_required")},
                "score_note": "Prototype heuristic score; not a validated probability.",
                "architecture": "decentralized evidence generation with authorized aggregation"}

    from .security import require_roles

    @app.post("/models/reload", tags=["meta"])
    def reload_models(who=Depends(require_roles("faculty", "admin"))) -> dict:
        """Pick up retrained model files without restarting."""
        return svc.load_models()

    @app.get("/", include_in_schema=False)
    def root():
        return RedirectResponse("/ui/" if (ROOT / "frontend" / "dist").exists() else "/docs")

    dist = ROOT / "frontend" / "dist"
    if dist.exists():
        app.mount("/ui", StaticFiles(directory=dist, html=True), name="ui")
    return app
