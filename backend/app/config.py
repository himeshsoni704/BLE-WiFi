from __future__ import annotations

import os
from dataclasses import dataclass, field

from .dwell import DwellConfig
from .features import MOTION_THRESHOLD, WALKING_THRESHOLD
from .fusion import FusionConfig


@dataclass(frozen=True)
class Settings:
    db_path: str = "ble_wifi.db"
    model_dir: str = "models"
    api_key: str | None = None            # shared secret for the X-API-Key header; None = open (dev only)

    # rotating tokens
    token_window_s: int = 30
    token_skew_windows: int = 1
    max_clock_skew_s: float = 120.0       # reject timestamps this far from server time; 0 disables

    # BLE zone estimation
    scan_window_s: float = 15.0
    min_scan_samples: int = 2
    default_min_rssi: int = -90

    # device reports
    report_fresh_s: float = 60.0
    report_gap_reset_s: float = 600.0     # a longer gap restarts the stationary timer
    interaction_window_s: float = 300.0
    owner_ema_alpha: float = 0.3
    owner_max_age_s: float = 2 * 3600.0
    motion_threshold: float = MOTION_THRESHOLD
    walking_threshold: float = WALKING_THRESHOLD

    retention_s: float = 30 * 86400.0     # scans/reports older than this are deleted at startup
    ws_interval_s: float = 2.0

    fusion: FusionConfig = field(default_factory=FusionConfig)
    dwell: DwellConfig = field(default_factory=DwellConfig)

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ
        fusion = FusionConfig(
            stationary_limit_s=float(env.get("BLEWIFI_STATIONARY_LIMIT_S", FusionConfig.stationary_limit_s)),
            require_owner_score=env.get("BLEWIFI_REQUIRE_OWNER", "0") == "1",
        )
        return cls(
            db_path=env.get("BLEWIFI_DB", cls.db_path),
            model_dir=env.get("BLEWIFI_MODEL_DIR", cls.model_dir),
            api_key=env.get("BLEWIFI_API_KEY") or None,
            max_clock_skew_s=float(env.get("BLEWIFI_MAX_SKEW_S", cls.max_clock_skew_s)),
            fusion=fusion,
        )
