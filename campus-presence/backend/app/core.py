"""Service container shared by routers, the pipeline and the simulator."""
from __future__ import annotations

import asyncio
import collections
import logging
import threading
import time
from pathlib import Path
from typing import Callable

from fastapi import WebSocket
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .anomaly import AnomalyModel
from .config import Settings
from .db import session_scope
from .models import Student
from .security import RateLimiter
from .tokens import StudentTokenResolver
from .wifi import WifiLocalizer

log = logging.getLogger("campus.core")


class Hub:
    """WebSocket fan-out. publish() is safe to call from worker threads."""

    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.recent: collections.deque[dict] = collections.deque(maxlen=200)
        self._lock = threading.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        with self._lock:
            self.clients.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        with self._lock:
            self.clients.discard(ws)

    def publish(self, event: dict) -> None:
        event = {"ts": time.time(), **event}
        self.recent.append(event)
        loop = self.loop
        if loop is not None and loop.is_running() and self.clients:
            asyncio.run_coroutine_threadsafe(self._broadcast(event), loop)

    async def _broadcast(self, event: dict) -> None:
        with self._lock:
            targets = list(self.clients)
        for ws in targets:
            try:
                await ws.send_json(event)
            except Exception:
                self.disconnect(ws)


class Services:
    def __init__(self, settings: Settings, session_factory: sessionmaker[Session],
                 clock: Callable[[], float] = time.time):
        self.settings = settings
        self.SessionLocal = session_factory
        self.clock = clock
        self.hub = Hub()
        self.limiter = RateLimiter()
        self.tokens = StudentTokenResolver(self._load_secrets, settings.token_window_s,
                                           settings.token_skew_windows)
        self.wifi_sim: WifiLocalizer | None = None
        self.wifi_live: WifiLocalizer | None = None
        self.anomaly: AnomalyModel | None = None
        self.rag = None            # set by rag.CaseIndex in bootstrap
        self.kb = None             # set by knowledge.KnowledgeBase in bootstrap
        self.llm = None            # set by llm.get_provider in bootstrap
        self.llm_note: str | None = None
        self.sim = None            # simulation.generator.CampusSim once started
        self.metrics = collections.Counter()
        self.sim_state: dict = {"running": False, "last": None, "progress": 0.0}
        self._lock = threading.RLock()
        self.write_lock = threading.RLock()     # serialises ingestion/evaluation writes (SQLite)

    def _load_secrets(self) -> dict[str, str]:
        with session_scope(self.SessionLocal) as db:
            return {k: s for k, s in db.execute(select(Student.student_key, Student.token_secret))}

    def load_models(self) -> dict:
        """(Re)load model files. Missing files leave the slot None and are reported."""
        s = self.settings
        status = {}
        for attr, path, loader in (("wifi_sim", s.wifi_model_path, WifiLocalizer.load),
                                   ("wifi_live", s.wifi_live_model_path, WifiLocalizer.load),
                                   ("anomaly", s.anomaly_model_path, AnomalyModel.load)):
            if Path(path).exists():
                try:
                    setattr(self, attr, loader(path))
                    status[attr] = "loaded"
                except Exception as exc:                      # corrupt / incompatible model file
                    setattr(self, attr, None)
                    status[attr] = f"failed to load: {exc}"
                    log.warning("could not load %s: %s", path, exc)
            else:
                setattr(self, attr, None)
                status[attr] = "missing"
        if self.anomaly is not None and not self.anomaly.has_reference:
            status["anomaly"] += "; " + self._recover_reference()
        return status

    def _recover_reference(self) -> str:
        """Models trained before attribution existed carry no 'typical value' per feature. If the synthetic training
        CSV is still around, derive it from there so explanations work without retraining."""
        path = Path(self.settings.data_dir) / "anomaly_features.csv"
        if not path.exists():
            return "no attribution reference (retrain to enable explanations)"
        try:
            import numpy as np
            from . import mltrain
            from .anomaly import FEATURES, feature_reference
            rows = [r for r in mltrain.read_feature_csv(path) if r.get("label") == "normal"]
            if len(rows) < 50:
                return "no attribution reference (too few rows in anomaly_features.csv)"
            self.anomaly.stats.update(feature_reference(np.array([[r[f] for f in FEATURES] for r in rows])))
            return "attribution reference recovered from ml/data/anomaly_features.csv"
        except Exception as exc:                                  # a bad CSV must not stop the server starting
            log.warning("could not recover the attribution reference: %s", exc)
            return "no attribution reference (could not read anomaly_features.csv)"

    def wifi_model_for(self, simulated: bool) -> WifiLocalizer | None:
        return self.wifi_sim if simulated else self.wifi_live
