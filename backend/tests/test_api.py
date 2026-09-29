"""End-to-end scenarios through the HTTP API with a fake clock."""
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.features import extract_windows
from app.fusion import FusionConfig
from app.main import create_app
from app.owner_model import ModelRegistry, train_owner_model
from app.tokens import token_for, window_index
from simulator import synth

T0 = 1_800_000_000.0
BSSID_101, BSSID_102 = "AA:BB:CC:00:01:01", "AA:BB:CC:00:01:02"
PEOPLE = {n: synth.Person.random(i) for i, n in enumerate(["asha", "ravi"])}      # have gait models
WALKERS = {**PEOPLE, "meena": synth.Person.random(2), "kiran": synth.Person.random(3)}


class Clock:
    def __init__(self):
        self.t = T0

    def __call__(self):
        return self.t


def _windows(person, seed):
    a, g = synth.walk(person, 60, seed=seed)
    X, m = extract_windows(a, g, 50.0)
    return X[m >= 0.8]


@pytest.fixture(scope="module")
def model_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("models")
    reg = ModelRegistry(d)
    x = {n: _windows(p, i) for i, (n, p) in enumerate(PEOPLE.items())}
    reg.put("asha", train_owner_model(x["asha"], x["ravi"]))
    reg.put("ravi", train_owner_model(x["ravi"], x["asha"]))
    return d


