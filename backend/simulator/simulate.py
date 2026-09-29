"""Simulate a hostel full of phones against the API.

    # server (fast mode posts historical timestamps, so disable the skew check;
    # a low stationary limit lets a 30 minute run show the left-behind rule)
    BLEWIFI_MAX_SKEW_S=0 BLEWIFI_STATIONARY_LIMIT_S=600 \\
        uvicorn app.main:create_app --factory

    python -m simulator.simulate --url http://127.0.0.1:8000 --residents 280 --check

Every resident gets a role and the run ends by comparing the roll-call with what
that role should produce:

    normal    walks and sits with own phone, BLE + Wi-Fi          -> verified
    handoff   pairs who swapped phones (need gait models)         -> suspect
    left      phone still on a desk the whole time                -> suspect
    bt_off    Wi-Fi reports only, Bluetooth off                   -> uncertain
    absent    nothing at all                                      -> not_detected
    walkby    seen by the scanner for 20 s, then gone             -> not counted in attendance

Sensor data is synthetic (simulator/synth.py). It exercises the pipeline; it says
nothing about real gait accuracy.
"""
from __future__ import annotations

import argparse
import sys
import time
import zlib
from collections import Counter, defaultdict
from dataclasses import dataclass, field

import numpy as np

from app.features import extract_windows
from app.owner_model import ModelRegistry, train_owner_model
from app.tokens import token_for, window_index
from simulator import synth

EXPECTED_STATE = {
    "normal": "verified", "handoff": "suspect", "left": "suspect",
    "bt_off": "uncertain", "absent": "not_detected",
}
FS = 50.0


class SimError(RuntimeError):
    pass


@dataclass
class SimConfig:
    residents: int = 60
    minutes: float = 30.0
    tick_s: int = 10
    report_every_s: int = 30
    floors: int = 4
    enrolled: int = 20                 # residents with a trained gait model
    handoff_pairs: int = 2
    left: int = 3
    bt_off: int = 4
    absent: int = 3
    walkby: int = 2
    walk_share: float = 0.3            # share of a normal resident's reports that are walking
    seed: int = 1
    prefix: str = "sim"
    model_dir: str = "models"
    realtime: bool = False
    start_ts: float | None = None


@dataclass
class Resident:
    sid: str
    role: str
    zone: str
    gait: synth.Person
    enrolled: bool = False
    partner: str | None = None
    secret: str = ""


@dataclass
class SimResult:
    residents: list[Resident]
    start: float
    end: float
    rollcall: dict
    attendance: dict
    mismatches: list[str] = field(default_factory=list)


def _build_residents(cfg: SimConfig, rng: np.random.Generator) -> list[Resident]:
    n_special = 2 * cfg.handoff_pairs + cfg.left + cfg.bt_off + cfg.absent + cfg.walkby
    if n_special > cfg.residents:
        raise SimError(f"{n_special} special residents do not fit in {cfg.residents}")
    n_enrolled = min(cfg.residents, max(cfg.enrolled, 2 * cfg.handoff_pairs + 2))
    people = [
        Resident(f"{cfg.prefix}{i:03d}", "normal", f"floor{i % cfg.floors + 1}",
                 synth.Person.random(cfg.seed * 10_000 + i), enrolled=i < n_enrolled)
        for i in range(cfg.residents)
    ]
    enrolled_idx = list(rng.permutation(n_enrolled))
    for k in range(cfg.handoff_pairs):
        a, b = people[enrolled_idx[2 * k]], people[enrolled_idx[2 * k + 1]]
        a.role = b.role = "handoff"
        a.partner, b.partner = b.sid, a.sid
        b.zone = a.zone                                   # the pair is together
    free = [i for i in rng.permutation(cfg.residents) if people[i].role == "normal"]
    for role, count in (("left", cfg.left), ("bt_off", cfg.bt_off),
                        ("absent", cfg.absent), ("walkby", cfg.walkby)):
        for _ in range(count):
            people[free.pop()].role = role
    return people


def _train_models(people: list[Resident], model_dir: str) -> None:
    enrolled = [p for p in people if p.enrolled]
    windows = {}
    for p in enrolled:
        parts = []
        for s in range(3):
            a, g = synth.walk(p.gait, 30, FS, seed=zlib.crc32(f"{p.sid}/{s}".encode()) % 100_000)
            X, m = extract_windows(a, g, FS)
            parts.append(X[m >= 0.8])
        windows[p.sid] = np.vstack(parts)
    registry = ModelRegistry(model_dir)
    for i, p in enumerate(enrolled):
        others = [enrolled[(i + k) % len(enrolled)].sid for k in range(1, min(6, len(enrolled)))]
        registry.put(p.sid, train_owner_model(windows[p.sid], np.vstack([windows[o] for o in others])))


