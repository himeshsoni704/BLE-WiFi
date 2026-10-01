import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT, ROOT / "backend", ROOT / "ml"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from app import mltrain                                   # noqa: E402
from app.bootstrap import build_services                  # noqa: E402
from app.config import Settings                          # noqa: E402
from app.main import create_app                           # noqa: E402


def make_settings(model_dir, data_dir, **kw) -> Settings:
    base = dict(database_url="sqlite://", model_dir=str(model_dir), data_dir=str(data_dir),
                jwt_secret="test-secret-" + "x" * 32, pepper="test-pepper", password_iterations=1000,
                rate_limit_enabled=False, demo_password="demo1234")
    base.update(kw)
    return Settings(**base)


@pytest.fixture(scope="session")
def model_dir(tmp_path_factory):
    """Small synthetic models trained once per test session."""
    d = tmp_path_factory.mktemp("models")
    settings = make_settings(d, d)
    wifi, rep = mltrain.train_wifi(150, 50)
    wifi.save(settings.wifi_model_path)
    svc = build_services(settings)
    svc.load_models()
    rows = mltrain.build_anomaly_dataset(svc, 200, 7, mltrain.default_scenarios(200))
    model, _ = mltrain.train_anomaly(rows)
    model.save(settings.anomaly_model_path)
    return d


class Clock:
    def __init__(self):
        self.t = time.time()

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s
        return self.t


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def make_app(model_dir, tmp_path, clock):
    def factory(**kw):
        settings = make_settings(model_dir, tmp_path, **kw)
        return create_app(settings, clock=clock, train_missing=False)
    return factory


@pytest.fixture
def app(make_app):
    return make_app()


@pytest.fixture
def client(app):
    return TestClient(app)


def login(client, username, password="demo1234") -> dict:
    r = client.post("/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}
