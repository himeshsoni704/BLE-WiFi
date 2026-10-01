from __future__ import annotations

from typing import Iterator

from fastapi import Request
from sqlalchemy.orm import Session

from .core import Services


def get_svc(request: Request) -> Services:
    return request.app.state.svc


def get_db(request: Request) -> Iterator[Session]:
    svc: Services = request.app.state.svc
    db = svc.SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
