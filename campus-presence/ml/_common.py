"""Shared path setup so the scripts run from anywhere: `python ml/train_wifi_model.py`."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "backend"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

DATA = ROOT / "ml" / "data"
MODELS = ROOT / "ml" / "models"
