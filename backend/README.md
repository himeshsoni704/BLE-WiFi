# BLE + Wi-Fi presence with handoff risk

Attendance and roll-call from phones the students already carry, with a **risk
score** for the "handed my phone to a friend" problem.

> ML can flag a handoff. It cannot prove one. Treat the output as a risk score,
> never as proof of identity. A determined cheater who carries both phones and
> imitates the owner's gait still passes. What this catches is casual handoffs and
> phones left behind, which is most real proxying.

This directory is the **backend**: fusion rules, owner model, token validation,
FastAPI service, and a simulator. The Android app, dashboards and ESP32 firmware
are not built yet; the API contract they must follow is below.

## How a verdict is made

Evidence, all collected by the phone or the scanners (nothing is sniffed from
other people's traffic):

| Signal | Source |
|---|---|
| BLE zone | scanner phones / ESP32s post `{scanner_id, token, rssi, ts}`; median RSSI over 15 s, strongest zone above the RSSI floor |
| Wi-Fi zone | student phone reports its BSSID; BSSIDs are registered to zones |
| Owner score | gait model (scikit-learn) on the phone's accel + gyro while walking, smoothed with an EMA |
| Stationary time | seconds since the phone last moved |

Fusion (`app/fusion.py`, pure function, pytest-covered):

| State | Rule |
|---|---|
| **verified** | BLE zone = Wi-Fi zone, owner score high (or none), phone not stationary |
| **suspect** | signals agree but owner score low, or phone stationary past the limit (left-behind) |
| **uncertain** | one signal only, or the two zones disagree |
| **not_detected** | neither |

Behaviour worth knowing:

- The stationary limit defaults to **2 hours**. Phones sit on desks during a lesson,
  so a short limit would flag the whole class. Set `BLEWIFI_STATIONARY_LIMIT_S` lower
  for the hostel roll-call if you want. Recent screen use cancels a left-behind flag.
- A student with **no gait model** is verified on presence alone (the reason says so).
  Set `BLEWIFI_REQUIRE_OWNER=1` to cap them at *uncertain* instead.
- The owner score only updates from **walking** windows; a seated phone gives no
  gait evidence, so the last score is carried for up to 2 hours.
- **Dwell time** (`app/dwell.py`): attendance needs about 70% of the period's
  one-minute slots, so walking past the door is not counted. A student is flagged if
  30% or more of their present slots were suspect.
- Only zone and score are stored. Raw sensor samples are scored in memory and
  discarded, and the reports table has no sensor columns (a test asserts this).
  Scans and reports older than 30 days are deleted at startup.

## Run it

```bash
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
pytest                                   # ~45 s, 91 tests

# server. Fast-mode simulation posts historical timestamps, so disable the skew check.
BLEWIFI_MAX_SKEW_S=0 BLEWIFI_STATIONARY_LIMIT_S=600 \
  uvicorn app.main:create_app --factory

# 280 residents, 30 simulated minutes, exits 1 if any resident lands in the wrong state
python -m simulator.simulate --residents 280 --minutes 30 --check
```

The simulator must share the server's model directory (`--model-dir` =
`BLEWIFI_MODEL_DIR`, default `models`); it refuses to run otherwise. Against a real
server, 280 residents × 30 minutes is about 140k scans and 16k reports and takes
about 90 s. The demo prints, for example: `280 residents: 249 verified, 11 suspect,
15 uncertain, 5 not detected`.

Settings (environment): `BLEWIFI_DB`, `BLEWIFI_MODEL_DIR`, `BLEWIFI_API_KEY`
(shared secret sent as `X-API-Key`; **unset means open, for local dev only**; the
WebSocket takes `?key=`), `BLEWIFI_MAX_SKEW_S` (default 120, 0 disables),
`BLEWIFI_STATIONARY_LIMIT_S`, `BLEWIFI_REQUIRE_OWNER`.

## API

| Endpoint | Purpose |
|---|---|
| `POST /students` `{student_id, name}` | enroll; returns the token `secret` **once**, to provision into the app |
| `GET /students` | list, with `has_model` |
| `PUT /scanners/{id}` `{zone, min_rssi?}` | register a scanner phone / ESP32 and its zone |
| `PUT /bssids/{bssid}` `{zone}` | register a Wi-Fi AP or phone hotspot as a zone |
| `POST /scan` `{scanner_id, token, rssi, ts?}` | one sighting (the ESP32 uses this) |
| `POST /scan/batch` `{scanner_id, scans:[…]}` | Android delivers scan results in batches |
| `POST /device-report` `{token, ts?, wifi?:{bssid,rssi}, window?:{fs,accel,gyro}, interacting?}` | phone report; `window` is a few seconds of raw samples |
| `GET /status/{id}` | verdict, zone, risk, owner score, reasons |
| `GET /zones` | live zone view; `WS /ws` pushes it every 2 s |
| `GET /rollcall?zone=&start=&end=` | one state per student over the window (default last 30 min) |
| `GET /attendance?zone=&start=&end=` | per-student presence ratio, `counted`, `flagged` |

`status`, `zones` accept `at=<unix ts>`; `zones`/`rollcall`/`attendance` are the
dashboard feeds.

### Contract for the Android app

- **Token** (BLE manufacturer data, 8 bytes): `hex(HMAC-SHA256(secret_bytes,
  "ble-wifi/v1|" ‖ int64_be(window))[:8])`, `window = floor(unix_s / 30)`. Known-answer
  vectors are in `tests/test_tokens_features.py`. The server accepts ±1 window.
  Verify it in nRF Connect before writing any scanner code.
- **Device report** every ~30 s: current token, connected BSSID/RSSI, and a 4 s
  accel + gyro window at 50 Hz (m/s² with gravity, rad/s), and `interacting` (screen
  in use recently). The server extracts features, so there is nothing to port to Kotlin.
- Report and scans identify the student by **token only**, so a student cannot report
  as someone else without their secret.

## Owner model

```bash
python -m app.train recordings/ --model-dir models
```

Recordings are `<carrier>__<device>.csv` with header `t,ax,ay,az,gx,gy,gz`. Day-1
protocol: three teammates each walk 10 minutes with their own phone, then swap
phones and repeat. The label is who *carried* the phone, so swaps teach the model
the walker rather than the sensor. The trainer scores each student with
**leave-one-recording-out** (a new session), and labels its single-recording fallback
"optimistic".

Things measured on **synthetic** gaits (`simulator/synth.py`), so they show the
pipeline works and say nothing about real accuracy:

- Record several sessions per person. With one training session the impostor margin
  was thin (others averaged 0.26 against the 0.5 threshold); with three it was 0.02.
- The model only rejects impostors it can generalise from. With 5 people as
  negatives, an *unseen* impostor's windows were accepted ~6% of the time (23% with 2
  negatives, 3% with 10). Train each student against as many other enrolled people as
  possible, and retrain as the enrolled pool grows.
- Real phone-in-pocket vs phone-in-hand will differ from the published gait numbers,
  and gait imitation is a studied attack.

## Not built yet

Android app (student + scanner foreground service), the two dashboards, ESP32
swap-in, class-session start/stop (attendance takes explicit `start`/`end` for now),
per-scanner RSSI calibration UI (`min_rssi` is settable), and a relay-attack defence
(a token can be relayed live within its window).
