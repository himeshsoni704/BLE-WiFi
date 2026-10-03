# Limitations

What this prototype does not do, and what its numbers do and do not mean. Read this before trusting an
attendance state or showing the system to people who will.

## What the scores mean

- The attendance score is a **transparent weighted checklist** (`backend/app/config.py`: classroom BLE 35, Wi-Fi 30,
  peer consistency 20, sustained presence 10, face 5, RFID 5). It is **not a validated probability** of presence. The
  weights and thresholds were chosen by hand and can be changed with `FUSION_CONFIG` or `W_<NAME>` variables.
- `PRESENT` needs a score of 70 **and** at least two independent signal families (BLE, Wi-Fi, face, RFID). BLE alone never
  reaches it.
- The Isolation Forest and the deterministic rules only **raise a case for a human to review**. They never mark anyone
  `ABSENT` and never decide misconduct. An "anomaly" is an unusual combination of signals, not proof of anything.
- Each reading is labelled with its provenance (measured, estimated, simulated). Wi-Fi zones are model estimates; their
  confidence is a model output, not a calibrated probability.

## Simulated data

- Everything under *Demo Control* is synthetic and tagged `SIMULATED` everywhere it surfaces. The Wi-Fi model and the
  Isolation Forest that ship with the first run are trained on **synthetic** data, so their reported accuracy says the
  pipeline works and nothing about real-world accuracy.
- The live Wi-Fi model is trained from surveys of real rooms. Its cross-validated accuracy comes from scans taken on the
  same walk, so it is optimistic; a check from a different spot and time is the real test.
- The Isolation Forest is skipped for a student with fewer than 3 distinct nearby devices: it was trained at classroom
  density and its score would be meaningless in a near-empty room.

## Things it cannot prevent

- **Relay.** A token proves a phone knows the secret, not where the phone is. Someone who forwards a live token to a
  phone elsewhere inside its 30 s window passes the token check. The contradiction, token-reuse and impossible-movement
  rules catch some relays after the fact; nothing here stops one.
- **Carrying two phones.** A student who carries a friend's phone into the room produces real evidence for both. The
  "twin" detection looks for two phones whose RSSI traces track each other, and it can be fooled by keeping them apart.
- **Gait / handed-phone detection.** That risk score exists only in the separate `../../backend` project. This system has
  no owner model.
- **A hostile device.** Anyone who roots their phone can fabricate observations that are individually valid.

## Radio realities

- BLE RSSI is noisy and depends on body, orientation, and phone model. It is used as a coarse proximity hint and never
  converted into a distance. Thresholds (`marker_rssi_min -85`, `marker_rssi_good -75`) are untuned guesses.
- Wi-Fi localisation needs rooms to be surveyed first (staff, via the Android surveyor or `POST /wifi/survey`), at least two
  rooms with 8+ scans each. A room nobody surveyed cannot be recognised. Access-point changes need a re-survey.
- Android throttles Wi-Fi scans (about 4 per 2 minutes in the foreground, far fewer in the background), so uploads may be
  cached results. A scan older than 100 s is rejected by the server and the app does not send it.

## The Android app

- BLE advertising is missing on some phones, and several makers kill background services despite a foreground
  notification. Exempt the app from battery optimisation.
- Phone and server clocks must agree within 120 s or readings are rejected as out of window.
- HTTP without TLS is enabled for lab use. Use HTTPS elsewhere. The password is kept in app-private storage so a login can
  refresh itself.
- The app has been verified without a device (unit tests, compile against the Android 14 API, real framework scan code,
  and its protocol code against a live backend). Its APK build and radio behaviour have not been exercised; see
  `android/README.md`.

## Security and privacy posture (prototype)

- With `DEMO_MODE=1` (the default) the server creates demo accounts that share one documented password, and demo node keys
  of the form `node-<room>-demo`. Set `DEMO_MODE=0` for anything but a lab.
- The rate limiter is in memory (one process). The database is SQLite. The JWT secret and ID pepper are generated into
  `backend/data/` on first start unless `JWT_SECRET` / `ID_PEPPER` are set.
- Students are referred to by a keyed hash (`student_key`); the real id and name live only in the `students` table and
  are shown to faculty/admin. The optional Gemini explainer is sent pseudonymous keys unless `GEMINI_SEND_REAL_IDS=1`.
- Observations and locations older than 30 days (`RETENTION_DAYS`) are deleted at startup.
- Face and RFID inputs are accepted only from faculty/admin accounts, and no face system is included.

## Not built

Class-session scheduling beyond the rolling demo session and `POST /sessions`, an ESP32 node (the protocol is documented in
`backend/app/tokens.py`), per-room RSSI calibration, and a relay-attack defence.
