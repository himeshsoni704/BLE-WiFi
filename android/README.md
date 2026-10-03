# Android app (student + scanner)

One app, two roles, talking to `../backend` (the gait / handed-phone backend). Pure platform APIs (no Retrofit/Compose).

> **This app does not work with the Proof-of-Presence dashboard.** It speaks the `../backend` protocol (`X-API-Key`,
> `/device-report`, `/scan/batch`, token prefix `ble-wifi/v1`). The dashboard's backend, `../campus-presence/backend`, uses
> JWT logins and a different token format and BLE payload. For the dashboard use the app in
> [`../campus-presence/android`](../campus-presence/android).

| Role | Service | What it does |
|---|---|---|
| Student | `StudentService` | Advertises the rotating 8-byte token (manufacturer id `0xFFFF`), restarting each 30 s window. Every 30 s posts `/device-report` with the connected BSSID/RSSI, a 4 s accel+gyro window (~50 Hz) and `interacting`. |
| Scanner | `ScannerService` | Scans for those tokens and posts them in batches every 5 s to `/scan/batch`. Registers itself with `PUT /scanners/{id}` first. |

## Build and run

1. Open `android/` in **Android Studio** (it generates the Gradle wrapper and downloads the SDK). JDK 17.
2. Run the backend reachable from the phone, e.g. `uvicorn app.main:create_app --factory --host 0.0.0.0`.
   For the emulator use `http://10.0.2.2:8000`; for a real phone use your PC's LAN IP.
   The emulator has no real BLE, so use physical devices for the broadcast/scan path.
3. Enroll a student and copy the secret (shown **once**):
   ```bash
   curl -X POST http://localhost:8000/students -H "Content-Type: application/json" \
        -d '{"student_id":"s1","name":"Student One"}'
   ```
4. **Student phone:** paste the secret, tap *Start student broadcast*.
5. **Scanner phone:** enter an id and zone, tap *Register scanner and start*. Register the Wi-Fi
   AP the student phone is on with `PUT /bssids/{bssid} {"zone": ...}` so both signals agree.
6. Check `GET /status/s1`.

`./gradlew test` runs `TokenTest`, which pins the Kotlin token code to vectors from `app/tokens.py`.

## Notes and limits

- Background BLE advertising and sensor sampling are throttled by some OEM battery managers;
  exempt the app from battery optimisation on the test phones.
- Reading the BSSID needs location permission and location services switched on.
- HTTP cleartext is enabled for local dev. Use HTTPS and a real `BLEWIFI_API_KEY` outside the lab.
- Not run on a device yet: it was written against the contract in `backend/README.md`.
  Verify the advertised token in nRF Connect first, as the backend README advises.
