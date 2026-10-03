# BLE-WiFi

Presence and attendance from the phones people already carry, using Bluetooth LE and Wi-Fi.

This repository holds **two separate projects**. They do not share a protocol or a server, so pick one:

| | [`campus-presence/`](campus-presence) | [`backend/`](backend) + [`android/`](android) |
|---|---|---|
| **What it is** | Proof-of-Presence: classroom attendance with a faculty dashboard, Wi-Fi room localisation, anomaly review and an AI explainer | A handed-phone risk score: BLE + Wi-Fi presence plus a gait model that flags when someone else is carrying the phone |
| **Server** | `campus-presence/backend` (JWT logins, rotating tokens `cp-student/v1`) | `backend/` (`X-API-Key`, rotating tokens `ble-wifi/v1`) |
| **Dashboard** | Yes, [`campus-presence/frontend`](campus-presence/frontend) | No (JSON API only) |
| **Phone app** | [`campus-presence/android`](campus-presence/android): student, classroom node, Wi-Fi surveyor | [`android/`](android): student and scanner |
| **Use it for** | The demo | The gait / handed-phone experiments |

**For the demo, use `campus-presence/`.** The `android/` app at the top level only works with `backend/`; it cannot
talk to the dashboard's server.

## Quick start (campus-presence)

```bash
cd campus-presence/backend
python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
uvicorn app.main:create_app --factory --host 0.0.0.0          # http://localhost:8000

cd ../frontend && npm install && npm run build                # dashboard at http://localhost:8000/ui/
```

Sign in as `faculty` / `demo1234`, open **Demo Control**, press **Start Simulation**, and explore. No phones needed.
For real phones, see [`campus-presence/android/README.md`](campus-presence/android/README.md). Everything else (how a verdict is
made, configuration, retraining, troubleshooting) is in [`campus-presence/README.md`](campus-presence/README.md), and what the
prototype cannot do is in [`campus-presence/docs/LIMITATIONS.md`](campus-presence/docs/LIMITATIONS.md).

## Repository map

```
campus-presence/
  backend/      FastAPI service, fusion, rules, Isolation Forest, RAG, tests
  frontend/     React + Vite dashboard
  android/      phone app (Kotlin, platform APIs only, no third-party libraries)
  simulation/   synthetic campus for Demo Control
  ml/           training scripts and reports
  docs/         limitations, third-party sources
backend/        separate gait / handed-phone service and simulator (see backend/README.md)
android/        separate app for that service (see android/README.md)
```
