"""Proof-of-Presence backend."""
import sys
from pathlib import Path

# `simulation/` lives next to `backend/`, one level above this package. Putting it on the path here means the
# server starts with plain `uvicorn app.main:create_app --factory`, with no PYTHONPATH to remember.
_CAMPUS_ROOT = str(Path(__file__).resolve().parents[2])
if _CAMPUS_ROOT not in sys.path:
    sys.path.append(_CAMPUS_ROOT)
