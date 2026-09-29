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
