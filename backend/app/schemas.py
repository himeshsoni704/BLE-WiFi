from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field, model_validator

from .features import MAX_FS, MIN_FS

TokenStr = Annotated[str, Field(pattern=r"^[0-9a-f]{16}$")]
Xyz = Annotated[list[float], Field(min_length=3, max_length=3)]
MAX_SAMPLES = 2000


class StudentIn(BaseModel):
    student_id: str
    name: str = Field(min_length=1, max_length=200)


class ScannerIn(BaseModel):
    zone: str = Field(min_length=1, max_length=100)
    min_rssi: int | None = Field(default=None, ge=-127, le=0)


class BssidIn(BaseModel):
    zone: str = Field(min_length=1, max_length=100)


class ScanItem(BaseModel):
    token: TokenStr
    rssi: int = Field(ge=-127, le=20)
    ts: float | None = None


class ScanIn(ScanItem):
    scanner_id: str = Field(min_length=1, max_length=100)


class ScanBatchIn(BaseModel):
    scanner_id: str = Field(min_length=1, max_length=100)
    scans: list[ScanItem] = Field(max_length=1000)


class WifiIn(BaseModel):
    bssid: str
    rssi: int | None = Field(default=None, ge=-127, le=0)


class SensorWindow(BaseModel):
    """A few seconds of raw samples. Scored on the server, then discarded."""
    fs: float = Field(ge=MIN_FS, le=MAX_FS)
    accel: list[Xyz] = Field(max_length=MAX_SAMPLES)
    gyro: list[Xyz] = Field(max_length=MAX_SAMPLES)

    @model_validator(mode="after")
    def _same_length(self) -> "SensorWindow":
        if len(self.accel) != len(self.gyro) or not self.accel:
            raise ValueError("accel and gyro must be non-empty and the same length")
        return self


class ReportIn(BaseModel):
    token: TokenStr
    ts: float | None = None
    wifi: WifiIn | None = None
    window: SensorWindow | None = None
    interacting: bool = False


# ---- Proof-of-Presence extension (brief sections 4-11, 13) ----------------

class ClassroomIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    building: str | None = None
    floor: int | None = None
    x: float
    y: float
    ble_marker_id: str | None = None
    display_name: str | None = None   # e.g. "Smart Board -- Room 204" (brief section 25)


class SessionIn(BaseModel):
    course: str = Field(min_length=1, max_length=100)
    classroom_id: str
    start_ts: float
    end_ts: float | None = None


class BleObservationIn(BaseModel):
    """Brief section 5's classroom-marker shape, plus the student token the
    brief's own JSON example omits but which is required to know WHO
    detected the marker -- without it this can't be attributed to a student
    at all. `session` is accepted but purely informational; the room is
    resolved from the marker's own registration (PUT /classrooms), which is
    the one place that can't be spoofed by whatever the client claims."""
    marker_id: str = Field(min_length=1, max_length=100)
    token: TokenStr
    rssi: int = Field(ge=-127, le=20)
    ts: float | None = None
    session: str | None = None


class PeerObservationIn(BaseModel):
    """Brief section 4's student-to-student shape. Both tokens, never raw
    identities -- the server is the only party that can resolve either."""
    observer_token: TokenStr
    observed_token: TokenStr
    rssi: int = Field(ge=-127, le=20)
    duration: float | None = Field(default=None, ge=0)
    ts: float | None = None


class WifiObservationIn(BaseModel):
    token: TokenStr
    fingerprint: dict[str, int] = Field(min_length=1, max_length=64)
    classroom_id: str | None = Field(
        default=None, description="if given, this fingerprint is ALSO logged as a live "
                                  "training sample for the zone KNN model")
    ts: float | None = None


class PresenceIn(BaseModel):
    token: TokenStr
    session_id: str = Field(min_length=1, max_length=100)
    classroom_id: str
    wifi_fingerprint: dict[str, int] | None = Field(default=None, max_length=64)
    ts: float | None = None


class FeedbackIn(BaseModel):
    anomaly_id: int
    decision: str = Field(pattern=r"^(false_positive|confirmed)$")
    comment: str | None = Field(default=None, max_length=2000)


class ExplainAnomalyIn(BaseModel):
    anomaly_id: int


class RagRetrieveIn(BaseModel):
    query: str | None = None
    anomaly_id: int | None = None
    k: int = Field(default=3, ge=1, le=10)


class FaceScanIn(BaseModel):
    student_id: str
    match: bool
    confidence: float | None = Field(default=None, ge=0, le=1)
    ts: float | None = None


class RfidIn(BaseModel):
    reader: str = Field(min_length=1, max_length=100)
    token: TokenStr | None = None
    ts: float | None = None


class SimulationStartIn(BaseModel):
    students: int = Field(default=300, ge=1, le=5000)
    classrooms: int = Field(default=20, ge=1, le=200)
    aps: int = Field(default=10, ge=1, le=100)
    anomaly_rate: float = Field(default=0.08, ge=0, le=1)
    seed: int = 0


DEMO_INJECT_KINDS = ("proxy_attendance", "impossible_movement", "wifi_ble_mismatch",
                     "token_replay", "device_clustering", "short_presence")


class DemoInjectIn(BaseModel):
    kind: str = Field(pattern="^(" + "|".join(DEMO_INJECT_KINDS) + ")$")
    student_id: str | None = None