def _check(resp, what: str):
    if resp.status_code >= 400:
        raise SimError(f"{what} failed: HTTP {resp.status_code} {resp.text[:200]}")
    return resp.json()


def run(client, cfg: SimConfig, log=print) -> SimResult:
    """`client` is an httpx.Client (or FastAPI TestClient) pointed at the API."""
    rng = np.random.default_rng(cfg.seed)
    people = _build_residents(cfg, rng)
    by_sid = {p.sid: p for p in people}
    zones = sorted({p.zone for p in people})

    log(f"registering {len(zones)} zones and {len(people)} residents")
    for z in zones:
        _check(client.put(f"/scanners/scan-{z}", json={"zone": z}), "scanner")
        _check(client.put(f"/bssids/02:00:00:00:00:{int(z[5:]):02x}", json={"zone": z}), "bssid")
    for p in people:
        r = client.post("/students", json={"student_id": p.sid, "name": f"Resident {p.sid}"})
        if r.status_code == 409:
            raise SimError(f"{p.sid} already exists (secret unknown); use a fresh database or --prefix")
        p.secret = _check(r, "enroll")["secret"]
    log(f"training gait models for {sum(p.enrolled for p in people)} enrolled residents")
    _train_models(people, cfg.model_dir)
    seen = {x["student_id"] for x in _check(client.get("/students"), "students") if x["has_model"]}
    missing = [p.sid for p in people if p.enrolled and p.sid not in seen]
    if missing:
        raise SimError(f"the server cannot see the gait models just written to {cfg.model_dir!r} "
                       f"({len(missing)} missing): --model-dir must match the server's BLEWIFI_MODEL_DIR")

    bssid = {z: f"02:00:00:00:00:{int(z[5:]):02x}" for z in zones}
    ticks = int(cfg.minutes * 60 // cfg.tick_s)
    t0 = cfg.start_ts if cfg.start_ts is not None else time.time() - ticks * cfg.tick_s
    if cfg.realtime:
        t0 = time.time()
    walkby_window = range(ticks // 2, ticks // 2 + 2)
    report_every = max(1, cfg.report_every_s // cfg.tick_s)
    stats = Counter()
    log(f"simulating {ticks} ticks of {cfg.tick_s}s ({cfg.minutes:g} min)")

    for k in range(1, ticks + 1):
        ts = time.time() if cfg.realtime else t0 + k * cfg.tick_s
        if cfg.realtime:
            time.sleep(cfg.tick_s)
        win = window_index(ts, 30)
        batches: dict[str, list] = defaultdict(list)
        for p in people:
            if p.role == "absent":
                continue
            if p.role == "walkby" and k not in walkby_window:
                continue
            tok = token_for(p.secret, win)
            if p.role != "bt_off":
                for j in range(3):
                    batches[p.zone].append({"token": token_for(p.secret, window_index(ts - j, 30)),
                                            "rssi": int(rng.normal(-62, 4)), "ts": ts - j})
            if k % report_every == 0 and p.role != "walkby":
                carrier = by_sid[p.partner].gait if p.partner else p.gait
                if p.role == "left":
                    a, g = synth.on_desk(4, FS, seed=int(rng.integers(1 << 30)))
                elif rng.random() < cfg.walk_share:
                    a, g = synth.walk(carrier, 4, FS, seed=int(rng.integers(1 << 30)))
                else:
                    a, g = synth.in_hand(4, FS, seed=int(rng.integers(1 << 30)))
                body = {"token": tok, "ts": ts, "wifi": {"bssid": bssid[p.zone], "rssi": -55},
                        "window": {"fs": FS, "accel": a.round(4).tolist(), "gyro": g.round(4).tolist()}}
                r = client.post("/device-report", json=body)
                if r.status_code == 401:
                    raise SimError("server rejected a report: unknown token or timestamp. "
                                   "Start it with BLEWIFI_MAX_SKEW_S=0 for fast mode, or use --realtime")
                _check(r, "device-report")
                stats["reports"] += 1
        for zone, scans in batches.items():
            out = _check(client.post("/scan/batch", json={"scanner_id": f"scan-{zone}", "scans": scans}), "scan")
            if out["bad_timestamp"]:
                raise SimError("server rejected scan timestamps; start it with BLEWIFI_MAX_SKEW_S=0 "
                               "for fast mode, or use --realtime")
            stats["scans"] += out["accepted"]
    log(f"posted {stats['scans']} scans and {stats['reports']} reports")

    end = t0 + ticks * cfg.tick_s if not cfg.realtime else time.time()
    rollcall = _check(client.get("/rollcall", params={"start": t0, "end": end}), "rollcall")
    attendance = _check(
        client.get("/attendance", params=[("zone", z) for z in zones] + [("start", t0), ("end", end)]),
        "attendance",
    )
    result = SimResult(people, t0, end, rollcall, attendance)
    result.mismatches = compare(result)
    return result


def compare(result: SimResult) -> list[str]:
    state_of = {s["student_id"]: st for st, rows in result.rollcall["students"].items() for s in rows}
    counted = {s["student_id"]: s["counted"] for s in result.attendance["students"]}
    problems = []
    for p in result.residents:
        if p.role == "walkby":
            if counted[p.sid]:
                problems.append(f"{p.sid} (walkby) was counted as attending")
        elif state_of[p.sid] != EXPECTED_STATE[p.role]:
            problems.append(f"{p.sid} ({p.role}) expected {EXPECTED_STATE[p.role]}, got {state_of[p.sid]}")
    return problems


def report(result: SimResult, out=print) -> None:
    rc = result.rollcall
    s = rc["summary"]
    out(f"\n{rc['total']} residents: {s['verified']} verified, {s['suspect']} suspect, "
        f"{s['uncertain']} uncertain, {s['not_detected']} not detected")
    state_of = {x["student_id"]: st for st, rows in rc["students"].items() for x in rows}
    table = defaultdict(Counter)
    for p in result.residents:
        table[p.role][state_of[p.sid]] += 1
    out(f"\n{'role':9}{'n':>5}  " + "  ".join(f"{k:>12}" for k in s))
    for role in ("normal", "handoff", "left", "bt_off", "absent", "walkby"):
        if role in table:
            out(f"{role:9}{sum(table[role].values()):>5}  " + "  ".join(f"{table[role][k]:>12}" for k in s))
    counted = sum(1 for x in result.attendance["students"] if x["counted"])
    out(f"\nattendance (>=70% dwell in any zone): {counted} of {result.attendance['total']} counted")
    if result.mismatches:
        out(f"\n{len(result.mismatches)} unexpected result(s):")
        for m in result.mismatches[:20]:
            out(f"  {m}")
    else:
        out("\nevery resident landed in the expected state")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--api-key")
    ap.add_argument("--model-dir", default="models", help="must be the directory the server loads models from")
    ap.add_argument("--residents", type=int, default=SimConfig.residents)
    ap.add_argument("--minutes", type=float, default=SimConfig.minutes)
    ap.add_argument("--floors", type=int, default=SimConfig.floors)
    ap.add_argument("--enrolled", type=int, default=SimConfig.enrolled)
    ap.add_argument("--handoff-pairs", type=int, default=SimConfig.handoff_pairs)
    ap.add_argument("--left", type=int, default=SimConfig.left)
    ap.add_argument("--bt-off", type=int, default=SimConfig.bt_off)
    ap.add_argument("--absent", type=int, default=SimConfig.absent)
    ap.add_argument("--walkby", type=int, default=SimConfig.walkby)
    ap.add_argument("--seed", type=int, default=SimConfig.seed)
    ap.add_argument("--prefix", default=SimConfig.prefix)
    ap.add_argument("--realtime", action="store_true", help="post at real time instead of fast historical timestamps")
    ap.add_argument("--check", action="store_true", help="exit 1 if any resident lands in an unexpected state")
    args = ap.parse_args(argv)

    import httpx
    cfg = SimConfig(
        residents=args.residents, minutes=args.minutes, floors=args.floors, enrolled=args.enrolled,
        handoff_pairs=args.handoff_pairs, left=args.left, bt_off=args.bt_off, absent=args.absent,
        walkby=args.walkby, seed=args.seed, prefix=args.prefix, model_dir=args.model_dir,
        realtime=args.realtime,
    )
    headers = {"X-API-Key": args.api_key} if args.api_key else {}
    try:
        with httpx.Client(base_url=args.url, headers=headers, timeout=60) as client:
            result = run(client, cfg)
    except SimError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    report(result)
    return 1 if args.check and result.mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
