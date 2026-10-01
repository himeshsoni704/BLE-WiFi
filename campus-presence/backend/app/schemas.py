"""Request/response models with strict validation (lengths, ranges, patterns)."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

TokenHex = Annotated[str, Field(pattern=r"^[0-9a-f]{16}$")]
Nonce = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{8,48}$")]
EpochSeconds = Annotated[float, Field(gt=1_000_000_000, lt=4_000_000_000)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class LoginIn(Strict):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class BleItem(Strict):
    """One aggregated BLE observation, smoothed on the phone.

    kind=peer   -> observed_token is another phone's rotating token
    kind=marker -> marker_idx (or marker_id) + marker_token from a classroom marker
    """
    kind: Literal["peer", "marker"]
    observer: TokenHex | None = Field(default=None, validation_alias=AliasChoices("observer", "observer_token"))
    observed_token: TokenHex | None = None
    marker_idx: int | None = Field(default=None, ge=1, le=65535)
    marker_id: str | None = Field(default=None, max_length=64)
    marker_token: TokenHex | None = None
    room: str | None = Field(default=None, max_length=16)        # informational; server resolves the room
    session: str | None = Field(default=None, max_length=32)     # informational
    rssi: float = Field(ge=-127, le=0)
    timestamp: EpochSeconds
    duration: float = Field(ge=0, le=3600)
    samples: int = Field(default=0, ge=0, le=1_000_000)
    nonce: Nonce


class BleBatchIn(Strict):
    observations: list[BleItem] = Field(min_length=1, max_length=200)


class WifiAp(Strict):
    ap_id: str | None = Field(default=None, max_length=32)
    bssid: str | None = Field(default=None, max_length=24)
    ssid: str | None = Field(default=None, max_length=64)
    rssi: float = Field(ge=-127, le=0)
    frequency: int | None = Field(default=None, ge=2000, le=7200)
    connected: bool = False


class WifiObservationIn(Strict):
    timestamp: EpochSeconds
    nonce: Nonce
    wifi: list[WifiAp] = Field(min_length=1, max_length=64)
    source: Literal["scan", "cached"] = "scan"
    scan_age_s: float | None = Field(default=None, ge=0, le=86400)


class WifiSurveyScan(Strict):
    wifi: list[WifiAp] = Field(min_length=1, max_length=64)


class WifiSurveyIn(Strict):
    zone: str = Field(min_length=1, max_length=16)
    scans: list[WifiSurveyScan] = Field(min_length=1, max_length=200)
    register_unknown_aps: bool = True


class ZoneEstimate(Strict):
    zone: str | None = Field(default=None, max_length=16)
    confidence: float | None = Field(default=None, ge=0, le=1)
    method: str | None = Field(default=None, max_length=32)


class FaceIn(Strict):
    face_system: bool = True
    student_id: str = Field(min_length=1, max_length=32)
    match: bool
    confidence: float = Field(ge=0, le=1)
    timestamp: EpochSeconds


class RfidBlock(Strict):
    detected: bool
    reader: str = Field(max_length=64)
    timestamp: EpochSeconds
    student_id: str | None = Field(default=None, max_length=32)


class RfidIn(Strict):
    rfid: RfidBlock


class PresenceIn(Strict):
    """Phone heartbeat with its own summary. Face/RFID blocks are accepted only from staff/readers."""
    timestamp: EpochSeconds
    nonce: Nonce
    session: str | None = Field(default=None, max_length=32)
    zone_estimate: ZoneEstimate | None = None
    ble_state: Literal["active", "off", "denied", "unsupported"] = "active"
    wifi_state: Literal["active", "off", "denied", "throttled", "unsupported"] = "active"
    counts: dict[str, int] | None = None
    face: FaceIn | None = Field(default=None, validation_alias=AliasChoices("face", "face_system"))
    rfid: RfidIn | RfidBlock | None = None

    @field_validator("counts")
    @classmethod
    def _bounded(cls, v):
        if v is not None and (len(v) > 16 or any(len(k) > 40 or not (0 <= n <= 10**9) for k, n in v.items())):
            raise ValueError("counts: at most 16 keys, non-negative integers")
        return v


class NodeSighting(Strict):
    token: TokenHex
    rssi: float = Field(ge=-127, le=0)
    duration: float = Field(default=0, ge=0, le=3600)
    timestamp: EpochSeconds | None = None


class NodeHeartbeatIn(Strict):
    detected_count: int = Field(ge=0, le=10000)
    node_kind: Literal["android", "esp32", "raspberry_pi", "laptop", "smartboard"] = "android"
    timestamp: EpochSeconds | None = None
    sightings: list[NodeSighting] = Field(default_factory=list, max_length=200)


class FeedbackIn(Strict):
    anomaly_id: int
    action: Literal["confirm", "false_positive", "comment"]
    comment: str | None = Field(default=None, max_length=2000)


class ExplainIn(Strict):
    anomaly_id: int
    provider: Literal["gemini", "mock"] | None = None


class RagQueryIn(Strict):
    anomaly_id: int | None = None
    query: str | None = Field(default=None, max_length=2000)
    k: int = Field(default=3, ge=1, le=10)


class SimulationStartIn(Strict):
    scenario: Literal["full", "normal", "proxy", "impossible_movement", "wifi_ble_mismatch",
                      "token_replay", "false_positive", "short_presence"] = "full"
    students: int = Field(default=300, ge=20, le=600)
    seed: int = Field(default=1, ge=0, le=10_000)
    wait: bool = False


class SessionCreateIn(Strict):
    code: str = Field(min_length=1, max_length=32)
    title: str = Field(default="", max_length=120)
    classroom_id: str = Field(min_length=1, max_length=16)
    minutes: int = Field(default=60, ge=1, le=600)
