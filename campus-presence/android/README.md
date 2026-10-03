# Campus Presence: Android app

The phone side of Proof-of-Presence. One app, three roles, talking to `../backend` (the same server the
dashboard in `../frontend` reads from):

| Role | What the phone does | Backend calls |
|---|---|---|
| **Student** | Signs in, fetches its own token secret, advertises a rotating token, scans for the classroom marker and for other students, uploads Wi-Fi scans and a heartbeat. | `POST /auth/login`, `GET /auth/provision`, `POST /ble-observation`, `POST /wifi-observation`, `POST /presence`, `GET /wifi/reference` |
| **Classroom node** | Stands in for the smart board: advertises the room's rotating marker and reports the student tokens it hears. | `GET /nodes/provision`, `POST /nodes/heartbeat` (header `X-Node-Key`) |
| **Wi-Fi surveyor** (faculty) | Collects Wi-Fi scans labelled with the room you are standing in, uploads them, and retrains the Wi-Fi model. | `POST /auth/login`, `GET /classrooms`, `POST /wifi/survey`, `POST /wifi/retrain` |

Platform APIs only: no AndroidX, Retrofit or OkHttp. Kotlin, `minSdk 26`, `targetSdk 34`.

The wire format (BLE payloads, HMAC tokens) is in `../backend/app/tokens.py`; `core/Tokens.kt` is its Kotlin twin and
`TokensTest` pins both to the same literal vectors.

## Build

Android Studio (JDK 17): open this `android/` folder and press Run. Or from a terminal with the Android SDK installed
(`ANDROID_HOME` set, or `sdk.dir` in `local.properties`):

```bash
./gradlew assembleDebug        # app/build/outputs/apk/debug/app-debug.apk
adb install -r app/build/outputs/apk/debug/app-debug.apk
./gradlew test                 # 39 JVM unit tests, no device needed
```

Use **physical phones**. Emulators have no usable BLE.

## Run a demo

You need the backend reachable from the phones (same Wi-Fi as the PC):

```bash
cd ../backend && uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000
cd ../frontend && npm install && npm run build      # dashboard at http://<PC-IP>:8000/ui/
```

Allow port 8000 through the PC's firewall. In the app, set **Server** to `http://<PC-IP>:8000` and press **Check server**.
It reports the phone's clock offset: the server rejects readings more than 120 s from its own clock, so fix a wrong
clock before anything else.

**1. Teach it the rooms' Wi-Fi (once, faculty).** Without this the backend cannot place a real phone from Wi-Fi.
Role *Wi-Fi surveyor*, sign in as `faculty` / `demo1234`, *Load rooms*, pick the room you are standing in, *Start collecting
scans*. Android hands out only a few fresh scans a minute, so stay a few minutes until you have 8+, then *Upload collected
scans*. Scans are labelled with the room picked when you pressed Start, and the app refuses to start another room while
scans for the first are waiting (upload or *Discard collected scans*). Walk to a second room and repeat, then
*Retrain Wi-Fi model*. (Developer options, "Wi-Fi scan throttling" off, makes this much faster.)

**2. Start a fresh session (just before the phones).** Dashboard, **Demo Control**, *Start a fresh live session*. A student only
reaches `PRESENT` when their Bluetooth evidence covers a fair share of the time since the session began, so a session that
has been open for half an hour caps out at `LIKELY_PRESENT`.

**3. Classroom node.** Role *Classroom node*, node key `node-204-demo`, *Start*. Put the phone where the board is.
Dashboard: Room 204's marker flips from SIMULATED to LIVE.

**4. Student.** Second phone: role *Student*, `HIMESH` / `demo1234` (also `STU101`..`STU103`), *Start*.
Watch **Live Location** and **Attendance** on the dashboard (session `CS301`, Room 204). After about two minutes of
sustained BLE plus a matching Wi-Fi room the student becomes `PRESENT`. BLE alone never gets there (`REVIEW_REQUIRED` or
`LIKELY_PRESENT`) by design: `PRESENT` needs two independent signal families.

Sessions last 60 minutes. The built-in `CS301` starts when the backend does; use step 2 for a fresh one.
The Student and Node roles run as foreground services (a notification is shown) and keep working with the screen off, but
some phone makers kill them anyway: exempt the app from battery optimisation. The surveyor needs the screen on.

Demo accounts and node keys only exist while the backend runs with `DEMO_MODE=1` (the default).

## What the screen tells you

The status block shows each role's state, the attendance state the **server** reported in its last reply, and a rolling
log: every upload with how many items the server accepted and, for rejects, the reason (`invalid_marker_token`,
`timestamp_out_of_window`, `no_known_aps`, ...).

## Verified, and not

Verified, without a device:

- 39 JVM unit tests (tokens against the backend's literal vectors, aggregation, request bodies against the backend's
  strict schemas, the HTTP client and 401 re-login against a local stub server).
- All sources compile against the real Android 14 (API 34) framework.
- The real framework `ScanFilter`/`ScanRecord` code accepts the app's adverts and the 0xFFFF manufacturer filter.
- The app's own protocol code (tokens, aggregator, request builders, HTTP client, session) driven against a live backend
  in all three roles: a student ends `PRESENT` with the node `LIVE` on the dashboard, and replays, forged marker tokens,
  stale readings and impersonation are all rejected.

**Not verified: the APK build (Android Gradle Plugin), BLE over the air, the runtime-permission and foreground-service flow
on real Android versions, battery behaviour.** Run on two phones before relying on it, and check the status log if a role
does not do what this page says.

## Limits

- **Clear-text HTTP** is enabled so a lab server works. Use HTTPS outside a lab network.
- The password is kept in app-private storage so a 12-hour login can refresh itself; backups are disabled.
- Android restricts Wi-Fi scans (about 4 per 2 minutes in the foreground, far fewer in the background). A scan older than
  100 s is not uploaded and the heartbeat reports Wi-Fi as `throttled`.
- BLE advertising is not supported on every phone. Location services must be on for Wi-Fi scan results.
- A token can be relayed live within its 30 s window; see `../docs/LIMITATIONS.md`.
- `../../android` is the older app for the separate `../../backend` gait demo. It speaks a different protocol and does
  not work with the dashboard.
