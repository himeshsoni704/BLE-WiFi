"""Runtime configuration. Everything is overridable by environment variable."""
from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass, field, fields
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # campus-presence/
BACKEND = ROOT / "backend"


def _env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


@dataclass(frozen=True)
class FusionWeights:
    """Prototype scoring weights (points, max 100 + optional signals, capped at 100).

    This is a transparent heuristic, NOT a validated probability of presence.
    """
    classroom_ble: float = 35
    wifi_match: float = 30
    peer_consistency: float = 20
    sustained_presence: float = 10
    face_match: float = 5
    rfid: float = 5


@dataclass(frozen=True)
class Thresholds:
    present: float = 70
    likely_present: float = 50
    review_required: float = 30        # below this -> ABSENT
    marker_rssi_min: float = -85       # smoothed marker RSSI must beat this to count
    marker_rssi_good: float = -75      # full BLE credit at/above this
    wifi_conf_min: float = 0.25        # ignore Wi-Fi predictions below this confidence
    peer_rssi_min: float = -88
    remote_rssi_min: float = -80       # another room's phone must see the token at least this strongly
    peer_target: int = 3               # distinct consistent peers for full peer credit
    sustained_fraction: float = 0.6    # share of the session with marker coverage
    face_conf_min: float = 0.8
    # deterministic anomaly rules
    max_speed_mps: float = 4.0         # faster than a run between rooms => impossible
    token_reuse_window_s: float = 90
    abnormal_token_reuse: int = 3
    contradiction_fraction: float = 0.6  # share of Wi-Fi evidence disagreeing with the BLE room (0.5 flagged 8.5% of simulated normal sessions, 0.6 flags 3.9%)
    session_switch_window_s: float = 300
    session_switch_max: int = 2        # >2 distinct classrooms in the window => rapid switching


@dataclass(frozen=True)
class Settings:
    database_url: str = f"sqlite:///{BACKEND / 'data' / 'campus.db'}"
    model_dir: str = str(ROOT / "ml" / "models")
    data_dir: str = str(ROOT / "ml" / "data")
    jwt_secret: str = ""
    jwt_ttl_s: int = 12 * 3600
    pepper: str = ""                     # server-side secret for hashed student keys
    password_iterations: int = 200_000
    token_window_s: int = 30
    token_skew_windows: int = 1
    ts_tolerance_s: float = 120
    retention_days: int = 30
    if_min_peers: int = 3                # Isolation Forest is skipped below this many distinct nearby devices
    demo_mode: bool = True               # seeds demo accounts + live demo session
    demo_password: str = "demo1234"
    llm_provider: str = "mock"
    gemini_api_key: str | None = None
    gemini_model: str | None = None
    gemini_send_real_ids: bool = False
    llm_timeout_s: float = 30
    rate_limit_enabled: bool = True
    cors_origins: tuple[str, ...] = ("http://localhost:5173", "http://127.0.0.1:5173")
    weights: FusionWeights = field(default_factory=FusionWeights)
    thresholds: Thresholds = field(default_factory=Thresholds)

    @property
    def wifi_model_path(self) -> str:
        return str(Path(self.model_dir) / "wifi_localization.joblib")

    @property
    def wifi_live_model_path(self) -> str:
        return str(Path(self.model_dir) / "wifi_localization_live.joblib")

    @property
    def anomaly_model_path(self) -> str:
        return str(Path(self.model_dir) / "isolation_forest.joblib")

    @classmethod
    def load(cls) -> "Settings":
        data_dir = BACKEND / "data"
        data_dir.mkdir(parents=True, exist_ok=True)

        def persisted_secret(name: str, env_name: str) -> str:
            v = _env(env_name)
            if v:
                return v
            f = data_dir / name
            if f.exists():
                return f.read_text().strip()
            v = secrets.token_hex(32)
            f.write_text(v)
            f.chmod(0o600)
            return v

        weights = FusionWeights()
        thresholds = Thresholds()
        cfg_path = _env("FUSION_CONFIG")
        if cfg_path and Path(cfg_path).exists():
            raw = json.loads(Path(cfg_path).read_text())
            weights = FusionWeights(**{k: v for k, v in raw.get("weights", {}).items()
                                       if k in {f.name for f in fields(FusionWeights)}})
            thresholds = Thresholds(**{k: v for k, v in raw.get("thresholds", {}).items()
                                       if k in {f.name for f in fields(Thresholds)}})
        for f in fields(FusionWeights):
            v = _env(f"W_{f.name.upper()}")
            if v is not None:
                weights = FusionWeights(**{**weights.__dict__, f.name: float(v)})

        return cls(
            database_url=_env("DATABASE_URL", cls.database_url),
            model_dir=_env("MODEL_DIR", cls.model_dir),
            data_dir=_env("ML_DATA_DIR", cls.data_dir),
            jwt_secret=persisted_secret(".jwt_secret", "JWT_SECRET"),
            pepper=persisted_secret(".pepper", "ID_PEPPER"),
            jwt_ttl_s=int(_env("JWT_TTL_S", str(cls.jwt_ttl_s))),
            ts_tolerance_s=float(_env("TS_TOLERANCE_S", str(cls.ts_tolerance_s))),
            retention_days=int(_env("RETENTION_DAYS", str(cls.retention_days))),
            demo_mode=_env("DEMO_MODE", "1") == "1",
            demo_password=_env("DEMO_PASSWORD", cls.demo_password),
            llm_provider=(_env("LLM_PROVIDER", "mock") or "mock").lower(),
            gemini_api_key=_env("GEMINI_API_KEY"),
            gemini_model=_env("GEMINI_MODEL"),
            gemini_send_real_ids=_env("GEMINI_SEND_REAL_IDS", "0") == "1",
            rate_limit_enabled=_env("RATE_LIMIT", "1") == "1",
            cors_origins=tuple((_env("CORS_ORIGINS", ",".join(cls.cors_origins)) or "").split(",")),
            weights=weights,
            thresholds=thresholds,
        )