class World:
    def __init__(self, client, clock, secrets):
        self.c, self.clock, self.secrets = client, clock, secrets

    def token(self, sid, t=None):
        return token_for(self.secrets[sid], window_index(self.clock.t if t is None else t, 30))

    def scan(self, sid, scanner="s1", rssi=-60, n=3):
        """n scans a second apart, ending at the current clock time."""
        scans = [{"token": self.token(sid, self.clock.t - i), "rssi": rssi, "ts": self.clock.t - i}
                 for i in range(n)]
        return self.c.post("/scan/batch", json={"scanner_id": scanner, "scans": scans})

    def report(self, sid, kind="walk", bssid=BSSID_101, carrier=None, interacting=False, seconds=6):
        if kind == "walk":
            person = WALKERS[carrier or sid]
            a, g = synth.walk(person, seconds, seed=int(self.clock.t) % 10_000)
        else:
            a, g = {"desk": synth.on_desk, "hand": synth.in_hand}[kind](seconds, seed=int(self.clock.t) % 10_000)
        body = {"token": self.token(sid), "ts": self.clock.t, "interacting": interacting,
                "window": {"fs": 50, "accel": a.tolist(), "gyro": g.tolist()}}
        if bssid:
            body["wifi"] = {"bssid": bssid, "rssi": -50}
        return self.c.post("/device-report", json=body)

    def run(self, seconds, who, step=10):
        """Advance the clock in `step`-second ticks. Each tick, every student in `who`
        gets a burst of 3 scans and a report. Per-student options: no_scan, no_report,
        scanner, rssi, and the report() arguments (kind, bssid, carrier, interacting)."""
        for _ in range(int(seconds // step)):
            self.clock.t += step
            for sid, opts in who.items():
                kw = dict(opts)
                scan_opts = {k: kw.pop(k) for k in ("scanner", "rssi") if k in kw}
                do_scan, do_report = not kw.pop("no_scan", False), not kw.pop("no_report", False)
                if do_scan:
                    self.scan(sid, n=3, **scan_opts)
                if do_report:
                    self.report(sid, **kw)

    def beat(self, sid, seconds, **opts):
        self.run(seconds, {sid: opts})

    def status(self, sid):
        return self.c.get(f"/status/{sid}").json()


@pytest.fixture
def world(tmp_path, model_dir):
    clock = Clock()
    settings = Settings(
        db_path=":memory:", model_dir=str(model_dir),
        fusion=FusionConfig(stationary_limit_s=3600),
    )
    client = TestClient(create_app(settings, clock))
    secrets = {}
    for sid in ("asha", "ravi", "meena", "kiran"):     # meena, kiran have no gait model
        r = client.post("/students", json={"student_id": sid, "name": sid.title()})
        assert r.status_code == 201
        secrets[sid] = r.json()["secret"]
    client.put("/scanners/s1", json={"zone": "r101"})
    client.put("/scanners/s2", json={"zone": "r102"})
    client.put(f"/bssids/{BSSID_101}", json={"zone": "r101"})
    client.put(f"/bssids/{BSSID_102}", json={"zone": "r102"})
    return World(client, clock, secrets)


# ---- the four fusion states ---------------------------------------------------------

def test_verified_when_owner_walks_and_both_signals_agree(world):
    world.beat("asha", seconds=60)
    s = world.status("asha")
    assert s["state"] == "verified" and s["zone"] == "r101"
    assert s["owner_score"] > 0.8


def test_swapped_phone_becomes_suspect(world):
    # ravi's phone (and ravi's model) but asha is the one walking with it
    world.beat("ravi", seconds=60, carrier="asha")
    s = world.status("ravi")
    assert s["state"] == "suspect", s
    assert s["owner_score"] < 0.3 and s["risk"] > 0.7
    assert any("does not match owner" in r for r in s["reasons"])


def test_bluetooth_off_drops_to_uncertain_wifi_only(world):
    world.beat("asha", seconds=30)
    assert world.status("asha")["state"] == "verified"
    world.run(30, {"asha": {"no_scan": True}})           # reports keep coming, scans stop
    s = world.status("asha")
    assert s["state"] == "uncertain" and any("Bluetooth" in r for r in s["reasons"])


def test_no_wifi_report_is_uncertain_ble_only(world):
    world.beat("meena", seconds=30, kind="hand", bssid=None)
    s = world.status("meena")
    assert s["state"] == "uncertain" and s["zone"] == "r101"


def test_nobody_home_is_not_detected(world):
    assert world.status("kiran")["state"] == "not_detected"


def test_phone_left_still_on_desk_is_flagged_after_the_limit(world):
    world.beat("meena", seconds=50 * 60, kind="desk")
    assert world.status("meena")["state"] == "verified"        # 50 min: normal for a lesson
    world.beat("meena", seconds=15 * 60, kind="desk")          # 65 min > 60 min limit
    s = world.status("meena")
    assert s["state"] == "suspect" and any("left behind" in r for r in s["reasons"])


def test_using_the_screen_prevents_left_behind_flag(world):
    world.beat("meena", seconds=65 * 60, kind="desk", interacting=True)
    assert world.status("meena")["state"] == "verified"


def test_moving_resets_the_stationary_timer(world):
    world.beat("meena", seconds=55 * 60, kind="desk")
    world.beat("meena", seconds=20, kind="walk")
    world.beat("meena", seconds=10 * 60, kind="desk")
    assert world.status("meena")["state"] == "verified"


def test_zone_mismatch_between_ble_and_wifi_is_uncertain(world):
    world.beat("asha", seconds=30, bssid=BSSID_102)            # scanned in r101, on r102's Wi-Fi
    s = world.status("asha")
    assert s["state"] == "uncertain" and "r102" in s["reasons"][0]


def test_weak_ble_signal_below_floor_is_ignored(world):
    world.c.put("/scanners/s1", json={"zone": "r101", "min_rssi": -70})
    world.beat("asha", seconds=30, rssi=-85)
    assert world.status("asha")["state"] == "uncertain"        # only Wi-Fi counts


def test_strongest_scanner_wins_zone(world):
    for _ in range(10):
        world.clock.t += 1
        world.scan("asha", "s1", rssi=-80, n=1)
        world.scan("asha", "s2", rssi=-55, n=1)
    assert world.status("asha")["zone"] == "r102"


# ---- ingestion checks -------------------------------------------------------------

def test_unknown_and_replayed_tokens_are_rejected(world):
    bad = world.c.post("/scan", json={"scanner_id": "s1", "token": "0" * 16, "rssi": -60}).json()
    assert bad["accepted"] == 0 and bad["unknown_token"] == 1
    old = world.token("asha", world.clock.t - 3600)
    replay = world.c.post("/scan", json={"scanner_id": "s1", "token": old, "rssi": -60}).json()
    assert replay["accepted"] == 0 and replay["unknown_token"] == 1


def test_far_off_timestamps_are_rejected(world):
    r = world.c.post("/scan", json={"scanner_id": "s1", "token": world.token("asha"), "rssi": -60,
                                    "ts": world.clock.t - 3600}).json()
    assert r["bad_timestamp"] == 1 and r["accepted"] == 0


def test_report_with_bad_token_is_401(world):
    r = world.c.post("/device-report", json={"token": "f" * 16})
    assert r.status_code == 401


def test_unregistered_scanner_is_404(world):
    r = world.c.post("/scan", json={"scanner_id": "ghost", "token": world.token("asha"), "rssi": -60})
    assert r.status_code == 404


def test_server_time_used_when_ts_omitted(world):
    r = world.c.post("/scan", json={"scanner_id": "s1", "token": world.token("asha"), "rssi": -60})
    assert r.json()["accepted"] == 1


@pytest.mark.parametrize("body", [
    {"token": "zz", "rssi": -60},
    {"token": "0" * 16, "rssi": 500},
])
def test_scan_validation(world, body):
    assert world.c.post("/scan", json={"scanner_id": "s1", **body}).status_code == 422


def test_sensor_window_validation(world):
    ok = {"fs": 50, "accel": [[0, 0, 9.8]] * 10, "gyro": [[0, 0, 0]] * 10}
    post = lambda w: world.c.post("/device-report", json={"token": world.token("asha"), "window": w})
    assert post({**ok, "gyro": ok["gyro"][:5]}).status_code == 422       # length mismatch
    assert post({**ok, "fs": 1}).status_code == 422                       # sample rate out of range
    assert post({**ok, "accel": [[0, 0]] * 10}).status_code == 422        # not xyz
    assert post({**ok, "accel": [], "gyro": []}).status_code == 422


def test_enrollment_rules(world):
    assert world.c.post("/students", json={"student_id": "asha", "name": "x"}).status_code == 409
    assert world.c.post("/students", json={"student_id": "../evil", "name": "x"}).status_code == 422
    listing = world.c.get("/students").json()
    assert {s["student_id"] for s in listing} == {"asha", "ravi", "meena", "kiran"}
    assert all("secret" not in s for s in listing)
    assert {s["student_id"]: s["has_model"] for s in listing} == {
        "asha": True, "ravi": True, "meena": False, "kiran": False}


def test_bssid_normalised_and_validated(world):
    assert world.c.put("/bssids/aa-bb-cc-00-01-09", json={"zone": "z"}).json()["bssid"] == "AA:BB:CC:00:01:09"
    assert world.c.put("/bssids/nonsense", json={"zone": "z"}).status_code == 422


def test_status_unknown_student_404(world):
    assert world.c.get("/status/nobody").status_code == 404


def test_only_scores_and_zones_are_stored_not_sensor_data(world):
    world.report("asha")
    cols = {r[1] for r in world.c.app.state.engine.store._read("PRAGMA table_info(reports)")}
    assert cols == {"ts", "student_id", "wifi_zone", "wifi_rssi", "owner_ema", "owner_ema_ts",
                    "last_motion_ts", "interaction_ts"}


# ---- roll-call, attendance, live view ---------------------------------------------

def test_rollcall_summary(world):
    world.run(300, {
        "asha": {},                                              # verified
        "ravi": {"carrier": "asha"},                             # phone carried by someone else
        "meena": {"kind": "hand", "bssid": None},                # BLE only
    })                                                           # kiran: never seen
    r = world.c.get("/rollcall", params={"start": T0, "end": world.clock.t}).json()
    assert r["total"] == 4
    assert r["summary"] == {"verified": 1, "suspect": 1, "uncertain": 1, "not_detected": 1}
    assert [x["student_id"] for x in r["students"]["suspect"]] == ["ravi"]
    assert [x["student_id"] for x in r["students"]["not_detected"]] == ["kiran"]


def test_rollcall_can_be_limited_to_zones(world):
    world.run(120, {"asha": {}})
    r = world.c.get("/rollcall", params={"zone": "r102", "start": T0, "end": world.clock.t}).json()
    assert r["summary"]["verified"] == 0 and r["summary"]["not_detected"] == 4


def test_attendance_needs_dwell_not_a_single_ping(world):
    # 10 minute class. asha stays throughout; kiran walks past the door for a few seconds.
    start = world.clock.t
    for _ in range(600):
        world.clock.t += 1
        world.scan("asha", n=1)
        if 296 <= world.clock.t - start <= 300:
            world.scan("kiran", n=1)
    r = world.c.get("/attendance", params={"zone": "r101", "start": start, "end": world.clock.t}).json()
    by = {s["student_id"]: s for s in r["students"]}
    assert by["asha"]["counted"] and by["asha"]["presence_ratio"] > 0.95
    assert not by["kiran"]["counted"] and 0 < by["kiran"]["present"] <= 2
    assert r["counted"] == 1


def test_attendance_flags_handoff(world):
    start = world.clock.t
    world.run(300, {"ravi": {"carrier": "asha"}})
    r = world.c.get("/attendance", params={"zone": "r101", "start": start, "end": world.clock.t}).json()
    ravi = next(s for s in r["students"] if s["student_id"] == "ravi")
    assert ravi["counted"] and ravi["flagged"] and r["flagged"] == 1


def test_dwell_period_validation(world):
    assert world.c.get("/attendance", params={"zone": "r101", "start": 100, "end": 50}).status_code == 422
    assert world.c.get("/attendance", params={"zone": "r101", "start": 0, "end": 10**9}).status_code == 422
    assert world.c.get("/attendance", params={"start": 0, "end": 60}).status_code == 422   # zone required


def test_zone_snapshot_and_websocket(world):
    world.beat("asha", seconds=30)
    snap = world.c.get("/zones").json()
    assert [row["student_id"] for row in snap["zones"]["r101"]] == ["asha"]
    assert snap["zones"]["r102"] == []                     # known zone, nobody in it
    assert snap["counts"]["verified"] == 1 and "kiran" in snap["not_detected"]
    with world.c.websocket_connect("/ws") as ws:
        msg = ws.receive_json()
    assert msg["counts"] == snap["counts"]


# ---- auth ---------------------------------------------------------------------------

def test_api_key_protects_everything_but_health(tmp_path):
    app = create_app(Settings(db_path=":memory:", model_dir=str(tmp_path), api_key="s3cret"), Clock())
    c = TestClient(app)
    assert c.get("/health").status_code == 200
    assert c.get("/zones").status_code == 401
    assert c.post("/students", json={"student_id": "a", "name": "A"}).status_code == 401
    assert c.get("/zones", headers={"X-API-Key": "wrong"}).status_code == 401
    assert c.get("/zones", headers={"X-API-Key": "s3cret"}).status_code == 200
    with pytest.raises(Exception):
        with c.websocket_connect("/ws"):
            pass
    with c.websocket_connect("/ws?key=s3cret") as ws:
        assert "counts" in ws.receive_json()
