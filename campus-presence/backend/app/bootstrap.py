"""Create the database, services, seed data, models, RAG index and LLM provider."""
from __future__ import annotations

import logging
import time
from pathlib import Path

from sqlalchemy import delete

from . import models  # noqa: F401  (register tables)
from .config import Settings
from .core import Services
from .db import Base, make_engine, make_session_factory, session_scope
from .llm import get_provider
from .models import Location, Observation
from .knowledge import KnowledgeBase
from .rag import CaseIndex
from .seed import ensure_live_session, seed_campus, seed_demo_accounts

log = logging.getLogger("campus.boot")


def build_services(settings: Settings, clock=time.time) -> Services:
    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)
    return Services(settings, make_session_factory(engine), clock)


def ensure_models(svc: Services, quick: bool = False) -> dict:
    """Train any missing model files from synthetic data so a fresh checkout runs end to end."""
    from . import mltrain
    s = svc.settings
    out = {}
    Path(s.model_dir).mkdir(parents=True, exist_ok=True)
    Path(s.report_dir).mkdir(parents=True, exist_ok=True)
    if not Path(s.wifi_model_path).exists():
        log.warning("wifi_localization.joblib missing: training a synthetic one (a few seconds)")
        model, report = mltrain.train_wifi(200 if quick else 400, 80 if quick else 150)
        model.save(s.wifi_model_path)
        mltrain.save_wifi_report(report, Path(s.report_dir))
        out["wifi"] = "trained"
    svc.load_models()
    if not Path(s.anomaly_model_path).exists():
        log.warning("isolation_forest.joblib missing: generating synthetic data and training (about a minute)")
        n = 150 if quick else 400
        rows = mltrain.build_anomaly_dataset(svc, n, seed=7, scenarios=mltrain.default_scenarios(n))
        model, report = mltrain.train_anomaly(rows)
        model.save(s.anomaly_model_path)
        mltrain.save_anomaly_report(report, Path(s.report_dir))
        out["anomaly"] = "trained"
        # the dataset run polluted the DB with simulated rows; wipe them
        from simulation.generator import CampusSim
        with session_scope(svc.SessionLocal) as db:
            CampusSim(svc).reset(db)
    svc.load_models()
    return out


def bootstrap(settings: Settings, clock=time.time, train_missing: bool = True, quick: bool = False) -> Services:
    svc = build_services(settings, clock)
    with session_scope(svc.SessionLocal) as db:
        seed_campus(db, settings)
        if settings.demo_mode:
            seed_demo_accounts(db, settings)
            ensure_live_session(db, settings, clock())
        cutoff = clock() - settings.retention_days * 86400
        db.execute(delete(Observation).where(Observation.ts < cutoff))
        db.execute(delete(Location).where(Location.ts < cutoff))
    svc.load_models()
    if train_missing:
        ensure_models(svc, quick=quick)
    else:
        svc.load_models()
    svc.rag = CaseIndex(svc.SessionLocal)
    svc.rag.seed_if_empty()
    svc.rag.rebuild()
    svc.kb = KnowledgeBase()
    svc.llm, svc.llm_note = get_provider(settings)
    return svc
